import duckdb
con=duckdb.connect(); con.execute("SET memory_limit='5GB'; SET threads=6; SET temp_directory='./dtmp';")
q=lambda s: print(con.execute(s).fetchall())
# Склейка идёт ВНУТРИ КОЛЛЕКТОРА. Это не мелочь оформления: до 15.09.2026 обе оконные
# функции стояли без PARTITION BY pfx, и запрос склеивал в один инцидент отказы
# на разных коллекторах, если их старты разошлись меньше чем на 10 минут. Окно
# апрель–июнь 2026 из-за этого давало 166 инцидентов вместо 176, и это число ушло
# в семь документов. Нашёл расхождение Николай, пересчитав своим ключом.
# Методика записана в code/predictive_metrics.py, функция group_incidents(): группа —
# префикс тега, то есть коллектор. Здесь должно получаться то же самое.
#
# Вторая ловушка того же места: после PARTITION BY номер кластера cl уникален только
# ВНУТРИ префикса. count(DISTINCT cl) считает номера, а не инциденты, и снова занижает.
# Считать надо пары (pfx, cl).
#
# ЭТОТ СКРИПТ ОСТАЛСЯ НА СТАРОМ КЛЮЧЕ, И ЭТО ОСОЗНАННО (17.09.2026, MOS-103).
# Коллектор здесь — префикс тега, а в продукте он с 17.09.2026 берётся из дерева
# объектов заказчика: префиксов 32, коллекторов 16, и два префикса ведут к разным
# коллекторам. Правильный ключ живёт в collector_key() в code/predictive_metrics.py.
# Переписывать запрос я не стал по одной причине: parquet-файлов, на которых он
# работает, в рабочем дереве нет, и проверить правку было бы нечем — а непроверенная
# правка тут хуже честной оговорки. На числе это не сказывается: пересчёт обоими
# ключами на окне апрель–июнь 2026 дал одно и то же — 176 инцидентов, помесячно
# 92 / 48 / 36. Разбор — docs/for-ml-team.md, раздел 3.
def clusters(lo,hi,gap=600):
    con.execute(f"""CREATE OR REPLACE TABLE g AS
     WITH e AS (SELECT ep.t_start, ep.ch, c.pfx FROM ep JOIN chan c USING(ch)
                 WHERE ep.dur_h>1 AND ep.t_start>='{lo}' AND ep.t_start<'{hi}'),
     s AS (SELECT t_start, ch, pfx, lag(t_start) OVER (PARTITION BY pfx ORDER BY t_start, ch) AS p FROM e),
     m AS (SELECT *, CASE WHEN p IS NULL OR date_diff('second', p, t_start) > {gap} THEN 1 ELSE 0 END AS brk FROM s)
     SELECT t_start, ch, pfx, sum(brk) OVER (PARTITION BY pfx ORDER BY t_start, ch ROWS UNBOUNDED PRECEDING) AS cl FROM m""")
    q("SELECT count(*) AS clusters, count(*) FILTER (WHERE n=1) AS singleton_clusters, max(n) AS biggest FROM (SELECT pfx, cl, count(*) n FROM g GROUP BY pfx, cl)")
    q("""SELECT min(t_start) AS t0, count(*) n, count(DISTINCT ch) nch, any_value(pfx) AS pfx FROM g GROUP BY pfx, cl ORDER BY n DESC LIMIT 6""")


def selfcheck():
    """Проверка обязана поймать ровно ту ошибку, которая здесь была.

    Без неё скрипт молчал: он не падал и не выдавал ничего странного — просто
    возвращал число меньше правильного, и отличить его от верного было нечем.
    """
    con.execute("CREATE OR REPLACE TABLE ep AS SELECT * FROM (VALUES "
                "(TIMESTAMP '2026-05-01 09:00:00', 1, 2.0), "
                "(TIMESTAMP '2026-05-01 09:04:00', 2, 2.0), "
                "(TIMESTAMP '2026-05-01 09:08:00', 3, 2.0), "
                "(TIMESTAMP '2026-05-01 09:03:00', 4, 2.0)) AS t(t_start, ch, dur_h)")
    con.execute("CREATE OR REPLACE TABLE chan AS SELECT * FROM (VALUES "
                "(1,'257'),(2,'884'),(3,'1044'),(4,'257')) AS t(ch, pfx)")
    clusters('2026-05-01','2026-05-02')
    n = con.execute("SELECT count(*) FROM (SELECT pfx, cl FROM g GROUP BY pfx, cl)").fetchone()[0]
    # Три коллектора: 257 (каналы 1 и 4, старты через 3 минуты — один инцидент),
    # 884 и 1044 по одному. Итого три, а не один: со старым запросом все четыре
    # отказа сливались в один кластер, потому что шли подряд по времени.
    assert n == 3, f"склейка разъехалась: инцидентов {n}, ждали 3"
    print("самопроверка: три коллектора дают три инцидента, склейка идёт внутри коллектора")


# Самопроверка идёт ДО чтения выгрузки: она работает на четырёх строках в памяти
# и не требует ни parquet, ни сервера. Данные ниже перезаписывают её таблицы.
selfcheck()

import os
for файл in ("episodes_all.parquet", "chan.parquet"):
    if not os.path.exists(файл):
        raise SystemExit(f"нет файла {файл} — запускать надо там, где лежит выгрузка "
                         f"(см. docs/server.md), из каталога с parquet")
con.execute("CREATE OR REPLACE TABLE ep AS SELECT * FROM 'episodes_all.parquet'")
con.execute("CREATE OR REPLACE TABLE chan AS SELECT * FROM 'chan.parquet'")
print("== 51 открытых: 13.06.2026 05:25 — что за каналы ==")
q("SELECT c.pfx, c.stype, count(*) FROM ep e JOIN chan c USING(ch) WHERE e.t_end IS NULL AND t_start>='2026-04-01' GROUP BY 1,2 ORDER BY 3 DESC")
print("== апр-июн 2026: кластеры стартов >1ч по паузе 10 мин ==")
clusters('2026-04-01','2026-07-01')
print("== 2025: кластеры ==")
clusters('2025-01-01','2026-01-01')
print("== сверка с day-one: 2021 в порядке файла вместо порядка времени ==")
q("""WITH r AS (SELECT row_number() OVER () AS rid, ch, ts, val FROM 'parquet/j2021.parquet'),
s AS (SELECT ch, ts, rid, val='Неисправен' AS bad, lag(val='Неисправен') OVER w AS pb, lead(val='Неисправен') OVER w AS nb, lead(ts) OVER w AS nts
      FROM r WINDOW w AS (PARTITION BY ch ORDER BY rid)),
e AS (SELECT ch, ts, rid, bad AND NOT coalesce(pb,false) AS st, bad AND NOT coalesce(nb,false) AS en, nts FROM s WHERE bad),
g AS (SELECT *, sum(CASE WHEN st THEN 1 ELSE 0 END) OVER (PARTITION BY ch ORDER BY rid ROWS UNBOUNDED PRECEDING) run FROM e)
SELECT count(*) AS episodes, count(*) FILTER (WHERE date_diff('second', min_ts, max_nts) > 3600) AS over1h_fileorder
FROM (SELECT ch, run, min(ts) min_ts, max(nts) max_nts FROM g GROUP BY ch, run)""")
