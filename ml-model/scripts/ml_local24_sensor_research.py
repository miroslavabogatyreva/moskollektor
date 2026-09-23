#!/usr/bin/env python3
"""Series3: fixed Local24 label/riskset, sensor history and archived weather ablations."""
from __future__ import annotations
import argparse, hashlib, json, os, sys, time
from pathlib import Path
os.environ.setdefault('OMP_NUM_THREADS','6')
os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
import lightgbm as lgb
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
sys.path.insert(0,str(ROOT/'scripts'))
import ml_local24_research as old
from ml.local24_weather import build_weather, FEATURES as WEATHER
SPECS=[
 {'name':'control_short','kind':'control'},
 {'name':'sensor_short','metric':'binary_logloss'},
 {'name':'sensor_only','metric':'average_precision','family':'sensor'},
 {'name':'sensor_tail','metric':'average_precision'},
 {'name':'sensor_recent_year','metric':'average_precision','years':1},
 {'name':'sensor_weather','metric':'average_precision','weather':True},
 {'name':'sensor_count','metric':'poisson','objective':'poisson'},
 {'name':'sensor_rank','metric':'ndcg','objective':'lambdarank'},
 {'name':'sensor_long','metric':'average_precision','long':True},
]

def names_for(base,sensors,spec):
    if spec.get('kind')=='control': return old.features_for(base,{'family':'short'})
    names=base if spec.get('long') else old.features_for(base,{'family':'short'})
    if spec.get('family')=='sensor': names=[]
    return list(names)+sensors+(WEATHER if spec.get('weather') else [])

def train(frame,names,start,spec,seed):
    if spec.get('kind')=='control':
        return old.fit(frame,names,start,{'leaves':15,'leaf':100,'family':'short'},seed)
    start=pd.Timestamp(start)
    cal_start=start-pd.Timedelta(days=32);es_start=start-pd.Timedelta(days=64)
    tr=frame[frame.as_of<es_start-pd.Timedelta(days=2)]
    if spec.get('years'): tr=tr[tr.as_of>=start-pd.Timedelta(days=365*spec['years'])]
    es=frame[(frame.as_of>=es_start)&(frame.as_of<cal_start-pd.Timedelta(days=2))]
    rng=np.random.default_rng(seed);q=min(1.,180000/max(1,int((tr.y==0).sum())))
    take=(tr.y.to_numpy()==1)|(rng.random(len(tr))<q);tr=tr.loc[take]
    weights=np.where(tr.y.to_numpy()==1,1.,1./q)
    obj=spec.get('objective','binary')
    target='n_events' if obj=='poisson' else 'y'
    params=dict(objective=obj,metric=spec['metric'],verbosity=-1,num_threads=6,
                seed=seed,deterministic=True,force_col_wise=True,num_leaves=15,
                min_data_in_leaf=100,learning_rate=.04,lambda_l2=5.,feature_fraction=.9)
    kw={};ekw={}
    if obj=='lambdarank':
        tr=tr.sort_values(['as_of','section_id']);es=es.sort_values(['as_of','section_id'])
        weights=None
        kw['group']=tr.groupby('as_of',sort=True).size().to_numpy()
        ekw['group']=es.groupby('as_of',sort=True).size().to_numpy()
        params.update(eval_at=[10],lambdarank_truncation_level=15)
    b=lgb.train(params,lgb.Dataset(tr[names],tr[target],weight=weights,**kw),
        num_boost_round=450,valid_sets=[lgb.Dataset(es[names],es[target],**ekw)],
        callbacks=[lgb.early_stopping(40,verbose=False)])
    return b,names,None,dict(train_rows=len(tr),sampling_probability=q,
        train_end=str(tr.as_of.max()),early_stopping_start=str(es_start),
        early_stopping_end=str(es.as_of.max()),calibration='not used; ranking research only',
        best_iteration=b.best_iteration,objective=obj)

def score(model,names,cal,rows):
    p=old.predict(model,names,cal,rows) if cal is not None else model.predict(rows[names],num_threads=6)
    if not np.isfinite(p).all(): raise ValueError('Nonfinite scores')
    return p

def load(root):
    data=root/'data/03_processed/local24_20260923'
    meta,frame,base,events=old.load(data)
    sensorpath=root/'data/03_processed/local24_sensor_20260923_v2/sensor_features.parquet'
    sensors=pd.read_parquet(sensorpath)
    feature_names=[n for n in sensors if n.startswith('sensor_')]
    sensors['as_of']=pd.to_datetime(sensors.as_of)
    for n in feature_names: sensors[n]=sensors[n].astype('float32')
    frame=frame.merge(sensors,on=['section_id','as_of'],validate='one_to_one',how='outer',indicator=True)
    if not frame._merge.eq('both').all(): raise ValueError('Sensor keys differ from frozen riskset')
    frame=frame.drop(columns='_merge')
    weather=build_weather(root/'data/01_raw/weather_20260915/weather_moscow_center_hourly.csv')
    frame=frame.merge(weather,on='as_of',how='left',validate='many_to_one')
    for n in WEATHER: frame[n]=frame[n].astype('float32')
    return data,frame.sort_values(['as_of','section_id']),base,feature_names,events

def run(root,out):
    out.mkdir(parents=True,exist_ok=False)
    old.write_json(out/'frozen_plan.json',dict(specs=SPECS,folds=old.FOLDS,
        budget=10,seed=42,seeds_verification=list(range(42,47)),
        label='unchanged local24 D5 onset >1h',known_replay=True,
        dataset_sha256=old.digest(root/'data/03_processed/local24_20260923/dataset.parquet'),
        sensor_sha256=old.digest(root/'data/03_processed/local24_sensor_20260923_v2/sensor_features.parquet'),
        code_sha256=old.digest(__file__),weather_lag_days=2,
        promotion='development gain >=0.005, wins >=2 folds, five seeds, paired block CI; no fresh holdout available'))
    data,frame,base,sensors,events=load(root)
    results=[]
    for spec in SPECS:
        reports=[];t=time.monotonic();names=names_for(base,sensors,spec)
        for i,(start,stop) in enumerate(old.FOLDS):
            rows=frame[(frame.as_of>=start)&(frame.as_of<stop)]
            b,n,cal,info=train(frame,names,start,spec,42);p=score(b,n,cal,rows)
            report=old.evaluate(rows,p,events,old.forecast_days(data,start,stop));report['fit']=info
            rows[old.KEYS].assign(score=p).to_parquet(out/f"{spec['name']}_fold{i}.parquet",index=False)
            reports.append(report)
            print(json.dumps(dict(name=spec['name'],fold=i,hits=report['budgets']['10']['hit_section_days'],precision=report['budgets']['10']['precision'],seconds=round(time.monotonic()-t,2))),flush=True)
        result=dict(name=spec['name'],spec=spec,features=names,folds=reports,
            mean_precision10=float(np.mean([r['budgets']['10']['precision'] for r in reports])),seconds=time.monotonic()-t)
        old.write_json(out/(spec['name']+'.json'),result);results.append(result)
    win=max(results,key=lambda r:r['mean_precision10'])
    selection=dict(winner=win['name'],spec=win['spec'],mean_precision10=win['mean_precision10'],
       control_precision10=results[0]['mean_precision10'],status='development_only',holdout=False)
    old.write_json(out/'selection.json',selection)
    print(json.dumps(selection),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();run(a.root,a.out)
