#!/usr/bin/env python3
"""Predeclared five-seed development stability. Never a new holdout."""
import argparse,json,sys,time
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parent))
import ml_local24_sensor_research as R

def run(root,out,version,control):
    out.mkdir(parents=True,exist_ok=False)
    sensor_dir=root/f'data/03_processed/local24_sensor_20260923_{version}'
    R.old.write_json(out/'frozen_plan.json',dict(version=version,folds=R.old.FOLDS,seeds=list(range(42,47)),
        dataset_sha256=R.old.digest(root/'data/03_processed/local24_20260923/dataset.parquet'),
        sensor_sha256=R.old.digest(sensor_dir/'sensor_features.parquet'),
        code_sha256=R.old.digest(__file__),control=control,budget=10,
        score='mean within-day percentile rank across five seeds; ranking not probability',
        qualification='development stability only; selection reused dates',
        canonical=version=='v3',canonical_changes='positive observed duration, exact observed tuple dedup; base short7/28 replaced by canonical counts' if version=='v3' else None))
    data,frame,base,sensors,events=R.load(root)
    if version=='v3':
        f=pd.read_parquet(sensor_dir/'sensor_features.parquet')
        f['as_of']=pd.to_datetime(f.as_of)
        frame=frame.drop(columns=sensors).merge(f,on=['section_id','as_of'],validate='one_to_one',how='left')
        for w in (7,28): frame[f'short_{w}d']=frame[f'sensor_valid_short_count_{w}d']
        sensors=[n for n in f if n.startswith('sensor_') and not n.startswith('sensor_valid_short_count_')]
        for n in sensors:frame[n]=frame[n].astype('float32')
        frame=frame.sort_values(['as_of','section_id'])
    specs=([{'name':'control_short','kind':'control'}] if control else [])+[{'name':f'sensor_{version}','metric':'binary_logloss'}]
    results=[]
    for spec in specs:
        names=R.names_for(base,sensors,spec);folds=[]
        for i,(start,stop) in enumerate(R.old.FOLDS):
            rows=frame[(frame.as_of>=start)&(frame.as_of<stop)]
            scores=[];reports=[]
            for seed in range(42,47):
                t=time.monotonic();b,n,cal,info=R.train(frame,names,start,spec,seed)
                p=R.score(b,n,cal,rows)
                # Monotonic percentile transform puts different uncalibrated seeds
                # on a common daily rank scale without fitting on outcomes.
                rank=pd.Series(p,index=rows.index).groupby(rows.as_of).rank(method='average',pct=True).to_numpy()
                scores.append(rank)
                ev=R.old.evaluate(rows,p,events,R.old.forecast_days(data,start,stop))
                reports.append(dict(seed=seed,hits=ev['budgets']['10']['hit_section_days'],precision=ev['budgets']['10']['precision'],fit=info))
                print(json.dumps(dict(name=spec['name'],fold=i,seed=seed,hits=reports[-1]['hits'],seconds=round(time.monotonic()-t,2))),flush=True)
            avg=np.mean(scores,axis=0)
            report=R.old.evaluate(rows,avg,events,R.old.forecast_days(data,start,stop))
            report['seeds']=reports;folds.append(report)
            rows[R.old.KEYS].assign(score=avg).to_parquet(out/f"{spec['name']}_fold{i}.parquet",index=False)
        result=dict(name=spec['name'],spec=spec,features=names,folds=folds,
            mean_precision10=float(np.mean([r['budgets']['10']['precision'] for r in folds])),
            hits=sum(r['budgets']['10']['hit_section_days'] for r in folds),alerts=sum(r['budgets']['10']['alerts'] for r in folds))
        R.old.write_json(out/(spec['name']+'.json'),result);results.append(result)
    R.old.write_json(out/'results.json',results)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--version',choices=['v2','v3'],required=True);p.add_argument('--with-control',action='store_true')
    a=p.parse_args();run(a.root,a.out,a.version,a.with_control)
