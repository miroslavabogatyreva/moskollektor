#!/usr/bin/env python3
import os
os.environ['OPENBLAS_NUM_THREADS']='1'
os.environ['OMP_NUM_THREADS']='3'
import sys,json,hashlib,argparse
from pathlib import Path
import duckdb,numpy as np,pandas as pd
W=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description="Read-only exact replay and paired weekly audit of Local24 series3")
parser.add_argument('--root',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
parser.add_argument('--stability-version',choices=['v2','v3'],action='append',default=[])
args=parser.parse_args()
R=args.root.resolve();S=R/'data/experiments/local24_series3_20260923';B=R/'data/experiments/local24_20260923';D=R/'data/03_processed/local24_20260923'
sys.path[:0]=[str(W/'scripts'),str(W/'src')]
import ml_local24_sensor_research as research
c=duckdb.connect();c.execute("SET threads=3;SET memory_limit='6GB'")
def view(name,path): c.execute(f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM read_parquet('{path}')")
def digest(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
 return h.hexdigest()
view('data',D/'dataset.parquet');base=json.load(open(D/'metadata.json'))['feature_names'];sensor=json.load(open(R/'data/03_processed/local24_sensor_20260923_v2/metadata.json'))['feature_names'];plan=json.load(open(S/'frozen_plan.json'))
out={'quality_gate':'NOT_ACCEPTED','resamples':20000,'seed':20260923,'method':'paired Monday calendar-week clusters stratified by development fold; partial boundary weeks retained; each draw resamples same number of weeks per fold; ratio hits/alerts then unweighted mean of three folds','holdout':False,'runs':[],'control_exact_reproduction':[]}
days={};rec=[];oldcontrols=[]
for i,(start,stop) in enumerate(plan['folds']):
 view('control',S/f'control_short_fold{i}.parquet');view('previous',B/f'short_history_fold{i}.parquet')
 diff=c.execute('select count(*),max(abs(a.score-b.score)) from control a full outer join previous b using(section_id,as_of) where a.section_id is null or b.section_id is null or a.score is distinct from b.score or a.y is distinct from b.y or a.n_events is distinct from b.n_events or a.collector_id is distinct from b.collector_id').fetchone()
 out['control_exact_reproduction'].append({'fold':i,'different_rows':diff[0],'max_score_delta':diff[1] or 0})
 rec.append(c.execute(f"""select as_of,sum(y) hits,count(*) alerts from (select *,row_number() over(partition by as_of order by days_since_confirmation asc,section_id asc) rk from data where as_of>='{start}' and as_of<'{stop}') where rk<=10 group by 1 order by 1""").df())
for spec in plan['specs']:
 j=json.load(open(S/(spec['name']+'.json')));expected=research.names_for(base,sensor,spec);assert expected==j['features'];assert not set(j['features'])&{'y','n_events','section_id','collector_id','as_of'}
 run={'name':spec['name'],'features':len(expected),'feature_names_match':True,'folds':[]};days[spec['name']]=[]
 for i,(start,stop) in enumerate(plan['folds']):
  p=S/f"{spec['name']}_fold{i}.parquet";view('pred',p)
  missing=c.execute(f"""select count(*) from pred p full outer join (select * from data where as_of>='{start}' and as_of<'{stop}') d using(section_id,as_of) where p.section_id is null or d.section_id is null or p.y is distinct from d.y or p.n_events is distinct from d.n_events or p.collector_id is distinct from d.collector_id""").fetchone()[0]
  n,uniq,lo,hi,nonfinite=c.execute('select count(*),count(distinct(section_id,as_of)),min(as_of),max(as_of),count(*) filter(where not isfinite(score)) from pred').fetchone();assert missing==nonfinite==0 and n==uniq
  fit=j['folds'][i]['fit'];t=pd.Timestamp(start);assert pd.Timestamp(fit['train_end'])<t-pd.Timedelta(days=66);assert pd.Timestamp(fit['early_stopping_start'])==t-pd.Timedelta(days=64)
  if 'early_stopping_end'in fit:assert pd.Timestamp(fit['early_stopping_end'])<t-pd.Timedelta(days=34)
  if 'calibration_end'in fit:assert pd.Timestamp(fit['calibration_end'])<t-pd.Timedelta(days=2)
  day=c.execute('select as_of,sum(y) hits,count(*) alerts from (select *,row_number() over(partition by as_of order by score desc,section_id asc) rk from pred) where rk<=10 group by 1 order by 1').df();days[spec['name']].append(day)
  hits=int(day.hits.sum());assert hits==j['folds'][i]['budgets']['10']['hit_section_days']
  run['folds'].append({'rows':n,'key_or_label_mismatches':missing,'first_as_of':str(lo),'last_as_of':str(hi),'hits':hits,'alerts':int(day.alerts.sum()),'fit':fit})
 run['hits']=sum(x['hits']for x in run['folds']);run['alerts']=sum(x['alerts']for x in run['folds']);run['mean_precision10']=j['mean_precision10'];out['runs'].append(run)

def compare(a,b):
 rng=np.random.default_rng(out['seed']);dist=np.zeros(out['resamples']);points=[];folds=[]
 for da,db in zip(a,b):
  assert da.as_of.equals(db.as_of) and da.alerts.equals(db.alerts)
  frame=pd.DataFrame({'as_of':da.as_of,'a':da.hits,'b':db.hits,'alerts':da.alerts});frame['week']=frame.as_of-pd.to_timedelta(frame.as_of.dt.dayofweek,unit='D');wk=frame.groupby('week')[['a','b','alerts']].sum().to_numpy();idx=rng.integers(0,len(wk),(out['resamples'],len(wk)));s=wk[idx].sum(axis=1);dist+=(s[:,0]-s[:,1])/s[:,2]/3;points.append(float((da.hits.sum()-db.hits.sum())/da.alerts.sum()));folds.append({'weeks':len(wk),'days':len(da),'hits_a':int(da.hits.sum()),'hits_b':int(db.hits.sum()),'delta':points[-1]})
 return {'mean_fold_precision_delta':float(np.mean(points)),'ci95':np.quantile(dist,[.025,.975]).tolist(),'resampled_fraction_delta_positive':float(np.mean(dist>0)),'folds':folds}
out['paired_week_bootstrap']={'sensor_short_vs_recency':compare(days['sensor_short'],rec),'sensor_short_vs_control':compare(days['sensor_short'],days['control_short'])}
out['gate']= {'minimum_gain':.005,'gain_vs_control':out['paired_week_bootstrap']['sensor_short_vs_control']['mean_fold_precision_delta'],'gain_vs_recency':out['paired_week_bootstrap']['sensor_short_vs_recency']['mean_fold_precision_delta'],'passed':False}
paths=[S/'frozen_plan.json',S/'selection.json',R/'data/03_processed/local24_sensor_20260923_v2/metadata.json',W/'scripts/ml_local24_sensor_research.py',W/'src/ml/local24_sensor_features.py',W/'src/ml/local24_weather.py']+[S/(x['name']+'.json')for x in plan['specs']]+list(S.glob('*_fold*.parquet'))
out['sha256']={str(p.relative_to(R)):digest(p)for p in paths};out['frozen_trainer_hash_matches']=digest(W/'scripts/ml_local24_sensor_research.py')==plan['code_sha256']
out['input_hashes_match_plan'] = dict(dataset=digest(D/'dataset.parquet')==plan['dataset_sha256'],sensor=digest(R/'data/03_processed/local24_sensor_20260923_v2/sensor_features.parquet')==plan['sensor_sha256'])
out['stability']={}
for version in args.stability_version:
 folder=R/f'data/experiments/local24_sensor_stability_{version}_20260923'
 frozen=json.load(open(folder/'frozen_plan.json'));assert frozen['control'] and frozen['seeds']==[42,43,44,45,46]
 assert digest(D/'dataset.parquet')==frozen['dataset_sha256']
 assert digest(R/f'data/03_processed/local24_sensor_20260923_{version}/sensor_features.parquet')==frozen['sensor_sha256']
 packs={};summary={}
 for name in ['control_short',f'sensor_{version}']:
  j=json.load(open(folder/(name+'.json')));packs[name]=[];folds=[]
  expected=research.names_for(base,sensor,{'kind':'control'} if name=='control_short' else {})
  assert j['features']==expected
  for i,(start,stop) in enumerate(plan['folds']):
   path=folder/f'{name}_fold{i}.parquet';view('pred',path)
   difference=c.execute(f"""select count(*) from pred p full outer join (select * from data where as_of>='{start}' and as_of<'{stop}') d using(section_id,as_of) where p.section_id is null or d.section_id is null or p.y is distinct from d.y or p.n_events is distinct from d.n_events or p.collector_id is distinct from d.collector_id""").fetchone()[0]
   assert difference==0
   n,uniq,bad=c.execute('select count(*),count(distinct(section_id,as_of)),count(*) filter(where score<0 or score>1 or not isfinite(score)) from pred').fetchone();assert n==uniq and bad==0
   day=c.execute('select as_of,sum(y) hits,count(*) alerts from (select *,row_number() over(partition by as_of order by score desc,section_id asc) rk from pred) where rk<=10 group by 1 order by 1').df();packs[name].append(day)
   assert int(day.hits.sum())==j['folds'][i]['budgets']['10']['hit_section_days']
   seeds=j['folds'][i]['seeds'];assert [x['seed']for x in seeds]==[42,43,44,45,46]
   for seed in seeds:
    fit=seed['fit'];t=pd.Timestamp(start)
    assert pd.Timestamp(fit['train_end'])<t-pd.Timedelta(days=66)
    assert pd.Timestamp(fit['early_stopping_start'])==t-pd.Timedelta(days=64)
    if 'early_stopping_end'in fit:assert pd.Timestamp(fit['early_stopping_end'])<t-pd.Timedelta(days=34)
    if 'calibration_end'in fit:assert pd.Timestamp(fit['calibration_end'])<t-pd.Timedelta(days=2)
   folds.append({'hits':int(day.hits.sum()),'alerts':int(day.alerts.sum()),'seed_hits':[x['hits']for x in seeds],'key_or_label_differences':difference})
   out['sha256'][str(path.relative_to(R))]=digest(path)
  summary[name]={'folds':folds,'hits':sum(x['hits']for x in folds),'seed_total_hits':[sum(x['seed_hits'][i]for x in folds)for i in range(5)]}
  out['sha256'][str((folder/(name+'.json')).relative_to(R))]=digest(folder/(name+'.json'))
 out['stability'][version]={'results':summary,'sensor_vs_control':compare(packs[f'sensor_{version}'],packs['control_short']),'sensor_vs_recency':compare(packs[f'sensor_{version}'],rec),'rank_scores_not_probabilities':True,'same_known_development_period':True}
 out['sha256'][str((folder/'frozen_plan.json').relative_to(R))]=digest(folder/'frozen_plan.json')
 for source in [W/'scripts/ml_local24_sensor_verify.py',W/('src/ml/local24_sensor_features_v3.py' if version=='v3' else 'src/ml/local24_sensor_features.py'),R/f'data/03_processed/local24_sensor_20260923_{version}/metadata.json']:
  out['sha256'][str(source.relative_to(R))]=digest(source)
 out['stability'][version]['input_hashes_match_plan']=True
 out['stability'][version]['minimum_gain_gate_passed']=all(out['stability'][version][k]['mean_fold_precision_delta']>=.005 for k in ['sensor_vs_control','sensor_vs_recency'])
P=args.output;P.parent.mkdir(parents=True,exist_ok=True);P.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n');print(json.dumps({k:out[k]for k in ['control_exact_reproduction','paired_week_bootstrap','gate','frozen_trainer_hash_matches']},indent=2))
