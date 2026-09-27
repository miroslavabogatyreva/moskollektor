"""autoresearch v3: протокол «одно открытое предупреждение». ЭТОТ ФАЙЛ ЗАМОРОЖЕН.

Агент цикла правит только `train.py`. Здесь — данные, фолды, политика выдачи
предупреждений и единственная метрика.

Почему версия 3. В версии 2 предупреждение выдавалось раз в сутки по каждому
коллектору. `evaluate_alerts` засчитывает верную серию один раз (остальное —
дубли), а ложную — за каждые сутки. Правило без модели «после подтверждённого
отказа открыть одно предупреждение на 7 суток» на тех же фолдах даёт Precision
0,56 при Recall 0,50; настроенная суточная модель — 0,11 при том же Recall
(`docs/research/alert_policy_20260920/README.md`).

Политика (функция `simulate`). У коллектора не больше одного открытого
предупреждения. Оно закрывается, когда на коллекторе стартовал инцидент
(подтвердилось) или прошло `WINDOW_H` часов (не подтвердилось). Пока открыто —
новых нет. Это и есть экран диспетчера: «коллектор под наблюдением».

Моменты решения — когда модель вправе открыть предупреждение: конец суток
(`tick`) и час после старта инцидента (`rearm`, раньше отказ D5 системе не виден).
`train.py` может добавить свои моменты; правила — в `program.md`.

Метрика. Для порога θ политика даёт набор предупреждений, его оценивает
`evaluate_alerts(horizon_hours=0, max_lead_hours=168)` из методики приёмки.
По сетке порогов строится огибающая `P_env(r)` — лучшая Precision при Recall
не ниже `r`. `val_score` фолда — среднее `P_env` по `RECALL_GRID`; по одной точке
`r = 0,5` решение прыгало бы от одного инцидента. Цель постановки: Precision
выше 0,7 при Recall выше 0,5 — строка `p_at_r50` в выводе.

Минимального упреждения нет (`MIN_LEAD_H = 0`): постановщик приравнял прогноз
к предупреждению (чат, сообщение 422). Для честности у рабочей точки печатаются
справочные Precision и Recall при упреждении не меньше 1 и 24 часов.

Горизонты. Срок жизни предупреждения — он же окно метки — не один, а три:
7, 14 и 21 сутки (`HORIZONS_H`). Правило без модели показывает, что Precision
при Recall 0,5 определяется прежде всего горизонтом: 0,56 при 7 сутках, 0,66 при
14, 0,72 при 21 (`docs/research/alert_policy_20260920/README.md`, раздел 9).
Семь суток — шаг внутреннего осмотра коллектора по Регламенту, 14–21 — окно
планирования внеочередных работ между ТО. Цикл оптимизирует среднее по трём
горизонтам: правка, полезная на одном горизонте и вредная на другом, не нужна.
Метка, зазоры и политика считаются для каждого горизонта свои.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from prepare_data import (  # noqa: F401 — `train.py` ходит сюда одним `import prepare`
    CONFIRM_H, DATA, ES_DAYS, FOLDS, GAP_DAYS, HORIZONS_H, IDENTITY_FEATS, KEY_COLS,
    SCORE_END, SEAL_DAY, TRAIN_FROM, WINDOW_H, _all_rows, connect, es_split,
    feature_cols, folds, gap_days, label, load_channel_frame, load_episodes,
    load_failures, load_incidents,
)

PREDICTIVE_METRICS = Path("data/02_interim/mk/predictive_metrics.py")

# --- политика и метрика (менять нельзя) ------------------------------------------
MIN_LEAD_H = 0                   # минимальное упреждение, часы
RECALL_GRID = (0.40, 0.45, 0.50, 0.55, 0.60)
TARGET_P, TARGET_R = 0.70, 0.50  # цель постановки задачи
REF_LEADS_H = (1, 24)            # справочные упреждения для рабочей точки
# Сетка порогов — квантили скоров моментов фолда. Густо сверху: предупреждений
# за квартал сотни, моментов — тысячи, рабочие точки лежат в верхних 15 %.
QUANTILES = tuple(np.round(np.concatenate([
    np.arange(0.00, 0.85, 0.05), np.arange(0.85, 0.995, 0.005),
    np.array([0.995, 0.9975, 0.999])]), 4))

TRAIN_BUDGET_S = 300.0           # wall-clock на признаки и обучение: 3 горизонта × 4 фолда

# Порог принятия правки. Разброс сводного `val_score` по пяти наборам сидов — std 0,0022
# (`AUTORESEARCH_DIR=autoresearch_v3 python3 scripts/ml_autoresearch_noise.py`), два std —
# 0,0045. Порог взят выше: в ячейке «горизонт × фолд» 76–159 инцидентов, и сдвиг
# нескольких предупреждений двигает Precision сильнее, чем смена сидов. Правило keep
# целиком — в `program.md`. Справочная константа: `train.py` её не читает.
KEEP_DELTA = 0.0075


def load_predictive_metrics():
    """Импорт методики по пути. Копировать её в проект нельзя (бриф разд. «Данные»)."""
    if not PREDICTIVE_METRICS.exists():
        raise FileNotFoundError(f"нет {PREDICTIVE_METRICS}")
    spec = importlib.util.spec_from_file_location("predictive_metrics", PREDICTIVE_METRICS)
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("predictive_metrics", mod)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------- политика


def simulate(scores: pd.DataFrame, incidents: pd.DataFrame, thr: float,
             window_h: int = WINDOW_H) -> pd.DataFrame:
    """Политика «одно открытое предупреждение» при пороге `thr`.

    `scores`: `pfx`, `t`, `p` — скор каждого момента решения. Возвращает
    предупреждения `pfx`, `t`. Старт инцидента система узнаёт через `CONFIRM_H`:
    до этого предупреждение считается открытым, и новое не выдаётся. Инцидент
    закрывает только предупреждение, открытое до его старта. При равном времени
    подтверждение обрабатывается раньше решения — так момент `rearm` видит
    коллектор уже без предупреждения.
    """
    win = np.timedelta64(window_h, "h")
    confirm = np.timedelta64(CONFIRM_H, "h")
    starts_by = {p: np.sort(g["t_start"].to_numpy().astype("datetime64[us]"))
                 for p, g in incidents.groupby("pfx")}
    none = np.array([], dtype="datetime64[us]")
    out_pfx, out_t = [], []
    for pfx, g in scores.groupby("pfx", sort=True):
        t = g["t"].to_numpy().astype("datetime64[us]")
        p = g["p"].to_numpy(dtype=float)
        s = starts_by.get(pfx, none)
        times = np.concatenate([s + confirm, t])
        kind = np.concatenate([np.zeros(len(s), dtype=int), np.ones(len(t), dtype=int)])
        open_at = None
        for j in np.lexsort((kind, times)):
            if open_at is not None and times[j] - open_at > win:
                open_at = None                                  # не подтвердилось
            if kind[j] == 0:
                if open_at is not None and open_at < s[j]:
                    open_at = None                              # подтвердилось
            elif open_at is None and p[j - len(s)] >= thr:
                open_at = t[j - len(s)]
                out_pfx.append(pfx)
                out_t.append(open_at)
    return pd.DataFrame({"pfx": out_pfx, "t": np.array(out_t, dtype="datetime64[us]")})


# -------------------------------------------------------------------- метрика


def _pairs(objs, times) -> list[tuple]:
    ts = pd.to_datetime(pd.Series(times)).to_numpy().astype("datetime64[us]").astype(object)
    return list(zip(np.asarray(objs), ts))


def envelope(tab: pd.DataFrame, recall: float) -> float:
    """Лучшая Precision среди порогов с Recall не ниже `recall`; нет таких — 0."""
    ok = tab[tab["recall"] >= recall]
    return float(ok["precision"].max()) if len(ok) else 0.0


def evaluate(scores: pd.DataFrame, incidents: pd.DataFrame,
             window_h: int = WINDOW_H) -> tuple[float, pd.DataFrame, dict]:
    """`(val_score, таблица по порогам, рабочая точка)` одного фолда.

    Рабочая точка — порог с лучшей Precision при Recall не ниже `TARGET_R`; для
    неё же справочно считаются Precision и Recall при упреждении из `REF_LEADS_H`.
    """
    for col in ("pfx", "t", "p"):
        if col not in scores.columns:
            raise KeyError(f"в scores нет колонки {col}")
    if scores["p"].isna().any():
        raise ValueError("в scores есть NaN")
    pm = load_predictive_metrics()
    fails = _pairs(incidents["pfx"], incidents["t_start"])
    n_days = int(pd.to_datetime(scores["t"]).dt.normalize().nunique())

    rows, alert_sets = [], []
    for thr in np.unique(np.quantile(scores["p"].to_numpy(dtype=float), QUANTILES)):
        al = simulate(scores, incidents, float(thr), window_h)
        m = pm.evaluate_alerts(_pairs(al["pfx"], al["t"]), fails,
                               horizon_hours=MIN_LEAD_H, max_lead_hours=window_h)
        tp, fp, fn = m["tp"], m["fp"], m["fn"]
        rows.append({"threshold": float(thr), "alerts": len(al),
                     "per_day": round(len(al) / n_days, 2) if n_days else 0.0,
                     "tp": tp, "fp": fp, "fn": fn, "dup": len(al) - tp - fp,
                     "precision": tp / (tp + fp) if tp + fp else 0.0,
                     "recall": tp / (tp + fn) if tp + fn else 0.0,
                     "median_lead_h": m["median_lead_hours"]})
        alert_sets.append(al)
    tab = pd.DataFrame(rows)
    score = float(np.mean([envelope(tab, r) for r in RECALL_GRID]))

    point: dict = {"p_at_r50": envelope(tab, TARGET_R)}
    hi_p = tab[tab["precision"] >= TARGET_P]
    point["r_at_p70"] = float(hi_p["recall"].max()) if len(hi_p) else 0.0
    ok = tab[tab["recall"] >= TARGET_R]
    if len(ok):
        i = ok["precision"].idxmax()
        point.update({k: tab.loc[i, k] for k in
                      ("threshold", "alerts", "per_day", "tp", "fp", "fn", "median_lead_h")})
        point["recall"] = float(tab.loc[i, "recall"])
        al = alert_sets[i]
        for lead in REF_LEADS_H:
            ref = pm.evaluate_alerts(_pairs(al["pfx"], al["t"]), fails,
                                     horizon_hours=lead, max_lead_hours=window_h)
            point[f"p_lead{lead}h"] = ref["precision"]
            point[f"r_lead{lead}h"] = ref["recall"]
    return score, tab, point


def evaluate_folds(pairs: list[tuple[pd.DataFrame, pd.DataFrame]],
                   window_h: int = WINDOW_H) -> dict:
    """Четыре фолда одного горизонта: `val_score` — среднее, `val_min` — худший фолд."""
    if len(pairs) != len(FOLDS):
        raise ValueError(f"ждали {len(FOLDS)} фолдов, дали {len(pairs)}")
    rows, tabs = [], []
    for i, ((start, end), (scores, inc)) in enumerate(zip(FOLDS, pairs), 1):
        score, tab, point = evaluate(scores, inc, window_h)
        rows.append({"fold": i, "from": start, "to": end, "moments": len(scores),
                     "incidents": len(inc), "val_score": score, **point})
        tabs.append(tab)
    by_fold = pd.DataFrame(rows)
    s = by_fold["val_score"]
    res = {"val_score": float(s.mean()), "val_min": float(s.min()),
           "val_std": float(s.std(ddof=0)),
           "p_at_r50": float(by_fold["p_at_r50"].mean()),
           "p_at_r50_min": float(by_fold["p_at_r50"].min()),
           "r_at_p70": float(by_fold["r_at_p70"].mean()),
           "by_fold": by_fold, "tables": tabs}
    for lead in REF_LEADS_H:
        for k in ("p", "r"):
            col = f"{k}_lead{lead}h"
            res[col] = float(by_fold[col].mean()) if col in by_fold else float("nan")
    return res


def combine(by_horizon: dict[int, dict]) -> dict:
    """Горизонты в одно решение цикла.

    `val_score` — среднее `val_score` горизонтов, `val_min` — худшая из ячеек
    «горизонт × фолд», `cells` — все ячейки в порядке горизонтов и фолдов:
    по ним правило keep проверяет, что прирост не куплен одной ячейкой.
    """
    cells = [float(v) for h in by_horizon for v in by_horizon[h]["by_fold"]["val_score"]]
    return {"val_score": float(np.mean([by_horizon[h]["val_score"] for h in by_horizon])),
            "val_min": float(min(cells)), "cells": cells,
            "p_at_r50": {h: by_horizon[h]["p_at_r50"] for h in by_horizon},
            "p_at_r50_min": {h: by_horizon[h]["p_at_r50_min"] for h in by_horizon},
            "r_at_p70": {h: by_horizon[h]["r_at_p70"] for h in by_horizon}}


def folds_text(res: dict) -> str:
    """Таблица по фолдам для лога. Имён и тегов каналов здесь нет."""
    head = ("фолд | моментов | инцидентов | val_score | P при R>=0,5 | R при P>=0,7 | "
            "предупр./день | TP | FP | FN | медиана упр., ч | P/R упр.>=1ч | P/R упр.>=24ч")
    lines = ["| " + head + " |", "|" + "---|" * 13]
    for _, r in res["by_fold"].iterrows():
        def g(key, fmt="{}"):
            v = r.get(key)
            return "нет" if v is None or (isinstance(v, float) and np.isnan(v)) else fmt.format(v)
        lines.append(
            f"| {int(r['fold'])} | {int(r['moments'])} | {int(r['incidents'])} | "
            f"{r['val_score']:.4f} | {r['p_at_r50']:.3f} | {r['r_at_p70']:.3f} | "
            f"{g('per_day')} | {g('tp')} | {g('fp')} | {g('fn')} | {g('median_lead_h')} | "
            f"{g('p_lead1h')}/{g('r_lead1h')} | {g('p_lead24h')}/{g('r_lead24h')} |")
    return "\n".join(lines)


__all__ = [
    "CONFIRM_H", "DATA", "ES_DAYS", "FOLDS", "GAP_DAYS", "HORIZONS_H", "IDENTITY_FEATS",
    "KEEP_DELTA",
    "KEY_COLS", "MIN_LEAD_H", "QUANTILES", "RECALL_GRID", "SCORE_END", "SEAL_DAY",
    "TARGET_P", "TARGET_R", "TRAIN_BUDGET_S", "WINDOW_H", "combine", "connect", "envelope",
    "es_split", "evaluate", "evaluate_folds", "feature_cols", "folds", "folds_text",
    "gap_days", "label", "load_channel_frame", "load_episodes", "load_failures", "load_incidents",
    "simulate",
]
