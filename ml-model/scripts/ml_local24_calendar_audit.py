#!/usr/bin/env python3
"""Read-only descriptive audit of gas notifications and D5 onset timing."""
import argparse,json
from pathlib import Path
import duckdb

def audit(root):
    c=duckdb.connect();c.execute("SET threads=3;SET memory_limit='4GB'")
    files=[str(p) for p in sorted((root/'data/02_interim/mk/parquet').glob('j*.parquet')) if p.stem[1:] in ['2022','2023','2024','2025','2026']]
    c.read_parquet(files).create_view('journal')
    c.execute("CREATE TABLE gas AS SELECT ch,ts,alarm FROM journal WHERE val='Обнаружен газ' AND ts>=TIMESTAMP '2022-04-01' AND ts<TIMESTAMP '2026-07-01'")
    c.read_parquet(str(root/'data/03_processed/local24_20260923/events.parquet')).create_view('d5')
    out={'kind':'descriptive_not_causal','period':['2022-04-01','2026-07-01'],
      'participant_claim':'Telegram444,periodnotfullyspecified; not an exact replication',
      'limits':['Timestamp is registration time, not verified physical onset.','Daily/weekly concentration does not prove operator interventions.','No visit schedule or repair labels supplied.']}
    for table,ts in [('gas','ts'),('d5','t_start')]:
        row=c.execute(f"SELECT count(*),count(DISTINCT ch)".replace('ch','channel_id' if table=='d5' else 'ch')+f",count(*) FILTER(WHERE isodow({ts})>=6),count(*) FILTER(WHERE isodow({ts})<=5 AND hour({ts}) BETWEEN 9 AND 13),min({ts}),max({ts}) FROM {table}").fetchone()
        out[table]={'rows':row[0],'channels':row[1],'weekend':row[2],'weekday_09_to_14_exclusive':row[3],'first':str(row[4]),'last':str(row[5]),
          'day_hour_counts':[{'iso_weekday':int(r[0]),'hour':int(r[1]),'rows':int(r[2])} for r in c.execute(f'SELECT isodow({ts}),hour({ts}),count(*) FROM {table} GROUP BY 1,2 ORDER BY 1,2').fetchall()]}
    out['gas']['same_channel_timestamp_duplicates']=c.execute('SELECT count(*)-count(DISTINCT(ch,ts)) FROM gas').fetchone()[0]
    out['gas']['year_counts']=[dict(year=r[0],rows=r[1],weekend=r[2]) for r in c.execute('SELECT year(ts),count(*),count(*) FILTER(WHERE isodow(ts)>=6) FROM gas GROUP BY 1 ORDER BY 1').fetchall()]
    c.close();return out
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    result=audit(a.root);a.out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:{n:v for n,v in result[k].items() if n not in ['day_hour_counts']} for k in ['gas','d5']},ensure_ascii=False))
