#!/usr/bin/env python3
"""Time score container -> worker -> live API on an explicitly isolated local DB.

Requires real registries/readings and migrations already loaded. Never resets DB.
Scores all collector history needed for the cut, and checks every exposed section.
"""
import argparse
import json
import subprocess
import re
from datetime import datetime
import time
import urllib.request
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ml-root', required=True, type=Path)
    p.add_argument('--network', default='codex-mk-fixes')
    p.add_argument('--dsn', default='postgresql://mk:e2e@codex-mk-fixes-db:5432/mk')
    p.add_argument('--ml-url', default='http://codex-mk-fixes-ml:8100')
    p.add_argument('--api', default='http://127.0.0.1:18000')
    p.add_argument('--as-of', default='2026-06-30 23:59:59')
    p.add_argument('--expect-new-orders',type=int)
    p.add_argument('--score-image', default='moskollektor/ml-score:collector-20260922')
    p.add_argument('--backend-image', default='codex-mk-fixes-backend')
    p.add_argument('--out', type=Path, default=Path('docs/proof/2026-09-22-collector-release'))
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[1]
    output = a.out.resolve()
    def run(cmd, log):
        start = time.perf_counter()
        with (output/log).open('w') as f:
            subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, check=True)
        return time.perf_counter()-start
    def get(path):
        req=urllib.request.Request(a.api+path,headers={'X-User-Login':'dispatcher1'})
        with urllib.request.urlopen(req,timeout=60) as r: return json.load(r)
    before_orders=get('/api/orders?limit=1')['total']
    started = time.perf_counter()
    score_s = run(['docker','run','--rm','--cpus','8',
        '-v',f'{(a.ml_root/"data").resolve()}:/app/data:ro', '-v',f'{output}:/out',
        a.score_image,'--as-of',a.as_of,'--out','/out/score.json'], 'score.log')
    score = json.loads((output/'score.json').read_text())
    assert score['object_level'] == 'collector' and score['horizon_h'] == 720
    worker_s = run(['docker','run','--rm','--network',a.network,
        '-e',f'DATABASE_URL={a.dsn}','-e',f'ML_URL={a.ml_url}',
        '-e','TZ=Europe/Moscow','-e','SCORE_V3_PATH=/score/score.json',
        '-v',f'{output}:/score:ro','-v',f'{root/"backend/app"}:/app/app:ro',
        a.backend_image,'python','-u','-m','app.worker.run','--as-of',a.as_of.replace(' ','T')+'+03:00'], 'worker.log')
    completed=re.search(r'^прогон (\d+): done,', (output/'worker.log').read_text(), re.M)
    assert completed, 'Worker did not complete a new run (possibly skipped due to its lock)'
    cut=datetime.fromisoformat(a.as_of.replace(' ','T')+'+03:00')
    risks, objects = get('/api/risks'), get('/api/objects')
    mapping={r['section_id']:r for r in objects}
    probabilities={int(c['collector_id']):c['p'] for c in score['collectors']}
    assert risks, 'API returned no risks'
    for r in risks:
        assert r['horizon_h']==score['horizon_h']
        assert not r['is_stale'] and datetime.fromisoformat(r['as_of'])==cut
        section=mapping[r['section_id']]
        assert section['mapping_status']=='resolved'
        assert abs(r['probability']-probabilities[section['collector']]) < 1e-6
    assert len(risks)==sum(o['mapping_status']=='resolved' for o in objects)
    new_orders=get('/api/orders?limit=1')['total']-before_orders
    if a.expect_new_orders is not None:
        assert new_orders==a.expect_new_orders,(new_orders,a.expect_new_orders)
    elapsed=time.perf_counter()-started
    result=dict(score_s=round(score_s,3),worker_s=round(worker_s,3),
        score_worker_api_s=round(elapsed,3),sections=len(risks),collectors=len(probabilities),
        warnings=len(score['warnings']),new_orders=new_orders,horizon_h=score['horizon_h'],
        model_version=score['model_version'],completed_run_id=int(completed.group(1)),all_api_values_match_score=True,
        under_300s=elapsed<300,scope='Local real archive score; PostgreSQL all June readings; scheduling and ingest excluded')
    (output/'pipeline.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    assert result['under_300s'], 'runtime budget exceeded'

if __name__=='__main__': main()
