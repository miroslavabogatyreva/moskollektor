"""Правила на экране датчиков: выбор порогов на Q1, замер на Q2, уровни на срезах.

Доработка PR #73 (MOS-263) после проверки MOS-264. Логистическая регрессия не лучше
простых правил ни на реальных отказах, ни в симуляции (проверка MOS-264),
поэтому балл и уровень на экране считают два правила:

  давность  — режим «без синтетики»: последний подтверждённый отказ канала (начало
              эпизода + 3601 с) случился меньше D часов назад;
  предвестник — только режим «с синтетикой»: наблюдено синтетическое «предупреждение
              прибора», и назначенный им отказ (момент предупреждения + P-F) ещё не
              наступил. Это ровно правило «предвестник + P-F», с которым Николай
              сравнивал модель.

Протокол — тот же, что у train_sensor_model.py: срез 21:00 МСК каждых суток,
evaluate_alerts через exact(), выбор на 2026-01-01…2026-03-31 (Q1), замер на
2026-04-01…2026-06-30 (Q2, ретроспектива). Горизонт H = 24 ч задан, на Q1
выбирается порог давности high из сетки часов — по лучшему F1 давности на реальных отказах,
при равенстве меньший порог; порог watch — лучший F2 (полнота вдвое
важнее точности) среди порогов больше high. Правило предвестника параметров не
подбирает: P-F берётся из генератора.

Балл — частота отказа в ближайшие H часов в своей корзине давности или состояния
предвестника, посчитанная на обучающем окне 2022-04-01…2025-12-31 (Q1 и Q2 её не
видят). Режим «с синтетикой» объединяет правила: балл 1 − (1 − давность)(1 − предвестник),
уровень — выше из двух.

Повтор из корня (около 3 минут):
    uv run --no-project --with numpy==2.3.3 --with pandas==2.3.3 --with scikit-learn==1.7.2 \\
        python3 ml-model/sensor/rules.py

Пишет backend/app/domain/sensor_rules.json (что читает тик) и rules.json рядом.
"""

import importlib.util
import json
from datetime import date, datetime
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent


