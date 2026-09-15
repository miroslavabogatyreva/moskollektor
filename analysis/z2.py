import duckdb, glob
con=duckdb.connect(); con.execute("SET memory_limit='5GB'; SET threads=6; SET temp_directory='./dtmp';")
q=lambda s,n=60: [print(r) for r in con.execute(s).fetchall()[:n]]
con.execute("CREATE TABLE chan AS SELECT * FROM 'chan.parquet'"); con.execute("CREATE TABLE ep AS SELECT * FROM 'episodes_all.parquet'")
files=sorted(glob.glob('parquet/j*.parquet'))
src=" UNION ALL ".join(f"SELECT ch, min(ts) t0, max(ts) t1, count(*) n FROM '{f}' GROUP BY ch" for f in files)
con.execute(f"CREATE TABLE life AS SELECT ch, min(t0) born, max(t1) died, sum(n) n FROM ({src}) GROUP BY ch")
con.execute("COPY life TO 'life.parquet' (FORMAT PARQUET)")
print("== З-4: что за 'д.1', 'наб.' в названиях каналов ==")
q("SELECT ch, pfx, stype, name FROM chan WHERE name ~ '(наб\\.|д\\.\\s?\\d)' LIMIT 10")
print("\n== префиксы: каналов, ид каналов min/median/max, рождение/смерть, есть ли g-каналы ==")
q("""SELECT c.pfx, count(*) n, min(c.ch::INT) idmin, median(c.ch::INT)::INT idmed, max(c.ch::INT) idmax,
   min(l.born)::DATE born_min, median(l.born)::DATE born_med, max(l.died)::DATE died_max,
   count(*) FILTER (WHERE c.tag LIKE '%-g%') g, count(DISTINCT c.sys) nsys
   FROM chan c LEFT JOIN life l ON l.ch=c.ch::INT GROUP BY 1 ORDER BY idmin""")
print("\n== g-каналы: живые названия объектов ==")
q("SELECT pfx, tag, name FROM chan WHERE tag LIKE '%-g%' ORDER BY pfx, tag", 60)
