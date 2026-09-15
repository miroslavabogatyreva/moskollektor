import duckdb, sys, time
sys.path.insert(0,'/Users/miroslavabogatyreva/Projects/moskollektor/code')
from predictive_metrics import evaluate_alerts
con=duckdb.connect(); con.execute("SET memory_limit='5GB'; SET threads=6; SET temp_directory='./dtmp';")
con.execute("CREATE TABLE cd AS SELECT * FROM 'chanday.parquet'"); con.execute("CREATE TABLE f AS SELECT * FROM 'fail.parquet'")
con.execute("CREATE TABLE chan AS SELECT ch::INT ch, pfx, stype FROM 'chan.parquet'")
# плотный календарь окон: для каждого канала-дня с записями — суммы за последние 7 дней и за предыдущие 8 недель
con.execute("""CREATE TABLE w AS
 SELECT ch, d, n, n_bad, n_undef,
   sum(n)      OVER (PARTITION BY ch ORDER BY d RANGE BETWEEN INTERVAL 6 DAY PRECEDING AND CURRENT ROW) n7,
   sum(n_bad)  OVER (PARTITION BY ch ORDER BY d RANGE BETWEEN INTERVAL 6 DAY PRECEDING AND CURRENT ROW) bad7,
   sum(n_undef)OVER (PARTITION BY ch ORDER BY d RANGE BETWEEN INTERVAL 6 DAY PRECEDING AND CURRENT ROW) undef7,
   sum(n)      OVER (PARTITION BY ch ORDER BY d RANGE BETWEEN INTERVAL 62 DAY PRECEDING AND INTERVAL 7 DAY PRECEDING) n56
 FROM cd""")
failures=[(ch, t) for ch,t in con.execute("SELECT ch, t_start FROM f WHERE t_start>='2025-01-01'").fetchall()]
fchans=set(ch for ch,_ in failures)
OBJDAYS=con.execute("SELECT count(*) FROM (SELECT DISTINCT ch FROM cd WHERE d>='2025-01-01')").fetchone()[0]*546
def run(name, sql):
    t=time.time()
    al=con.execute(f"SELECT DISTINCT ch, d + INTERVAL 1 DAY - INTERVAL 1 SECOND AS t FROM ({sql}) WHERE d>='2025-01-01' AND d<'2026-06-30'").fetchall()
    on_f=[(c,t) for c,t in al if c in fchans]; fp_other=len(al)-len(on_f)
    m24=evaluate_alerts(on_f, failures, horizon_hours=24, max_lead_hours=168)
    m0=evaluate_alerts(on_f, failures, horizon_hours=0, max_lead_hours=168)
    fp=m24['fp']+fp_other; tp=m24['tp']
    print(f"{name:34s} alerts={len(al):7d} tp={tp:4d} fn={m24['fn']:4d} fp={fp:7d} recall={m24['recall']:.3f} prec={tp/(tp+fp) if tp+fp else 0:.3f} "
          f"med_lead={m24['median_lead_hours']} | h0: recall={m0['recall']:.3f} med={m0['median_lead_hours']} late<24h={m0['lead_under_24h_share']} | fa/1000 obj-days={fp/OBJDAYS*1000:.2f} [{time.time()-t:.0f}s]", flush=True)
print('отказов', len(failures), 'каналов с отказами', len(fchans), 'объекто-дней', OBJDAYS)
run('A1 дребезг: bad7>=1',      "SELECT ch, d FROM w WHERE bad7>=1")
run('A3 дребезг: bad7>=3',      "SELECT ch, d FROM w WHERE bad7>=3")
run('A10 дребезг: bad7>=10',    "SELECT ch, d FROM w WHERE bad7>=10")
run('C неопр: undef7/n7>=0.2, n7>=10', "SELECT ch, d FROM w WHERE n7>=10 AND undef7*1.0/n7>=0.2")
run('D1 темп: n7 >= 5*n56/8, n56>=40', "SELECT ch, d FROM w WHERE n56>=40 AND n7 >= 5*n56/8.0")
run('D2 тишина: n7 <= 0.2*n56/8, n56>=80', "SELECT ch, d FROM w WHERE n56>=80 AND n7 <= 0.2*n56/8.0")
run('E повтор: отказ канала в 30 дн', """SELECT c.ch, g.d FROM f c, range(1,30) r(i), LATERAL (SELECT (c.t_start::DATE + INTERVAL (r.i) DAY)::DATE d) g""")
run('E7 повтор: отказ канала в 7 дн', """SELECT c.ch, g.d FROM f c, range(1,7) r(i), LATERAL (SELECT (c.t_start::DATE + INTERVAL (r.i) DAY)::DATE d) g""")
run('B сосед: отказ в префиксе за 7 дн', """SELECT ch2.ch, g.d FROM f c JOIN chan ch2 ON ch2.pfx=c.pfx, range(1,7) r(i), LATERAL (SELECT (c.t_start::DATE + INTERVAL (r.i) DAY)::DATE d) g""")
run('B* сосед того же типа за 7 дн', """SELECT ch2.ch, g.d FROM f c JOIN chan ch2 ON ch2.pfx=c.pfx AND ch2.stype=c.stype, range(1,7) r(i), LATERAL (SELECT (c.t_start::DATE + INTERVAL (r.i) DAY)::DATE d) g""")
