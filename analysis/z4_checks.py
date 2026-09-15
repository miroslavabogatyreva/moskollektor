#!/usr/bin/env python3
"""Замеры, которыми поправлены четыре числа в docs/HLD.md разд. 6.3-бис и 6.3-трис.

Считает прямо по CSV выгрузки, без промежуточных Parquet: проверки лёгкие,
готовить данные ради них незачем. Все числа — за 2025 год, кроме газового
разреза по годам, который идёт по всем восьми файлам.

Две ловушки учтены, без них числа другие:
  1. Внутри ext-journal-2025.csv на строке 26 140 585 повторяется строка заголовка.
     Фильтр `ид_события <> 'ид_события'` обязателен, иначе разбор колонок падает.
  2. Сортировка по (дата, время, ид_события), а не по порядку строк в файле.
     1 305 295 строк за 2025 год делят канал и метку времени; без тай-брейка
     по ид_события число эпизодов «Неисправен» меняется на 7,2 %.

Запуск:  ~/.venv/mk/bin/python analysis/z4_checks.py     (нужен duckdb)
"""

import duckdb

D = '/Users/miroslavabogatyreva/Projects/moskollektor/dataset/'
con = duckdb.connect()
con.execute("SET threads TO 8; SET memory_limit='8GB'")

con.execute(f"""CREATE VIEW ch AS
  SELECT CAST(ид_канала_данных AS BIGINT) ch, тип_датчика
  FROM read_csv('{D}справочник_каналов_датчиков.csv', all_varchar=true)""")

con.execute(f"""CREATE VIEW j25 AS
  SELECT CAST(ид_канала_данных AS BIGINT) ch,
         CAST(дата AS DATE) d, время AS t, CAST(ид_события AS BIGINT) ev,
         значение_датчика AS val, TRY_CAST(значение_датчика AS DOUBLE) num
  FROM read_csv('{D}ext-journal-2025.csv', all_varchar=true)
  WHERE ид_события <> 'ид_события'""")

# Соседние записи канала. Порядок — (дата, время, ид_события), см. ловушку 2.
con.execute("""CREATE TABLE lagged AS
  SELECT ch, d, val,
         lag(val, 1) OVER w AS p1,
         lag(val, 2) OVER w AS p2
  FROM j25 WINDOW w AS (PARTITION BY ch ORDER BY d, t, ev)""")

print("1. Повторы предыдущего значения — HLD 6.3-трис писал 0,0090 %")
print(con.sql("""SELECT count(*) FILTER (WHERE p1 IS NOT NULL) пар,
                        count(*) FILTER (WHERE val = p1) повторов,
                        round(100.0 * count(*) FILTER (WHERE val = p1)
                              / count(*) FILTER (WHERE p1 IS NOT NULL), 4) доля_проц
                 FROM lagged"""))

print("   он же по каналу 196723 — HLD писал 144 повтора")
print(con.sql("""SELECT count(*) записей, count(*) FILTER (WHERE val = p1) повторов
                 FROM lagged WHERE ch = 196723"""))

print("2. Залипание freeze_n=3 — HLD 6.3-трис писал «21 раз на 1 129 каналах»")
print(con.sql("""SELECT count(*) срабатываний, count(DISTINCT ch) каналов
                 FROM lagged WHERE val = p1 AND p1 = p2"""))

print("   на чём именно срабатывает")
print(con.sql("""SELECT val значение, count(*) срабатываний, count(DISTINCT ch) каналов
                 FROM lagged WHERE val = p1 AND p1 = p2
                 GROUP BY 1 ORDER BY 2 DESC LIMIT 6"""))

print("3. Числовые каналы — HLD 6.3-бис писал 1 072 (600 температурных + 472 тепловых)")
print(con.sql("""WITH s AS (SELECT ch, count(*) n, count(num) nn FROM j25 GROUP BY 1)
                 SELECT тип_датчика, count(*) всего_пишут,
                        count(*) FILTER (WHERE nn > 0) хоть_одно_число,
                        count(*) FILTER (WHERE nn * 1.0 / n > 0.5) числовых
                 FROM s JOIN ch USING(ch) GROUP BY 1
                 ORDER BY числовых DESC, всего_пишут DESC LIMIT 8"""))

print("   «Тепловой датчик» отдельно: сколько записей и сколько из них чисел")
print(con.sql("""SELECT count(*) записей, count(num) числовых, count(DISTINCT j25.ch) каналов
                 FROM j25 JOIN ch USING(ch) WHERE тип_датчика = 'Тепловой датчик'"""))

print("4. Газовые каналы за 2025 — HLD 6.3-бис писал диапазон 0,00-0,37")
print(con.sql("""SELECT count(num) числовых_строк, count(DISTINCT j25.ch) каналов,
                        min(num) мин, quantile_cont(num, 0.5) медиана,
                        quantile_cont(num, 0.99) p99, max(num) макс
                 FROM j25 JOIN ch USING(ch)
                 WHERE тип_датчика = 'Газовый датчик' AND num IS NOT NULL"""))

# Дрейф нуля — это отрицательные значения. По всем восьми файлам, по годам:
# он был в 2020-2021 и прекратился, поэтому признак zero_drift_max мёртв
# не только из-за неизвестной единицы измерения (вопрос В-9).
con.execute(f"""CREATE VIEW jall AS
  SELECT CAST(ид_канала_данных AS BIGINT) ch, CAST(дата AS DATE) d,
         TRY_CAST(значение_датчика AS DOUBLE) num
  FROM read_csv('{D}ext-journal-*.csv', all_varchar=true, union_by_name=true)
  WHERE ид_события <> 'ид_события'""")

print("   отрицательные значения по годам, все восемь файлов (2-3 минуты)")
print(con.sql("""SELECT year(d) год, тип_датчика, count(*) строк,
                        count(DISTINCT jall.ch) каналов, min(num) минимум
                 FROM jall JOIN ch USING(ch) WHERE num < 0
                 GROUP BY 1, 2 ORDER BY 1, 3 DESC"""))
