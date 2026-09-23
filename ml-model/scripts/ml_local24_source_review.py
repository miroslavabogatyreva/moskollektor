#!/usr/bin/env python3
"""Independent read-only exact ensemble and weekly uncertainty audit; no training."""
import argparse,hashlib,json,os
from pathlib import Path
os.environ['OPENBLAS_NUM_THREADS']='1'
import duckdb,numpy as np,pandas as pd

def digest(path):
 h=hashlib.sha256()
 with Path(path).open('rb')as f:
  for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
 return h.hexdigest()

def weekly(a,b):
 rng=np.random.default_rng(20260923);draw=np.zeros(20000);point=[];folds=[]
 for x,y in zip(a,b):
  assert x.as_of.equals(y.as_of)and x.alerts.equals(y.alerts)
  f=pd.DataFrame({'as_of':x.as_of,'a':x.hits,'b':y.hits,'alerts':x.alerts})
  f['week']=f.as_of-pd.to_timedelta(f.as_of.dt.dayofweek,unit='D')
  w=f.groupby('week')[['a','b','alerts']].sum().to_numpy();idx=rng.integers(0,len(w),(20000,len(w)));v=w[idx].sum(axis=1)
  draw+=(v[:,0]-v[:,1])/v[:,2]/3
  point.append(float((x.hits.sum()-y.hits.sum())/x.alerts.sum()))
  folds.append({'candidate_hits':int(x.hits.sum()),'comparator_hits':int(y.hits.sum()),'alerts':int(x.alerts.sum()),'week_clusters':len(w)})
 return {'mean_fold_precision_delta':float(np.mean(point)),'ci95':np.quantile(draw,[.025,.975]).tolist(),'fold_wins':sum(p>0 for p in point),'folds':folds}

