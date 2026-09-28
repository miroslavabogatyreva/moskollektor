"""Задача 1: базовые таблицы chanday, pfxday, calendar.

chanday пересобирается с 2022-04-01 (старый файл начинался с 2024-10-01 и терял
3,5 из 4,5 тыс. отказов train — episodes.md, «Блокирующее ограничение»).
"""

from __future__ import annotations

from . import config as C

# Агрегаты дня канала. Считаются один раз по журналу, дальше всё строится из них.
_CHANDAY_SQL = """
SELECT ch, ts::DATE AS d,
       count(*)                                        AS n,
       count(*) FILTER (WHERE val = '{bad}')           AS n_bad,
       count(*) FILTER (WHERE val = '{undef}')         AS n_undef,
       count(*) FILTER (WHERE alarm)                   AS n_alarm,
       count(DISTINCT val)                             AS n_vals,
       count(*) FILTER (WHERE val = '{obes}')          AS n_obes,
       count(*) FILTER (WHERE val = '{batt}')          AS n_batt,
       count(*) FILTER (WHERE v IS NOT NULL)           AS n_numeric,
       -- последнее числовое значение дня в порядке (ts, ev) — том же, что в episodes.py
       max_by(v, (ts, ev)) FILTER (WHERE v IS NOT NULL) AS val_num_last
FROM (SELECT ev, ch, ts, alarm, val, TRY_CAST(val AS DOUBLE) AS v FROM ({src}))
GROUP BY 1, 2
"""


def build_chanday(con, log=print, ts_to: str | None = None,
                  ts_from: str | None = None) -> None:
    """Собирает chanday по годам: день не пересекает границу года, поэтому
    пооконная сборка даёт тот же результат, что один проход, но держит память.

    `ts_to` — журнал только до этой метки времени включительно. Нужен проверке
    причинности: она пересобирает суточные признаки из журнала, каким он был в момент `t`.
    `ts_from` — журнал только с этой метки. Нужен расчёту на исторический момент: самое
    длинное окно признаков — год, и читать четыре года журнала на каждый расчёт незачем.
    """
    con.execute("DROP TABLE IF EXISTS chanday")
    con.execute("""CREATE TABLE chanday (
        ch INT, d DATE, n BIGINT, n_bad BIGINT, n_undef BIGINT, n_alarm BIGINT,
        n_vals BIGINT, n_obes BIGINT, n_batt BIGINT, n_numeric BIGINT,
        val_num_last DOUBLE)""")
    start_year = int(C.DATE_START[:4])
    for year in C.JOURNAL_YEARS:
        if year < start_year or (ts_from is not None and year < int(ts_from[:4])):
            continue
        path = C.MK / "parquet" / f"j{year}.parquet"
        if not path.exists():
            continue
        conds = [f"ts >= DATE '{C.DATE_START}'"] if year == start_year else []
        if ts_to is not None:
            if year > int(ts_to[:4]):
                continue
            conds.append(f"ts <= TIMESTAMP '{ts_to}'")
        if ts_from is not None:
            conds.append(f"ts >= TIMESTAMP '{ts_from}'")
        where = f" WHERE {' AND '.join(conds)}" if conds else ""
        src = f"SELECT ev, ch, ts, alarm, val FROM '{path}'{where}"
        sql = _CHANDAY_SQL.format(bad=C.VAL_BAD, undef=C.VAL_UNDEF, obes=C.VAL_DEENERGIZED,
                                  batt=C.VAL_BATTERY, src=src)
        con.execute(f"INSERT INTO chanday {sql}")
        got = con.execute("SELECT count(*) FROM chanday").fetchone()[0]
        log(f"  chanday {year}: {got:,} строк")


