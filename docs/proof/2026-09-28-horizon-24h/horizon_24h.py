#!/usr/bin/env python3
"""Модель lgbm-v3 (обучена на 720 ч) на горизонте 24 ч: той же методикой, что замер 22.09.2026.

Ничего не считает своим отбором: инциденты D5, склейку 60 минут, связку «префикс ->
коллектор» и evaluate_alerts берёт `замер()` из code/check_metrics_report.py. Отсюда
меняются только окно зачёта (через глобальные ГОРИЗОНТ_Ч, ВЕРХ_ОКНА_Ч модуля) и набор
предупреждений (через параметр `фильтр`, который подменяет список целиком).
Политику выдачи предупреждений берём у ML-команды: simulate() из
ml-model/autoresearch_v3/prepare.py (файл совпадает по SHA-256 с кодом модели).

Запуск из корня репозитория:
    uv run --with pandas==2.3.3 --with duckdb==1.4.3 python3 \
        docs/proof/2026-09-28-horizon-24h/horizon_24h.py
"""

import csv
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "code"))
sys.path.insert(0, os.path.join(ROOT, "ml-model/autoresearch_v3"))

import check_metrics_report as cmr  # noqa: E402
import model_failure  # noqa: E402
import pandas as pd  # noqa: E402
import prepare  # noqa: E402  simulate() ML-команды

СКЛЕЙКА = model_failure.загрузить_контракт()["incident"]["merge_minutes"]
ПОРОГ_МОДЕЛИ = 0.63  # model_meta.json: alert_threshold
СРОК_ЖИЗНИ_МОДЕЛИ = 720  # model_meta.json: horizon_h, он же срок жизни предупреждения
# Суточная выдача. Тики стоят ровно через 24 ч (23:59:59), а simulate() гасит предупреждение
# только при возрасте строго больше срока, поэтому при сроке 24 тик следующих суток
# застал бы его открытым и новое не выдал. 23 ч — «прогноз на сутки, каждые сутки заново».
СУТКИ = 23

scores = pd.read_csv(os.path.join(HERE, "scores_test.csv"), parse_dates=["t"])
incidents = pd.read_csv(
    os.path.join(ROOT, "docs/proof/2026-09-21-metrics/our-metric/incidents_pfx.csv"),
    parse_dates=["t_start"],
)


def мерить(lo, hi, alerts=None):
    """Замер 22.09 с окном зачёта [lo, hi] ч; alerts=None — alerts.csv как есть."""
    cmr.ГОРИЗОНТ_Ч, cmr.ВЕРХ_ОКНА_Ч = lo, hi
    фильтр = None if alerts is None else (lambda _: alerts)
    return cmr.замер(отказы_файл=cmr.ОТКАЗЫ_D5, склейка=СКЛЕЙКА, фильтр=фильтр)


def выдача(p_scores, thr, life_h):
    """Предупреждения политики «одно открытое» ML-команды: [(префикс, момент), ...]."""
    al = prepare.simulate(p_scores, incidents, thr, life_h)
    return [
        (str(p), datetime.fromisoformat(str(t))) for p, t in zip(al["pfx"], al["t"])
    ]


def строка(имя, m):
    print(
        f"{имя:<52} P {m['precision']:.3f}  R {m['recall']:.3f}  "
        f"TP {m['tp']:>3}  FP {m['fp']:>3}  FN {m['fn']:>3}  дублей {m['дублей']:>3}  "
        f"предупр. {m['предупреждений']:>4}  инцид. {m['инцидентов']}"
    )
    return m


print(f"склейка {СКЛЕЙКА} мин, цель D5, окно 01.04–30.06.2026\n")

# 1. Контроль: alerts.csv, окно зачёта [24, 168] — числа отчёта 22.09.2026.
к = строка("контроль: alerts.csv, окно [24, 168] ч", мерить(24, 168))
assert (к["precision"], к["recall"], к["tp"], к["fp"], к["fn"]) == (
    0.643,
    0.544,
    92,
    51,
    77,
), к
# Пересчитанные скоры дают ту же выдачу: 166 предупреждений, совпадают поштучно.
повтор = выдача(scores, ПОРОГ_МОДЕЛИ, СРОК_ЖИЗНИ_МОДЕЛИ)
with open(
    os.path.join(ROOT, "docs/proof/2026-09-21-metrics/our-metric/alerts.csv")
) as f:
    исходные = sorted(
        (r["pfx"], datetime.fromisoformat(r["t"])) for r in csv.DictReader(f)
    )
assert sorted(повтор) == исходные, (len(повтор), len(исходные))
строка("контроль: скоры -> simulate(0,63; 720 ч), [24, 168]", мерить(24, 168, повтор))

# 2. Та же выдача, окно зачёта 0…24 ч (и 1…24 ч для справки).
print()
строка("24 ч: alerts.csv, окно [0, 24] ч", мерить(0, 24))
строка("24 ч: alerts.csv, окно [1, 24] ч", мерить(1, 24))

# 3. Кривая порога на 24 ч, две политики выдачи.
for life in (СРОК_ЖИЗНИ_МОДЕЛИ, СУТКИ):
    print(f"\nкривая, срок жизни предупреждения {life} ч, окно зачёта [0, 24] ч")
    for thr in (0.3, 0.4, 0.5, 0.6, 0.63, 0.7, 0.75, 0.8, 0.85, 0.9, 0.92, 0.94, 0.95):
        m = мерить(0, 24, выдача(scores, thr, life))
        f1 = 2 * m["precision"] * m["recall"] / (m["precision"] + m["recall"] or 1)
        строка(f"  порог {thr:.2f}  F1 {f1:.3f}", m)

# 4. Без модели, те же моменты решения.
print("\nбез модели, окно зачёта [0, 24] ч")
недавность = scores.assign(p=(scores["kind"] == "rearm").astype(float))
for life in (СРОК_ЖИЗНИ_МОДЕЛИ, 168, СУТКИ):
    строка(
        f"  недавность (rearm), срок жизни {life} ч",
        мерить(0, 24, выдача(недавность, 0.5, life)),
    )
всегда = scores.assign(p=1.0)
строка(
    "  тревога на всех коллекторах каждый день, 24 ч",
    мерить(0, 24, выдача(всегда, 0.5, СУТКИ)),
)
строка(
    "  недавность, срок 720 ч, окно [24, 168] (к контролю)",
    мерить(24, 168, выдача(недавность, 0.5, СРОК_ЖИЗНИ_МОДЕЛИ)),
)
