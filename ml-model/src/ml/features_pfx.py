"""Задача 2: признаки уровня префикса (коллектора).

Все окна заканчиваются в `d` включительно. Ничего из `d+1` и позже.
Скользящие окна — `RANGE` по датам поверх плотной сетки `grid_pfx`, не python-циклы.

Часть признаков считается по каналам префикса (умирающие каналы, максимумы
давности). Их даёт `channel_rollup()` — лёгкий проход по `grid_ch` частями
по `ch % CHUNKS`: `sum` и `max` ассоциативны, поэтому части складываются.
"""

from __future__ import annotations

from . import config as C

DYING_RATE = 0.3        # канал «умирающий»: темп 7 сут к своему фону за год ниже 0,3 (HLD 6.3-трис)


def _w(days: int) -> str:
    return (f"PARTITION BY pfx ORDER BY d "
            f"RANGE BETWEEN INTERVAL {days - 1} DAY PRECEDING AND CURRENT ROW")


def _roll(col: str, days: int) -> str:
    """Скользящая сумма col за `days` суток, окно кончается в d."""
    if days == 1:
        return f"{col} AS {col}_1d"
    return f"sum({col}) OVER ({_w(days)}) AS {col}_{days}d"


def channel_rollup(con) -> None:
    """Сводка по каналам префикса: живые, умирающие, максимумы давности.

    Считается на сетке канал-день, но результат — признак префикса, поэтому
    живёт здесь, а не в features.py: features.py его только читает.
    """
    con.execute("DROP TABLE IF EXISTS pfx_ch_rollup")
    first = True
    for k in range(C.CHUNKS):
        sql = f"""
        WITH src AS (
            SELECT g.ch, c.pfx, g.d, g.n, g.n_bad, g.days_since_rec
            FROM grid_ch g JOIN '{C.CHAN}' c USING (ch)
            WHERE g.ch % {C.CHUNKS} = {k} AND c.pfx IS NOT NULL),
        w AS (
            SELECT pfx, d, days_since_rec,
                   sum(n) OVER (PARTITION BY ch ORDER BY d
                        RANGE BETWEEN INTERVAL 6 DAY PRECEDING AND CURRENT ROW) AS n_7d,
                   sum(n) OVER (PARTITION BY ch ORDER BY d
                        RANGE BETWEEN INTERVAL 364 DAY PRECEDING AND CURRENT ROW) AS n_365d,
                   date_diff('day', last_value(CASE WHEN n_bad > 0 THEN d END IGNORE NULLS)
                        OVER (PARTITION BY ch ORDER BY d ROWS UNBOUNDED PRECEDING), d)
                        AS days_since_bad
            FROM src)
        SELECT pfx, d,
               count(*)                                   AS n_alive_ch,
               count(*) FILTER (WHERE n_365d > 0
                    AND 52.0 * n_7d / n_365d < {DYING_RATE}) AS n_dying_ch,
               max(days_since_bad)                        AS max_days_since_bad,
               max(days_since_rec)                        AS max_days_since_rec
        FROM w GROUP BY 1, 2"""
        if first:
            con.execute(f"CREATE TABLE pfx_ch_rollup_parts AS {sql}")
            first = False
        else:
            con.execute(f"INSERT INTO pfx_ch_rollup_parts {sql}")
    # части по ch % CHUNKS складываем: каналы не пересекаются между частями
    con.execute("""
    CREATE TABLE pfx_ch_rollup AS
    SELECT pfx, d, sum(n_alive_ch) AS n_alive_ch, sum(n_dying_ch) AS n_dying_ch,
           max(max_days_since_bad) AS max_days_since_bad,
           max(max_days_since_rec) AS max_days_since_rec
    FROM pfx_ch_rollup_parts GROUP BY 1, 2""")
    con.execute("DROP TABLE pfx_ch_rollup_parts")


