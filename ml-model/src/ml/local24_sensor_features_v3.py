"""Separate V3 episode canonicalization; frozen sensor V2 is untouched.

Censor future endpoints first, remove observed zero/negative-duration intervals,
then deduplicate exact(ch,start,end,close_value) tuples. Ongoing unknown intervals
are retained. Conflicting remaining onset identities fail closed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile

import pandas as pd

from .local24_data import ORIGIN,CUTOFF,_literal,sha256
from .local24_sensor_features import SensorFeatures


class SensorFeaturesV3(SensorFeatures):
    def _prepare(self):
        super()._prepare()
        c=self.con
        c.execute(f'''CREATE OR REPLACE TABLE ep AS WITH observed AS (
            SELECT e.ch,e.t_start,
                CASE WHEN e.t_end<=TIMESTAMP '{self.cutoff}' THEN e.t_end END AS t_end,
                CASE WHEN e.t_end<=TIMESTAMP '{self.cutoff}' THEN e.close_val END AS close_val
            FROM episodes_input e JOIN mapping m USING(ch)
            WHERE e.t_start>=DATE '2022-01-01' AND e.t_start<=TIMESTAMP '{self.cutoff}')
            SELECT DISTINCT ch,t_start,t_end,close_val FROM observed
            WHERE t_end IS NULL OR t_end>t_start''')
        conflicts=c.execute('''SELECT count(*) FROM (
            SELECT ch,t_start FROM ep GROUP BY 1,2 HAVING count(*)>1)''').fetchone()[0]
        if conflicts:
            raise ValueError(f'{conflicts} ambiguous positive/ongoing episode onset identities after censoring')
        c.execute(f'''CREATE OR REPLACE TABLE confirmation AS SELECT ch,
            (t_start+INTERVAL 1 HOUR+INTERVAL 1 SECOND)::DATE AS d,count(*) AS confirmations
            FROM ep WHERE coalesce(t_end,TIMESTAMP '{self.cutoff}')>t_start+INTERVAL 1 HOUR
            GROUP BY 1,2''')
        c.execute('''CREATE OR REPLACE TABLE closed AS SELECT ch,t_end::DATE AS d,count(*) AS closed_count,
            count(*) FILTER(WHERE t_end<=t_start+INTERVAL 1 HOUR) AS short_count,
            count(*) FILTER(WHERE close_val='Норма') AS normal_count,
            sum(date_diff('second',t_start,t_end)/3600.0) AS duration_sum,
            max(date_diff('second',t_start,t_end)/3600.0) AS duration_max
            FROM ep WHERE t_end IS NOT NULL GROUP BY 1,2''')


        c.execute("""CREATE OR REPLACE TABLE valid_short_daily AS
            SELECT m.section_id,cl.d,sum(cl.short_count) AS n FROM closed cl
            JOIN mapping m USING(ch) GROUP BY 1,2""")

    def features(self,start,end):
        result=super().features(start,end)
        c=self.con;c.register('v3_feature_keys',result[['section_id','as_of']])
        counts=c.execute("""SELECT k.section_id,k.as_of,
            coalesce(sum(s.n) FILTER(WHERE s.d>=k.as_of::DATE-7),0)::FLOAT AS sensor_valid_short_count_7d,
            coalesce(sum(s.n),0)::FLOAT AS sensor_valid_short_count_28d
            FROM v3_feature_keys k LEFT JOIN valid_short_daily s ON s.section_id=k.section_id
                AND s.d>=k.as_of::DATE-28 AND s.d<k.as_of::DATE
            GROUP BY 1,2 ORDER BY 2,1""").df()
        c.unregister('v3_feature_keys')
        return result.merge(counts,on=['section_id','as_of'],validate='one_to_one')


def build(root,output=None):
    root=Path(root).resolve();data=root/'data/03_processed/local24_20260923'
    output=Path(output or root/'data/03_processed/local24_sensor_20260923_v3').resolve()
    if output.exists():raise ValueError(f'Refusing to overwrite {output}')
    old=root/'data/03_processed/local24_sensor_20260923_v2'
    meta=json.loads((old/'metadata.json').read_text())
    builder=SensorFeaturesV3(pd.read_parquet(data/'mapping.parquet'),meta['sources']['daily'],meta['sources']['episodes'])
    output.mkdir(parents=True)
    try:
        c=builder.con
        c.execute(f'CREATE VIEW riskkeys AS SELECT section_id,as_of FROM read_parquet({_literal(data/"dataset.parquet")})')
        with tempfile.TemporaryDirectory(prefix='parts-',dir=output) as tmp:
            for month in pd.date_range(ORIGIN,CUTOFF,freq='MS'):
                end=min((month+pd.offsets.MonthEnd()).to_pydatetime(),CUTOFF.replace(hour=0,minute=0,second=0))
                frame=builder.features(month.to_pydatetime(),end)
                c.register('features',frame)
                c.execute(f'''COPY (SELECT f.* FROM features f JOIN riskkeys USING(section_id,as_of)
                    ORDER BY as_of,section_id) TO {_literal(Path(tmp)/f'{month:%Y%m}.parquet')} (FORMAT PARQUET)''')
                c.unregister('features');print(f'{month:%Y-%m}',flush=True)
            c.execute(f"COPY (SELECT * FROM read_parquet({_literal(str(Path(tmp)/'*.parquet'))}) ORDER BY as_of,section_id) TO {_literal(output/'sensor_features.parquet')} (FORMAT PARQUET)")
        c.execute(f'CREATE VIEW v2 AS SELECT * FROM read_parquet({_literal(old/"sensor_features.parquet")})')
        c.execute(f'CREATE VIEW v3 AS SELECT * FROM read_parquet({_literal(output/"sensor_features.parquet")})')
        # Restrict this hypothesis to episode-derived columns. Every journal and
        # numeric value must remain identical at every frozen riskset key.
        episode_names=[n for n in meta['feature_names'] if any(x in n for x in
            ('confirmed','confirmation','closed_duration','returns_normal','repeated_short','since_close'))]
        stable=[n for n in meta['feature_names'] if n not in episode_names]
        checks=' OR '.join(f'a.{n} IS DISTINCT FROM b.{n}' for n in stable)
        mismatch=c.execute(f'''SELECT count(*) FROM v2 a FULL JOIN v3 b USING(section_id,as_of)
            WHERE a.section_id IS NULL OR b.section_id IS NULL OR {checks}''').fetchone()[0]
        if mismatch:raise ValueError(f'Unexpected non-episode feature/key differences: {mismatch}')
        changed=c.execute('SELECT '+','.join(f'count(*) FILTER(WHERE a.{n} IS DISTINCT FROM b.{n}) AS {n}' for n in episode_names)+
                          ' FROM v2 a JOIN v3 b USING(section_id,as_of)').df().iloc[0].to_dict()
        restored=c.execute(f'''SELECT count(*) FROM ep e JOIN episode_duplicates q USING(ch,t_start)
            WHERE coalesce(e.t_end,TIMESTAMP '{CUTOFF}')>e.t_start+INTERVAL 1 HOUR''').fetchone()[0]
        meta.update(schema_version='local24.sensor.v3',file='sensor_features.parquet',
            feature_names=meta['feature_names']+['sensor_valid_short_count_7d','sensor_valid_short_count_28d'],
            code_sha256=sha256(__file__),base_code_sha256=sha256(Path(__file__).with_name('local24_sensor_features.py')),
            features_sha256=sha256(output/'sensor_features.parquet'),
            canonicalization='Censor future endpoints; drop known nonpositive durations; exacttuplededup; retain unknown ongoing; reject remaining sameonset conflicts.',
            quarantine_repeated_episode_keys=0,quarantine_source_rows=0,quarantined_long_episode_rows=0,
            quarantine_reason=None,restored_mapped_long_episode_rows=restored,
            unchanged_non_episode_features=stable,changed_episode_feature_rows={n:int(v) for n,v in changed.items()},
            rows=c.execute('SELECT count(*) FROM v3').fetchone()[0],
            invocation='PYTHONPATH=src python -m ml.local24_sensor_features_v3 --root <main-root>')
        meta['limits']=[x for x in meta['limits'] if not x.startswith('Some143')]+[
            'Prefix invariance against supplied episode archive does not establish original online ingestion timestamps.',
            'Closure can be unknown-status transition, not recovery.']
        (output/'metadata.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'rows':meta['rows'],'restored_mapped_long_episode_rows':restored,'stable_features':len(stable),'episode_features':len(episode_names)}),flush=True)
        return meta
    finally:builder.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path)
    a=p.parse_args();build(a.root,a.output)
