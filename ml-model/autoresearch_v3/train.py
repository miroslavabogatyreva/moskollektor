"""autoresearch v3: единственный файл, который правит агент.

Протокол — «одно открытое предупреждение», см. докстринг `prepare.py`. Строка
обучения — момент решения `(pfx, t)`: конец суток (`tick`) или час после старта
инцидента (`rearm`). Метка: инцидент префикса стартует в `(t, t + H]`, где `H` —
горизонт из `prepare.HORIZONS_H` (7, 14 и 21 сутки). Один и тот же код обучается
и оценивается на каждом горизонте отдельно. Скор момента идёт в `prepare.simulate`,
политика сама решает, какие моменты станут предупреждениями.

Цель цикла: поднять `val_score` — среднее по горизонтам и фолдам от средней
Precision на огибающей при Recall 0,40 … 0,60. Цель постановки задачи —
`p_at_r50` выше 0,7: печатается по каждому горизонту.

Править можно четыре секции: FEATURE_ENGINEERING, PARAMS, SAMPLE_WEIGHTS, TRAINING.

Главное правило причинности. Признак момента `t` вправе знать только то, что
система знала в `t`: инцидент и отказ — с `t_start + CONFIRM_H`, конец эпизода —
с `t_end`, суточные признаки — за день `d_feat` и раньше.

Запуск из корня проекта:

    python3 autoresearch_v3/train.py > autoresearch_v3/run.log 2>&1
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

sys.path.insert(0, str(Path(__file__).resolve().parent))

import prepare  # noqa: E402


# ============================================================ FEATURE_ENGINEERING
# Суточные признаки уже приклеены в `prepare.folds()` по `d_feat`. Здесь — история
# событий на момент `t`: сколько инцидентов и отказов система уже видела, как давно
# и как часто у этого коллектора за отказом шёл следующий.

HOUR = np.timedelta64(1, "h")
INC_WINDOWS_H = (6, 24, 72, 168, 672)      # окна счётчиков инцидентов, часы
FAIL_WINDOWS_H = (3, 24, 168)              # окна счётчиков отказов каналов, часы

_EVENTS: dict | None = None


def _events() -> dict:
    """Инциденты и отказы по префиксам, отсортированные. Читается раз на прогон."""
    global _EVENTS
    if _EVENTS is None:
        inc, fails = prepare.load_incidents(), prepare.load_failures()
        _EVENTS = {
            "inc": {p: np.sort(g["t_start"].to_numpy().astype("datetime64[us]"))
                    for p, g in inc.groupby("pfx")},
            "fail": {p: np.sort(g["t_start"].to_numpy().astype("datetime64[us]"))
                     for p, g in fails.dropna(subset=["pfx"]).groupby("pfx")},
            "fail_end": {p: np.sort(g["t_end"].dropna().to_numpy().astype("datetime64[us]"))
                         for p, g in fails.dropna(subset=["pfx"]).groupby("pfx")},
        }
    return _EVENTS


def _count(starts: np.ndarray, t: np.ndarray, known: np.ndarray, hours: int) -> np.ndarray:
    """Сколько событий стартовало в `(t − hours, …]` и уже видно системе."""
    lo = np.searchsorted(starts, t - hours * HOUR, side="right")
    return np.maximum(known - lo, 0)


def event_features(df: pd.DataFrame, window_h: int) -> pd.DataFrame:
    """История событий коллектора на момент `t`. Будущего здесь нет по построению:
    событие входит в счёт, только если `t_start + CONFIRM_H <= t`."""
    ev = _events()
    confirm = prepare.CONFIRM_H * HOUR
    week = window_h * HOUR
    t_all = df["t"].to_numpy().astype("datetime64[us]")
    cols = {f"ev_inc_{h}h": np.zeros(len(df)) for h in INC_WINDOWS_H}
    cols.update({f"ev_fail_{h}h": np.zeros(len(df)) for h in FAIL_WINDOWS_H})
    for name in ("ev_h_since_inc", "ev_h_since_prev", "ev_gap_last"):
        cols[name] = np.full(len(df), np.nan)
    # У коллектора без единого известного исхода доля равна априорной 1/2. NaN здесь
    # нельзя: он отличал бы «инцидентов не будет никогда» от «пока не было» — а это
    # знание о будущем (поймано `scripts/ml_autoresearch_v3_causality.py`).
    cols["ev_relapse_rate"] = np.full(len(df), 0.5)
    cols["ev_open_ch"] = np.zeros(len(df))
    empty = np.array([], dtype="datetime64[us]")

    for pfx, idx in df.groupby("pfx").indices.items():
        t = t_all[idx]
        s = ev["inc"].get(pfx, empty)
        k = np.searchsorted(s + confirm, t, side="right")       # инцидентов видно к t
        for h in INC_WINDOWS_H:
            cols[f"ev_inc_{h}h"][idx] = _count(s, t, k, h)
        if len(s):
            last = np.where(k >= 1, s[np.maximum(k - 1, 0)], np.datetime64("NaT"))
            prev = np.where(k >= 2, s[np.maximum(k - 2, 0)], np.datetime64("NaT"))
            cols["ev_h_since_inc"][idx] = (t - last) / HOUR
            cols["ev_h_since_prev"][idx] = (t - prev) / HOUR
            cols["ev_gap_last"][idx] = (last - prev) / HOUR
            # Доля прошлых инцидентов, за которыми в пределах горизонта шёл следующий.
            # Исход инцидента известен через горизонт и час после его старта — не раньше.
            followed = np.concatenate([np.diff(s) <= week, [False]]).cumsum()
            n = np.searchsorted(s + week + confirm, t, side="right")
            hit = np.where(n >= 1, followed[np.maximum(n - 1, 0)], 0)
            cols["ev_relapse_rate"][idx] = (hit + 1.0) / (n + 2.0)
        f = ev["fail"].get(pfx, empty)
        kf = np.searchsorted(f + confirm, t, side="right")
        for h in FAIL_WINDOWS_H:
            cols[f"ev_fail_{h}h"][idx] = _count(f, t, kf, h)
        closed = np.searchsorted(ev["fail_end"].get(pfx, empty), t, side="right")
        cols["ev_open_ch"][idx] = kf - closed
    return pd.DataFrame(cols, index=df.index)


def feature_engineering(train: pd.DataFrame, valid: pd.DataFrame,
                        window_h: int = prepare.WINDOW_H) -> tuple[pd.DataFrame, pd.DataFrame]:
    """История событий, вид момента и календарь момента."""
    out = []
    for df in (train, valid):
        df = pd.concat([df, event_features(df, window_h)], axis=1)
        t = pd.to_datetime(df["t"])
        df["is_rearm"] = (df["kind"] == "rearm").astype(int)
        df["hour"] = t.dt.hour
        df["dow"] = t.dt.dayofweek
        out.append(df)
    return out[0], out[1]


# Четыре суточных счётчика версии 2 выброшены по разбору серии 1. Они считают отказы
# по дате старта, и отказ последнего часа суток попадал в тик до своего подтверждения
# (9 тиков из 10 353 — мало, но правило причинности одно для всех). К тому же они
# собраны по словарю D0, а метка — D5. Их причинные двойники по D5 уже есть:
# `ev_inc_672h`, `ev_fail_168h`, `ev_h_since_inc`.
DROP_FEATS: tuple[str, ...] = ("inc_28d", "fails_7d", "fails_28d", "days_since_inc")


def feature_names(train: pd.DataFrame) -> list[str]:
    """Имена признаков в порядке обучения. Признаки-идентичности коллектора убраны,
    как в версии 2: по ним дерево узнаёт сам префикс, а не его состояние."""
    drop = set(prepare.IDENTITY_FEATS) | set(DROP_FEATS)
    return [c for c in prepare.feature_cols(train) if c not in drop]


# ======================================================================== PARAMS
# Рамки задания: num_leaves <= 31, min_data_in_leaf >= 50.

PARAMS = {
    "objective": "binary",
    "learning_rate": 0.05,
    "num_leaves": 7,
    "min_data_in_leaf": 200,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 5.0,
    "num_threads": 6,
    "verbosity": -1,
    "seed": 42,
    "deterministic": True,
    # Без этого LightGBM выбирает схему гистограмм замером времени на старте,
    # и два прогона одного кода дают разные модели.
    "force_col_wise": True,
    "metric": ["average_precision"],
    "first_metric_only": True,
}

NUM_ROUNDS = 2000
EARLY_STOP = 100


# ================================================================ SAMPLE_WEIGHTS

REARM_W = 15.0   # строк `rearm` 2 % выборки, подтверждений у них 37 % против 3 % у тиков


def sample_weights(rows: pd.DataFrame) -> np.ndarray:
    """Вес строк `rearm` поднят до `REARM_W`, у тиков остаётся единица.

    Деревья с листом от 200 строк ради 2 % выборки по событийным признакам не
    делятся. Отдельная модель `rearm` в серии 1 проиграла (тикам нужны эти строки),
    поэтому вес, а не разделение выборки.
    """
    heavy = np.where((rows["kind"] == "rearm").to_numpy(), REARM_W, 1.0)
    return rows["w"].to_numpy(dtype=float) * heavy


# ====================================================================== TRAINING
# Ранняя остановка — по `es`, хвосту train. Оценочный блок фолда сюда не передаётся.

SEEDS = (42, 43, 44, 45, 46)   # мешок — часть бейзлайна: без него решает розыгрыш сида
ES_DAYS = 84
MIN_TREES = 50


def train_model(fit: pd.DataFrame, es: pd.DataFrame, feats: list[str],
                weights: np.ndarray, callbacks: list) -> list:
    boosters = []
    for seed in SEEDS:
        dfit = lgb.Dataset(fit[feats], label=fit["y"], weight=weights, free_raw_data=False)
        # Веса и здесь: без них average_precision ранней остановки видит почти одни
        # тики, и число деревьев подбирается под ту часть выборки, которая не менялась.
        des = lgb.Dataset(es[feats], label=es["y"], weight=sample_weights(es),
                          reference=dfit, free_raw_data=False)
        # keep_training_booster=True — из-за пола MIN_TREES: иначе модель обрезана
        # по best_iteration, и пятидесятое дерево взять неоткуда.
        boosters.append(lgb.train(dict(PARAMS, seed=seed), dfit,
                                  num_boost_round=NUM_ROUNDS,
                                  valid_sets=[des], valid_names=["es"],
                                  keep_training_booster=True,
                                  callbacks=callbacks + [
                                      lgb.early_stopping(EARLY_STOP, verbose=False)]))
    return boosters


def n_trees(booster) -> int:
    return min(max(booster.best_iteration, MIN_TREES), booster.num_trees())


def _logit(p: np.ndarray) -> np.ndarray:
    q = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    return np.log(q / (1 - q))


def platt(p_es: np.ndarray, y_es, p_new: np.ndarray) -> np.ndarray:
    """Приведение скоров одного вида моментов к шкале вероятности по хвосту `es`.

    Порог в политике один на все моменты, а доля подтверждений у `rearm` и у тиков
    разная: 37 % против 3 %. Модель учится на общей выборке и сглаживает эту
    разницу, поэтому общий порог сравнивает величины с разным смыслом. Калибровка
    по видам момента ставит их на одну шкалу. Платт, а не изотоника: строк `rearm`
    в `es` 46–115, ступенчатая калибровка рвёт сетку порогов. Оценочный блок фолда
    сюда не попадает — только `es`.
    """
    y = np.asarray(y_es, dtype=int)
    if y.min() == y.max():
        return p_new
    lr = LogisticRegression(solver="lbfgs")
    lr.fit(_logit(p_es).reshape(-1, 1), y)
    return lr.predict_proba(_logit(p_new).reshape(-1, 1))[:, 1]


def predict(boosters: list, valid: pd.DataFrame, feats: list[str]) -> np.ndarray:
    return np.median([b.predict(valid[feats], num_iteration=n_trees(b))
                      for b in boosters], axis=0)


REARM_HEAD_FEATS = ("ev_inc_168h", "ev_inc_672h", "ev_fail_168h")
# На 168 голова роняла ячейки фолдов 1 и 3, с 336 и дальше помогает. Граница, а не
# список: отложенная проверка судит ещё и 720 ч, которых в горизонтах цикла нет;
# на репетиции с переносом порога голова дала там 0,789 / 0,613 против 0,776 / 0,616.
REARM_HEAD_FROM_H = 336
REARM_HEAD_MIN_ROWS = 30


def rearm_head(fit: pd.DataFrame, es: pd.DataFrame, valid: pd.DataFrame,
               p_es: np.ndarray, p_va: np.ndarray,
               window_h: int) -> tuple[np.ndarray, np.ndarray]:
    """Линейная голова на строках `rearm`: полусумма логитов до калибровки.

    Общая модель на 97 % состоит из тиков и по событийным признакам почти не
    делится. Линейная голова учится только на `rearm`-строках `fit` и видит три
    причинных счётчика событий, каждый из которых учитывает событие лишь после
    подтверждения. В опыте 10 она дала +0,069 и +0,056 в ячейках h336 и h504
    фолда 1, но роняла h168 — поэтому только на длинных горизонтах.

    Зазоры соблюдены: голова обучается на `fit`, а применяется к `es` и к
    оценочному блоку, отделённым от `fit` шириной окна метки.
    """
    if window_h < REARM_HEAD_FROM_H:
        return p_es, p_va
    m_fit = (fit["kind"] == "rearm").to_numpy()
    y = fit.loc[m_fit, "y"]
    if m_fit.sum() < REARM_HEAD_MIN_ROWS or y.min() == y.max():
        return p_es, p_va
    cols = list(REARM_HEAD_FEATS)
    lr = LogisticRegression(solver="lbfgs", max_iter=1000)
    lr.fit(np.log1p(fit.loc[m_fit, cols].to_numpy(dtype=float)), y)
    out = []
    for df, p in ((es, p_es), (valid, p_va)):
        m = (df["kind"] == "rearm").to_numpy()
        p = p.copy()
        if m.any():
            x = np.log1p(df.loc[m, cols].to_numpy(dtype=float))
            z = 0.5 * _logit(p[m]) + 0.5 * _logit(lr.predict_proba(x)[:, 1])
            p[m] = 1.0 / (1.0 + np.exp(-z))
        out.append(p)
    return out[0], out[1]


# ======================================================================== ОБВЯЗКА
# Ниже — не экспериментальная часть. Меняйте только если чинится поломка.

class Budget(Exception):
    """Бюджет времени исчерпан."""


def budget_callback(started: float, limit: float):
    def _cb(_env) -> None:
        if time.time() - started > limit:
            raise Budget(f"обучение дольше {limit:.0f} с")
    return _cb


def run_fold(fold: tuple, started: float,
             window_h: int = prepare.WINDOW_H) -> tuple[pd.DataFrame, pd.DataFrame, int, list]:
    train, valid, incidents = fold
    train, valid = feature_engineering(train, valid, window_h)
    feats = feature_names(train)
    fit, es = prepare.es_split(train, days=ES_DAYS, window_h=window_h)
    boosters = train_model(fit, es, feats, sample_weights(fit),
                           [budget_callback(started, prepare.TRAIN_BUDGET_S)])
    p_es, p_va = predict(boosters, es, feats), predict(boosters, valid, feats)
    p_es, p_va = rearm_head(fit, es, valid, p_es, p_va, window_h)
    p = np.zeros(len(valid))
    for kind in ("rearm", "tick"):
        m_es, m_va = (es["kind"] == kind).to_numpy(), (valid["kind"] == kind).to_numpy()
        p[m_va] = platt(p_es[m_es], es.loc[m_es, "y"], p_va[m_va])
    scores = pd.DataFrame({"pfx": valid["pfx"].to_numpy(), "t": valid["t"].to_numpy(),
                           "p": p})
    return scores, incidents, len(feats), [n_trees(b) for b in boosters]


def run_all(started: float, log=print) -> tuple[dict, dict]:
    """Все горизонты: `(сводка цикла, результаты по горизонтам)`."""
    by_h = {}
    for window_h in prepare.HORIZONS_H:
        pairs, trees, n_feats = [], [], 0
        for fold in prepare.folds(window_h):
            scores, incidents, n_feats, used = run_fold(fold, started, window_h)
            pairs.append((scores, incidents))
            trees.append(f"{min(used)}–{max(used)}")
        by_h[window_h] = prepare.evaluate_folds(pairs, window_h)
        log(f"=== горизонт {window_h} ч ({window_h // 24} сут): признаков {n_feats}, "
            f"деревьев по фолдам {', '.join(trees)}")
        log(prepare.folds_text(by_h[window_h]))
        log("")
    return prepare.combine(by_h), by_h


def main() -> int:
    started = time.time()
    try:
        res, by_h = run_all(started)
    except Budget as exc:
        print(f"status: timeout ({exc})")
        print(f"train_seconds: {time.time() - started:.1f}")
        return 0
    train_seconds = time.time() - started

    print("---")
    for window_h, r in by_h.items():
        cells = "/".join(f"{v:.4f}" for v in r["by_fold"]["val_score"])
        print(f"h{window_h}: val {r['val_score']:.4f} min {r['val_min']:.4f} cells {cells} "
              f"p_at_r50 {r['p_at_r50']:.4f} (худший {r['p_at_r50_min']:.4f}) "
              f"r_at_p70 {r['r_at_p70']:.4f} "
              f"lead1h {r['p_lead1h']:.3f}/{r['r_lead1h']:.3f} "
              f"lead24h {r['p_lead24h']:.3f}/{r['r_lead24h']:.3f}")
    print(f"val_score: {res['val_score']:.4f}")
    print(f"val_min: {res['val_min']:.4f}")
    print("cells: " + "/".join(f"{v:.4f}" for v in res["cells"]))
    print("p_at_r50: " + "/".join(f"{res['p_at_r50'][h]:.4f}" for h in by_h))
    print("r_at_p70: " + "/".join(f"{res['r_at_p70'][h]:.4f}" for h in by_h))
    print(f"train_seconds: {train_seconds:.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
