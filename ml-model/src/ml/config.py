"""Пути, константы и подключение DuckDB для ML-пайплайна (эпик MOS-9, задача MOS-74).

Все пути относительные от корня проекта: скрипты запускаются из корня
(`python3 scripts/ml_build_dataset.py`), как требует бриф разд. «Правила для агентов» п. 6.
"""

from __future__ import annotations

import os
from pathlib import Path

import duckdb

from . import journal_vals as JV

# --- вход (менять ничего в этом каталоге нельзя, бриф п. 5) ---------------------
MK = Path("data/02_interim/mk")
JOURNAL_GLOB = str(MK / "parquet" / "j*.parquet")
JOURNAL_YEARS = tuple(range(2019, 2027))
CHAN = str(MK / "chan.parquet")
EPISODES = str(MK / "episodes_all.parquet")
CHANDAY_OLD = str(MK / "chanday.parquet")   # устаревший, только для сверки контрольных чисел

# --- выход ----------------------------------------------------------------------
# Каталог выхода и начало окна сборки переопределяются переменными окружения.
# Зачем: эксперимент «даёт ли что-то история 2019 … 2022-03» должен собрать
# второй датасет, не тронув основной. По умолчанию всё как было, поэтому
# ни одна точка входа и ни один тест правки не замечают.
OUT = Path(os.environ.get("ML_OUT_DIR", "data/03_processed/ml_20260915"))
TMP = OUT / "dtmp"
DB = TMP / "build.duckdb"       # рабочая БД на диске: 19,6 млн строк сетки не держим в RAM

# --- решения брифа (не пересматривать) ------------------------------------------
# Начало окна сборки. По умолчанию — граница смены парка (episodes.md разд.
# «Граница»): до неё парк другой. `ML_START_DATE` сдвигает границу назад только
# для отдельной сборки истории, основной датасет собирается с 2022-04-01.
DATE_START = os.environ.get("ML_START_DATE", "2022-04-01")

# Разрез по времени. Верхняя граница берётся из данных (max(ts)), а не из константы.
# Нижняя граница train — начало сборки: собранный день без разреза не значил бы
# ничего. При сборке по умолчанию это ровно 2022-04-01 из брифа.
SPLITS = (
    ("train", DATE_START, "2025-12-31"),
    ("valid", "2026-01-01", "2026-03-31"),
    ("test", "2026-04-01", "2026-06-30"),
)

# Момент предупреждения — конец дня d (как в z3_ceiling.py), метка = отказ стартует
# в [d_end + 24 ч, d_end + 168 ч]. Ровно окно evaluate_alerts(24, 168).
LABEL_LO_H = 24
LABEL_HI_H = 168

FAIL_DUR_H = 1.0                # отказ = эпизод «Неисправен» длиннее часа
INCIDENT_WINDOW_MIN = 10        # group_incidents(window_minutes=10, group_key=pfx)
ALIVE_DAYS = 365                # канал «жив», если писал хоть раз за последние 365 сут
OUTAGE_SHARE = 0.10             # день-провал: строк меньше 10 % медианы своего года

# Значения `val`, которые считаем отдельно (quality.md разд. 3). Строки — из словаря
# `journal_vals`: там они записаны один раз и сверены с выгрузкой.
VAL_BAD = JV.FAULT
VAL_UNDEF = JV.UNDEF
VAL_DEENERGIZED = JV.DEENERGIZED
VAL_BATTERY = JV.BATTERY_FAULT

CHUNKS = 6                      # каналы режем на CHUNKS частей по ch % CHUNKS, как в episodes.py


def connect(memory: str = "16GB", threads: int = 8, persistent: bool = True):
    """DuckDB с лимитами из задания. persistent=False — для тестов на срезах."""
    TMP.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB) if persistent else ":memory:")
    con.execute(f"SET memory_limit='{memory}'")
    con.execute(f"SET threads={threads}")
    con.execute(f"SET temp_directory='{TMP}'")
    con.execute("SET preserve_insertion_order=false")
    con.execute("SET enable_progress_bar=false")   # иначе лог забивается кадрами полосы
    return con


def out(name: str) -> str:
    """Путь к выходному файлу в data/03_processed/ml_20260915/."""
    OUT.mkdir(parents=True, exist_ok=True)
    return str(OUT / name)


def save(con, table: str, order_by: str = "") -> str:
    """Выгружает таблицу в Parquet. Запись файлов держим в одном месте, а не
    в сборщиках: тесты гоняют сборщики на срезах и не должны трогать выход."""
    path = out(f"{table}.parquet")
    by = f" ORDER BY {order_by}" if order_by else ""
    con.execute(f"COPY (SELECT * FROM {table}{by}) TO '{path}' (FORMAT PARQUET)")
    return path


def journal_union(date_from: str | None = DATE_START) -> str:
    """SQL-источник журнала. Отдельный union по годам, а не glob: так DuckDB
    отбрасывает лишние файлы по имени и не открывает 3,4 ГБ ради среза."""
    where = f" WHERE ts >= DATE '{date_from}'" if date_from else ""
    parts = []
    for y in JOURNAL_YEARS:
        f = MK / "parquet" / f"j{y}.parquet"
        if date_from and y < int(date_from[:4]):
            continue
        if f.exists():
            parts.append(f"SELECT ev, ch, ts, alarm, val FROM '{f}'{where}")
    return " UNION ALL ".join(parts)


def max_ts(con) -> str:
    """Конец выгрузки — из данных, не из константы (требование задания)."""
    src = journal_union(None)
    return con.execute(f"SELECT max(ts) FROM ({src})").fetchone()[0]
