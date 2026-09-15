import duckdb, sys, time, os
DS='/Users/miroslavabogatyreva/Projects/moskollektor/dataset'
con=duckdb.connect()
con.execute("SET memory_limit='4GB'; SET threads=8; SET preserve_insertion_order=false;")
for y in sys.argv[1:]:
    t=time.time()
    src=f'{DS}/ext-journal-{y}.csv'; dst=f'parquet/j{y}.parquet'
    con.execute(f"""
      COPY (SELECT ид_события::BIGINT AS ev, ид_канала_данных::INTEGER AS ch,
                   (дата || ' ' || время)::TIMESTAMP AS ts,
                   тревожное AS alarm, значение_датчика AS val
            FROM read_csv('{src}', header=true, delim=',', quote='"',
                 columns={{'ид_события':'BIGINT','ид_канала_данных':'INTEGER','дата':'VARCHAR',
                           'время':'VARCHAR','тревожное':'BOOLEAN','значение_датчика':'VARCHAR'}},
                 store_rejects=true, rejects_table='rej_{y}', rejects_scan='rsc_{y}', rejects_limit=1000000))
      TO '{dst}' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 1000000)""")
    n=con.execute(f"SELECT count(*) FROM '{dst}'").fetchone()[0]
    r=con.execute(f"SELECT count(*) FROM rej_{y}").fetchone()[0]
    print(f'{y}: rows={n:,} rejected={r} {time.time()-t:.0f}s size={os.path.getsize(dst)//2**20}MB', flush=True)
    if r: print(con.execute(f"SELECT * FROM rej_{y} LIMIT 5").fetchall())
