# Эпизоды "Неисправен" по всем файлам: открывается на первой записи Неисправен, закрывается
# первой следующей записью канала с другим значением. Каналы режем на K частей по ch % K.
import duckdb, sys, time, glob
K=int(sys.argv[1]); files=sorted(glob.glob('parquet/j*.parquet'))
con=duckdb.connect(); con.execute("SET memory_limit='5GB'; SET threads=6; SET temp_directory='./dtmp'; SET preserve_insertion_order=false;")
con.execute("CREATE TABLE ep (ch INT, t_start TIMESTAMP, t_last TIMESTAMP, n_bad INT, t_end TIMESTAMP, close_val VARCHAR, dur_h DOUBLE)")
for k in range(K):
    t=time.time()
    src=" UNION ALL ".join(f"SELECT ch, ts, ev, val FROM '{f}' WHERE ch % {K} = {k}" for f in files)
    con.execute(f"""
    INSERT INTO ep
    WITH r AS ({src}),
    s AS (SELECT ch, ts, ev, val, val='Неисправен' AS bad,
             lag(val='Неисправен')  OVER w AS prev_bad,
             lead(val='Неисправен') OVER w AS next_bad,
             lead(ts) OVER w AS next_ts, lead(val) OVER w AS next_val
          FROM r WINDOW w AS (PARTITION BY ch ORDER BY ts, ev)),
    e AS (SELECT ch, ts, bad AND NOT coalesce(prev_bad,false) AS is_start,
                 bad AND NOT coalesce(next_bad,false) AS is_end, next_ts, next_val
          FROM s WHERE bad),
    g AS (SELECT *, sum(CASE WHEN is_start THEN 1 ELSE 0 END) OVER (PARTITION BY ch ORDER BY ts ROWS UNBOUNDED PRECEDING) AS run FROM e)
    SELECT ch, min(ts), max(ts), count(*), max(next_ts), max(next_val),
           date_diff('second', min(ts), max(next_ts))/3600.0
    FROM g GROUP BY ch, run""")
    print(k, con.execute("SELECT count(*) FROM ep").fetchone()[0], f'{time.time()-t:.0f}s', flush=True)
con.execute("COPY ep TO 'episodes_all.parquet' (FORMAT PARQUET)")
print(con.execute("""SELECT count(*) AS episodes, count(DISTINCT ch) AS channels,
   sum(CASE WHEN t_end IS NULL THEN 1 ELSE 0 END) AS still_open,
   sum(CASE WHEN dur_h>1 THEN 1 ELSE 0 END) AS over_1h, count(DISTINCT CASE WHEN dur_h>1 THEN ch END) AS ch_over_1h,
   sum(CASE WHEN n_bad=1 THEN 1 ELSE 0 END) AS single_row, median(dur_h*3600) AS med_s FROM ep""").fetchall())
