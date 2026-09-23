#!/usr/bin/env python3
"""Two predeclared source ablations; same development periods, no new holdout."""
import argparse,sys,time
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parent))
import ml_local24_sensor_research as R

def run(root,out,source):
    out.mkdir(parents=True,exist_ok=False)
    source_file=root/('data/03_processed/local24_intraday_20260923/intraday_features.parquet' if source=='intraday'
                     else 'data/03_processed/local24_history_20192020_20260923/dataset.parquet')
    R.old.write_json(out/'frozen_plan.json',dict(source=source,source_sha256=R.old.digest(source_file),
        code_sha256=R.old.digest(__file__),folds=R.old.FOLDS,seeds=list(range(42,47)),
        dependencies_sha256={str(p.relative_to(R.ROOT)):R.old.digest(p) for p in [R.ROOT/'scripts/ml_local24_sensor_research.py',R.ROOT/'scripts/ml_local24_research.py',R.ROOT/'src/ml/local24_eval.py',R.ROOT/'src/ml/local24_weather.py',R.ROOT/'docs/research/local24_audit_20260923/SOURCE_ABLATIONS.md']},
        inputs_sha256={p:R.old.digest(root/p) for p in ['data/03_processed/local24_20260923/dataset.parquet','data/03_processed/local24_20260923/metadata.json','data/03_processed/local24_20260923/events.parquet','data/03_processed/local24_20260923/forecast_days.parquet','data/03_processed/local24_sensor_20260923_v3/sensor_features.parquet','data/03_processed/local24_sensor_20260923_v2/sensor_features.parquet','data/01_raw/weather_20260915/weather_moscow_center_hourly.csv']},
        label='unchanged D5 journal onsets >1h',budget=10,known_replay=True,
        base='canonical_v3_short_counts',ensemble='mean daily within-seed percentile ranks',
        comparison='sensor_v3' if source=='intraday' else 'control_short_v3',
        hypothesis='Use intraday signal lost in daily last values' if source=='intraday' else 'Add only 2019/2020 training rows; evaluate same2025 riskset; no2021 bridge',
        gate='delta precision10>=0.005, >=2foldwins, weeklypairedCI excludes0; development only'))
    data,frame,base,sensors,events=R.load(root)
    canonical=pd.read_parquet(root/'data/03_processed/local24_sensor_20260923_v3/sensor_features.parquet')
    canonical['as_of']=pd.to_datetime(canonical.as_of)
    frame=frame.drop(columns=sensors).merge(canonical,on=['section_id','as_of'],validate='one_to_one',how='outer',indicator=True)
    if not frame._merge.eq('both').all():raise ValueError('Canonical keys differ from frozen riskset')
    frame=frame.drop(columns='_merge')
    for w in (7,28):frame[f'short_{w}d']=frame[f'sensor_valid_short_count_{w}d']
    sensors=[n for n in canonical if n.startswith('sensor_') and not n.startswith('sensor_valid_short_count_')]
    if source=='intraday':
        extra=pd.read_parquet(source_file);extra['as_of']=pd.to_datetime(extra.as_of)
        frame=frame.merge(extra,on=['section_id','as_of'],how='outer',validate='one_to_one',indicator=True)
        if not frame._merge.eq('both').all():raise ValueError('Intraday keys differ from frozen riskset')
        frame=frame.drop(columns='_merge')
        names=R.old.features_for(base,{'family':'short'})+sensors+[n for n in extra if n.startswith('intra_')]
        spec={'name':'intraday','metric':'binary_logloss'}
    else:
        extra=pd.read_parquet(source_file);extra['as_of']=pd.to_datetime(extra.as_of)
        if extra.duplicated(['section_id','as_of']).any():raise ValueError('Duplicate historical keys')
        if not np.isfinite(extra[base].to_numpy()).all():raise ValueError('Nonfinite historical base features')
        if not extra.y.eq(extra.n_events.gt(0).astype(int)).all() or (extra.n_events<0).any():raise ValueError('Historical labels inconsistent')
        if extra.as_of.max()+pd.Timedelta(hours=25)>pd.Timestamp('2020-12-31 23:59:59'):raise ValueError('Immature historical target at cutoff')
        if not extra.as_of.dt.year.isin([2019,2020]).all():raise ValueError('Unexpected history year')
        if not set(base+R.old.KEYS).issubset(extra.columns):raise ValueError('Incompatible historical schema')
        if frame.as_of.min()<=extra.as_of.max():raise ValueError('Historical/current periods overlap')
        frame=pd.concat([extra,frame],ignore_index=True)
        names=R.old.features_for(base,{'family':'short'})
        spec={'name':'history_20192020','kind':'control'}
    frame=frame.sort_values(['as_of','section_id']).reset_index(drop=True)
    for n in names:frame[n]=frame[n].astype('float32')
    plan=R.json.loads((out/'frozen_plan.json').read_text());plan['feature_names']=names;plan['rows']=len(frame)
    R.old.write_json(out/'frozen_plan.json',plan)
    folds=[]
    for i,(start,stop) in enumerate(R.old.FOLDS):
        rows=frame[(frame.as_of>=start)&(frame.as_of<stop)]
        scores=[];seedreports=[]
        for seed in range(42,47):
            t=time.monotonic();b,n,cal,info=R.train(frame,names,start,spec,seed)
            p=R.score(b,n,cal,rows)
            rank=pd.Series(p,index=rows.index).groupby(rows.as_of).rank(method='average',pct=True).to_numpy()
            scores.append(rank)
            rows[R.old.KEYS].assign(raw_score=p,rank_score=rank).to_parquet(out/f'{source}_fold{i}_seed{seed}.parquet',index=False)
            R.old.write_json(out/f'{source}_fold{i}_seed{seed}_gain.json',dict(zip(n,map(float,b.feature_importance(importance_type='gain')))))
            report=R.old.evaluate(rows,p,events,R.old.forecast_days(data,start,stop))
            seedreports.append(dict(seed=seed,hits=report['budgets']['10']['hit_section_days'],precision=report['budgets']['10']['precision'],fit=info))
            print(R.json.dumps(dict(source=source,fold=i,seed=seed,hits=seedreports[-1]['hits'],seconds=round(time.monotonic()-t,2))),flush=True)
        average=np.mean(scores,axis=0)
        report=R.old.evaluate(rows,average,events,R.old.forecast_days(data,start,stop));report['seeds']=seedreports;folds.append(report)
        rows[R.old.KEYS].assign(score=average).to_parquet(out/f'{source}_fold{i}.parquet',index=False)
    result=dict(name=source,features=names,folds=folds,
       mean_precision10=float(np.mean([r['budgets']['10']['precision'] for r in folds])),
       hits=sum(r['budgets']['10']['hit_section_days'] for r in folds),alerts=sum(r['budgets']['10']['alerts'] for r in folds))
    R.old.write_json(out/'result.json',result)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--source',choices=['intraday','history'],required=True)
    a=p.parse_args();run(a.root,a.out,a.source)
