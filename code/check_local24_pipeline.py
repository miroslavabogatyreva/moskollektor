#!/usr/bin/env python3
"""Real archive acceptance: score -> HTTP model -> DB -> risk API, without orders."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import urllib.request

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.worker.run_local24 import run, validate_score


def assert_parity(sections, actual):
    expected = {r['section_id']: r for r in sections}
    got = {r['section_id']: r for r in actual}
    if len(got) != len(actual) or set(got) != set(expected):
        raise AssertionError('Published section set differs from score')
    delta = max(abs(got[s]['probability']-r['p']) for s,r in expected.items())
    if delta > 1e-12:
        raise AssertionError(f'Published probability differs: {delta}')
    if any(got[s]['risk_rank'] != r['rank'] or got[s]['horizon_h'] != 24 for s,r in expected.items()):
        raise AssertionError('Published rank/horizon differs')
    return delta


def get_json(url):
    req = urllib.request.Request(url, headers={'X-User-Login':'dispatcher1'})
    with urllib.request.urlopen(req,timeout=30) as reply:
        return json.load(reply)


async def check(path, api):
    data=json.loads(Path(path).read_text())
    validate_score(data)
    conn=await asyncpg.connect(os.environ['DATABASE_URL'])
    try:
        # This command is an integration test; do not let it overwrite a shared product DB.
        database=await conn.fetchval('SELECT current_database()')
        if database!='mk_local24_e2e':
            raise ValueError('Acceptance requires isolated mk_local24_e2e database')
        counts='SELECT (SELECT count(*) FROM maint.notification) AS notifications,(SELECT count(*) FROM maint.work_order) AS orders'
        before=dict(await conn.fetchrow(counts))
        result=await run(conn,path)
        actual=[dict(r) for r in await conn.fetch('SELECT section_id,probability,risk_rank,horizon_h FROM pred.forecast_current')]
        db_delta=assert_parity(data['sections'],actual)
        api_rows=await asyncio.to_thread(get_json,api.rstrip('/')+'/api/risks')
        api_delta=assert_parity(data['sections'],api_rows)
        method=await asyncio.to_thread(get_json,api.rstrip('/')+'/api/forecast-method')
        assert method['run_id']==result['run_id'] and method['score_metadata']['orders_enabled'] is False
        after=dict(await conn.fetchrow(counts))
        assert before==after, 'Archive publication created work orders or notifications'
        variety={}
        for row in data['sections']:
            variety.setdefault(row['collector_id'],set()).add(row['p'])
        assert any(len(values)>1 for values in variety.values()), 'No local variation within any collector'
        return dict(result,db_max_error=db_delta,api_max_error=api_delta,orders_before_after=before,
                    distinct_probabilities_by_collector={str(k):len(v) for k,v in sorted(variety.items())},
                    api_metadata_verified=True)
    finally:
        await conn.close()


def selfcheck():
    source=[dict(section_id=1,p=.125,rank=1)]
    output=[dict(section_id=1,probability=.125,risk_rank=1,horizon_h=24)]
    assert assert_parity(source,output)==0
    for corrupt in [[],[dict(output[0],probability=.1)],[dict(output[0],risk_rank=2)]]:
        try:assert_parity(source,corrupt)
        except AssertionError:pass
        else:raise AssertionError('Parity guard did not reject corruption')
    print('local24 acceptance selfcheck OK')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--score',type=Path);p.add_argument('--api',default='http://127.0.0.1:18004')
    p.add_argument('--selfcheck',action='store_true');a=p.parse_args()
    if a.selfcheck:selfcheck()
    elif a.score:print(json.dumps(asyncio.run(check(a.score,a.api)),ensure_ascii=False,allow_nan=False))
    else:p.error('--score or --selfcheck required')
