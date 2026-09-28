"""Балл риска по датчику (эпик MOS-248, 28.09.2026).

Единственная реализация формулы: её зовёт тик worker (app.worker.sensor_scores),
который пишет pred.sensor_risk; GET /api/sensor-risk (backend/app/api/objects.py)
только читает готовые строки. Реальную часть и синтетическую добавку делит split().

Балл = 0,5·давность последнего отказа + 0,3·число отказов за 90 сут (реальные
отказы smvu.model_failure_event — то же определение, что у таблицы «Отказы
по каналам» карточки) + 0,1·выработка срока службы + 0,1·давность поверки/ТО
(паспорт СИНТЕТИЧЕСКИЙ, source_system='synthetic-demo', см. db/seed/sensor_demo.sql).

Эпизод, начатый внутри окна планового демонтажа по графику ППР заказчика,
в балл не идёт: это не отказ, датчик сняли на поверку.
Разбор реальных отказов — docs/proof/2026-09-28-sensor-level/kappa-du.md.
"""

import math
from datetime import date, datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))
SRC = "synthetic-demo"

# ponytail: ручные веса, заменить обученной моделью, когда будут реальные данные.
W_RECENT, W_COUNT, W_LIFE, W_CHECK = 0.50, 0.30, 0.10, 0.10
RECENT_TAU = 30  # сутки: отказ месячной давности даёт e^-1 от полного веса
COUNT_FULL = 5  # пять отказов за 90 суток — полный вес
HIGH, WATCH = 0.5, 0.25
# Интервал проверки по виду точки измерения: поверка газоанализатора раз в год,
# ТО насоса и вентилятора (снимают моточасы) раз в полгода.
INTERVAL = {"calib": 365, "motohours": 182}

# Окна планового демонтажа — maint.ppr_window (миграция 060, сид db/seed/ppr_2026.sql,
# 26 строк графика ППР заказчика). В балл идут только окна match = 'sure':
# Объект 14 = Каппа ДУ (5657) и Объект 9 = Мю ДУ (5675), разбор —
# docs/proof/2026-09-28-sensor-level/ppr-match.md. Тик читает их запросом ОКНА
# и передаёт сюда аргументом, поэтому самопроверка обходится без базы.
ОКНА = """
SELECT plan_row, object_id, sensor_kind, dismantle_from, return_to
  FROM maint.ppr_window
 WHERE match = 'sure' AND object_id IS NOT NULL
"""


def window(plan_row, dismantle_from, return_to):
    """Строка maint.ppr_window → окно для score(): [начало 00:00; вывоз 23:59:59] Москвы."""
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


def level(s):
    return "high" if s >= HIGH else "watch" if s >= WATCH else "normal"


def score(starts, eq, at, plan=()):
    """starts — моменты начала отказов канала (datetime с поясом);
    eq — {"in_service": date, "life": лет, "points": [{"kind", "readings": [(date, …)]}]}
    или None; plan — окна канала из plan_windows(). Отдаёт {"score", "level", "reasons"}."""
    reasons, faults = [], []
    for s in starts:
        if s > at:
            continue
        w = next((w for w in plan if w["from"] <= s <= w["to"]), None)
        if w is None:
            faults.append(s)
        elif not any(r["text"] == w["text"] for r in reasons):
            reasons.append({"text": w["text"], "weight": 0.0, "kind": "plan"})

    total = 0.0

    def add(w, text, kind):
        nonlocal total
        total += w
        reasons.append({"text": text, "weight": round(w, 3), "kind": kind})

    last = max(faults, default=None)
    if last:
        days = (at - last).total_seconds() / 86400
        add(
            W_RECENT * math.exp(-days / RECENT_TAU),
            f"последний отказ канала {days:.0f} сут назад "
            f"({last.astimezone(MSK):%d.%m.%Y})",
            "real",
        )
    n90 = sum(1 for s in faults if at - s < timedelta(days=90))
    if n90:
        add(
            W_COUNT * min(n90 / COUNT_FULL, 1.0),
            f"отказов дольше 1 ч за 90 сут: {n90}",
            "real",
        )
    if eq:
        today = at.astimezone(MSK).date()
        age = max((today - eq["in_service"]).days, 0) / 365.25
        add(
            W_LIFE * min(age / eq["life"], 1.0),
            f"выработано {age / eq['life']:.0%} срока службы ({age:.1f} из {eq['life']} лет)",
            "synthetic",
        )
        for p in eq["points"]:
            what = "поверки" if p["kind"] == "calib" else "ТО"
            done = [d for d, *_ in p["readings"] if d <= today]
            if not done:
                add(W_CHECK, f"{what} не было ни разу", "synthetic")
                continue
            since, iv = (today - done[-1]).days, INTERVAL[p["kind"]]
            over = f", просрочено на {since - iv} сут" if since > iv else ""
            add(
                W_CHECK * min(since / (2 * iv), 1.0),
                f"с последней {what} {since} сут при интервале {iv}{over}",
                "synthetic",
            )
    reasons.sort(key=lambda r: -r["weight"])
    total = round(total, 3)
    return {"score": total, "level": level(total), "reasons": reasons}


