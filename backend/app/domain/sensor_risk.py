"""Балл риска по датчику (демо на «объект Каппа ДУ», 28.09.2026).

Одна реализация на двоих: GET /api/sensor-risk (backend/app/api/objects.py)
и code/synth_sensor_level.py импортируют score() отсюда.

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

# Окна планового демонтажа. Источник: docs/График ППР АКМ на 2026г. РЭК.xlsx,
# строка «Июнь, Объект 14, 33 шт., начало демонтажа 04.06.2026, вывоз из ОМ
# 18.06.2026». Объект 14 графика = «объект Каппа ДУ» (5657): 33 газовых канала
# ушли в «Неисправен» 04.06.2026 09:22–10:38 на ~11 суток.
# ponytail: константа; брать из maint.plan, когда заказчик даст график
# с нашими именами объектов.
ППР = [
    {
        "node": 5657,
        "sensor_kind": "Газовый датчик",
        "from": datetime(2026, 6, 4, tzinfo=MSK),
        "to": datetime(2026, 6, 18, 23, 59, 59, tzinfo=MSK),
        "text": "04.06.2026 — плановый демонтаж на поверку по графику ППР "
        "заказчика (Объект 14), не отказ",
    }
]


def plan_windows(node, sensor_kind):
    return [w for w in ППР if w["node"] == node and w["sensor_kind"] == sensor_kind]


def level(s):
    return "high" if s >= HIGH else "watch" if s >= WATCH else "normal"


def score(starts, eq, at, plan=()):
    """starts — моменты начала отказов канала (datetime с поясом);
    eq — {"in_service": date, "life": лет, "points": [{"kind", "readings": [(date, …)]}]}
    или None; plan — окна plan_windows(). Отдаёт {"score", "level", "reasons"}."""
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


def _selfcheck():
    at = datetime(2026, 6, 22, 21, tzinfo=MSK)
    eq = {
        "in_service": date(2015, 3, 1),
        "life": 10,
        "points": [{"kind": "calib", "readings": [(date(2025, 12, 1), 1.0)]}],
    }
    plan = plan_windows(5657, "Газовый датчик")
    base = score([], eq, at, plan)
    # ППР-эпизод балл не поднимает и оставляет причину plan с весом 0
    ппр = score([datetime(2026, 6, 4, 9, 22, tzinfo=MSK)] * 2, eq, at, plan)
    assert ппр["score"] == base["score"], (ппр, base)
    assert [r for r in ппр["reasons"] if r["kind"] == "plan"] == [
        {"text": ППР[0]["text"], "weight": 0.0, "kind": "plan"}
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
    print("sensor_risk selfcheck ok")


if __name__ == "__main__":
    _selfcheck()
