#!/usr/bin/env python3
"""Метрики предиктивной аналитики: уровень соответствия правила и оценка предупреждений.

Два расчёта, перенесённые из опыта En+ (СПА для ТЭЦ):

1. rule_compliance — «процент выполнения диагностического правила». Дословно из ТЗ СПА
   v4.0, п.8.3: «каждой метрике диагностического правила должна быть возможность
   назначения весов, при этом сумма весов всех метрик - 100%. Данный инструмент должен
   позволить оценить в текущий момент времени вероятность наступления того или иного
   события, т.е. ещё до его наступления».

2. evaluate_alerts — то, чего в ТЗ En+ не было и из-за чего приёмку нечем было мерить:
   сопоставление предупреждений системы с реальными отказами из журнала в пределах окна
   упреждения. Даёт precision / recall / медиану упреждения / частоту ложных тревог.
   Это методика проверки целевых метрик Москоллектора: Precision > 0.7, Recall > 0.5,
   горизонт >= 24 ч.

Только стандартная библиотека. Самопроверка: python3 predictive_metrics.py
"""

from datetime import datetime, timedelta
from statistics import median

# ---------------------------------------------------------------- правило


def rule_compliance(weights, fired):
    """Уровень соответствия диагностического правила, %.

    weights: {имя метрики: вес}, сумма весов должна быть ровно 100.
    fired:   множество/список имён метрик, сработавших сейчас.

    Возвращает (процент, список сработавших, список несработавших).
    """
    total = sum(weights.values())
    if round(total, 6) != 100:
        raise ValueError(f"сумма весов метрик должна быть 100, получено {total}")
    unknown = set(fired) - set(weights)
    if unknown:
        raise ValueError(f"метрики вне правила: {sorted(unknown)}")

    hit = [m for m in weights if m in set(fired)]
    miss = [m for m in weights if m not in set(fired)]
    return sum(weights[m] for m in hit), hit, miss


# ------------------------------------------------------------ оценка алертов


def evaluate_alerts(alerts, failures, horizon_hours=24, max_lead_hours=None,
                    observed_object_days=None):
    """Сопоставляет предупреждения с фактическими отказами.

    alerts:   [(object_id, datetime выдачи предупреждения), ...]
    failures: [(object_id, datetime фактического отказа), ...]
    horizon_hours: минимальное упреждение. Предупреждение засчитывается,
        только если выдано НЕ ПОЗЖЕ чем за horizon_hours до отказа — иначе
        ремонтники не успевают, и польза нулевая.
    max_lead_hours: верхняя граница окна. Предупреждение за полгода до отказа
        связано с ним случайно. None — без верхней границы.
    observed_object_days: сколько объекто-суток наблюдали (для частоты ложных тревог).

    Один отказ закрывается одним предупреждением (самым поздним из подходящих —
    оно точнее по времени). Остальные предупреждения по тому же объекту в том же
    окне считаются дублями, а не отдельными ложными: диспетчер видит один инцидент.
    """
    lo = timedelta(hours=horizon_hours)
    hi = timedelta(hours=max_lead_hours) if max_lead_hours else None

    alerts = sorted(alerts, key=lambda a: a[1])
    used = [False] * len(alerts)
    matched_to_failure = [False] * len(alerts)  # включая дубли в окне
    tp, fn, leads = 0, 0, []

    for obj, t_fail in sorted(failures, key=lambda f: f[1]):
        best = None
        for i, (a_obj, t_alert) in enumerate(alerts):
            if a_obj != obj or t_alert > t_fail:
                continue
            lead = t_fail - t_alert
            if lead < lo or (hi and lead > hi):
                continue
            matched_to_failure[i] = True
            if not used[i] and (best is None or t_alert > alerts[best][1]):
                best = i
        if best is None:
            fn += 1
        else:
            used[best] = True
            tp += 1
            leads.append((t_fail - alerts[best][1]).total_seconds() / 3600)

    fp = sum(1 for i in range(len(alerts)) if not matched_to_failure[i])

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    out = {
        "tp": tp, "fp": fp, "fn": fn,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "median_lead_hours": round(median(leads), 1) if leads else None,
        "min_lead_hours": round(min(leads), 1) if leads else None,
        "horizon_hours": horizon_hours,   # нужен verdict(), см. ниже
    }
    if observed_object_days:
        out["false_alarms_per_1000_object_days"] = round(
            fp / observed_object_days * 1000, 2)
    return out


