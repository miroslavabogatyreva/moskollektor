#!/usr/bin/env python3
"""Compare actual PostgreSQL D5 episodes with the real ML dataset on an interior slice.

Use DATABASE_URL and --failures /absolute/path/to/failures.parquet. Boundary dates
exclude clipped month edges; neither side uses generated analytical fixtures.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import asyncpg
import duckdb

async def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--failures',required=True,type=Path)
    p.add_argument('--from',dest='start',default='2026-06-03')
    p.add_argument('--to',dest='end',default='2026-06-30')
    p.add_argument('--out',type=Path)
    a=p.parse_args()
    c=await asyncpg.connect(os.environ['DATABASE_URL'])
    try:
        rows=await c.fetch("""SELECT channel_id,
            started_at AT TIME ZONE 'Europe/Moscow' AS s,
            ended_at AT TIME ZONE 'Europe/Moscow' AS e
            FROM smvu.model_failure_episode
            WHERE started_at >= ($1::text::timestamp AT TIME ZONE 'Europe/Moscow')
              AND ended_at < ($2::text::timestamp AT TIME ZONE 'Europe/Moscow')""",a.start,a.end)
    finally: await c.close()
    got={(r['channel_id'],r['s'],r['e']) for r in rows}
    d=duckdb.connect()
    try:
        want=set(d.execute('SELECT ch,t_start,t_end FROM read_parquet(?) WHERE t_start >= ?::TIMESTAMP AND t_end < ?::TIMESTAMP',[str(a.failures),a.start,a.end]).fetchall())
    finally: d.close()
    result=dict(product=len(got),ml=len(want),common=len(got&want),only_product=len(got-want),only_ml=len(want-got),start=a.start,end=a.end)
    text=json.dumps(result,indent=2);print(text)
    if a.out: a.out.write_text(text+'\n')
    assert got and got==want,'D5 parity mismatch or empty comparison'

if __name__=='__main__': asyncio.run(main())
