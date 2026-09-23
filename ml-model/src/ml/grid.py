"""Сетки наблюдений: канал-день и префикс-день.

Канал «жив» в день `d`, если у него есть хотя бы одна запись за последние
365 суток, считая `d` (определение из задания). Сетка строится только по живым
дням: день без записей и без записей за год назад — это не пропуск наблюдения,
а отсутствие канала, и в знаменатель base rate он попадать не должен.

Окна признаков считаются по этой же сетке. Выкинутые дни не несут информации:
у них по определению нет записей, а `RANGE`-окна трактуют отсутствующую строку
как ноль — ровно то, что нужно.
"""

from __future__ import annotations

from . import config as C

# Колонки chanday, которые тянутся в сетку. Счётчики на молчащем дне = 0
# (счётчик без строк — это ноль), val_num_last остаётся NULL (значения не было).
_COUNTERS = ("n", "n_bad", "n_undef", "n_alarm", "n_vals", "n_obes", "n_batt", "n_numeric")


def build_grid_ch(con, date_end) -> None:
    """Плотная сетка живых канало-дней с приклеенными агрегатами дня."""
    zeros = ", ".join(f"coalesce(cd.{c}, 0) AS {c}" for c in _COUNTERS)
    con.execute("DROP TABLE IF EXISTS grid_ch")
    con.execute(f"""
    CREATE TABLE grid_ch AS
    WITH span AS (
        -- от первой записи канала до последней плюс год: дальше он заведомо не жив
        SELECT ch, min(d) AS d0,
               least(max(d) + INTERVAL {C.ALIVE_DAYS - 1} DAY,
                     DATE '{date_end:%Y-%m-%d}')::DATE AS d1
        FROM chanday GROUP BY 1),
    dense AS (
        SELECT ch, unnest(generate_series(d0, d1, INTERVAL 1 DAY))::DATE AS d FROM span),
    j AS (
        SELECT dense.ch, dense.d, {zeros}, cd.val_num_last,
               CASE WHEN cd.ch IS NOT NULL THEN dense.d END AS d_rec
        FROM dense LEFT JOIN chanday cd ON cd.ch = dense.ch AND cd.d = dense.d),
    w AS (
        SELECT *, date_diff('day', last_value(d_rec IGNORE NULLS)
                   OVER (PARTITION BY ch ORDER BY d ROWS UNBOUNDED PRECEDING), d)
                  AS days_since_rec
        FROM j)
    SELECT * EXCLUDE (d_rec) FROM w WHERE days_since_rec < {C.ALIVE_DAYS}""")


def build_grid_pfx(con, date_end) -> None:
    """Плотная сетка префикс-день: от первой записи префикса до конца выгрузки.

    Конец сетки — общая граница данных, а не последняя запись префикса. Иначе
    само наличие строки зависело бы от будущего, и признак дня `d` менялся бы
    от того, что случится после `d` — ровно та утечка, которую ловит тест.
    Аналога «жив» для префикса не вводим: ни один из 32 префиксов не пропустил
    ни одного календарного месяца между первой и последней записью
    (quality.md разд. 7, «Молчащие месяцы префиксов» — ноль).
    """
    zeros = ", ".join(f"coalesce(pd.{c}, 0) AS {c}"
                      for c in ("n_rows", "n_active_ch", "n_ch_bad", "n_bad", "n_undef", "n_alarm"))
    con.execute("DROP TABLE IF EXISTS grid_pfx")
    con.execute(f"""
    CREATE TABLE grid_pfx AS
    WITH span AS (SELECT pfx, min(d) AS d0, max(n_ch_total) AS n_ch_total
                  FROM pfxday GROUP BY 1),
    dense AS (SELECT pfx, n_ch_total,
                     unnest(generate_series(d0, DATE '{date_end:%Y-%m-%d}',
                                            INTERVAL 1 DAY))::DATE AS d FROM span)
    SELECT dense.pfx, dense.d, {zeros}, dense.n_ch_total
    FROM dense LEFT JOIN pfxday pd ON pd.pfx = dense.pfx AND pd.d = dense.d""")
