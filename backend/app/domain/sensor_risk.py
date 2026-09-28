"""Вероятность отказа датчика (эпик MOS-248; модель — SL.10, MOS-263, 28.09.2026).

Единственная реализация: её зовёт тик worker (app.worker.sensor_scores), который
пишет pred.sensor_risk; GET /api/sensor-risk (backend/app/api/objects.py) только
читает готовые строки. Обучение сверяет с features() свои признаки поштучно
(docs/proof/2026-09-28-sensor-model/train_sensor_model.py).

Балл — вероятность отказа канала в ближайшие HORIZON_H часов после среза, её даёт
логистическая регрессия, обученная на строках «канал × сутки» (срез 21:00 МСК).
Коэффициенты лежат в sensor_model.json рядом, предсказание — чистый Python.
Моделей две, архитектура одна:
  real      — признаки только из журнала СМВУ (smvu.model_failure_event):
              давность последнего отказа, число отказов за 7/30/90 сут, отказы
              соседей на том же пикете и коллекторе за 7 сут, вид датчика;
  synthetic — те же плюс СИНТЕТИЧЕСКИЙ паспорт (source_system='synthetic-demo'):
              доля выработки срока службы, давность поверки или ТО к интервалу,
              флаг просрочки. Учена на «симулированном мире»: к реальным отказам
              добавлены отказы, досимулированные из паспорта по нормам регламента
              (code/failure_sim.py, docs/proof/2026-09-28-sensor-model/simulation.md).
              Это не качество на реальных данных.

Эпизод, начатый внутри окна планового демонтажа по графику ППР заказчика,
в признаки не идёт: это не отказ, датчик сняли на поверку.
"""

import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

MSK = timezone(timedelta(hours=3))
SRC = "synthetic-demo"
MODEL_PATH = Path(__file__).with_name("sensor_model.json")

CAP = 365  # сутки: давность больше года — «давно», и вклад её уже ноль
# Интервал проверки по виду точки измерения: поверка газоанализатора и датчика
# температуры раз в год, ТО насоса и вентилятора (снимают моточасы) раз в полгода.
# Источники — docs/proof/2026-09-28-sensor-model/simulation.md.
INTERVAL = {"calib": 365, "motohours": 182}

REAL = ["r1", "r30", "n7", "n30", "n90", "nb_picket", "nb_coll"]
SYNTH = ["life", "check", "overdue"]

# Окна планового демонтажа — maint.ppr_window (миграция 060, сид db/seed/ppr_2026.sql).
# В признаки не идут эпизоды окон match = 'sure': Объект 14 = Каппа ДУ (5657)
# и Объект 9 = Мю ДУ (5675), разбор — docs/proof/2026-09-28-sensor-level/ppr-match.md.
ОКНА = """
SELECT plan_row, object_id, sensor_kind, dismantle_from, return_to
  FROM maint.ppr_window
 WHERE match = 'sure' AND object_id IS NOT NULL
"""


def window(plan_row, dismantle_from, return_to):
    """Строка maint.ppr_window → окно: [начало 00:00; вывоз 23:59:59] Москвы."""
    return {
        "from": datetime.combine(dismantle_from, datetime.min.time(), MSK),
        "to": datetime.combine(return_to, datetime.max.time(), MSK).replace(
            microsecond=0
        ),
        "text": f"{dismantle_from:%d.%m.%Y} — плановый демонтаж на поверку по графику "
        f"ППР заказчика ({plan_row}), не отказ",
    }


def plan_windows(rows):
    """Строки запроса ОКНА → {(object_id, sensor_kind): [окно, …]}."""
    окна = {}
    for r in rows:
        окна.setdefault((r["object_id"], r["sensor_kind"]), []).append(
            window(r["plan_row"], r["dismantle_from"], r["return_to"])
        )
    return окна


def in_plan(s, plan):
    """Окно ППР, в котором начался эпизод s, или None."""
    return next((w for w in plan if w["from"] <= s <= w["to"]), None)


