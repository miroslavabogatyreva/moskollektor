import duckdb, sys
sys.path.insert(0,'/Users/miroslavabogatyreva/Projects/moskollektor/code')
from predictive_metrics import evaluate_alerts
con=duckdb.connect(); con.execute("SET memory_limit='5GB'; SET threads=6; SET temp_directory='./dtmp';")
q=lambda s,n=40: [print(r) for r in con.execute(s).fetchall()[:n]]
con.execute("CREATE TABLE f AS SELECT * FROM 'fail.parquet'"); con.execute("CREATE TABLE cd AS SELECT * FROM 'chanday.parquet'")
print("== потолок: сколько отказов 2025–06.2026 имеют хоть одну запись канала в окне [t-8 сут, t-24 ч] ==")
q("""SELECT count(*) n, count(*) FILTER (WHERE k>0) has_rows, round(count(*) FILTER (WHERE k>0)*1.0/count(*),3) AS sh,
   count(*) FILTER (WHERE k>=5) has5, count(*) FILTER (WHERE k>=20) has20
 FROM (SELECT f.ch, f.t_start, (SELECT coalesce(sum(n),0) FROM cd WHERE cd.ch=f.ch AND cd.d >= (f.t_start - INTERVAL 8 DAY)::DATE AND cd.d < (f.t_start - INTERVAL 1 DAY)::DATE) k FROM f)""")
print("== то же по типу датчика ==")
q("""SELECT stype, count(*) n, round(count(*) FILTER (WHERE k>0)*1.0/count(*),2) AS sh_rows
 FROM (SELECT f.stype, (SELECT coalesce(sum(n),0) FROM cd WHERE cd.ch=f.ch AND cd.d >= (f.t_start - INTERVAL 8 DAY)::DATE AND cd.d < (f.t_start - INTERVAL 1 DAY)::DATE) k FROM f) GROUP BY 1 ORDER BY 2 DESC""")
print("== доля отказов в пачках: старт в пределах 10 минут от >=4 других отказов того же префикса ==")
con.execute("""CREATE TABLE fb AS SELECT a.ch, a.t_start, a.pfx, (SELECT count(*) FROM f b WHERE b.pfx=a.pfx AND abs(date_diff('second', a.t_start, b.t_start))<=600)-1 AS mates FROM f a""")
q("SELECT count(*) n, count(*) FILTER (WHERE mates>=4) in_burst, round(count(*) FILTER (WHERE mates>=4)*1.0/count(*),3) AS sh, count(*) FILTER (WHERE mates=0) alone FROM fb")
print("== одиночные отказы (mates=0): есть ли у них записи за неделю до ==")
q("""SELECT count(*) n, round(count(*) FILTER (WHERE k>0)*1.0/count(*),3) AS sh_rows
 FROM (SELECT fb.ch, (SELECT coalesce(sum(n),0) FROM cd WHERE cd.ch=fb.ch AND cd.d >= (fb.t_start - INTERVAL 8 DAY)::DATE AND cd.d < (fb.t_start - INTERVAL 1 DAY)::DATE) k FROM fb WHERE mates=0)""")
print("== уровень префикса: инциденты-пачки (>=5 отказов префикса в 10 мин) как цель; признак — число дребезгов Неисправен по префиксу за 7 дней ==")
con.execute("""CREATE TABLE inc AS SELECT pfx, min(t_start) t FROM (SELECT *, sum(brk) OVER (PARTITION BY pfx ORDER BY t_start ROWS UNBOUNDED PRECEDING) cl FROM
  (SELECT *, CASE WHEN lag(t_start) OVER (PARTITION BY pfx ORDER BY t_start) IS NULL OR date_diff('second', lag(t_start) OVER (PARTITION BY pfx ORDER BY t_start), t_start)>600 THEN 1 ELSE 0 END brk FROM f)) GROUP BY pfx, cl HAVING count(*)>=5""")
print('инцидентов-пачек:', con.execute("SELECT count(*), count(DISTINCT pfx) FROM inc").fetchone())
con.execute("""CREATE TABLE pd AS SELECT c.pfx, cd.d, sum(cd.n_bad) bad, sum(cd.n) n FROM cd JOIN (SELECT ch::INT ch, pfx FROM 'chan.parquet') c USING(ch) GROUP BY 1,2""")
con.execute("""CREATE TABLE pw AS SELECT pfx, d, sum(bad) OVER (PARTITION BY pfx ORDER BY d RANGE BETWEEN INTERVAL 6 DAY PRECEDING AND CURRENT ROW) bad7,
   sum(bad) OVER (PARTITION BY pfx ORDER BY d RANGE BETWEEN INTERVAL 62 DAY PRECEDING AND INTERVAL 7 DAY PRECEDING) bad56 FROM pd""")
incs=[(p,t) for p,t in con.execute("SELECT pfx, t FROM inc WHERE t>='2025-01-01'").fetchall()]
for name,cond in [('bad7>=20','bad7>=20'),('bad7>=50','bad7>=50'),('bad7>=3*bad56/8 и bad7>=20','bad7>=20 AND bad7>=3*bad56/8.0')]:
    al=con.execute(f"SELECT DISTINCT pfx, d + INTERVAL 1 DAY - INTERVAL 1 SECOND FROM pw WHERE d>='2025-01-01' AND {cond}").fetchall()
    m=evaluate_alerts(al, incs, horizon_hours=24, max_lead_hours=168); m0=evaluate_alerts(al, incs, horizon_hours=0, max_lead_hours=168)
    print(f"  {name:30s} alerts={len(al):5d} tp={m['tp']} fn={m['fn']} fp={m['fp']} recall={m['recall']} prec={m['precision']} med={m['median_lead_hours']} | h0 recall={m0['recall']} late<24h={m0['lead_under_24h_share']}")
