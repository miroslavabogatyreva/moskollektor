"""Задача 3: разметка — отказы, инциденты, метки канал-день и префикс-день.

Отказ = эпизод «Неисправен» длиннее часа, включая незакрытые на конец выгрузки
(вариант «638», episodes.md). Инцидент = склейка отказов префикса окном 10 минут
через `group_incidents` из `predictive_metrics.py` — модуль импортируется по пути,
копировать его в `src/ml/` запрещено брифом.

Метка дня `d`: отказ (инцидент) стартует в `[d_end + 24 ч, d_end + 168 ч]`,
где `d_end = d + 1 сут − 1 с`. То есть окно `[d + 2 сут − 1 с, d + 8 сут − 1 с]`.
Это ровно то, что зачитывает `evaluate_alerts(horizon_hours=24, max_lead_hours=168)`.
"""

from __future__ import annotations

import importlib.util
import sys

from . import config as C

# Границы окна метки в сутках от начала дня d, чтобы не дублировать арифметику.
_LO = f"INTERVAL {1 + C.LABEL_LO_H // 24} DAY - INTERVAL 1 SECOND"
_HI = f"INTERVAL {1 + C.LABEL_HI_H // 24} DAY - INTERVAL 1 SECOND"


def load_predictive_metrics():
    """Импорт `predictive_metrics.py` по пути из data/02_interim/mk/ (бриф: не копировать)."""
    path = C.MK / "predictive_metrics.py"
    if not path.exists():
        raise FileNotFoundError(f"нет {path}")
    spec = importlib.util.spec_from_file_location("predictive_metrics", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("predictive_metrics", mod)
    spec.loader.exec_module(mod)
    return mod


def build_failures(con, max_ts, source: str | None = None) -> None:
    """Отказы: dur_h > 1 с 2022-04-01, закрытые и незакрытые.

    `source` — имя таблицы эпизодов вместо файла episodes_all.parquet.
    Нужен тестам: они подают срез, обрезанный по дате.

    У незакрытых `dur_h` в episodes_all.parquet = NULL (нет `t_end`). Считаем его
    до конца выгрузки и помечаем `closed = false`: длительность цензурирована,
    и признак, построенный на ней, обязан это учитывать (episodes.md, минус варианта 638).
    """
    open_dur = f"date_diff('second', e.t_start, TIMESTAMP '{max_ts:%Y-%m-%d %H:%M:%S}') / 3600.0"
    src = source if source else f"'{C.EPISODES}'"
    con.execute("DROP TABLE IF EXISTS failures")
    con.execute(f"""
    CREATE TABLE failures AS
    SELECT e.ch, c.pfx, c.stype, e.t_start,
           CASE WHEN e.t_end IS NULL THEN {open_dur} ELSE e.dur_h END AS dur_h,
           e.t_end IS NOT NULL AS closed
    FROM {src} e LEFT JOIN '{C.CHAN}' c USING (ch)
    WHERE e.t_start >= DATE '{C.DATE_START}'
      AND coalesce(e.dur_h, {open_dur}) > {C.FAIL_DUR_H}
    ORDER BY e.t_start, e.ch""")


def build_incidents(con, window_minutes: int | None = None) -> None:
    """Инциденты: group_incidents(window_minutes=10, group_key=pfx).

    Отказы каналов без префикса в инциденты не входят (решение брифа): склеивать
    их не с чем, а собственной группой они раздували бы знаменатель Recall.

    `window_minutes` — окно склейки; по умолчанию 10 минут из брифа. Протокол v3
    склеивает по часу: столько система ждёт, прежде чем эпизод станет отказом D5.
    """
    pm = load_predictive_metrics()
    rows = con.execute(
        "SELECT ch, pfx, t_start FROM failures WHERE pfx IS NOT NULL ORDER BY t_start").fetchall()
    pfx_of = {ch: pfx for ch, pfx, _ in rows}
    window = C.INCIDENT_WINDOW_MIN if window_minutes is None else window_minutes
    inc = pm.group_incidents([(ch, t) for ch, _, t in rows],
                             window_minutes=window,
                             group_key=lambda ch: pfx_of[ch])
    con.execute("DROP TABLE IF EXISTS incidents")
    con.execute("CREATE TABLE incidents (pfx VARCHAR, t_start TIMESTAMP)")
    con.executemany("INSERT INTO incidents VALUES (?, ?)", inc)


def build_labels_ch(con, max_ts) -> None:
    """Метка канал-день по всем живым парам (ch, d)."""
    con.execute("DROP TABLE IF EXISTS labels_ch")
    con.execute(f"""
    CREATE TABLE labels_ch AS
    SELECT g.ch, g.d,
           CASE WHEN count(f.ch) > 0 THEN 1 ELSE 0 END AS y,
           CASE WHEN g.d + {_HI} > TIMESTAMP '{max_ts:%Y-%m-%d %H:%M:%S}'
                THEN 1 ELSE 0 END AS y_censored
    FROM grid_ch g
    LEFT JOIN failures f ON f.ch = g.ch
         AND f.t_start >= g.d + {_LO} AND f.t_start <= g.d + {_HI}
    GROUP BY g.ch, g.d""")


def build_labels_pfx(con, max_ts) -> None:
    """Метка префикс-день. n_inc_in_window — сколько инцидентов попало в окно."""
    con.execute("DROP TABLE IF EXISTS labels_pfx")
    con.execute(f"""
    CREATE TABLE labels_pfx AS
    SELECT g.pfx, g.d,
           CASE WHEN count(i.pfx) > 0 THEN 1 ELSE 0 END AS y,
           CASE WHEN g.d + {_HI} > TIMESTAMP '{max_ts:%Y-%m-%d %H:%M:%S}'
                THEN 1 ELSE 0 END AS y_censored,
           count(i.pfx) AS n_inc_in_window
    FROM grid_pfx g
    LEFT JOIN incidents i ON i.pfx = g.pfx
         AND i.t_start >= g.d + {_LO} AND i.t_start <= g.d + {_HI}
    GROUP BY g.pfx, g.d""")
