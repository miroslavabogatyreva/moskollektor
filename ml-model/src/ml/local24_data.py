"""Causal daily 24h D5 data at product picket-section granularity.

Only this new dataset is written. Registry reads are read-only; old research is
unchanged. Times are local Europe/Moscow wall times, matching source Parquet.
Run: PYTHONPATH=src python -m ml.local24_data --root /path/to/main --dsn ...
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import tempfile

import duckdb
import pandas as pd

ORIGIN = datetime(2022, 4, 1)
CUTOFF = datetime(2026, 6, 30, 23, 59, 59)
COUNTS = ('n_rows', 'n_alarm', 'n_bad', 'n_undef', 'n_numeric', 'n_obes', 'n_batt', 'n_reporting')
FEATURE_NAMES = ([f'{c}_{w}d' for c in COUNTS for w in (1, 7, 28)]
                 + [f'confirmed_{w}d' for w in (7, 28, 90, 365)]
                 + ['short_7d', 'short_28d', 'observed_days_7d', 'observed_days_28d',
                    'known_channels', 'known_smoke', 'known_gas', 'known_temperature',
                    'days_since_reading', 'days_since_confirmation', 'weekday', 'day_of_year'])


def _literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def _midnight(value):
    t = pd.Timestamp(value)
    if t.tzinfo is not None:
        t = t.tz_convert('Europe/Moscow').tz_localize(None)
    if t != t.normalize():
        raise ValueError('local24 supports daily 00:00 Europe/Moscow only')
    if t < ORIGIN:
        raise ValueError('as_of precedes frozen 2022-04-01 origin')
    return t.to_pydatetime()


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


async def read_mapping(dsn):
    """Actual product sections, not the 78 customer-tree nodes called sections by ML."""
    import asyncpg
    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction(readonly=True):
            rows = await conn.fetch('''SELECT c.channel_id AS ch,c.section_id,c.sensor_kind AS stype,
                parent.object_id AS collector_id
                FROM smvu.channel c
                LEFT JOIN smvu.object_tree node ON node.object_id=c.object_id
                LEFT JOIN smvu.object_tree parent ON parent.object_id=node.parent_id
                    AND parent.level=2 ORDER BY c.channel_id''')
            return pd.DataFrame([dict(r) for r in rows]).astype({'ch':'Int64','section_id':'Int64','collector_id':'Int64'})
    finally:
        await conn.close()


class Local24Data:
    """One implementation for dataset construction and single-date serving features."""
    def __init__(self, mapping, chanday, episodes, cutoff=CUTOFF):
        self.cutoff = pd.Timestamp(cutoff).to_pydatetime()
        self.tempdir = tempfile.TemporaryDirectory(prefix='local24-duckdb-')
        self.con = duckdb.connect()
        self.con.execute("SET threads=6; SET memory_limit='8GB'; SET preserve_insertion_order=false")
        self.con.execute(f"SET temp_directory={_literal(self.tempdir.name)}")
        self.con.register('mapping_input', mapping)
        for name, source in [('daily_input', chanday), ('episodes_input', episodes)]:
            if isinstance(source, pd.DataFrame):
                self.con.register(name, source)
            else:
                self.con.execute(f'CREATE VIEW {name} AS SELECT * FROM read_parquet({_literal(source)})')
        self._prepare()

    def close(self):
        self.con.close()
        self.tempdir.cleanup()

    def _prepare(self):
        c = self.con
        c.execute('''CREATE TABLE section_status AS SELECT section_id,
            count(DISTINCT collector_id)=1 AND count(collector_id)=count(*) AS resolved
            FROM mapping_input WHERE section_id IS NOT NULL GROUP BY section_id''')
        c.execute('''CREATE TABLE mapping AS SELECT m.* FROM mapping_input m
            JOIN section_status s USING(section_id) WHERE s.resolved''')
        c.execute('''CREATE TABLE sections AS SELECT section_id,min(collector_id) AS collector_id
            FROM mapping GROUP BY section_id''')
        c.execute(f'''CREATE TABLE cd AS SELECT d.* FROM daily_input d JOIN mapping m USING(ch)
            WHERE d.d >= DATE '{ORIGIN.date()}' AND d.d < DATE '{self.cutoff.date()}' + INTERVAL 1 DAY''')
        c.execute(f'''CREATE TABLE ep AS SELECT e.ch,m.section_id,m.collector_id,e.t_start,
            CASE WHEN e.t_end <= TIMESTAMP '{self.cutoff}' THEN e.t_end END AS t_end
            FROM episodes_input e JOIN mapping m USING(ch)
            WHERE e.t_start >= DATE '2022-01-01' AND e.t_start <= TIMESTAMP '{self.cutoff}' ''')
        # Event identity is channel+onset; confirmation needs strictly >3600 seconds.
        c.execute(f'''CREATE TABLE events AS WITH selected AS (
            SELECT ch AS channel_id,section_id,collector_id,t_start,t_end,
                   t_start + INTERVAL 1 HOUR + INTERVAL 1 SECOND AS confirmed_at,
                   concat(ch,':',strftime(t_start,'%Y-%m-%dT%H:%M:%S')) AS event_id
            FROM ep WHERE t_start >= DATE '{ORIGIN.date()}'
              AND coalesce(t_end,TIMESTAMP '{self.cutoff}') > t_start + INTERVAL 1 HOUR)
            SELECT *, min(t_start) OVER(PARTITION BY section_id)<t_start AS is_repeat,
                min(t_start) OVER(PARTITION BY channel_id)<t_start AS channel_is_repeat
            FROM selected''')
        c.execute('''CREATE TABLE daily AS SELECT m.section_id,d.d,sum(n) AS n_rows,
            sum(n_alarm) AS n_alarm,sum(n_bad) AS n_bad,sum(n_undef) AS n_undef,
            sum(n_numeric) AS n_numeric,sum(n_obes) AS n_obes,sum(n_batt) AS n_batt,
            count(*) AS n_reporting FROM cd d JOIN mapping m USING(ch) GROUP BY 1,2''')
        c.execute('''CREATE TABLE activations AS WITH firsts AS (
            SELECT ch,min(d) AS d FROM cd WHERE n>0 GROUP BY ch)
            SELECT m.section_id,f.d,count(*) AS known_channels,
                count(*) FILTER(WHERE m.stype='Датчик дыма') AS known_smoke,
                count(*) FILTER(WHERE m.stype='Газовый датчик') AS known_gas,
                count(*) FILTER(WHERE m.stype='Датчик температуры') AS known_temperature
            FROM firsts f JOIN mapping m USING(ch) GROUP BY 1,2''')
        c.execute('''CREATE TABLE confirmed AS SELECT section_id,confirmed_at::DATE AS d,
            count(*) AS n_confirmed,max(confirmed_at) AS last_confirmation
            FROM events GROUP BY 1,2''')
        c.execute('''CREATE TABLE shorts AS SELECT section_id,t_end::DATE AS d,count(*) AS n_short
            FROM ep WHERE t_end IS NOT NULL AND t_end<=t_start+INTERVAL 1 HOUR GROUP BY 1,2''')
        # Coverage is an archive-level proxy, not an assertion of healthy silent sensors.
        # Baseline uses earlier days only, never annual/full-archive statistics.
        c.execute(f'''CREATE TABLE coverage AS WITH days AS (
            SELECT unnest(generate_series(DATE '{ORIGIN.date()}',DATE '{self.cutoff.date()}',INTERVAL 1 DAY))::DATE AS d),
            totals AS (SELECT d,sum(n) AS n FROM daily_input WHERE d>=DATE '{ORIGIN.date()}' GROUP BY d),
            history AS (SELECT days.d,coalesce(n,0) AS n,
                median(n) OVER(ORDER BY days.d ROWS BETWEEN 28 PRECEDING AND 1 PRECEDING) AS prior_median,
                count(n) OVER(ORDER BY days.d ROWS BETWEEN 28 PRECEDING AND 1 PRECEDING) AS prior_days
                FROM days LEFT JOIN totals USING(d))
            SELECT *, n>0 AND prior_days>=7 AND n>=0.1*prior_median AS covered FROM history''')

    def _features(self, end):
        """Materialize features for daily origins up to end (never target data)."""
        end = _midnight(end)
        if end > self.cutoff + timedelta(seconds=1):
            raise ValueError('requested features exceed available source cutoff')
        c = self.con
        c.execute('DROP TABLE IF EXISTS base')
        cols = ','.join(f'coalesce(v.{col},0) AS {col}' for col in COUNTS)
        acts = ','.join(f'coalesce(a.{col},0) AS {col}' for col in
                        ('known_channels','known_smoke','known_gas','known_temperature'))
        c.execute(f'''CREATE TABLE base AS SELECT s.section_id,s.collector_id,g.d,
                g.d + INTERVAL 1 DAY AS as_of,{cols},{acts},
                coalesce(f.n_confirmed,0) AS n_confirmed,f.last_confirmation,
                coalesce(sh.n_short,0) AS n_short
            FROM sections s CROSS JOIN (SELECT unnest(generate_series(DATE '{ORIGIN.date()}',
                    DATE '{end.date()}'-INTERVAL 1 DAY,INTERVAL 1 DAY))::DATE AS d) g
            LEFT JOIN daily v USING(section_id,d) LEFT JOIN activations a USING(section_id,d)
            LEFT JOIN confirmed f USING(section_id,d) LEFT JOIN shorts sh USING(section_id,d)''')
        expressions = []
        for col in COUNTS:
            for w in (1,7,28):
                expressions.append(f'sum({col}) OVER w{w} AS {col}_{w}d')
        for w in (7,28,90,365):
            expressions.append(f'sum(n_confirmed) OVER w{w} AS confirmed_{w}d')
        for w in (7,28):
            expressions += [f'sum(n_short) OVER w{w} AS short_{w}d',
                            f'sum((n_rows>0)::INT) OVER w{w} AS observed_days_{w}d']
        for col in ('known_channels','known_smoke','known_gas','known_temperature'):
            expressions.append(f'sum({col}) OVER allpast AS {col}')
        expressions += ["least(366,date_diff('day',max(CASE WHEN n_rows>0 THEN d END) OVER allpast,as_of)) AS days_since_reading",
                        "coalesce(least(366,date_diff('second',max(last_confirmation) OVER allpast,as_of)/86400.0),366) AS days_since_confirmation",
                        'dayofweek(as_of) AS weekday','dayofyear(as_of) AS day_of_year']
        windows = ','.join(f'w{w} AS (PARTITION BY section_id ORDER BY d ROWS BETWEEN {w-1} PRECEDING AND CURRENT ROW)' for w in (1,7,28,90,365))
        c.execute('DROP TABLE IF EXISTS features')
        c.execute(f'''CREATE TABLE features AS SELECT section_id,collector_id,as_of,{','.join(expressions)}
            FROM base WINDOW {windows},allpast AS (PARTITION BY section_id ORDER BY d ROWS UNBOUNDED PRECEDING)''')
        c.execute('DROP TABLE base')
        return end

    def build_features(self, as_of):
        """Features only. Exposure/ongoing flags are separately returned by riskset()."""
        as_of = self._features(as_of)
        return self.con.execute('SELECT section_id,collector_id,as_of,'+','.join(FEATURE_NAMES)+
                                ' FROM features WHERE as_of=? ORDER BY section_id',[as_of]).df()

    def riskset(self, end):
        end = self._features(end)
        c = self.con
        c.execute('DROP TABLE IF EXISTS ongoing')
        c.execute(f'''CREATE TABLE ongoing AS SELECT DISTINCT section_id,as_of FROM ep,
            LATERAL (SELECT unnest(generate_series(
                greatest(DATE '{ORIGIN.date()}',date_trunc('day',t_start)+
                    CASE WHEN t_start=date_trunc('day',t_start) THEN INTERVAL 0 DAY ELSE INTERVAL 1 DAY END),
                least(TIMESTAMP '{end}',CASE WHEN t_end IS NULL THEN TIMESTAMP '{end}'
                    WHEN t_end=date_trunc('day',t_end) THEN t_end-INTERVAL 1 DAY ELSE date_trunc('day',t_end) END),
                INTERVAL 1 DAY)) AS as_of) x''')
        c.execute('DROP TABLE IF EXISTS labeled')
        # Onsets exactly at t are ongoing, not a future outcome. ceil-day assigns a
        # midnight onset to the preceding prediction window (t,t+24h].
        c.execute('''CREATE TABLE labeled AS SELECT section_id,
            date_trunc('day',t_start)-CASE WHEN t_start=date_trunc('day',t_start)
                THEN INTERVAL 1 DAY ELSE INTERVAL 0 DAY END AS as_of,count(*) AS n_events
            FROM events GROUP BY 1,2''')
        c.execute('DROP TABLE IF EXISTS riskset')
        c.execute(f'''CREATE TABLE riskset AS SELECT f.*,coalesce(l.n_events,0)::INT AS n_events,
            (coalesce(l.n_events,0)>0)::INT AS y,
            coalesce(f.known_channels>0 AND f.days_since_reading<=365,false) AS exposed,
            o.section_id IS NOT NULL AS ongoing_d5,
            coalesce(p.covered,false) AS past_covered,
            coalesce(t.covered,false) AND coalesce(next.covered,false)
                AND f.as_of+INTERVAL 25 HOUR <= TIMESTAMP '{self.cutoff}' AS target_covered
            FROM features f LEFT JOIN ongoing o USING(section_id,as_of)
            LEFT JOIN labeled l USING(section_id,as_of)
            LEFT JOIN coverage p ON p.d=f.as_of::DATE-1
            LEFT JOIN coverage t ON t.d=f.as_of::DATE
            LEFT JOIN coverage next ON next.d=f.as_of::DATE+1''')
        return c


def build(root, output, dsn):
    root, output = Path(root).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError(f'refusing to overwrite dataset: {output}')
    sources = {'daily':root/'data/03_processed/ml_20260915/chanday.parquet',
               'episodes':root/'data/03_processed/faildef_20260915/D5/episodes_ext.parquet',
               'channel_dictionary':root/'data/01_raw/dataset_update_20260916/справочник_каналов_датчиков.csv',
               'object_dictionary':root/'data/01_raw/dataset_20260915/справочник_объектов_диспетчер.csv'}
    mapping = asyncio.run(read_mapping(dsn))
    builder = Local24Data(mapping,sources['daily'],sources['episodes'])
    try:
        output.mkdir(parents=True)
        mapping.to_parquet(output/'mapping.parquet',index=False)
        c = builder.riskset(CUTOFF.replace(hour=0,minute=0,second=0))
        eligibility='exposed AND NOT ongoing_d5 AND past_covered AND target_covered'
        c.execute(f"COPY (SELECT section_id,collector_id,as_of,y,n_events,{','.join(FEATURE_NAMES)} FROM riskset WHERE {eligibility} ORDER BY as_of,section_id) TO {_literal(output/'dataset.parquet')} (FORMAT PARQUET)")
        c.execute(f"COPY (SELECT * FROM events ORDER BY t_start,section_id,channel_id) TO {_literal(output/'events.parquet')} (FORMAT PARQUET)")
        c.execute(f"COPY (SELECT section_id,collector_id,as_of,exposed,ongoing_d5,past_covered,target_covered FROM riskset ORDER BY as_of,section_id) TO {_literal(output/'coverage.parquet')} (FORMAT PARQUET)")
        c.execute(f"COPY (SELECT DISTINCT as_of FROM riskset WHERE past_covered AND target_covered ORDER BY as_of) TO {_literal(output/'forecast_days.parquet')} (FORMAT PARQUET)")
        counts=c.execute(f'''SELECT count(*) AS candidate_rows,count(*) FILTER(WHERE {eligibility}) AS eligible_rows,
            count(*) FILTER(WHERE NOT exposed) AS no_exposure,
            count(*) FILTER(WHERE ongoing_d5) AS ongoing_d5,
            count(*) FILTER(WHERE NOT past_covered) AS unknown_past_coverage,
            count(*) FILTER(WHERE NOT target_covered) AS incomplete_target_coverage,
            sum(n_events) FILTER(WHERE {eligibility}) AS eligible_events,
            sum(y) FILTER(WHERE {eligibility}) AS positive_rows FROM riskset''').df().iloc[0].to_dict()
        meta={'schema_version':'local24.v1','origin':ORIGIN.isoformat(),'archive_cutoff':CUTOFF.isoformat(),
              'horizon_h':24,'timezone':'Europe/Moscow; naive wall timestamps','feature_names':FEATURE_NAMES,
              'row_file':'dataset.parquet','event_file':'events.parquet','mapping_file':'mapping.parquet',
              'coverage_file':'coverage.parquet','forecast_calendar_file':'forecast_days.parquet','counts':{k:int(v) for k,v in counts.items()},
              'source_sha256':{k:sha256(p) for k,p in sources.items()},'sources':{k:str(p) for k,p in sources.items()},
              'mapping_sha256':sha256(output/'mapping.parquet'),
              'mapping_counts':{'channels':len(mapping),'unmapped_channels':int(mapping.section_id.isna().sum()),
                                'ambiguous_sections':c.execute('SELECT count(*) FROM section_status WHERE NOT resolved').fetchone()[0],
                                'resolved_sections':c.execute('SELECT count(*) FROM sections').fetchone()[0]},
              'word_label':'New D5 channel episode onset in (as_of,as_of+24h] at the exact product picket section; duration strictly >1h. This is a journal-defined fault, not independently verified physical failure.',
              'failure_values':['Неисправен','Батарея неисправна','Много неисправных устройств','Не определено'],
              'eligibility':'Past-observed component, last section record<=365d ago; no open D5 episode at prediction (even if subsequently short); prior day and complete target+confirmation archive coverage.',
              'coverage_limit':'Global daily row-count proxy: >=10% median previous28days, at least7 prior days. Sparse section silence is not called failure. Target censoring uses future observation coverage only, never feature values.',
              'identity_limit':'Current product channel→picket and customer-tree collector mapping is assumed historically stable; no installation/retirement/versioned registry available.',
              'causality':'Prediction at00:00 only; journal complete days strictly before t; confirmation at onset+3601s; shorts known only after close; known-component counts require first past observation.',
              'exclusion_counts_overlap':True,'ongoing_mask_includes_any_D5_episode':True,
              'repro_command':'PYTHONPATH=src python -m ml.local24_data --root <main-root> --dsn <read-only-product-dsn>',
              'source_code_sha256':sha256(__file__),
              'events_total':c.execute('SELECT count(*) FROM events').fetchone()[0],
              'forecast_days':c.execute('SELECT count(DISTINCT as_of) FROM riskset WHERE past_covered AND target_covered').fetchone()[0]}
        (output/'metadata.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(meta['counts'],indent=2),flush=True)
        return meta
    finally:
        builder.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--output',type=Path)
    p.add_argument('--dsn',required=True)
    a=p.parse_args()
    build(a.root,a.output or a.root/'data/03_processed/local24_20260923',a.dsn)