def split(starts, eq, at, plan=()):
    """Балл двумя слагаемыми для pred.sensor_risk (MOS-252): score_real — без
    паспорта, score_synth — что добавил синтетический паспорт. Причины — полного
    балла; синтетические выбрасывает метод при ?synthetic=0."""
    real = score(starts, None, at, plan)
    full = score(starts, eq, at, plan)
    return {
        "score_real": real["score"],
        "score_synth": round(full["score"] - real["score"], 3),
        "level_real": real["level"],
        "level_full": full["level"],
        "reasons": full["reasons"],
    }


def _selfcheck():
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
    # Мю ДУ: эпизод 1535 (23.04 09:34) в окне Объекта 9, отказ 08.05 — уже нет
    мю = окна[(5675, "Газовый датчик")]
    assert score([datetime(2026, 4, 23, 9, 34, tzinfo=MSK)], None, at, мю)["score"] == 0
    assert score([datetime(2026, 5, 8, 0, 0, tzinfo=MSK)], None, at, мю)["score"] > 0
    base = score([], eq, at, plan)
    # ППР-эпизод балл не поднимает и оставляет причину plan с весом 0
    ппр = score([datetime(2026, 6, 4, 9, 22, tzinfo=MSK)] * 2, eq, at, plan)
    assert ппр["score"] == base["score"], (ппр, base)
    assert [r for r in ппр["reasons"] if r["kind"] == "plan"] == [
        {"text": plan[0]["text"], "weight": 0.0, "kind": "plan"}
    ]
    # тот же эпизод у фазы (окна нет) — отказ
    assert (
        score([datetime(2026, 6, 4, 9, 22, tzinfo=MSK)], eq, at)["score"]
        > base["score"]
    )
    # свежий отказ поднимает, отказ после at не видим
    fresh = score([at - timedelta(hours=5)], eq, at)
    assert fresh["score"] > base["score"] + 0.3 and fresh["level"] == "high", fresh
    assert score([at - timedelta(days=30)], eq, at)["level"] == "watch"
    assert score([at + timedelta(hours=1)], eq, at)["score"] == base["score"]
    # пять свежих отказов — high; без паспорта балл только из отказов
    many = score([at - timedelta(days=d) for d in range(5)], None, at)
    assert many["level"] == "high" and many["score"] == 0.8, many
    assert [level(x) for x in (0.5, 0.499, 0.25, 0.249)] == [
        "high",
        "watch",
        "watch",
        "normal",
    ]
    assert base["level"] == "normal"
    # split: реальная часть — балл без паспорта, сумма слагаемых — полный балл
    sp = split([at - timedelta(days=30)], eq, at, plan)
    full = score([at - timedelta(days=30)], eq, at, plan)
    assert sp["score_real"] == score([at - timedelta(days=30)], None, at)["score"]
    assert round(sp["score_real"] + sp["score_synth"], 3) == full["score"], (sp, full)
    assert sp["level_full"] == full["level"] and sp["reasons"] == full["reasons"]
    assert (
        split([], None, at)["score_synth"] == 0 and split([], eq, at)["score_real"] == 0
    )
    print("sensor_risk selfcheck ok")


if __name__ == "__main__":
    _selfcheck()
