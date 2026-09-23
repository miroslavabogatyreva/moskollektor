#!/usr/bin/env python3
"""Read-only response deadline comparison with actual next D5 collector incidents.

This descriptive replay does not establish that work was actually performed.
"""
import argparse,asyncio,json,os
from bisect import bisect_right
from pathlib import Path
from datetime import timezone,timedelta
import asyncpg,duckdb

async def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--incidents',required=True,type=Path)
    p.add_argument('--out',required=True,type=Path)
    a=p.parse_args();zone=timezone(timedelta(hours=3))
    inc={}
    with duckdb.connect() as d:
        for k,t in d.execute('SELECT pfx,t_start FROM read_parquet(?) ORDER BY t_start',[str(a.incidents)]).fetchall():
            inc.setdefault(int(k),[]).append(t.replace(tzinfo=zone))
    c=await asyncpg.connect(os.environ['DATABASE_URL'])
    try:
        rows=await c.fetch('''SELECT w.warning_key,w.collector_id,w.opened_at,w.expires_at,n.due_at
            FROM pred.warning w JOIN pred.warning_section ws USING(warning_key)
            JOIN maint.notification n ON n.warning_section_id=ws.id''')
    finally: await c.close()
    matched=before=0
    for r in rows:
        times=inc.get(r['collector_id'],[]);i=bisect_right(times,r['opened_at'])
        if i<len(times) and times[i]<=r['expires_at']:
            matched+=1;before+=r['due_at']<times[i]
    result=dict(orders=len(rows),orders_with_next_incident_in_window=matched,
        deadline_before_next_incident=before,share=round(before/matched,4) if matched else None,
        limitation='Deadlines are operational response targets, not predicted failure times or evidence of completed maintenance. Unmatched tail windows are censored.')
    a.out.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
    assert rows,'No orders to compare'
if __name__=='__main__':asyncio.run(main())