def _active7(con) -> None:
    """Сколько разных каналов префикса писали хоть что-то за 7 суток.

    `count(DISTINCT)` в оконной функции DuckDB не поддерживает, поэтому
    диапазонное соединение: сетка префиксов мала (45 546 строк).
    """
    con.execute("DROP TABLE IF EXISTS pfx_act7")
    con.execute(f"""
    CREATE TABLE pfx_act7 AS
    WITH pcd AS (SELECT c.pfx, cd.ch, cd.d FROM chanday cd JOIN '{C.CHAN}' c USING (ch)
                 WHERE c.pfx IS NOT NULL)
    SELECT g.pfx, g.d, count(DISTINCT pcd.ch) AS act_ch_7d
    FROM grid_pfx g JOIN pcd ON pcd.pfx = g.pfx
         AND pcd.d > g.d - INTERVAL 7 DAY AND pcd.d <= g.d
    GROUP BY 1, 2""")


def build(con) -> None:
    """Таблица features_pfx."""
    channel_rollup(con)
    _active7(con)
    rolls = [_roll(c, w) for c in ("n_rows", "n_bad", "n_undef", "n_alarm")
             for w in (1, 3, 7, 28)]
    rolls += [_roll("n_ch_bad", w) for w in (1, 7, 28)]
    rolls.append(f"sum(n_rows) OVER ({_w(365)}) AS n_rows_365d")
    con.execute("DROP TABLE IF EXISTS features_pfx")
    con.execute(f"""
    CREATE TABLE features_pfx AS
    WITH inc_day AS (SELECT pfx, t_start::DATE AS d, count(*) AS n_inc
                     FROM incidents GROUP BY 1, 2),
         fail_day AS (SELECT pfx, t_start::DATE AS d, count(*) AS n_fail
                      FROM failures WHERE pfx IS NOT NULL GROUP BY 1, 2),
    base AS (
        SELECT g.pfx, g.d, g.n_ch_total, g.n_active_ch,
               coalesce(inc_day.n_inc, 0) AS n_inc, coalesce(fail_day.n_fail, 0) AS n_fail,
               g.n_rows, g.n_bad, g.n_undef, g.n_alarm, g.n_ch_bad,
               CASE WHEN inc_day.n_inc > 0 THEN g.d END AS d_inc
        FROM grid_pfx g
        LEFT JOIN inc_day USING (pfx, d)
        LEFT JOIN fail_day USING (pfx, d)),
    w AS (
        SELECT pfx, d, n_ch_total, n_active_ch, {', '.join(rolls)},
               sum(n_inc) OVER ({_w(28)})  AS inc_28d,
               sum(n_inc) OVER ({_w(90)})  AS inc_90d,
               sum(n_inc) OVER ({_w(365)}) AS inc_365d,
               sum(n_fail) OVER ({_w(7)})  AS fails_7d,
               sum(n_fail) OVER ({_w(28)}) AS fails_28d,
               date_diff('day', last_value(d_inc IGNORE NULLS)
                    OVER (PARTITION BY pfx ORDER BY d ROWS UNBOUNDED PRECEDING), d)
                    AS days_since_inc
        FROM base)
    SELECT w.*,
           w.n_active_ch::DOUBLE / nullif(w.n_ch_total, 0)      AS act_share_1d,
           a.act_ch_7d::DOUBLE / nullif(w.n_ch_total, 0)        AS act_share_7d,
           52.0 * w.n_rows_7d / nullif(w.n_rows_365d, 0)        AS rate_7d,
           13.0 * w.n_rows_28d / nullif(w.n_rows_365d, 0)       AS rate_28d,
           r.n_alive_ch, r.n_dying_ch,
           r.n_dying_ch::DOUBLE / nullif(r.n_alive_ch, 0)       AS dying_share,
           r.max_days_since_bad, r.max_days_since_rec
    FROM w LEFT JOIN pfx_act7 a USING (pfx, d) LEFT JOIN pfx_ch_rollup r USING (pfx, d)""")


def feature_columns(con) -> list[str]:
    """Имена признаков префикса без ключей — для приклейки к канальным признакам."""
    cols = [r[0] for r in con.execute("DESCRIBE features_pfx").fetchall()]
    return [c for c in cols if c not in ("pfx", "d")]
