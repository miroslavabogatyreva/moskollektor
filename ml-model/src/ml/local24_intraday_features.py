"""Past-only intraday numeric journal dynamics; these are not physical readings.

All scales are fitted separately per channel, from the preceding 28 calendar
days. Customer-identified service codes are excluded only without an alarm.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile

import duckdb
import pandas as pd

from .local24_data import _literal, sha256

KINDS = ('gas', 'temperature', 'ups')
METRICS = ('range_z_max', 'std_z_max', 'iqr_z_max', 'plateau_share_mean',
           'changed_constant_share', 'numeric_n_log1p')
FEATURE_NAMES = [f'intra_{kind}_{metric}' for kind in KINDS for metric in METRICS]
CODES = '-3276,-127,-100,255'


def connection():
    con = duckdb.connect()
    con.execute("SET threads=4;SET memory_limit='8GB';SET preserve_insertion_order=false")
    return con


def install_mapping(con, mapping):
    con.register('mapping_input', mapping)
    con.execute('''CREATE TABLE mapping AS WITH resolved AS (
        SELECT section_id FROM mapping_input WHERE section_id IS NOT NULL
        GROUP BY section_id HAVING count(DISTINCT collector_id)=1 AND count(collector_id)=count(*))
        SELECT m.ch,m.section_id,CASE stype WHEN 'Газовый датчик' THEN 'gas'
            WHEN 'Датчик температуры' THEN 'temperature' WHEN 'ИБП' THEN 'ups' END AS kind
        FROM mapping_input m JOIN resolved USING(section_id)
        WHERE stype IN ('Газовый датчик','Датчик температуры','ИБП')''')
    assert con.execute('SELECT count(*)=count(DISTINCT ch) FROM mapping').fetchone()[0]


def numeric_daily(con, sources, before, after='2022-04-01'):
    """Read raw timestamps strictly before `before`; exact dedup includes alarm.

    Plateau ordering is timestamp,event id with value/alarm deterministic tie
    breaks. Conflicting timestamp/event keys are counted, not silently removed.
    """
    paths = '[' + ','.join(_literal(p) for p in sources) + ']'
    con.execute(f'''CREATE OR REPLACE TEMP TABLE numeric_input AS
        SELECT j.ch,j.ts,j.ev,j.val,j.alarm,try_cast(j.val AS DOUBLE) AS v
        FROM read_parquet({paths}) j SEMI JOIN mapping m USING(ch)
        WHERE ts>=TIMESTAMP {_literal(after)} AND ts<TIMESTAMP {_literal(before)}
          AND isfinite(try_cast(val AS DOUBLE))''')
    stats = dict(zip(('numeric_rows_before_dedup','removed_non_alarm_code_rows','retained_alarm_code_rows'),
        con.execute(f'''SELECT count(*),count(*) FILTER(WHERE v IN ({CODES}) AND NOT coalesce(alarm,false)),
        count(*) FILTER(WHERE v IN ({CODES}) AND coalesce(alarm,false)) FROM numeric_input''').fetchone()))
    con.execute(f'''CREATE OR REPLACE TEMP TABLE numeric_clean AS
        SELECT DISTINCT ch,ts,ev,val,alarm,v FROM numeric_input
        WHERE NOT (v IN ({CODES}) AND NOT coalesce(alarm,false))''')
    stats['retained_distinct_numeric_rows'] = con.execute('SELECT count(*) FROM numeric_clean').fetchone()[0]
    stats['conflicting_timestamp_event_keys'] = con.execute('''SELECT count(*) FROM (
        SELECT ch,ts,ev FROM numeric_clean GROUP BY ALL HAVING count(*)>1)''').fetchone()[0]
    con.execute('DROP TABLE numeric_input')
    con.execute('''CREATE OR REPLACE TEMP TABLE numeric_daily AS WITH ordered AS (
        SELECT *,ts::DATE AS d,lag(v) OVER(PARTITION BY ch,ts::DATE
            ORDER BY ts,ev,val,alarm) AS previous_value FROM numeric_clean)
        SELECT ch,d,count(*) AS n,min(v) AS v_min,max(v) AS v_max,avg(v) AS v_mean,
            coalesce(var_samp(v),0) AS v_var,
            quantile_cont(v,0.75)-quantile_cont(v,0.25) AS v_iqr,
            count(previous_value) AS adjacent_n,
            count(*) FILTER(WHERE previous_value=v) AS plateau_n,
            count(*) FILTER(WHERE v IN (''' + CODES + ''')) AS alarm_code_n
        FROM ordered GROUP BY ch,d''')
    con.execute('DROP TABLE numeric_clean')
    return stats


def install_features(con, daily):
    """Generate prior-day features with numerically stable pooled channel SD."""
    if isinstance(daily,pd.DataFrame):
        con.register('daily',daily)
    else:
        con.execute(f'CREATE VIEW daily AS SELECT * FROM read_parquet({_literal(daily)})')
    if con.execute('SELECT count(*)<>count(DISTINCT(ch,d)) FROM daily').fetchone()[0]:
        raise ValueError('overlapping channel-day source partitions')
    # Only small channel-day summaries are joined, never the raw archive twice.
    # Sum within-day SSD plus between-day SSD around the past-only pooled mean.
    con.execute('''CREATE TABLE baseline_mean AS SELECT a.ch,a.d,
        sum(b.n) AS prior_n,sum(b.n*b.v_mean)/sum(b.n) AS prior_mean
        FROM daily a LEFT JOIN daily b ON a.ch=b.ch AND b.d<a.d AND b.d>=a.d-INTERVAL 28 DAY
        GROUP BY a.ch,a.d''')
    con.execute('''CREATE TABLE baseline AS SELECT a.ch,a.d,a.prior_n,a.prior_mean,
        sqrt(greatest(0,sum((b.n-1)*b.v_var+b.n*pow(b.v_mean-a.prior_mean,2))/(a.prior_n-1))) AS prior_sd
        FROM baseline_mean a LEFT JOIN daily b ON a.ch=b.ch AND b.d<a.d AND b.d>=a.d-INTERVAL 28 DAY
        GROUP BY a.ch,a.d,a.prior_n,a.prior_mean''')
    con.execute('''CREATE TABLE channel_features AS SELECT d.ch,d.d,
        CASE WHEN prior_n>=2 AND prior_sd>0 THEN least(10,(v_max-v_min)/prior_sd) ELSE 0 END AS range_z,
        CASE WHEN prior_n>=2 AND prior_sd>0 THEN least(10,sqrt(v_var)/prior_sd) ELSE 0 END AS std_z,
        CASE WHEN prior_n>=2 AND prior_sd>0 THEN least(10,v_iqr/prior_sd) ELSE 0 END AS iqr_z,
        coalesce(plateau_n/nullif(adjacent_n,0),0) AS plateau_share,
        (coalesce(prior_n>=2 AND prior_sd=0 AND (v_min<>prior_mean OR v_max<>prior_mean),false))::INT AS changed_constant,n
        FROM daily d JOIN baseline b USING(ch,d)''')
    expr=[]
    for kind in KINDS:
        for metric,aggregate in [('range_z_max','max(range_z)'),('std_z_max','max(std_z)'),
            ('iqr_z_max','max(iqr_z)'),('plateau_share_mean','avg(plateau_share)'),
            ('changed_constant_share','avg(changed_constant)'),('numeric_n_log1p','ln(1+sum(n))')]:
            # FILTER applies to the aggregate inside ln for the count feature.
            value = (f'ln(1+sum(n) FILTER(WHERE kind={_literal(kind)}))' if metric=='numeric_n_log1p'
                     else f'{aggregate} FILTER(WHERE kind={_literal(kind)})')
            expr.append(f'coalesce({value},0)::FLOAT AS intra_{kind}_{metric}')
    con.execute('''CREATE TABLE section_features AS SELECT section_id,(d+INTERVAL 1 DAY)::TIMESTAMP AS as_of,
        '''+','.join(expr)+''' FROM channel_features JOIN mapping USING(ch) GROUP BY section_id,d''')


def features_for_keys(con, keys):
    con.register('requested_keys',keys)
    cols=','.join(f'coalesce(f.{x},0)::FLOAT AS {x}' for x in FEATURE_NAMES)
    return con.execute(f'''SELECT k.section_id,k.as_of,{cols} FROM requested_keys k
        LEFT JOIN section_features f USING(section_id,as_of) ORDER BY section_id,as_of''').df()


def build(root,output):
    root,output=Path(root),Path(output)
    if output.exists(): raise FileExistsError(output)
    output.mkdir(parents=True)
    base=root/'data/03_processed/local24_20260923'
    mapping=pd.read_parquet(base/'mapping.parquet')
    raw=root/'data/02_interim/mk/parquet'
    sourcefiles=[raw/f'j{year}.parquet' for year in range(2022,2027)]
    con=connection();stats=[]
    try:
        with tempfile.TemporaryDirectory(prefix='local24-intraday-') as tmp:
            con.execute(f'SET temp_directory={_literal(tmp)}')
            install_mapping(con,mapping)
            for path in sourcefiles:
                year=int(path.stem[1:])
                counts=numeric_daily(con,[path],'2026-07-01')
                counts['year']=year;stats.append(counts)
                con.execute(f'COPY numeric_daily TO {_literal(output/f"numeric_daily_{year}.parquet")} (FORMAT PARQUET)')
                print(json.dumps(counts),flush=True)
            con.execute(f'COPY (SELECT * FROM read_parquet({_literal(output/"numeric_daily_*.parquet")})) '
                f'TO {_literal(output/"numeric_daily.parquet")} (FORMAT PARQUET)')
            install_features(con,output/'numeric_daily.parquet')
            cols=','.join(f'coalesce(f.{x},0)::FLOAT AS {x}' for x in FEATURE_NAMES)
            con.execute(f'''COPY (SELECT k.section_id,k.as_of,{cols}
                FROM read_parquet({_literal(base/'dataset.parquet')}) k
                LEFT JOIN section_features f USING(section_id,as_of) ORDER BY as_of,section_id)
                TO {_literal(output/'intraday_features.parquet')} (FORMAT PARQUET)''')
            counts=con.execute(f'''SELECT count(*),count(DISTINCT(section_id,as_of))
                FROM read_parquet({_literal(output/'intraday_features.parquet')})''').fetchone()
            expected=con.execute(f'SELECT count(*) FROM read_parquet({_literal(base/"dataset.parquet")})').fetchone()[0]
            assert counts==(expected,expected)
            metadata={'version':'local24_intraday_20260923','feature_names':FEATURE_NAMES,'rows':expected,
                'schema':'section_id,as_of plus 18 float32 features','raw_daily_stats':stats,
                'hypothesis':'Past-day within-channel numeric dynamics add predictive signal beyond canonical sensor history; frozen validation only.',
                'time_rule':'ts < as_of; feature day=as_of-1d; channel baseline [feature day-28d,feature day); no 2021.',
                'service_codes':{'values':[-3276,-127,-100,255],'filter':'remove only when alarm is false or null; retain alarm-coded in all numeric statistics; journal signal, not physical quantities'},
                'plateau_order':'ts,ev,val,alarm; conflicting ts/ev counted; no guaranteed physical sequence for ties',
                'missing_rule':'zero when no numeric observations; log count records availability; zero SD has separate change flag',
                'source_sha256':{str(p.relative_to(root)):sha256(p) for p in sourcefiles+[base/'mapping.parquet',base/'dataset.parquet']},
                'output_sha256':sha256(output/'intraday_features.parquet')}
            (output/'metadata.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n')
            print(json.dumps({'rows':expected,'features':len(FEATURE_NAMES),'output_sha256':metadata['output_sha256']}),flush=True)
    finally:con.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    build(args.root,args.output)