def features(starts, at, eq=None, nb=(0, 0)):
    """Признаки строки «канал × срез». starts — начала отказов канала без эпизодов
    ППР (datetime с поясом); nb — (отказов соседей на том же пикете, на том же
    коллекторе) за 7 сут до at, без самого канала; eq — синтетический паспорт
    {"in_service": date, "life": лет, "points": [{"kind", "readings": [(date, …)]}]}
    или None. Отдаёт {имя: число} и служебные поля для текста причин."""
    past = [s for s in starts if s <= at]
    last = max(past, default=None)
    days = min((at - last).total_seconds() / 86400, CAP) if last else CAP

    def n(d):
        return sum(1 for s in past if (at - s).total_seconds() < d * 86400)

    x = {
        "r1": math.exp(-days),
        "r30": math.exp(-days / 30),
        "n7": math.log1p(n(7)),
        "n30": math.log1p(n(30)),
        "n90": math.log1p(n(90)),
        "nb_picket": math.log1p(nb[0]),
        "nb_coll": math.log1p(nb[1]),
        "_days": days,
        "_last": last,
        "_n": (n(7), n(30), n(90)),
        "_nb": tuple(nb),
    }
    if eq:
        today = at.astimezone(MSK).date()
        age = max((today - eq["in_service"]).days, 0)
        x["life"] = min(age / 365.25 / eq["life"], 2.0)
        x["check"] = x["overdue"] = 0.0
        x["_life"] = (age / 365.25, eq["life"])
        for p in eq["points"]:
            iv = INTERVAL[p["kind"]]
            done = [d for d, *_ in p["readings"] if d <= today]
            since = (today - done[-1]).days if done else age
            x["check"] = min(since / iv, 3.0)
            x["overdue"] = float(since > iv)
            x["_check"] = (p["kind"], since, iv, bool(done))
    return x


_MODEL = None


def model():
    global _MODEL
    if _MODEL is None:
        _MODEL = json.loads(MODEL_PATH.read_text())
    return _MODEL


def _logit(m, x, kind):
    z = m["intercept"] + m["kind"].get(kind or "", 0.0)
    return z + sum(w * x[k] for k, w in m["coef"].items())


def level(p, m):
    t = m["thresholds"]
    return "high" if p >= t["high"] else "watch" if p >= t["watch"] else "normal"


def _reasons(m, x, kind):
    """Причины — вклад признаков. Вклад группы в логит — Σ w·(x − x₀), где x₀ —
    канал без отказов и с новым паспортом. Разницу вероятностей p − p₀ делим между
    группами с положительным вкладом пропорционально вкладу: сумма весов причин
    равна тому, на сколько балл выше, чем у здорового канала того же вида."""
    base = m["baseline"]
    z = _logit(m, x, kind)
    z0 = _logit(m, {**x, **base}, kind)
    p, p0 = 1 / (1 + math.exp(-z)), 1 / (1 + math.exp(-z0))
    groups = {
        "recent": ["r1", "r30"],
        "count": ["n7", "n30", "n90"],
        "picket": ["nb_picket"],
        "coll": ["nb_coll"],
        "life": ["life"],
        "check": ["check", "overdue"],
    }
    вклад = {
        g: sum(m["coef"][k] * (x[k] - base[k]) for k in ks if k in m["coef"])
        for g, ks in groups.items()
    }
    плюс = sum(v for v in вклад.values() if v > 0)
    out = []
    for g, v in вклад.items():
        if v <= 0 or p <= p0:
            continue
        w = round((p - p0) * v / плюс, 6)
        if w <= 0:
            continue
        if g == "recent":
            text = (
                f"последний отказ канала {x['_days']:.0f} сут назад "
                f"({x['_last'].astimezone(MSK):%d.%m.%Y})"
            )
        elif g == "count":
            text = "отказов дольше 1 ч за 7 / 30 / 90 сут: {} / {} / {}".format(*x["_n"])
        elif g == "picket":
            text = f"отказов соседних датчиков на том же пикете за 7 сут: {x['_nb'][0]}"
        elif g == "coll":
            text = f"отказов других датчиков на том же коллекторе за 7 сут: {x['_nb'][1]}"
        elif g == "life":
            age, life = x["_life"]
            text = f"выработано {age / life:.0%} срока службы ({age:.1f} из {life} лет)"
        else:
            what, since, iv, done = x["_check"]
            what = "поверки" if what == "calib" else "ТО"
            over = f", просрочено на {since - iv} сут" if since > iv else ""
            text = (
                f"с последней {what} {since} сут при интервале {iv}{over}"
                if done
                else f"{what} не было ни разу"
            )
        kind_ = "synthetic" if g in ("life", "check") else "real"
        out.append({"text": text, "weight": w, "kind": kind_})
    return round(p, 6), out


