"""Общая обвязка тестов ML-пайплайна.

Тесты идут по реальным Parquet из `data/02_interim/mk/` (бриф: никаких
синтетических и мок-данных). Срез маленький — один префикс, несколько месяцев.
Если файлов нет, тесты пропускаются, а не падают.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from ml import config as C  # noqa: E402

# Префикс среза и окно. Префикс 257 — самый крупный по отказам в проверочном
# окне, поэтому на нём есть и отказы, и инциденты, и незакрытые эпизоды.
SLICE_PFX = "257"
SLICE_FROM = "2026-01-01"
SLICE_TO = "2026-06-30"


def _have(*paths) -> bool:
    return all(Path(p).exists() for p in paths)


requires_data = pytest.mark.skipif(
    not _have(C.CHAN, C.EPISODES, C.MK / "parquet" / "j2026.parquet"),
    reason="нет исходных Parquet в data/02_interim/mk/",
)


def build_slice(con, date_to: str = SLICE_TO, pfx: str = SLICE_PFX, date_from: str = SLICE_FROM):
    """Собирает chanday/pfxday/сетки/разметку по срезу журнала до `date_to`.

    Отдельная сборка chanday, а не daily.build_chanday: та читает журнал целиком.
    Колонки и формулы те же, источник — тот же реальный файл.
    """
    from datetime import datetime

    src = (f"SELECT ev, ch, ts, alarm, val FROM '{C.MK / 'parquet' / 'j2026.parquet'}' "
           f"WHERE ts >= DATE '{date_from}' AND ts < DATE '{date_to}' + INTERVAL 1 DAY "
           f"AND ch IN (SELECT ch FROM '{C.CHAN}' WHERE pfx = '{pfx}')")
    con.execute("DROP TABLE IF EXISTS chanday")
    con.execute(f"""
    CREATE TABLE chanday AS
    SELECT ch, ts::DATE AS d, count(*) AS n,
           count(*) FILTER (WHERE val = '{C.VAL_BAD}')   AS n_bad,
           count(*) FILTER (WHERE val = '{C.VAL_UNDEF}') AS n_undef,
           count(*) FILTER (WHERE alarm)                 AS n_alarm,
           count(DISTINCT val)                           AS n_vals,
           count(*) FILTER (WHERE val = '{C.VAL_DEENERGIZED}') AS n_obes,
           count(*) FILTER (WHERE val = '{C.VAL_BATTERY}')     AS n_batt,
           count(*) FILTER (WHERE v IS NOT NULL)         AS n_numeric,
           max_by(v, (ts, ev)) FILTER (WHERE v IS NOT NULL) AS val_num_last
    FROM (SELECT ev, ch, ts, alarm, val, TRY_CAST(val AS DOUBLE) AS v FROM ({src}))
    GROUP BY 1, 2""")

    from ml import daily, grid, labels
    end = datetime.fromisoformat(f"{date_to} 23:59:59")
    daily.build_pfxday(con)
    daily.build_calendar(con, end)
    grid.build_grid_ch(con, end)
    grid.build_grid_pfx(con, end)
    # Эпизоды журнала обрезаем по той же границе: «удалить все строки после d».
    con.execute("DROP TABLE IF EXISTS episodes_slice")
    con.execute(f"""
    CREATE TABLE episodes_slice AS
    SELECT ch, t_start, t_last, n_bad,
           CASE WHEN t_end <= TIMESTAMP '{end:%Y-%m-%d %H:%M:%S}' THEN t_end END AS t_end,
           CASE WHEN t_end <= TIMESTAMP '{end:%Y-%m-%d %H:%M:%S}' THEN close_val END AS close_val,
           CASE WHEN t_end <= TIMESTAMP '{end:%Y-%m-%d %H:%M:%S}' THEN dur_h END AS dur_h
    FROM '{C.EPISODES}'
    WHERE t_start >= DATE '{date_from}' AND t_start <= TIMESTAMP '{end:%Y-%m-%d %H:%M:%S}'
      AND ch IN (SELECT ch FROM '{C.CHAN}' WHERE pfx = '{pfx}')""")
    labels.build_failures(con, end, source="episodes_slice")
    labels.build_incidents(con)
    labels.build_labels_ch(con, end)
    labels.build_labels_pfx(con, end)
    return end


@pytest.fixture()
def con():
    c = C.connect(memory="2GB", threads=2, persistent=False)
    yield c
    c.close()


@pytest.fixture(scope="module")
def isolated_v3_code():
    """Keep frozen v2/v3 modules and dataset caches independent across test suites."""
    names = ("prepare", "prepare_data", "train")
    modules = {name: sys.modules.get(name) for name in names}
    path = list(sys.path)
    mutable = [(module, attr, getattr(module, attr))
               for module in modules.values() if module is not None
               for attr in ("DATA", "_EVENTS") if hasattr(module, attr)]
    yield
    for module, attr, value in mutable:
        setattr(module, attr, value)
    for name, module in modules.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module
    sys.path[:] = path
