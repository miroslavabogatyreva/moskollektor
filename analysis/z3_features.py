import duckdb, time
con=duckdb.connect(); con.execute("SET memory_limit='5GB'; SET threads=6; SET temp_directory='./dtmp';")
t=time.time()
con.execute("""COPY (
 SELECT ch, ts::DATE d, count(*) n, count(*) FILTER (WHERE val='Неисправен') n_bad,
        count(*) FILTER (WHERE val='Неопределен') n_undef, count(*) FILTER (WHERE alarm) n_alarm,
        count(DISTINCT val) n_vals
 FROM (SELECT * FROM 'parquet/j2024.parquet' WHERE ts >= '2024-10-01' UNION ALL SELECT * FROM 'parquet/j2025.parquet' UNION ALL SELECT * FROM 'parquet/j2026.parquet')
 GROUP BY 1,2) TO 'chanday.parquet' (FORMAT PARQUET)""")
print(con.execute("SELECT count(*), count(DISTINCT ch), min(d), max(d) FROM 'chanday.parquet'").fetchone(), f'{time.time()-t:.0f}s')
# отказы и дребезг из таблицы эпизодов
con.execute("""COPY (SELECT e.ch, e.t_start, e.dur_h, c.pfx, c.stype FROM 'episodes_all.parquet' e LEFT JOIN 'chan.parquet' c ON c.ch::INT=e.ch
   WHERE e.dur_h>1 AND e.t_start>='2025-01-01') TO 'fail.parquet' (FORMAT PARQUET)""")
print(con.execute("SELECT count(*), count(DISTINCT ch) FROM 'fail.parquet'").fetchone())