def score(starts, eq, at, plan=(), nb=(0, 0), kind=None, mode=None):
    """Вероятность, уровень и причины одного канала. mode — "real" или
    "synthetic"; по умолчанию synthetic, если есть паспорт. starts — все начала
    отказов канала: эпизоды в окне ППР (plan) выбрасываются здесь и дают причину
    plan с весом 0. nb — отказы соседей за 7 сут (уже без ППР)."""
    mode = mode or ("synthetic" if eq else "real")
    m = model()["modes"][mode]
    faults, plan_reasons = [], []
    for s in starts:
        if s > at:
            continue
        w = in_plan(s, plan)
        if w is None:
            faults.append(s)
        elif not any(r["text"] == w["text"] for r in plan_reasons):
            plan_reasons.append({"text": w["text"], "weight": 0.0, "kind": "plan"})
    x = features(faults, at, eq if mode == "synthetic" else None, nb)
    if mode == "synthetic" and not eq:
        x.update({"life": 0.0, "check": 0.0, "overdue": 0.0})
    p, reasons = _reasons(m, x, kind)
    reasons = sorted(reasons + plan_reasons, key=lambda r: -r["weight"])
    return {"score": p, "level": level(p, m), "reasons": reasons}


def split(starts, eq, at, plan=(), nb=(0, 0), kind=None):
    """Строка pred.sensor_risk: score_real — вероятность модели без синтетики,
    score_synth — насколько модель «с синтетикой» дала больше (может быть
    отрицательной, миграция 061); reasons — причины модели с синтетикой,
    reasons_real — без неё."""
    real = score(starts, None, at, plan, nb, kind, "real")
    full = score(starts, eq, at, plan, nb, kind, "synthetic")
    return {
        "score_real": real["score"],
        "score_synth": round(full["score"] - real["score"], 6),
        "level_real": real["level"],
        "level_full": full["level"],
        "reasons": full["reasons"],
        "reasons_real": real["reasons"],
    }


