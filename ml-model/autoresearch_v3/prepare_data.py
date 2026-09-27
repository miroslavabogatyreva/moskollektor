"""autoresearch v3: набор данных, метка, фолды. ЭТОТ ФАЙЛ ЗАМОРОЖЕН.

Вторая половина `prepare.py`, вынесенная, чтобы тот не перерос 500 строк. Единая точка
входа для `train.py` — по-прежнему `import prepare`: всё отсюда видно как `prepare.*`.
Что такое момент решения, метка и горизонт — в докстринге `prepare.py`.
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

# --- пути (относительные от корня проекта, запуск из корня) ---------------------
DATA = Path(os.environ.get("AUTORESEARCH_V3_DATA",
                           "data/03_processed/autoresearch_v3_20260920"))

# --- горизонты и разрез (менять нельзя) ------------------------------------------
WINDOW_H = 168                   # горизонт по умолчанию: срок жизни предупреждения, часы
HORIZONS_H = (168, 336, 504)     # горизонты цикла: 7, 14 и 21 сутки
CONFIRM_H = 1                    # отказ D5 виден системе через час после старта

FOLDS = (
    (dt.date(2025, 4, 1), dt.date(2025, 6, 30)),
    (dt.date(2025, 7, 1), dt.date(2025, 9, 30)),
    (dt.date(2025, 10, 1), dt.date(2025, 12, 31)),
    (dt.date(2026, 1, 1), dt.date(2026, 3, 24)),
)
TRAIN_FROM = "2022-04-01"
GAP_DAYS = 7                     # зазор при горизонте по умолчанию; общий случай — gap_days()
ES_DAYS = 84
SEAL_DAY = "2026-03-31"
# Последний день моментов оценки — общий для всех горизонтов цикла: печать минус самый
# длинный горизонт. Так три горизонта судят одни и те же моменты, и разница между
# ними — это горизонт, а не состав выборки.
SCORE_END = "2026-03-10"

IDENTITY_FEATS = ("n_ch_total", "n_alive_ch", "n_rows_365d", "inc_365d", "inc_90d",
                  "max_days_since_bad", "max_days_since_rec")
KEY_COLS = ("pfx", "t", "kind", "d", "d_feat", "y", "n_inc_in_window", "w")


def _pq(name: str) -> str:
    return str(DATA / f"{name}.parquet")


def connect():
    con = duckdb.connect()
    con.execute("SET memory_limit='12GB'")
    con.execute("SET threads=6")
    con.execute("SET enable_progress_bar=false")
    return con


# ------------------------------------------------------------------ загрузка


def _all_rows(con) -> pd.DataFrame:
    """Все моменты решения с суточными признаками последнего полного дня.

    `ORDER BY` обязателен: порядок строк меняет подвыборки bagging, и два
    прогона одного кода иначе дают разные модели (урок версии 2).
    """
    df = con.execute(f"""
        SELECT m.pfx, m.t, m.kind, m.d, m.d_feat, m.y, m.n_inc_in_window,
               f.* EXCLUDE (pfx, d)
        FROM '{_pq('moments')}' m
        LEFT JOIN '{_pq('features')}' f ON f.pfx = m.pfx AND f.d = m.d_feat
        ORDER BY m.t, m.pfx, m.kind
    """).df()
    df["w"] = 1.0
    return df


def load_incidents() -> pd.DataFrame:
    """Все инциденты D5 набора: `pfx`, `t_start`. Системе виден с `t_start + CONFIRM_H`."""
    con = connect()
    try:
        return con.execute(f"""
            SELECT pfx, t_start FROM '{_pq('incidents')}' ORDER BY t_start, pfx""").df()
    finally:
        con.close()


def load_failures() -> pd.DataFrame:
    """Отказы каналов: `ch`, `pfx`, `stype`, `t_start`, `t_end`.

    Причинность. Отказ виден с `t_start + CONFIRM_H`; его конец — с `t_end`
    (NULL — не закрыт к печати набора). Признак момента `t` вправе брать
    `t_end` только если `t_end <= t`.
    """
    con = connect()
    try:
        return con.execute(f"""
            SELECT ch, pfx, stype, t_start, t_end
            FROM '{_pq('failures')}' ORDER BY t_start, ch""").df()
    finally:
        con.close()


def load_episodes() -> pd.DataFrame:
    """Эпизоды словаря D5 всех длительностей: `ch`, `pfx`, `stype`, `t_start`, `t_end`.

    Причинность та же: старт виден сразу (запись уже в журнале), конец — с `t_end`.
    Длительность на момент `t` — `min(t_end, t) − t_start`.
    """
    con = connect()
    try:
        return con.execute(f"""
            SELECT ch, pfx, stype, t_start, t_end
            FROM '{_pq('episodes')}' ORDER BY t_start, ch""").df()
    finally:
        con.close()


def load_channel_frame(d_from, d_to) -> pd.DataFrame:
    """Канал-день за диапазон `[d_from, d_to]`. Окна кончаются днём `d` включительно:
    моменту `t` доступны строки с `d <= d_feat`."""
    con = connect()
    try:
        return con.execute(f"""
            SELECT * FROM '{_pq('features_ch')}'
            WHERE d BETWEEN DATE '{d_from}' AND DATE '{d_to}' ORDER BY d, ch""").df()
    finally:
        con.close()


def gap_days(window_h: int) -> int:
    """Зазор train/valid и fit/es в сутках — ширина окна метки горизонта."""
    return int(np.ceil(window_h / 24))


def label(moments: pd.DataFrame, window_h: int = WINDOW_H) -> pd.DataFrame:
    """Метка моментов решения: колонки `y`, `n_inc_in_window`.

    `moments`: колонки `pfx`, `t`. Инцидент префикса стартует в `(t, t + window_h]`.
    Этой же функцией размечаются моменты набора для каждого горизонта и свои
    моменты агента. Считать метку иначе нельзя.
    """
    inc = load_incidents()
    win = np.timedelta64(window_h, "h")
    by = {p: np.sort(g["t_start"].to_numpy()) for p, g in inc.groupby("pfx")}
    t = pd.to_datetime(moments["t"]).to_numpy()
    n = np.zeros(len(moments), dtype=int)
    for pfx, idx in moments.groupby("pfx").indices.items():
        s = by.get(pfx)
        if s is None:
            continue
        n[idx] = (np.searchsorted(s, t[idx] + win, side="right")
                  - np.searchsorted(s, t[idx], side="right"))
    out = moments.copy()
    out["n_inc_in_window"] = n
    out["y"] = (n > 0).astype(int)
    return out


def feature_cols(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if c not in KEY_COLS]


# --------------------------------------------------------------------- фолды


def folds(window_h: int = WINDOW_H) -> list[tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]]:
    """Четыре среза `(train, valid, incidents)` для горизонта `window_h`.

    `train` — моменты до `fold_start − (gap + 1) суток` включительно: окно метки
    последнего из них кончается до начала фолда. `valid` — моменты дней фолда,
    не позже `SCORE_END`. `incidents` — старты в `(fold_start, fold_end + window_h]`:
    дальше предупреждения фолда не дотягиваются. Инцидент первых дней фолда,
    предупреждение о котором было бы открыто ещё до фолда, идёт в FN — Recall
    слегка занижен, одинаково для всех прогонов.
    """
    if not DATA.exists():
        raise FileNotFoundError(
            f"нет {DATA}: python3 scripts/ml_autoresearch_v3_prepare.py seal")
    con = connect()
    try:
        rows = _all_rows(con)
    finally:
        con.close()
    inc = load_incidents()
    rows = label(rows.drop(columns=["y", "n_inc_in_window"]), window_h)

    day = pd.to_datetime(rows["d"])
    t_start = pd.to_datetime(inc["t_start"])
    out = []
    for start, end in FOLDS:
        end = min(pd.Timestamp(end), pd.Timestamp(SCORE_END))
        train_to = pd.Timestamp(start) - pd.Timedelta(days=gap_days(window_h) + 1)
        train = rows[(day >= pd.Timestamp(TRAIN_FROM)) & (day <= train_to)]
        valid = rows[(day >= pd.Timestamp(start)) & (day <= end)]
        hi = end + pd.Timedelta(days=1, hours=window_h)
        window = inc[(t_start > pd.Timestamp(start)) & (t_start < hi)]
        out.append((train.reset_index(drop=True), valid.reset_index(drop=True),
                    window.reset_index(drop=True)))
    return out


def es_split(train: pd.DataFrame, days: int = ES_DAYS,
             window_h: int = WINDOW_H) -> tuple[pd.DataFrame, pd.DataFrame]:
    """`(fit, es)`: последние `days` суток train — набор ранней остановки.
    Зазор между ними — ширина окна метки горизонта."""
    day = pd.to_datetime(train["d"])
    es_from = day.max() - pd.Timedelta(days=days - 1)
    fit_to = es_from - pd.Timedelta(days=gap_days(window_h) + 1)
    fit = train[day <= fit_to].reset_index(drop=True)
    es = train[day >= es_from].reset_index(drop=True)
    if fit.empty or es.empty:
        raise ValueError(f"es_split: пустая часть при days={days}")
    return fit, es