def build_pfxday(con) -> None:
    """День префикса. Только каналы со pfx: 1 142 канала журнала справочника не имеют."""
    con.execute("DROP TABLE IF EXISTS pfxday")
    con.execute(f"""
    CREATE TABLE pfxday AS
    WITH tot AS (SELECT pfx, count(*) AS n_ch_total FROM '{C.CHAN}'
                 WHERE pfx IS NOT NULL GROUP BY 1)
    SELECT c.pfx, cd.d,
           sum(cd.n)                            AS n_rows,
           count(*)                             AS n_active_ch,
           count(*) FILTER (WHERE cd.n_bad > 0) AS n_ch_bad,
           sum(cd.n_bad)                        AS n_bad,
           sum(cd.n_undef)                      AS n_undef,
           sum(cd.n_alarm)                      AS n_alarm,
           any_value(tot.n_ch_total)            AS n_ch_total
    FROM chanday cd
    JOIN '{C.CHAN}' c USING (ch)
    JOIN tot ON tot.pfx = c.pfx
    WHERE c.pfx IS NOT NULL
    GROUP BY 1, 2""")


def build_calendar(con, max_ts) -> None:
    """Календарь парка: строк за день, флаг провала, разрез.

    is_outage — правило, а не список: строк за день меньше 10 % медианы своего
    календарного года (quality.md разд. 7). Список из отчёта получается как следствие.
    split = NULL у дней-провалов внутри train: бриф исключает их из обучения,
    но из test отказы не выбрасывает — там split остаётся 'test'.
    """
    con.execute("DROP TABLE IF EXISTS calendar")
    splits = " ".join(
        f"WHEN d BETWEEN DATE '{a}' AND DATE '{b}' THEN '{name}'" for name, a, b in C.SPLITS)
    con.execute(f"""
    CREATE TABLE calendar AS
    WITH days AS (
        SELECT unnest(generate_series(DATE '{C.DATE_START}',
                                      DATE '{max_ts:%Y-%m-%d}', INTERVAL 1 DAY))::DATE AS d),
    tot AS (SELECT d, sum(n) AS n_rows_total FROM chanday GROUP BY 1),
    j AS (SELECT days.d, coalesce(tot.n_rows_total, 0) AS n_rows_total
          FROM days LEFT JOIN tot USING (d)),
    med AS (SELECT year(d) AS y, median(n_rows_total) AS m FROM j GROUP BY 1),
    f AS (SELECT j.d, j.n_rows_total,
                 j.n_rows_total < {C.OUTAGE_SHARE} * med.m AS is_outage,
                 CASE {splits} END AS period
          FROM j JOIN med ON med.y = year(j.d))
    SELECT d, n_rows_total, is_outage,
           CASE WHEN is_outage AND period = 'train' THEN NULL ELSE period END AS split
    FROM f ORDER BY d""")


def check_vs_old_chanday(con) -> list[tuple]:
    """Контроль задания: суммы n_bad за общий период должны совпасть со старым файлом.

    Общий период — пересечение [2024-10-01, 2026-06-30] старого файла и нашего окна.
    Сравниваем помесячно за 2025-01..2026-06, как просит задание, плюс итог.
    """
    return con.execute(f"""
    WITH new AS (SELECT strftime(d, '%Y-%m') AS m, sum(n_bad) AS nb, sum(n) AS n
                 FROM chanday WHERE d BETWEEN DATE '2025-01-01' AND DATE '2026-06-30' GROUP BY 1),
         old AS (SELECT strftime(d, '%Y-%m') AS m, sum(n_bad) AS nb, sum(n) AS n
                 FROM '{C.CHANDAY_OLD}'
                 WHERE d BETWEEN DATE '2025-01-01' AND DATE '2026-06-30' GROUP BY 1)
    SELECT coalesce(new.m, old.m) AS m, old.nb AS old_bad, new.nb AS new_bad,
           coalesce(new.nb, 0) - coalesce(old.nb, 0) AS d_bad,
           old.n AS old_n, new.n AS new_n, coalesce(new.n, 0) - coalesce(old.n, 0) AS d_n
    FROM new FULL OUTER JOIN old USING (m) ORDER BY 1""").fetchall()