def _selfcheck():
    M = model()
    assert set(M["modes"]) == {"real", "synthetic"}, M.keys()
    assert set(M["modes"]["real"]["coef"]) == set(REAL)
    assert set(M["modes"]["synthetic"]["coef"]) == set(REAL + SYNTH)
    at = datetime(2026, 6, 22, 21, tzinfo=MSK)
    eq = {
        "in_service": date(2015, 3, 1),
        "life": 10,
        "points": [{"kind": "calib", "readings": [(date(2025, 12, 1), 1.0)]}],
    }
    окна = plan_windows(
        [
            {
                "plan_row": "Объект 14",
                "object_id": 5657,
                "sensor_kind": "Газовый датчик",
                "dismantle_from": date(2026, 6, 4),
                "return_to": date(2026, 6, 18),
            },
            {
                "plan_row": "Объект 9",
                "object_id": 5675,
                "sensor_kind": "Газовый датчик",
                "dismantle_from": date(2026, 4, 23),
                "return_to": date(2026, 5, 7),
            },
        ]
    )
    plan = окна[(5657, "Газовый датчик")]
    assert plan == [
        {
            "from": datetime(2026, 6, 4, tzinfo=MSK),
            "to": datetime(2026, 6, 18, 23, 59, 59, tzinfo=MSK),
            "text": "04.06.2026 — плановый демонтаж на поверку по графику ППР "
            "заказчика (Объект 14), не отказ",
        }
    ], plan
    assert (5657, "Датчик температуры") not in окна
    gas = "Газовый датчик"
    здоровый = score([], None, at, kind=gas)
    assert здоровый["reasons"] == [] and здоровый["level"] == "normal", здоровый
    # Мю ДУ: эпизод 23.04 09:34 в окне Объекта 9 — не отказ; эпизод за 5 ч до среза
    # вне окна — отказ. Отказ трёхсуточной давности модель считает почти нейтральным:
    # вклад давности уже мал, а счёт за 7 сут с отрицательным весом его гасит.
    мю = окна[(5675, gas)]
    assert score([datetime(2026, 4, 23, 9, 34, tzinfo=MSK)], None, at, мю, kind=gas)[
        "score"
    ] == здоровый["score"]
    assert (
        score([at - timedelta(hours=5)], None, at, мю, kind=gas)["score"]
        > здоровый["score"]
    )
    # ППР-эпизод балл не поднимает и оставляет причину plan с весом 0
    base = score([], eq, at, plan, kind=gas)
    ппр = score([datetime(2026, 6, 4, 9, 22, tzinfo=MSK)] * 2, eq, at, plan, kind=gas)
    assert ппр["score"] == base["score"], (ппр, base)
    assert [r for r in ппр["reasons"] if r["kind"] == "plan"] == [
        {"text": plan[0]["text"], "weight": 0.0, "kind": "plan"}
    ]
    # свежий отказ поднимает сильнее давнего; отказ после at не видим
    fresh = score([at - timedelta(hours=5)], None, at, kind=gas)
    old = score([at - timedelta(days=60)], None, at, kind=gas)
    assert fresh["score"] > old["score"] > здоровый["score"], (fresh, old)
    assert fresh["reasons"][0]["kind"] == "real"
    assert score([at + timedelta(hours=1)], None, at, kind=gas) == здоровый
    # серия отказов — уровень high в обоих режимах
    many = [at - timedelta(hours=h) for h in (3, 20, 50, 100, 200)]
    assert score(many, None, at, kind=gas)["level"] == "high"
    assert score(many, eq, at, kind=gas)["level"] == "high"
    # сумма весов причин — разница с тем же видом без отказов
    r = score(many, None, at, kind=gas)
    assert abs(sum(x["weight"] for x in r["reasons"]) - (r["score"] - здоровый["score"])) < 0.01
    # признаки: давность и счёт
    x = features([at - timedelta(days=2), at - timedelta(days=40)], at)
    assert abs(x["_days"] - 2) < 1e-9 and x["_n"] == (1, 1, 2)
    xe = features([], at, eq)
    assert xe["overdue"] == 0 and 0.5 < xe["check"] < 0.6 and 1 < xe["life"] <= 2
    # split: реальная часть — модель без паспорта, сумма слагаемых — модель с паспортом
    sp = split(many, eq, at, plan, kind=gas)
    full = score(many, eq, at, plan, kind=gas)
    assert sp["score_real"] == score(many, None, at, plan, kind=gas)["score"]
    assert round(sp["score_real"] + sp["score_synth"], 6) == full["score"], (sp, full)
    assert sp["level_full"] == full["level"] and sp["reasons"] == full["reasons"]
    assert not any(x["kind"] == "synthetic" for x in sp["reasons_real"])
    print("sensor_risk selfcheck ok")


if __name__ == "__main__":
    _selfcheck()
