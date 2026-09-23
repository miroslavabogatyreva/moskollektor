"""Causal per-channel history, aggregated to the frozen Local24 picket riskset.

No labels are read to construct features. Closed durations/recoveries enter only
on the day they become known. Numeric changes are standardized within a channel;
physical values are never averaged across instruments or measurement units.
"""
from __future__ import annotations

import argparse
from datetime import timedelta
import json
from pathlib import Path
import tempfile

import duckdb
import pandas as pd

from .local24_data import ORIGIN, CUTOFF, _literal, _midnight, sha256

TYPES = ('smoke', 'gas', 'temperature', 'motion', 'contact', 'other')
NUMERIC_TYPES = ('gas', 'temperature', 'ups')


class SensorFeatures:
    def __init__(self, mapping, daily, episodes, cutoff=CUTOFF):
        self.cutoff = pd.Timestamp(cutoff).to_pydatetime()
        self.con = duckdb.connect()
        self.con.execute("SET threads=4;SET memory_limit='8GB';SET preserve_insertion_order=false")
        self._spill=tempfile.TemporaryDirectory(prefix='local24-sensor-spill-')
        self.con.execute(f'SET temp_directory={_literal(self._spill.name)}')
        self.con.register('mapping_input', mapping)
        for name, source in [('daily_input', daily), ('episodes_input', episodes)]:
            if isinstance(source,pd.DataFrame):
                self.con.register(name,source)
            else:
                self.con.execute(f'CREATE VIEW {name} AS SELECT * FROM read_parquet({_literal(source)})')
        self._prepare()

    def close(self):
        self.con.close()
        self._spill.cleanup()

    def _prepare(self):
        c=self.con
        c.execute('''CREATE TABLE mapping AS WITH resolved AS (
            SELECT section_id FROM mapping_input WHERE section_id IS NOT NULL
            GROUP BY section_id HAVING count(DISTINCT collector_id)=1 AND count(collector_id)=count(*))
            SELECT m.*,CASE stype WHEN 'Датчик дыма' THEN 'smoke' WHEN 'Газовый датчик' THEN 'gas'
                WHEN 'Датчик температуры' THEN 'temperature' WHEN 'Датчик движения' THEN 'motion'
                ELSE CASE WHEN starts_with(stype,'КД ') OR stype='9-секционный люк' THEN 'contact' ELSE 'other' END END AS kind,
                CASE stype WHEN 'Газовый датчик' THEN 'gas' WHEN 'Датчик температуры' THEN 'temperature'
                    WHEN 'ИБП' THEN 'ups' ELSE NULL END AS numeric_kind
            FROM mapping_input m JOIN resolved USING(section_id)''')
        c.execute(f'''CREATE TABLE cd AS SELECT d.* FROM daily_input d JOIN mapping m USING(ch)
            WHERE d.d >= DATE '{ORIGIN.date()}' AND d.d < DATE '{self.cutoff.date()}' + INTERVAL 1 DAY''')
        c.execute('CREATE TABLE first_seen AS SELECT ch,min(d) AS d FROM cd WHERE n>0 GROUP BY ch')
        # A repeated onset key cannot distinguish within-second transitions from
        # duplicated rows. Quarantine by onset multiplicity (not future endpoints).
        # This is prefix-invariant for the supplied episode archive, not proof
        # that all episode representations were available to an online ingester.
        c.execute('''CREATE TABLE episode_duplicates AS SELECT ch,t_start,count(*) AS n
            FROM episodes_input GROUP BY ch,t_start HAVING count(*)>1''')
        c.execute(f'''CREATE TABLE ep AS SELECT e.ch,e.t_start,
            CASE WHEN e.t_end<=TIMESTAMP '{self.cutoff}' THEN e.t_end END AS t_end,
            CASE WHEN e.t_end<=TIMESTAMP '{self.cutoff}' THEN e.close_val END AS close_val
            FROM episodes_input e JOIN mapping m USING(ch)
            LEFT JOIN episode_duplicates q ON q.ch=e.ch AND q.t_start=e.t_start
            WHERE q.ch IS NULL AND e.t_start>=DATE '2022-01-01' AND e.t_start<=TIMESTAMP '{self.cutoff}' ''')
        c.execute(f'''CREATE TABLE confirmation AS SELECT ch,
            (t_start+INTERVAL 1 HOUR+INTERVAL 1 SECOND)::DATE AS d,count(*) AS confirmations
            FROM ep WHERE coalesce(t_end,TIMESTAMP '{self.cutoff}')>t_start+INTERVAL 1 HOUR
            GROUP BY 1,2''')
        c.execute('''CREATE TABLE closed AS SELECT ch,t_end::DATE AS d,count(*) AS closed_count,
            count(*) FILTER(WHERE t_end<=t_start+INTERVAL 1 HOUR) AS short_count,
            count(*) FILTER(WHERE close_val='Норма') AS normal_count,
            sum(date_diff('second',t_start,t_end)/3600.0) AS duration_sum,
            max(date_diff('second',t_start,t_end)/3600.0) AS duration_max
            FROM ep WHERE t_end IS NOT NULL GROUP BY 1,2''')
        # Daily values are sparse. The previous 28-day scale excludes the current
        # observation; gaps remain gaps, and constant baselines get a separate flag.
        c.execute('''CREATE TABLE numeric_day AS WITH history AS (
            SELECT ch,d,val_num_last,
                lag(val_num_last) OVER(PARTITION BY ch ORDER BY d) AS previous_value,
                stddev_samp(val_num_last) OVER(PARTITION BY ch ORDER BY d
                    RANGE BETWEEN INTERVAL 28 DAY PRECEDING AND INTERVAL 1 DAY PRECEDING) AS previous_sd,
                count(*) OVER(PARTITION BY ch ORDER BY d
                    RANGE BETWEEN INTERVAL 28 DAY PRECEDING AND INTERVAL 1 DAY PRECEDING) AS previous_n
            FROM cd WHERE val_num_last IS NOT NULL AND isfinite(val_num_last))
            SELECT ch,d,1 AS numeric_observation,
                CASE WHEN previous_sd>0 THEN least(10.0,abs(val_num_last-previous_value)/previous_sd) ELSE 0 END AS standardized_change,
                (previous_n>=2 AND previous_sd=0 AND val_num_last<>previous_value)::INT AS changed_constant
            FROM history''')

    def features(self, start, end):
        """Return all past-observed mapped sections for inclusive midnight interval."""
        start,end=_midnight(start),_midnight(end)
        if end<start or end>self.cutoff+timedelta(seconds=1):
            raise ValueError('invalid interval or features beyond source cutoff')
        # 28-day windows are complete across monthly output boundaries.
        warm=max(ORIGIN,start-timedelta(days=28))
        c=self.con
        c.execute('DROP TABLE IF EXISTS chbase')
        c.execute(f'''CREATE TABLE chbase AS SELECT m.ch,m.section_id,m.kind,m.numeric_kind,g.d,
            coalesce(cd.n,0) AS n,coalesce(cd.n_bad,0) AS bad,
            coalesce(cd.n_alarm,0) AS alarm,coalesce(cd.n_undef,0) AS undef,
            coalesce(f.confirmations,0) AS confirmations,
            coalesce(cl.closed_count,0) AS closed_count,coalesce(cl.short_count,0) AS short_count,
            coalesce(cl.normal_count,0) AS normal_count,coalesce(cl.duration_sum,0) AS duration_sum,
            coalesce(cl.duration_max,0) AS duration_max,
            CASE WHEN cl.closed_count>0 THEN g.d END AS last_close_day,
            coalesce(num.numeric_observation,0) AS numeric_observation,
            coalesce(num.standardized_change,0) AS standardized_change,
            coalesce(num.changed_constant,0) AS changed_constant
            FROM mapping m JOIN first_seen firsts USING(ch)
            CROSS JOIN (SELECT unnest(generate_series(DATE '{warm.date()}',
                DATE '{end.date()}'-INTERVAL 1 DAY,INTERVAL 1 DAY))::DATE AS d) g
            LEFT JOIN cd ON cd.ch=m.ch AND cd.d=g.d
            LEFT JOIN confirmation f ON f.ch=m.ch AND f.d=g.d
            LEFT JOIN closed cl ON cl.ch=m.ch AND cl.d=g.d
            LEFT JOIN numeric_day num ON num.ch=m.ch AND num.d=g.d
            WHERE firsts.d<=g.d''')
        windows=','.join(f'w{w} AS (PARTITION BY ch ORDER BY d ROWS BETWEEN {w-1} PRECEDING AND CURRENT ROW)' for w in (7,28))
        rolling=[]
        for w in (7,28):
            for col in ('bad','alarm','undef','confirmations','closed_count','short_count','normal_count',
                        'duration_sum','numeric_observation','standardized_change','changed_constant'):
                rolling.append(f'sum({col}) OVER w{w} AS {col}_{w}d')
            rolling += [f'max(duration_max) OVER w{w} AS duration_max_{w}d',
                        f'max(standardized_change) OVER w{w} AS numeric_change_max_{w}d']
        rolling.append("coalesce(date_diff('day',max(last_close_day) OVER w28,d),29) AS days_since_close")
        c.execute('DROP TABLE IF EXISTS chrolling')
        c.execute(f'''CREATE TABLE chrolling AS SELECT ch,section_id,kind,numeric_kind,d,
            {','.join(rolling)} FROM chbase WINDOW {windows}''')
        c.execute('DROP TABLE chbase')
        aggregations=[]
        def add(name,expr):
            aggregations.append(f'cast({expr} AS FLOAT) AS sensor_{name}')
        for w in (7,28):
            for signal in ('bad','alarm','undef'):
                value=f'{signal}_{w}d'
                add(f'{signal}_channels_{w}d',f'count(*) FILTER(WHERE {value}>0)')
                add(f'{signal}_max_channel_{w}d',f'max({value})')
                add(f'{signal}_max_share_{w}d',f'coalesce(max({value})/nullif(sum({value}),0),0)')
                add(f'{signal}_hhi_{w}d',f'coalesce(sum(({value}::DOUBLE)^2)/nullif(sum({value})^2,0),0)')
            add(f'confirmed_channels_{w}d',f'count(*) FILTER(WHERE confirmations_{w}d>0)')
            add(f'repeated_confirmation_channels_{w}d',f'count(*) FILTER(WHERE confirmations_{w}d>=2)')
            add(f'closed_duration_mean_h_{w}d',f'coalesce(sum(duration_sum_{w}d)/nullif(sum(closed_count_{w}d),0),0)')
            add(f'closed_duration_max_h_{w}d',f'max(duration_max_{w}d)')
            add(f'returns_normal_{w}d',f'sum(normal_count_{w}d)')
            add(f'repeated_short_channels_{w}d',f'count(*) FILTER(WHERE short_count_{w}d>=3)')
        for kind in TYPES:
            add(f'{kind}_bad_channels_7d',f"count(*) FILTER(WHERE kind='{kind}' AND bad_7d>0)")
            add(f'{kind}_confirmed_channels_28d',f"count(*) FILTER(WHERE kind='{kind}' AND confirmations_28d>0)")
        for kind in NUMERIC_TYPES:
            filt=f"numeric_kind='{kind}'"
            add(f'{kind}_numeric_channels_28d',f'count(*) FILTER(WHERE {filt} AND numeric_observation_28d>0)')
            add(f'{kind}_numeric_change_mean_28d',f'coalesce(sum(standardized_change_28d) FILTER(WHERE {filt})/nullif(sum(numeric_observation_28d) FILTER(WHERE {filt}),0),0)')
            add(f'{kind}_numeric_change_max_28d',f'coalesce(max(numeric_change_max_28d) FILTER(WHERE {filt}),0)')
            add(f'{kind}_numeric_changed_constant_28d',f'coalesce(sum(changed_constant_28d) FILTER(WHERE {filt}),0)')
        add('days_since_close_capped29','least(29,min(days_since_close)+1)')
        # Absence of a past observation does not invent a channel. Caller joins the
        # frozen riskset (which already requires exposure) to obtain its exact rows.
        query=f'''SELECT section_id,d+INTERVAL 1 DAY AS as_of,{','.join(aggregations)}
            FROM chrolling WHERE d>=DATE '{start.date()}'-INTERVAL 1 DAY
            GROUP BY section_id,d ORDER BY as_of,section_id'''
        result=c.execute(query).df()
        c.execute('DROP TABLE chrolling')
        return result

    def build_features(self, as_of):
        return self.features(as_of,as_of)