def load_training():
    spec = importlib.util.spec_from_file_location(
        "sensor_training", HERE / "train_sensor_model.py"
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


T = load_training()
sr, fs = T.sensor_risk, T.failure_sim
HORIZONS = T.HORIZONS
GRID_H = [2, 6, 12, 24, 48, 72, 168]  # пороги давности, часы
# корзины давности для балла, часы; последняя — отказа не было 30 сут и больше
BUCKETS = [0, 2, 6, 12, 24, 48, 72, 168, 720]


def hours_since(mc, mt):
    """Часы от начала последнего подтверждённого отказа канала до момента (inf — нет)."""
    out = np.full(len(mt), np.inf)
    starts = np.searchsorted(mc, np.arange(T.C + 1))
    for i, s in T.F.items():
        a, b = starts[i], starts[i + 1]
        t = mt[a:b]
        idx = np.searchsorted(s, t - T.CONFIRM, "right")
        ok = idx > 0
        out[a:b][ok] = (t[ok] - s[idx[ok] - 1]) / 3600
    return out


def precursor_due(mc, mt, P, pf_seconds):
    """Секунды до отказа, назначенного последним наблюдённым предвестником, который
    ещё не наступил (inf — такого нет). Предвестник наблюдён, если его момент ≤ t."""
    out = np.full(len(mt), np.inf)
    starts = np.searchsorted(mc, np.arange(T.C + 1))
    for i, p in P.items():
        a, b = starts[i], starts[i + 1]
        t = mt[a:b]
        # наблюдённые до t и с отказом после t: p ≤ t и p + pf > t
        lo = np.searchsorted(p, t - pf_seconds, "right")
        hi = np.searchsorted(p, t, "right")
        has = hi > lo
        # ближайший назначенный отказ — от самого раннего такого предвестника
        out[a:b][has] = p[lo[has]] + pf_seconds - t[has]
    return out


def alerts(D, m, sel):
    return D.mc[m][sel], D.mt[m][sel]


def freq_table(values, edges, y):
    """Частота y в корзинах [edges[k], edges[k+1]) и последней [edges[-1], inf)."""
    out = []
    for k, lo in enumerate(edges):
        hi = edges[k + 1] if k + 1 < len(edges) else np.inf
        sel = (
            (values >= lo) & (values < hi) if np.isfinite(hi) else values >= lo
        )  # inf — отказов не было
        out.append(
            {
                "from_h": lo,
                "to_h": None if hi == np.inf else hi,
                "rows": int(sel.sum()),
                "score": float(y[sel].mean()) if sel.any() else 0.0,
            }
        )
    return out


def f_beta(r, b):
    p, q = r["precision"], r["recall"]
    return (1 + b * b) * p * q / (b * b * p + q) if p + q else 0.0


def slim(r):
    return {k: r[k] for k in ("tp", "fp", "fn", "alerts", "precision", "recall", "f1")}


def main():
    T.load_data()
    _, sim_pre, SIM, PRE = T.simulate(fs.PF_DAYS, fs.FALSE_RATIO)
    pf = int(fs.PF_DAYS * 86400)
    mc, mt, mk = T.moments(lambda i: [])
    D = T.Design(mc, mt, mk, np.zeros((len(mt), 0), "float32"), "rule")
    since = hours_since(mc, mt)
    due = precursor_due(mc, mt, PRE, pf)
    out = {
        "protocol": "срез 21:00 МСК; evaluate_alerts; выбор Q1, замер Q2 (ретроспектива)",
        "grid_hours": GRID_H,
        "select": {},
        "pf_days": fs.PF_DAYS,
        "false_ratio": fs.FALSE_RATIO,
    }

    # Q1: H и порог давности
    for H in HORIZONS:  # все H — для справки, выбор ниже
        m, lo, hi = T.window_mask(mt, T.SELECT, H)
        fr = T.failures_in(T.F, lo, hi, H)
        row = {}
        for g in GRID_H:
            r = T.exact(*alerts(D, m, since[m] < g), fr, H)
            row[g] = slim(r) | {"f2": f_beta(r, 2)}
        fs_ = T.failures_in(SIM, lo, hi, H)
        row["precursor"] = slim(T.exact(*alerts(D, m, due[m] <= H * 3600), fs_, H))
        out["select"][H] = row
        T.log(
            f"Q1 H={H}: " + ", ".join(f"<{g} ч F1 {row[g]['f1']:.4f}" for g in GRID_H)
        )
    # H не подбираем: 24 ч — решение Славы 28.09.2026 (целевой горизонт постановки).
    # Прежде H брался из run.json (12 ч, выбор регрессии), но при 12 ч давность на Q2
    # давала P 0,022 / R 0,018, при 24 ч — 0,120 / 0,097. Перебор H под правило дал бы
    # 48 ч, при котором у предвестника (P-F 48 ч) не остаётся уровня watch.
    H = 24
    g_hi = max(GRID_H, key=lambda g: (out["select"][H][g]["f1"], -g))
    g_wa = max(
        (g for g in GRID_H if g > g_hi), key=lambda g: (out["select"][H][g]["f2"], -g)
    )
    out["chosen"] = {
        "horizon_h": H,
        "recency_high_h": g_hi,
        "recency_watch_h": g_wa,
        "rule": "H — из run.json (выбран на Q1 по F1 модели); порог high — лучший F1 давности на Q1, при равенстве меньший; watch — лучший F2 среди порогов больше high",
    }
    T.log(f"выбрано: H={H}, давность high < {g_hi} ч, watch < {g_wa} ч")

    # таблицы балла по обучающему окну
    m_tr, _, _ = T.window_mask(mt, T.TRAIN, H)
    y_real = T.labels(mc, mt, T.F, H)
    real_table = freq_table(since[m_tr], BUCKETS, y_real[m_tr])
    # Балл предвестника — доля наблюдённых на обучающем окне предупреждений, за которыми
    # отказ действительно случился. Одна на оба состояния: иначе у «отказ позже H» балл
    # по построению 0, и такие датчики в watch вставали бы в списке ниже здоровых.
    # Какое из двух состояний, различает уровень (high — отказ в ближайшие H часов).
    tr_lo, tr_hi = T.ts(T.TRAIN[0]), T.ts(T.TRAIN[1])
    obs = [ok for _, t, ok in sim_pre if tr_lo <= t.timestamp() < tr_hi]
    share = sum(obs) / len(obs)
    pre_table = {"none": 0.0, "pending": share, "due": share}

    # Q2: только замер
    m, lo, hi = T.window_mask(mt, T.TEST, H)
    fr = T.failures_in(T.F, lo, hi, H)
    fsim = T.failures_in(SIM, lo, hi, H)
    both = {
        i: np.sort(np.concatenate([fr.get(i, T.EMPTY), fsim.get(i, T.EMPTY)]))
        for i in set(fr) | set(fsim)
    }
    rec_hi, rec_wa = since[m] < g_hi, since[m] < g_wa
    pre_hi, pre_wa = due[m] <= H * 3600, np.isfinite(due[m])
    out["test"] = {
        "cuts": [datetime.fromtimestamp(t, T.MSK).isoformat() for t in (lo, hi)],
        "real_recency_high": slim(T.exact(*alerts(D, m, rec_hi), fr, H)),
        "real_recency_watch": slim(T.exact(*alerts(D, m, rec_wa), fr, H)),
        "sim_precursor_high_on_sim": slim(T.exact(*alerts(D, m, pre_hi), fsim, H)),
        "sim_precursor_watch_on_sim": slim(T.exact(*alerts(D, m, pre_wa), fsim, H)),
        "synthetic_mode_high_on_all": slim(
            T.exact(*alerts(D, m, rec_hi | pre_hi), both, H)
        ),
        "synthetic_mode_watch_on_all": slim(
            T.exact(*alerts(D, m, rec_wa | pre_wa), both, H)
        ),
        "failures": {
            "real": sum(map(len, fr.values())),
            "sim": sum(map(len, fsim.values())),
        },
    }
    for k, v in out["test"].items():
        if isinstance(v, dict) and "tp" in v:
            T.log(
                f"Q2 {k}: P {v['precision']:.4f} R {v['recall']:.4f} ({v['tp']}/{v['fp']}/{v['fn']})"
            )

    # уровни на срезах: давность и предвестник на произвольный момент (не только 21:00)
    levels = {}
    for label, at in (
        ("2026-06-01 00:00", datetime(2026, 6, 1, 0, 0, tzinfo=T.MSK)),
        ("2026-06-27 21:00", datetime(2026, 6, 27, 21, 0, tzinfo=T.MSK)),
    ):
        t = np.full(T.C, int(at.timestamp()))
        c = np.arange(T.C, dtype="int32")
        s = hours_since(c, t)
        d = precursor_due(c, t, PRE, pf)
        lv_real = np.where(s < g_hi, 2, np.where(s < g_wa, 1, 0))
        lv_pre = np.where(d <= H * 3600, 2, np.where(np.isfinite(d), 1, 0))
        lv_full = np.maximum(lv_real, lv_pre)
        name = {2: "high", 1: "watch", 0: "normal"}
        levels[label] = {
            mode: {name[k]: int((lv == k).sum()) for k in (2, 1, 0)}
            for mode, lv in (("real", lv_real), ("synthetic", lv_full))
        }
        T.log(f"уровни {label}: {levels[label]}")
    out["levels"] = levels

    rules = {
        "version": f"sensor-rules-h{H}-2026.09.28",
        "horizon_h": H,
        "confirm_seconds": sr.CONFIRM_SECONDS,
        "recency": {"high_h": g_hi, "watch_h": g_wa, "table": real_table},
        "precursor": {
            "pf_days": fs.PF_DAYS,
            "false_ratio": fs.FALSE_RATIO,
            "window_days": 5,
            "score": pre_table,
        },
        "chosen_on": "2026-01-01…2026-03-31",
        "tables_from": "2022-04-01…2025-12-31",
    }
    (T.ROOT / "backend/app/domain/sensor_rules.json").write_text(
        json.dumps(rules, ensure_ascii=False, indent=1, allow_nan=False) + "\n"
    )
    out["rules"] = rules
    (HERE / "rules.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1, default=float) + "\n"
    )
    T.log("готово: backend/app/domain/sensor_rules.json, rules.json")


if __name__ == "__main__":
    main()
