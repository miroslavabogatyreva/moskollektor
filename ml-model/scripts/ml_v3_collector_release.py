#!/usr/bin/env python3
"""Rebuild and fit v3 on direct customer-tree collector IDs, preserving old artifacts.

Q2 2026 has been seen previously and is a development replay, not pristine holdout.
Threshold selection uses only four donor windows ending before 2026-04-01.
"""
import argparse
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from ml import config as C, daily, grid, features_pfx, labels, moments as M
from ml import failure_defs as F, collector_identity as I, v3_score as S
from ml.serving import v3_bag
import ml_v3_holdout as H
import ml_v3_export as X

DATA = Path('data/03_processed/v3_collector_20260922')
MODEL = Path('models/v3_collector_20260922')
END = pd.Timestamp('2026-06-30 23:59:59')


def build():
    if DATA.exists():
        raise ValueError(f'Refusing to overwrite existing {DATA}')
    DATA.mkdir(parents=True)
    identity = I.build_registry(DATA / 'channels.parquet')
    con = duckdb.connect()
    con.execute("SET threads=8; SET memory_limit='16GB'; SET preserve_insertion_order=false")
    con.execute(f"SET temp_directory='{DATA / 'tmp'}'")
    try:
        with I.registry_context(DATA / 'channels.parquet'):
            con.execute(f"CREATE TABLE chanday AS SELECT * FROM '{C.OUT / 'chanday.parquet'}'")
            con.execute(f"CREATE TABLE ep_0 AS SELECT * FROM '{F.OUT / 'D5/episodes_ext.parquet'}'")
            F.build_failures(con, F.DEFS['D5'], [F.EXT], END)
            labels.build_incidents(con, window_minutes=M.MERGE_MIN)
            print('direct-collector incidents built', flush=True)
            daily.build_pfxday(con)
            grid.build_grid_ch(con, END)
            grid.build_grid_pfx(con, END)
            print('channel and collector grids built', flush=True)
            features_pfx.build(con)
            # Runtime and reference share causal outage rule and exact input coverage.
            S.outage_days(con)
            con.execute('CREATE VIEW features AS SELECT * FROM features_pfx')
            M.build_moments(con, str(END.date()), str(END))
            M.seal_failures(con, str(END))
            M.seal_episodes(con, 'ep_0', C.CHAN, C.DATE_START, str(END))
            for name, table, order in [('features', 'features_pfx', 'd,pfx'),
                                        ('incidents', 'incidents', 't_start,pfx'),
                                        ('failures', 'failures_sealed', 't_start,ch'),
                                        ('episodes', 'episodes_sealed', 't_start,ch'),
                                        ('moments', 'moments', 't,pfx,kind'),
                                        ('calendar', 'calendar', 'd')]:
                con.execute(f"COPY (SELECT * FROM {table} ORDER BY {order}) TO '{DATA / (name+'.parquet')}' (FORMAT PARQUET)")
                print(name, con.execute(f'SELECT count(*) FROM {table}').fetchone()[0], flush=True)
    finally:
        con.close()
    identity.update(object_level='collector', internal_key='pfx contains collector ID, not tag prefix',
                    data_end=str(END), created_at=datetime.now(timezone.utc).isoformat())
    (DATA / 'identity.json').write_text(json.dumps(identity, indent=2) + '\n')
    return identity