def build(root, output=None):
    root=Path(root).resolve()
    data=root/'data/03_processed/local24_20260923'
    output=Path(output or root/'data/03_processed/local24_sensor_20260923_v2').resolve()
    if output.exists():
        raise ValueError(f'Refusing to overwrite {output}')
    source_meta=json.loads((data/'metadata.json').read_text())
    mapping=pd.read_parquet(data/'mapping.parquet')
    builder=SensorFeatures(mapping,source_meta['sources']['daily'],source_meta['sources']['episodes'])
    output.mkdir(parents=True)
    counts=0
    try:
        c=builder.con
        c.execute(f'CREATE VIEW riskkeys AS SELECT section_id,as_of FROM read_parquet({_literal(data/"dataset.parquet")})')
        with tempfile.TemporaryDirectory(prefix='parts-',dir=output) as tmp:
            for month in pd.date_range(ORIGIN,CUTOFF,freq='MS'):
                start=month.to_pydatetime();end=min((month+pd.offsets.MonthEnd()).to_pydatetime(),CUTOFF.replace(hour=0,minute=0,second=0))
                frame=builder.features(start,end)
                c.register('new_features',frame)
                part=Path(tmp)/f'{month:%Y%m}.parquet'
                c.execute(f'''COPY (SELECT f.* FROM new_features f JOIN riskkeys r USING(section_id,as_of)
                    ORDER BY as_of,section_id) TO {_literal(part)} (FORMAT PARQUET)''')
                n=c.execute('SELECT count(*) FROM read_parquet(?)',[str(part)]).fetchone()[0]
                counts+=n
                print(f'{month:%Y-%m}: {n} rows',flush=True)
                c.unregister('new_features')
            c.execute(f"COPY (SELECT * FROM read_parquet({_literal(str(Path(tmp)/'*.parquet'))}) ORDER BY as_of,section_id) TO {_literal(output/'sensor_features.parquet')} (FORMAT PARQUET)")
        total=c.execute('SELECT count(*) FROM riskkeys').fetchone()[0]
        if counts!=total:
            raise ValueError(f'Frozen riskset coverage mismatch {counts} != {total}')
        names=[x for x in frame.columns if x.startswith('sensor_')]
        meta={'schema_version':'local24.sensor.v2','feature_names':names,'rows':counts,
              'keys':['section_id','as_of'],'file':'sensor_features.parquet','origin':ORIGIN.isoformat(),
              'archive_cutoff':CUTOFF.isoformat(),'timezone':'Europe/Moscow naive wall time','labels_used':False,
              'quarantined_long_episode_rows':c.execute(f"SELECT count(*) FROM episodes_input e JOIN episode_duplicates q USING(ch,t_start) WHERE coalesce(e.t_end,TIMESTAMP '{CUTOFF}')>e.t_start+INTERVAL 1 HOUR").fetchone()[0],
              'quarantine_repeated_episode_keys':c.execute('SELECT count(*) FROM episode_duplicates').fetchone()[0],
              'quarantine_source_rows':c.execute('SELECT coalesce(sum(n),0) FROM episode_duplicates').fetchone()[0],
              'quarantine_reason':'All repeated(ch,t_start) groups excluded from new episode features, including exact duplicates; This quarantine is prefix-invariant for the available episode archive; online event-ingestion chronology is not established.',
              'sources':{'riskset':str(data/'dataset.parquet'),'mapping':str(data/'mapping.parquet'),
                         'daily':source_meta['sources']['daily'],'episodes':source_meta['sources']['episodes']},
              'source_sha256':{'riskset':sha256(data/'dataset.parquet'),'mapping':sha256(data/'mapping.parquet'),
                              'daily':sha256(source_meta['sources']['daily']),'episodes':sha256(source_meta['sources']['episodes'])},
              'code_sha256':sha256(__file__),'features_sha256':sha256(output/'sensor_features.parquet'),
              'numeric_semantics':'Within-channel daily-last-value change divided by previous28day standard deviation, clipped at10. Separate constant-baseline change indicator; aggregate by gas/temperature/UPS only. Not a calibrated physical fault.',
              'causality':'All inputs strictly earlier than daily as_of. Confirmation at onset+3601s; duration/return status only after observed close. Exposure starts at first observed channel day. No2021 inputs.',
              'limits':['Daily last values discard intraday excursions.','Current registry is assumed historically stable.','Missing numeric baseline yields0; numeric coverage features distinguish absence.'],
              'invocation':'PYTHONPATH=src python -m ml.local24_sensor_features --root <main-root>'}
        (output/'metadata.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'rows':counts,'features':len(names)}),flush=True)
        return meta
    finally:
        builder.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    build(args.root,args.output)
