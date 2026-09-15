import duckdb
con=duckdb.connect(); con.execute("SET memory_limit='5GB'; SET threads=6; SET temp_directory='./dtmp';")
q=lambda s,n=80: [print(r) for r in con.execute(s).fetchall()[:n]]
con.execute("CREATE TABLE chan AS SELECT * FROM 'chan.parquet'"); con.execute("CREATE TABLE ep AS SELECT * FROM 'episodes_all.parquet'"); con.execute("CREATE TABLE life AS SELECT * FROM 'life.parquet'")
print("== каналы диспетчерских (ДП/ДУ/пультов/шкаф) по префиксам ==")
q("""SELECT pfx, count(*) n, count(*) FILTER (WHERE name ~ '(?i)(^|[^А-Яа-я])ДП([^А-Яа-я]|$)') dp,
     count(*) FILTER (WHERE name ~ '(?i)(^|[^А-Яа-я])ДУ([^А-Яа-я]|$)') du,
     count(*) FILTER (WHERE name ~ '(?i)пультов') pult, count(*) FILTER (WHERE name ~ '(?i)шкаф') shkaf,
     count(*) FILTER (WHERE name !~ 'ПК') no_pk
   FROM chan GROUP BY 1 ORDER BY pfx::INT""")
print("\n== 885 и 890: названия со 'щит' или 'ПК48' ==")
q("SELECT pfx, tag, sys, name FROM chan WHERE pfx IN ('885','890') AND name ~ '(?i)(щит|ПК48\\b|ПК5\\b|Г1)' ORDER BY pfx, tag", 40)
print("\n== 885: состав ==")
q("SELECT pfx, sys, stype, count(*), min(name), max(name) FROM chan WHERE pfx='885' GROUP BY 1,2,3")
print("\n== (а) рождение: сколько дней рождения покрывают 80% каналов префикса; и партии рождения, охватывающие >1 префикса ==")
q("""WITH b AS (SELECT c.pfx, l.born::DATE d, count(*) n FROM chan c JOIN life l ON l.ch=c.ch::INT WHERE l.born > '2019-01-02' GROUP BY 1,2)
SELECT d, count(DISTINCT pfx) npfx, sum(n) nch, list(pfx || ':' || n) FROM b GROUP BY d HAVING count(DISTINCT pfx)>1 AND sum(n)>=20 ORDER BY nch DESC LIMIT 25""")
print("\n== (а) смерть: партии умерших (последняя запись до 2026-06-01) по датам, охватывающие >1 префикса ==")
q("""WITH b AS (SELECT c.pfx, l.died::DATE d, count(*) n FROM chan c JOIN life l ON l.ch=c.ch::INT WHERE l.died < '2026-06-01' GROUP BY 1,2)
SELECT d, count(DISTINCT pfx) npfx, sum(n) nch, list(pfx || ':' || n) FROM b GROUP BY d HAVING sum(n)>=10 ORDER BY nch DESC LIMIT 25""")
print("\n== (б) совместные старты Неисправен в 5-мин корзинах между префиксами: lift = набл/ожид при независимости ==")
con.execute("""CREATE TABLE pb AS SELECT DISTINCT c.pfx, (epoch(e.t_start)::BIGINT // 300) AS b FROM ep e JOIN chan c ON c.ch::INT=e.ch""")
con.execute("CREATE TABLE pn AS SELECT pfx, count(*) n FROM pb GROUP BY 1")
NB=con.execute("SELECT (epoch(TIMESTAMP '2026-07-01')-epoch(TIMESTAMP '2019-01-01'))//300").fetchone()[0]
q(f"""SELECT a.pfx pa, b.pfx pb2, count(*) obs, round(count(*)*1.0/(na.n*nb.n/{NB}),1) lift, na.n, nb.n
  FROM pb a JOIN pb b ON a.b=b.b AND a.pfx<b.pfx JOIN pn na ON na.pfx=a.pfx JOIN pn nb ON nb.pfx=b.pfx
  GROUP BY 1,2,na.n,nb.n HAVING count(*)>=30 ORDER BY lift DESC LIMIT 40""")
print("\n== (б) то же только для эпизодов >1ч (отказы), окно 10 мин ==")
con.execute("""CREATE TABLE pb2 AS SELECT DISTINCT c.pfx, (epoch(e.t_start)::BIGINT // 600) AS b FROM ep e JOIN chan c ON c.ch::INT=e.ch WHERE dur_h>1""")
con.execute("CREATE TABLE pn2 AS SELECT pfx, count(*) n FROM pb2 GROUP BY 1")
NB2=NB//2
q(f"""SELECT a.pfx pa, b.pfx pb2, count(*) obs, round(count(*)*1.0/(na.n*nb.n/{NB2}),1) lift, na.n, nb.n
  FROM pb2 a JOIN pb2 b ON a.b=b.b AND a.pfx<b.pfx JOIN pn2 na ON na.pfx=a.pfx JOIN pn2 nb ON nb.pfx=b.pfx
  GROUP BY 1,2,na.n,nb.n HAVING count(*)>=5 ORDER BY lift DESC LIMIT 40""")
