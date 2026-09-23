#!/usr/bin/env python3
"""Rebuild causal section features and score an explicit archived daily cutoff."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
import time
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from ml.local24_data import Local24Data, FEATURE_NAMES, _midnight, sha256
from ml.serving.model_store import load_model


def score(model_dir,data,as_of):
    started=time.monotonic()
    model=load_model(model_dir)
    meta=json.loads((data/'metadata.json').read_text())
    t=pd.Timestamp(_midnight(as_of))
    if t<pd.Timestamp(model.meta['valid_from']):raise ValueError('as_of precedes model training boundary')
    if t>pd.Timestamp(meta['archive_cutoff']):raise ValueError('as_of exceeds observed archive cutoff')
    if sha256(data/'mapping.parquet')!=meta['mapping_sha256']:raise ValueError('Mapping hash mismatch')
    sources={k:Path(meta['sources'][k]) for k in ('daily','episodes')}
    sources={k:(v if v.is_absolute() else data/v) for k,v in sources.items()}
    for key in ('daily','episodes'):
        if sha256(sources[key])!=meta['source_sha256'][key]:raise ValueError(f'Source hash mismatch: {key}')
    # Truncate available information before prediction, including episode endings.
    builder=Local24Data(pd.read_parquet(data/'mapping.parquet'),sources['daily'],
                        sources['episodes'],cutoff=t)
    try:
        con=builder.riskset(t)
        names=model.feature_names
        rows=con.execute('SELECT section_id,collector_id,as_of,'+','.join(FEATURE_NAMES)+
            ' FROM riskset WHERE as_of=? AND exposed AND NOT ongoing_d5 AND past_covered ORDER BY section_id',[t.to_pydatetime()]).df()
        if rows.empty:raise ValueError('No causally eligible sections at requested cutoff')
        for name in FEATURE_NAMES:rows[name]=rows[name].astype('float32')
        if model.meta.get('engineering'):
            from ml.local24_engineering import transform
            rows=transform(rows,model.meta['engineering'])
        x=rows[names].to_numpy(dtype=np.float32)
        probabilities=model.predict_proba(x)
        _,contrib=model.contributions(x)
        order=np.lexsort((rows.section_id.to_numpy(),-probabilities))
        ranks=np.empty(len(rows),dtype=int);ranks[order]=np.arange(1,len(rows)+1)
        sections=[]
        for i,r in enumerate(rows.itertuples()):
            top=np.argsort(-np.abs(contrib[i,:len(names)]))[:5]
            sections.append(dict(section_id=int(r.section_id),collector_id=int(r.collector_id),
                p=float(probabilities[i]),rank=int(ranks[i]),
                features=[None if not np.isfinite(v) else float(v) for v in x[i]],
                factors=[dict(f=names[k],v=float(contrib[i,k])) for k in top]))
        return dict(schema_version='score.local24.v1',feature_schema=model.meta['feature_schema'],
            object_level='section',model_version=model.version,model_sha256=model.sha256,
            horizon_h=24,as_of=t.isoformat(),timezone='Europe/Moscow',archive=True,
            data_watermark=meta['archive_cutoff'],feature_names=names,sections=sections,
            recommended_budget=10,explanation_scope=model.explanation_scope,explains_probability=False,
            failure_values=meta['failure_values'],cadence=model.meta['cadence'],
            eligibility='Past-only coverage and exposure; currently open D5 excluded; future target coverage not used.',
            seconds=time.monotonic()-started)
    finally:builder.close()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--model',type=Path,required=True)
    p.add_argument('--data',type=Path,required=True);p.add_argument('--as-of',required=True)
    p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    result=score(a.model,a.data,a.as_of)
    a.out.parent.mkdir(parents=True,exist_ok=True)
    tmp=a.out.with_suffix(a.out.suffix+'.tmp')
    tmp.write_text(json.dumps(result,ensure_ascii=False,allow_nan=False)+'\n')
    os.replace(tmp,a.out)
    print(json.dumps(dict(sections=len(result['sections']),seconds=result['seconds'],out=str(a.out))))
