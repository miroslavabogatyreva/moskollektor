import duckdb
con=duckdb.connect(); con.execute("SET memory_limit='5GB'; SET threads=6; SET temp_directory='./dtmp';")
q=lambda s: print(con.execute(s).fetchall())
con.execute("CREATE TABLE ep AS SELECT * FROM 'episodes_all.parquet'"); con.execute("CREATE TABLE chan AS SELECT * FROM 'chan.parquet'")
def clusters(lo,hi,gap=600):
    con.execute(f"""CREATE OR REPLACE TABLE g AS
     WITH s AS (SELECT t_start, ch, lag(t_start) OVER (ORDER BY t_start, ch) AS p FROM ep WHERE dur_h>1 AND t_start>='{lo}' AND t_start<'{hi}'),
     m AS (SELECT *, CASE WHEN p IS NULL OR date_diff('second', p, t_start) > {gap} THEN 1 ELSE 0 END AS brk FROM s)
     SELECT t_start, ch, sum(brk) OVER (ORDER BY t_start, ch ROWS UNBOUNDED PRECEDING) AS cl FROM m""")
    q("SELECT count(DISTINCT cl) AS clusters, count(*) FILTER (WHERE n=1) AS singleton_clusters, max(n) AS biggest FROM (SELECT cl, count(*) n FROM g GROUP BY cl)")
    q("""SELECT min(t_start) AS t0, count(*) n, count(DISTINCT ch) nch, count(DISTINCT c.pfx) npfx, list(DISTINCT c.pfx) FROM g JOIN chan c USING(ch) GROUP BY cl ORDER BY n DESC LIMIT 6""")
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