def fit():
    if MODEL.exists():
        raise ValueError(f'Refusing to overwrite existing {MODEL}')
    prepare, train = S.load_code()
    S.use_data(prepare, train, DATA)
    train.SEEDS = H.SEEDS
    train.PARAMS['num_threads'] = 8
    rows = H._load_rows(prepare, 720)
    inc = prepare.load_incidents()
    donor_grids, donor_reports = [], []
    for i, block in enumerate(H.validation_blocks(prepare, rows, inc, 720), 1):
        assert block['moments'].t.max() < pd.Timestamp('2026-04-01')
        started = time.perf_counter()
        scores = H.score_block(train, block)
        scores.to_parquet(DATA / f'donor_{i}_scores.parquet', index=False)
        donor_grids.append(H.donor_grid(prepare, scores, block, 'tail'))
        donor_reports.append(dict(fold=i, train_max=str(block['train'].t.max()),
                                  score_min=str(scores.t.min()), score_max=str(scores.t.max()),
                                  elapsed_s=time.perf_counter()-started))
        print('donor', i, donor_reports[-1], flush=True)
    threshold = H.pick_threshold('margin', donor_grids, .7, .5)
    print('donor-only threshold', threshold, flush=True)
    block = H.make_block(prepare, rows, inc, H.TEST_FROM, H.score_end(H.TEST_END, 720), 720)
    boosters, feats, head, platt, n_rows = X.fit_bag(prepare, train, block['train'])
    MODEL.mkdir(parents=True)
    spec = []
    for seed, b in zip(H.SEEDS, boosters):
        name = f'booster_{seed}.txt'
        n = train.n_trees(b)
        b.save_model(str(MODEL / name), num_iteration=n)
        spec.append(dict(file=name, seed=seed, n_trees=n))
    shutil.copyfile(DATA / 'channels.parquet', MODEL / 'channels.parquet')
    meta = dict(model_version='lgbm-v3-collector-2026.09.22', model_format='v3-bag',
                trained_at=datetime.now(timezone.utc).isoformat(), feature_schema='feat.v3',
                feature_names=feats, object_level='collector', directions=['sensor_failure'],
                train_rows=n_rows, train_until=str(block['train'].t.max()),
                holdout_precision=None, holdout_recall=None, holdout_median_lead_hours=None,
                evaluation_status='Q2 2026 development replay; previously seen, not pristine holdout',
                alert_threshold=threshold, horizon_h=720,
                policy='one open warning per customer-tree collector; incident confirmation 1h; expiry720h',
                boosters=spec, rearm_head=head, platt=platt,
                channel_registry=dict(file='channels.parquet', sha256=I.sha256(MODEL / 'channels.parquet')),
                reference_dataset=str(DATA), identity=json.loads((DATA / 'identity.json').read_text()),
                train_code_sha256=H.LOOK_CODE,
                threshold_selection=dict(rule='margin', donors=donor_reports, no_Q2_tuning=True))
    meta['sha256'] = v3_bag.bag_sha256(MODEL, [b['file'] for b in spec])
    (MODEL / 'model_meta.json').write_text(json.dumps(meta, indent=2) + '\n')
    bag = v3_bag.Bag.load(MODEL, meta)
    # Independent frozen training path compared with serialized inference on train tail.
    error = X.check(MODEL, prepare, train, block['train'], inc)
    if error > 1e-9:
        raise ValueError(f'Export does not reproduce training: {error}')
    _, va = train.feature_engineering(block['train'].iloc[:0], block['moments'], 720)
    scores = block['moments'][['pfx','t','kind']].copy()
    scores['p'] = bag.predict_proba(va[feats].to_numpy(dtype=float))
    scores.to_parquet(DATA / 'development_scores.parquet', index=False)
    alerts = prepare.simulate(scores, block['incidents'], threshold, 720)
    pm = prepare.load_predictive_metrics()
    # One all-warning common-population comparison, with right-censoring disclosed.
    metrics = pm.evaluate_alerts(prepare._pairs(alerts.pfx, alerts.t),
                                prepare._pairs(block['incidents'].pfx, block['incidents'].t_start),
                                horizon_hours=0, max_lead_hours=720)
    lead24 = pm.evaluate_alerts(prepare._pairs(alerts.pfx, alerts.t),
                                prepare._pairs(block['incidents'].pfx, block['incidents'].t_start),
                                horizon_hours=24, max_lead_hours=720)
    counts = H.counts(prepare, scores, block, threshold, 'tail')
    report = dict(evaluation_status=meta['evaluation_status'], threshold=threshold,
                  horizon_h=720, export_max_abs_error=error, warnings=len(alerts),
                  all_warning_metrics=metrics, all_warning_lead24=lead24,
                  body_and_tail_counts=counts,
                  observed_decision_days=int(scores.t.dt.normalize().nunique()),
                  warnings_per_observed_day=len(alerts)/scores.t.dt.normalize().nunique(),
                  boundary_convention_comparison=H.judge(prepare,scores,block,threshold,'tail'),
                  censoring='All-warning windows near June30 are truncated; report is not production acceptance.')
    (DATA / 'validation.json').write_text(json.dumps(report, indent=2, default=str)+'\n')
    print(json.dumps(report, indent=2, default=str), flush=True)


if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('stage', choices=['build','fit','all']);a=p.parse_args()
    if a.stage in ('build','all'): build()
    if a.stage in ('fit','all'): fit()