def verdict(metrics, min_precision=0.7, min_recall=0.5, min_lead_hours=24):
    """Приёмочное решение по целевым метрикам Москоллектора.

    Про lead_time. evaluate_alerts уже выбросила все предупреждения с
    упреждением меньше horizon_hours, поэтому при horizon_hours >= min_lead_hours
    проверка упреждения не может провалиться — она ничего не измеряет.
    В этом случае возвращаем None вместо True, чтобы вакуумная проверка
    не выглядела на приёмке как пройденная. Чтобы реально померить упреждение,
    прогоните evaluate_alerts(..., horizon_hours=0) и смотрите распределение.
    """
    horizon = metrics.get("horizon_hours", 0)
    if horizon >= min_lead_hours:
        lead_ok = None          # не измерено: отбор совпадений уже гарантировал порог
    else:
        lead_ok = (metrics["min_lead_hours"] or 0) >= min_lead_hours
    checks = {
        "precision": metrics["precision"] >= min_precision,
        "recall": metrics["recall"] >= min_recall,
        "lead_time": lead_ok,
    }
    passed = all(v for v in checks.values() if v is not None)
    return passed, checks


# ---------------------------------------------------------------- самопроверка

def _demo():
    # Пример из md: правило «неисправность подшипникового узла».
    w = {"рост вибрации": 50, "рост температуры": 30, "падение расхода масла": 20}
    pct, hit, miss = rule_compliance(w, ["рост вибрации", "рост температуры"])
    assert pct == 80, pct
    assert miss == ["падение расхода масла"]
    assert rule_compliance(w, [])[0] == 0
    assert rule_compliance(w, list(w))[0] == 100

    try:
        rule_compliance({"a": 60, "b": 30}, ["a"])
        raise AssertionError("должно было упасть: сумма весов 90")
    except ValueError:
        pass

    d = lambda s: datetime.fromisoformat(s)

    alerts = [
        ("K-101", d("2026-01-01 08:00")),   # TP: за 72 ч до отказа
        ("K-101", d("2026-01-02 08:00")),   # дубль в том же окне, не FP
        ("K-205", d("2026-02-10 12:00")),   # FP: отказа не было
        ("K-307", d("2026-03-01 23:00")),   # слишком поздно (2 ч) -> не спасает
    ]
    failures = [
        ("K-101", d("2026-01-04 08:00")),
        ("K-307", d("2026-03-02 01:00")),   # FN: предупредили за 2 ч
        ("K-409", d("2026-03-15 06:00")),   # FN: не предупредили вовсе
    ]

    m = evaluate_alerts(alerts, failures, horizon_hours=24,
                        observed_object_days=2000)
    assert m["tp"] == 1, m
    assert m["fn"] == 2, m
    # K-307 выдан позже порога и ни к чему не привязан -> FP; K-205 -> FP
    assert m["fp"] == 2, m
    assert m["precision"] == round(1 / 3, 3), m
    assert m["recall"] == round(1 / 3, 3), m
    assert m["median_lead_hours"] == 48.0, m  # берём самое позднее подходящее
    assert m["false_alarms_per_1000_object_days"] == 1.0, m

    ok, checks = verdict(m)
    assert ok is False and checks["precision"] is False

    good = evaluate_alerts(
        [("A", d("2026-01-01 00:00")), ("B", d("2026-01-01 00:00"))],
        [("A", d("2026-01-03 00:00")), ("B", d("2026-01-02 12:00"))],
        horizon_hours=24, observed_object_days=500)
    assert good["precision"] == 1.0 and good["recall"] == 1.0, good
    assert verdict(good)[0] is True

    print("rule_compliance: 50+30 из 100 ->", pct, "% | сработали:", hit)
    print("evaluate_alerts:", m)
    print("verdict:", verdict(m))
    print("OK")


if __name__ == "__main__":
    _demo()
