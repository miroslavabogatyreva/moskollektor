"""События модели v3 — отказы D5 и инциденты — по журналу, каким он был в `as_of`.

Зачем. Исторический расчёт на момент раньше брал отказы и инциденты из готового
набора `v3_final_20260920`, собранного по журналу до 30.06.2026. На новой выгрузке такие
события устаревают: суточные признаки видят свежий журнал, а 13 признаков `ev_*` — нет.
Здесь события собираются заново теми же функциями, что собрали набор
(исторического сборщика контрольной выборки): эпизоды словаря D5
(`failure_defs.build_episodes`), отказы (`failure_defs.build_failures`), склейка в
инциденты окном `moments.MERGE_MIN` (`labels.build_incidents`), конец отказа без
будущего (`moments.seal_failures`).

Причинность. Из журнала берутся только строки `ts <= as_of`. Эпизод, у которого к
`as_of` нет закрывающей записи, — незакрытый: `t_end` NULL, длительность — до `as_of`.
Обучение видело такой эпизод отказом с `t_start + CONFIRM_H`, если он потом длился
больше часа; в `as_of` он длится `as_of − t_start`. Поэтому незакрытый эпизод — отказ,
если `as_of − t_start >= CONFIRM_H`: `build_failures` сравнивает строго (`> 1 ч`), и ей
передаётся край `as_of + 1 с`. С точностью журнала до секунды это то же условие.

Что система знает в `as_of`, режет `cut` — одно правило для пересборки и для готового
набора: инцидент и отказ видны с `t_start + CONFIRM_H`, конец отказа — с `t_end`.
"""

from __future__ import annotations

import pandas as pd

from . import config as C
from . import failure_defs as F
from . import journal_vals as JV
from . import labels
from . import moments as M

# Каналов с D5 — 4,8 тыс. из 11,5 тыс., и четырёх частей хватает в пределах 6 ГБ
# исторический расчёт v3: 22 с против 34 с при 16 частях (замер 21.09.2026, 30.06.2026).
CHUNKS = 4


def _ts(t: pd.Timestamp) -> str:
    return f"{t:%Y-%m-%d %H:%M:%S}"


def rebuild(con, as_of: pd.Timestamp, log=print) -> None:
    """Таблицы `incidents_all` и `failures_all` по журналу `ts <= as_of`.

    Эпизоды считаются только у каналов, где к `as_of` было хоть одно значение D5:
    у прочих эпизодов нет, а чтение их строк — большая часть работы.
    """
    ts = _ts(as_of)
    src = " UNION ALL ".join(
        f"SELECT ch FROM '{f}' WHERE {F.EXT} AND ts <= TIMESTAMP '{ts}'"
        for y in F.SRC_YEARS if (f := C.MK / "parquet" / f"j{y}.parquet").exists()
        and y <= as_of.year)
    con.execute(f"CREATE OR REPLACE TABLE d5_ch AS SELECT DISTINCT ch FROM ({src})")
    F.build_episodes(con, [F.EXT], chunks=CHUNKS, log=lambda _m: None,
                     ch_where="ch IN (SELECT ch FROM d5_ch)", ts_to=ts)
    F.build_failures(con, F.DEFS["D5"], [F.EXT], as_of + pd.Timedelta(seconds=1))
    if con.execute("SELECT count(*) FROM failures WHERE pfx IS NOT NULL").fetchone()[0]:
        labels.build_incidents(con, window_minutes=M.MERGE_MIN)
    else:
        # `executemany` не принимает пустой список; на парке так не бывает, на срезе — да
        con.execute("CREATE OR REPLACE TABLE incidents (pfx VARCHAR, t_start TIMESTAMP)")
    M.seal_failures(con, ts)
    con.execute("CREATE OR REPLACE TABLE incidents_all AS SELECT * FROM incidents")
    con.execute("CREATE OR REPLACE TABLE failures_all AS SELECT * FROM failures_sealed")
    for name in ("ep_0", "d5_ch", "failures", "failures_sealed", "incidents"):
        con.execute(f"DROP TABLE IF EXISTS {name}")
    n = con.execute("SELECT (SELECT count(*) FROM incidents_all), "
                    "(SELECT count(*) FROM failures_all)").fetchone()
    log(f"события по журналу до {ts}: инцидентов {n[0]}, отказов {n[1]}")


def cut(con, inc_src: str, fail_src: str, as_of: pd.Timestamp) -> None:
    """Таблицы `incidents` и `failures`, какими их знала система в `as_of`.

    `inc_src`, `fail_src` — таблица или `'путь.parquet'`: пересборка и готовый набор
    режутся одним запросом, чтобы сравнение двух путей сравнивало только события.
    """
    ts = f"TIMESTAMP '{_ts(as_of)}'"
    confirm = f"INTERVAL {M.CONFIRM_H} HOUR"
    con.execute(f"""CREATE OR REPLACE TABLE incidents AS
        SELECT pfx, t_start FROM {inc_src}
        WHERE t_start + {confirm} <= {ts} ORDER BY t_start, pfx""")
    con.execute(f"""CREATE OR REPLACE TABLE failures AS
        SELECT ch, pfx, stype, t_start, CASE WHEN t_end <= {ts} THEN t_end END AS t_end
        FROM {fail_src}
        WHERE t_start + {confirm} <= {ts} ORDER BY t_start, ch""")


def unknown_values(con, ts_from: str, ts_to: str) -> list[dict]:
    """Значения журнала `[ts_from, ts_to]`, которых нет в словаре `journal_vals`.

    Модель их не знает: они не входят ни в D5, ни в суточные счётчики. Расчёт от
    этого не останавливается — новое значение может быть безобидным, — но продукт
    должен его увидеть.
    """
    parts = []
    for y in C.JOURNAL_YEARS:
        f = C.MK / "parquet" / f"j{y}.parquet"
        if int(ts_from[:4]) <= y <= int(ts_to[:4]) and f.exists():
            parts.append(f"SELECT val FROM '{f}' WHERE ts >= TIMESTAMP '{ts_from}' "
                         f"AND ts <= TIMESTAMP '{ts_to}'")
    if not parts:
        return []
    rows = con.execute(JV.unknown_sql(" UNION ALL ".join(parts))).fetchall()
    return [{"value": v, "rows": int(n)} for v, n in rows]