def audit(root):
 root=Path(root);work=Path(__file__).resolve().parents[1];base=root/'data/03_processed/local24_20260923';cache={};hashes={}
 def sha(p):
  p=Path(p)
  if p not in cache:cache[p]=digest(p)
  return cache[p]
 c=duckdb.connect();c.execute("SET threads=3;SET memory_limit='6GB'")
 def view(n,p):c.execute(f"CREATE OR REPLACE VIEW {n} AS SELECT * FROM read_parquet('{str(p).replace(chr(39),chr(39)*2)}')")
 def daily(score):return c.execute(f'''SELECT as_of,sum(y) hits,count(*) alerts FROM
   (SELECT *,row_number()OVER(PARTITION BY as_of ORDER BY {score} DESC,section_id) rk FROM p)
   WHERE rk<=10 GROUP BY 1 ORDER BY 1''').df()
 def check_keys(start,stop):
  n,u,bad=c.execute('SELECT count(*),count(DISTINCT(section_id,as_of)),count(*)FILTER(WHERE y<>(n_events>0)::INT OR n_events<0) FROM p').fetchone();assert n==u and bad==0
  different=c.execute(f'''SELECT count(*) FROM p FULL JOIN (SELECT * FROM d WHERE as_of>='{start}' AND as_of<'{stop}') d USING(section_id,as_of)
   WHERE p.section_id IS NULL OR d.section_id IS NULL OR p.y IS DISTINCT FROM d.y OR p.n_events IS DISTINCT FROM d.n_events OR p.collector_id IS DISTINCT FROM d.collector_id''').fetchone()[0];assert different==0
  return n
 view('d',base/'dataset.parquet')
 result={'quality_gate':'NOT_ACCEPTED','training_performed':False,'holdout':False,'weekly_bootstrap':{'seed':20260923,'replicates':20000,'method':'paired Monday-week blocks; stratified quarters; partial weeks retained; mean three fold precision differences'},'sources':{}}
 for source in ['history','intraday']:
  folder=root/f'data/experiments/local24_{source}_ablation_20260923';plan=json.load(open(folder/'frozen_plan.json'));report=json.load(open(folder/'result.json'))
  paths=[folder/'frozen_plan.json',folder/'result.json'];feature_names=plan['feature_names'];assert report['features']==feature_names and len(feature_names)==len(set(feature_names))
  assert not set(feature_names)&{'y','n_events','section_id','collector_id','as_of'}
  assert sha(work/'scripts/ml_local24_source_ablation.py')==plan['code_sha256']
  for p,h in plan['dependencies_sha256'].items():assert sha(work/p)==h
  for p,h in plan['inputs_sha256'].items():assert sha(root/p)==h
  sourcepath=root/('data/03_processed/local24_intraday_20260923/intraday_features.parquet'if source=='intraday'else'data/03_processed/local24_history_20192020_20260923/dataset.parquet');assert sha(sourcepath)==plan['source_sha256']
  all_days=[];comparators=[];recency=[];folds=[];gains=[]
  for i,(start,stop)in enumerate(plan['folds']):
   ranks=[];fold={'seed_checks':[]};keys=None
   for seed in plan['seeds']:
    path=folder/f'{source}_fold{i}_seed{seed}.parquet';paths.append(path);view('p',path);n=check_keys(start,stop)
    df=c.execute('''SELECT *, (rank()OVER(PARTITION BY as_of ORDER BY raw_score)
      +(count(*)OVER(PARTITION BY as_of,raw_score)-1)/2.0)/count(*)OVER(PARTITION BY as_of) AS recomputed_rank
      FROM p ORDER BY as_of,section_id''').df()
    assert np.isfinite(df[['raw_score','rank_score']].to_numpy()).all()
    np.testing.assert_array_equal(df.rank_score.to_numpy(),df.recomputed_rank.to_numpy())
    k=df[['section_id','as_of']]
    if keys is None:keys=k
    else:pd.testing.assert_frame_equal(keys,k)
    ranks.append(df.rank_score.to_numpy());hits=int(daily('raw_score').hits.sum())
    stored=next(x for x in report['folds'][i]['seeds']if x['seed']==seed);assert stored['hits']==hits
    fit=stored['fit'];t=pd.Timestamp(start)
    assert pd.Timestamp(fit['train_end'])<t-pd.Timedelta(days=66)
    assert pd.Timestamp(fit['early_stopping_start'])==t-pd.Timedelta(days=64)
    if 'early_stopping_end'in fit:assert pd.Timestamp(fit['early_stopping_end'])<t-pd.Timedelta(days=34)
    if 'calibration_end'in fit:assert pd.Timestamp(fit['calibration_end'])<t-pd.Timedelta(days=2)
    gp=folder/f'{source}_fold{i}_seed{seed}_gain.json';paths.append(gp);gain=json.load(open(gp));assert set(gain)==set(feature_names)and all(np.isfinite(v)and v>=0 for v in gain.values());gains.append(gain)
    fold['seed_checks'].append({'seed':seed,'rows':n,'hits':hits,'rank_max_delta':0,'key_label_differences':0})
   path=folder/f'{source}_fold{i}.parquet';paths.append(path);view('p',path);n=check_keys(start,stop)
   df=c.execute('SELECT * FROM p ORDER BY as_of,section_id').df();pd.testing.assert_frame_equal(keys,df[['section_id','as_of']]);np.testing.assert_array_equal(np.mean(ranks,axis=0),df.score.to_numpy())
   day=daily('score');all_days.append(day);assert int(day.hits.sum())==report['folds'][i]['budgets']['10']['hit_section_days'];fold.update(ensemble_rows=n,ensemble_max_delta=0,hits=int(day.hits.sum()),alerts=int(day.alerts.sum()));folds.append(fold)
   name='sensor_v3'if source=='intraday'else'control_short';p=root/f'data/experiments/local24_sensor_stability_v3_20260923/{name}_fold{i}.parquet';paths.append(p);view('p',p);check_keys(start,stop);comparators.append(daily('score'))
   c.execute(f"CREATE OR REPLACE VIEW p AS SELECT * FROM d WHERE as_of>='{start}' AND as_of<'{stop}'");recency.append(daily('-days_since_confirmation'))
  used={n:{'gain_sum':sum(g[n]for g in gains),'models_with_positive_gain':sum(g[n]>0 for g in gains)}for n in feature_names if n.startswith('intra_')}
  total_gain=sum(sum(g.values())for g in gains);intra_gain=sum(x['gain_sum']for x in used.values())
  versus=weekly(all_days,comparators);versus_recency=weekly(all_days,recency)
  result['sources'][source]={'feature_count':len(feature_names),'hashes_match_frozen_plan':True,'folds':folds,'hits':sum(x['hits']for x in folds),'alerts':sum(x['alerts']for x in folds),'seed_total_hits':[sum(f['seed_checks'][j]['hits']for f in folds)for j in range(5)],'against_matched_v3':versus,'against_recency':versus_recency,'minimum_gain_gate_passed':versus['mean_fold_precision_delta']>=.005 and versus_recency['mean_fold_precision_delta']>=.005,'intraday_gain':used,'intraday_pooled_training_gain_share':intra_gain/total_gain if total_gain else None}
  for p in paths:hashes[str(p.relative_to(root))]=sha(p)
 result['sha256']=hashes;c.close();return result

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();r=audit(a.root);a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n');print(json.dumps({k:{x:v[x]for x in ['hits','seed_total_hits','against_matched_v3','against_recency','intraday_pooled_training_gain_share']}for k,v in r['sources'].items()},indent=2))
