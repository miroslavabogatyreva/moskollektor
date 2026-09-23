#!/usr/bin/env python3
"""Separate, conditional-current-mapping Local24 history ablation (2019–2020).

Rebuilds D5 from raw journals in the existing (ts,ev) order. Never reads 2021,
never changes the original builder or datasets, and refuses output overwrite.
Run as a dedicated process: the separately loaded calendar module has its own
2019 origin. Resource cap: four DuckDB threads, 8 GB, sequential passes.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import sys
import tempfile

import duckdb
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'src'))
from ml import config as C, daily, failure_defs as F

START = datetime(2019, 1, 1)
END = datetime(2020, 12, 31, 23, 59, 59)
spec = importlib.util.spec_from_file_location('_isolated_history_calendar_20192020', REPO / 'src/ml/local24_data.py')
local = importlib.util.module_from_spec(spec)
spec.loader.exec_module(local)
CALENDAR_SOURCE_SHA256 = local.sha256(REPO / 'src/ml/local24_data.py')
# This module is a private in-memory copy loaded by this dedicated process.
# The source file and the normal ml.local24_data module are never changed.
local.ORIGIN = START
literal = local._literal


def connection():
    c = duckdb.connect()
    c.execute("SET threads=4; SET memory_limit='8GB'; SET preserve_insertion_order=false")
    return c


class HistoryData(local.Local24Data):
    """Same 40 columns, historical boundary, canonical positive closed shorts.

    _features/riskset/_prepare_events are inherited from the actual section
    builder. _prepare is explicit because its frozen episode warmup is 2022.
    """
    def __init__(self, mapping, chanday, episodes, cutoff=END):
        self.cutoff = pd.Timestamp(cutoff).to_pydatetime()
        self.tempdir = tempfile.TemporaryDirectory(prefix='local24-history-')
        self.con = connection()
        self.con.execute(f'SET temp_directory={literal(self.tempdir.name)}')
        self.con.register('mapping_input', mapping)
        self.con.read_parquet(str(chanday)).create_view('daily_input')
        self.con.read_parquet(str(episodes)).create_view('episodes_input')
        self._prepare()

    def _prepare(self):
        c = self.con
        c.execute('''CREATE TABLE section_status AS SELECT section_id,
            count(DISTINCT collector_id)=1 AND count(collector_id)=count(*) AS resolved
            FROM mapping_input WHERE section_id IS NOT NULL GROUP BY section_id''')
        c.execute('''CREATE TABLE mapping AS SELECT m.* FROM mapping_input m
            JOIN section_status USING(section_id) WHERE resolved''')
        c.execute('''CREATE TABLE sections AS SELECT section_id,min(collector_id) AS collector_id
            FROM mapping GROUP BY section_id''')
        c.execute(f'''CREATE TABLE cd AS SELECT d.* FROM daily_input d JOIN mapping USING(ch)
            WHERE d.d>=DATE '{START.date()}' AND d.d<=DATE '{self.cutoff.date()}' ''')
        c.execute(f'''CREATE TABLE ep AS SELECT e.ch,m.section_id,m.collector_id,e.t_start,
            CASE WHEN e.t_end<=TIMESTAMP '{self.cutoff}' THEN e.t_end END AS t_end,
            CASE WHEN e.t_end<=TIMESTAMP '{self.cutoff}' THEN e.close_val END AS close_val
            FROM episodes_input e JOIN mapping m USING(ch)
            WHERE e.t_start>=DATE '{START.date()}' AND e.t_start<=TIMESTAMP '{self.cutoff}' ''')
        self._prepare_events()
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
            count(*) AS n_confirmed,max(confirmed_at) AS last_confirmation FROM events GROUP BY 1,2''')
        # Censor endpoints BEFORE deciding positive duration; do not retrospectively
        # drop unknown ongoing records. Only the short feature gets this correction.
        c.execute('''CREATE TABLE canonical_closed AS SELECT DISTINCT ch,section_id,t_start,t_end,close_val
            FROM ep WHERE t_end>t_start''')
        conflicts = c.execute('''SELECT count(*) FROM (SELECT ch,t_start FROM canonical_closed
            GROUP BY 1,2 HAVING count(*)>1)''').fetchone()[0]
        if conflicts:
            raise ValueError(f'Ambiguous positive-duration episodes: {conflicts}')
        c.execute('''CREATE TABLE shorts AS SELECT section_id,t_end::DATE AS d,count(*) AS n_short
            FROM canonical_closed WHERE t_end<=t_start+INTERVAL 1 HOUR GROUP BY 1,2''')
        c.execute(f'''CREATE TABLE coverage AS WITH days AS (
            SELECT unnest(generate_series(DATE '{START.date()}',DATE '{self.cutoff.date()}',INTERVAL 1 DAY))::DATE AS d),
            totals AS (SELECT d,sum(n) AS n FROM daily_input WHERE d>=DATE '{START.date()}'
                AND d<=DATE '{self.cutoff.date()}' GROUP BY d),
            history AS (SELECT days.d,coalesce(n,0) AS n,
                median(n) OVER(ORDER BY days.d ROWS BETWEEN 28 PRECEDING AND 1 PRECEDING) AS prior_median,
                count(n) OVER(ORDER BY days.d ROWS BETWEEN 28 PRECEDING AND 1 PRECEDING) AS prior_days
                FROM days LEFT JOIN totals USING(d))
            SELECT *,n>0 AND prior_days>=7 AND n>=0.1*prior_median AS covered FROM history''')


def chronology_check(c, root):
    result = []
    for year in (2019, 2020):
        c.read_parquet(str(root / f'data/02_interim/mk/parquet/j{year}.parquet')).create_view('journal', replace=True)
        duplicates = conflicts = 0
        for k in range(8):
            n, bad = c.execute(f'''SELECT count(*),count(*) FILTER(WHERE states>1) FROM (
                SELECT ch,ts,ev,count(DISTINCT coalesce(({F.EXT}),false)) AS states FROM journal
                WHERE ch%8={k} GROUP BY 1,2,3 HAVING count(*)>1)''').fetchone()
            duplicates += n
            conflicts += bad
        if conflicts:
            raise ValueError(f'{year}: {conflicts} conflicting D5 states for ORDER(ch,ts,ev); cannot assign labels')
        result.append({'year': year, 'duplicate_order_key_groups': duplicates,
                       'conflicting_d5_boolean_groups': conflicts,
                       'journal_rows': c.execute('SELECT count(*) FROM journal').fetchone()[0]})
    return result


def build_episodes(c, root, destination, cutoff=END, ch_where=''):
    # Changes only module configuration inside this standalone process.
    F.SRC_YEARS = (2019, 2020)
    C.MK = root / 'data/02_interim/mk'
    F.build_episodes(c, [F.EXT], chunks=8 if not ch_where else 1,
                     ch_where=ch_where, ts_to=str(cutoff), log=lambda s: print(s, flush=True))
    c.execute(f'''COPY (SELECT * FROM ep_0 ORDER BY ch,t_start,t_end)
        TO {literal(destination)} (FORMAT PARQUET)''')


def prefix_checks(root, mapping, daily_path, episodes_path, directory):
    # Event-rich real sections per collector, chosen only for an implementation
    # test. Selection is independent of any model outcomes or validation metrics.
    events = pd.read_parquet(directory / 'events.parquet')
    selected = (events.groupby(['collector_id', 'section_id']).size().rename('n').reset_index()
                .sort_values(['collector_id', 'n', 'section_id'], ascending=[True, False, True])
                .drop_duplicates('collector_id').section_id.tolist())
    subset = mapping[mapping.section_id.isin(selected)]
    channels = ','.join(str(int(v)) for v in sorted(subset.ch))
    result = []
    with tempfile.TemporaryDirectory(prefix='local24-history-prefix-') as tmp:
        tmp = Path(tmp)
        for date in ('2019-07-01', '2020-07-01'):
            cutoff = pd.Timestamp(date).to_pydatetime()
            c = connection()
            try:
                ep = tmp / f'episodes-{date}.parquet'
                dp = tmp / f'daily-{date}.parquet'
                build_episodes(c, root, ep, cutoff, f'ch IN ({channels})')
                # Real raw daily reconstruction through the instant, including all
                # channels so the global archive coverage denominator is unchanged.
                C.DATE_START = '2019-01-01'
                C.JOURNAL_YEARS = (2019, 2020)
                daily.build_chanday(c, ts_to=str(cutoff), log=lambda s: print(s, flush=True))
                c.execute(f'COPY chanday TO {literal(dp)} (FORMAT PARQUET)')
                c.read_parquet(str(daily_path)).create_view('cached_daily')
                counts = ' OR '.join(f'a.{n} IS DISTINCT FROM b.{n}' for n in
                                  ('n','n_bad','n_undef','n_alarm','n_numeric','n_obes','n_batt'))
                mismatch = c.execute(f'''SELECT count(*) FROM
                    (SELECT * FROM chanday WHERE d<DATE '{date}') a FULL JOIN
                    (SELECT * FROM cached_daily WHERE d<DATE '{date}') b USING(ch,d)
                    WHERE a.ch IS NULL OR b.ch IS NULL OR {counts}''').fetchone()[0]
                if mismatch:
                    raise ValueError(f'Cached/raw daily counter mismatch at {date}: {mismatch}')
            finally:
                c.close()
            frames = []
            for daily_file, episode_file, end in ((daily_path, episodes_path, END), (dp, ep, cutoff)):
                b = HistoryData(subset, daily_file, episode_file, end)
                try:
                    frames.append(b.build_features(date).sort_values('section_id').reset_index(drop=True))
                finally:
                    b.close()
            pd.testing.assert_frame_equal(frames[0], frames[1], check_exact=True)
            result.append({'as_of': date, 'sections': len(selected), 'features': len(local.FEATURE_NAMES),
                           'raw_daily_counter_mismatches': mismatch, 'feature_values_exactly_equal': True,
                           'method': 'Raw journal prefix rebuild versus full historical archive; static mapping held fixed.'})
    return result


def build(root, output):
    if output.exists():
        raise ValueError(f'Refusing overwrite: {output}')
    mapping_path = root / 'data/03_processed/local24_20260923/mapping.parquet'
    daily_source = root / 'data/03_processed/autoresearch_hist_20260915/chanday.parquet'
    mapping = pd.read_parquet(mapping_path)
    if mapping.ch.duplicated().any():
        raise ValueError('Duplicate channel mapping')
    c = connection()
    try:
        chronology = chronology_check(c, root)
        output.mkdir(parents=True)
        mapping.to_parquet(output / 'mapping.parquet', index=False)
        c.read_parquet(str(daily_source)).create_view('daily_source')
        c.execute(f'''COPY (SELECT * FROM daily_source WHERE d>=DATE '2019-01-01' AND d<DATE '2021-01-01'
            ORDER BY ch,d) TO {literal(output/'chanday.parquet')} (FORMAT PARQUET)''')
        c.read_parquet(str(output/'chanday.parquet')).create_view('historical_daily')
        daily_stats = c.execute('SELECT year(d) AS year,count(*) AS channel_days,sum(n) AS readings FROM historical_daily GROUP BY 1 ORDER BY 1').df().to_dict('records')
        for row, source in zip(daily_stats, chronology):
            if row['year'] != source['year'] or row['readings'] != source['journal_rows']:
                raise ValueError('Historical cached daily totals do not match journal')
        if c.execute('SELECT count(*)-count(DISTINCT (ch,d)) FROM historical_daily').fetchone()[0]:
            raise ValueError('Duplicate channel-day rows')
        build_episodes(c, root, output / 'episodes_d5.parquet')
    finally:
        c.close()
    builder = HistoryData(mapping, output/'chanday.parquet', output/'episodes_d5.parquet')
    try:
        c = builder.riskset(END.replace(hour=0, minute=0, second=0))
        eligible = 'exposed AND NOT ongoing_d5 AND past_covered AND target_covered'
        c.execute(f'''COPY (SELECT section_id,collector_id,as_of,y,n_events,{','.join(local.FEATURE_NAMES)}
            FROM riskset WHERE {eligible} ORDER BY as_of,section_id)
            TO {literal(output/'dataset.parquet')} (FORMAT PARQUET)''')
        c.execute(f'COPY (SELECT * FROM events ORDER BY t_start,section_id,channel_id) TO {literal(output/"events.parquet")} (FORMAT PARQUET)')
        c.execute(f'''COPY (SELECT section_id,collector_id,as_of,exposed,ongoing_d5,past_covered,target_covered
            FROM riskset ORDER BY as_of,section_id) TO {literal(output/'coverage.parquet')} (FORMAT PARQUET)''')
        c.execute(f'''COPY (SELECT DISTINCT as_of FROM riskset WHERE past_covered AND target_covered ORDER BY as_of)
            TO {literal(output/'forecast_days.parquet')} (FORMAT PARQUET)''')
        counts = c.execute(f'''SELECT count(*) AS candidate_rows,count(*) FILTER(WHERE {eligible}) AS eligible_rows,
            sum(y) FILTER(WHERE {eligible}) AS positive_rows,sum(n_events) FILTER(WHERE {eligible}) AS eligible_events,
            count(*) FILTER(WHERE NOT exposed) AS no_exposure,count(*) FILTER(WHERE ongoing_d5) AS ongoing_d5,
            count(*) FILTER(WHERE NOT past_covered) AS unknown_past_coverage,
            count(*) FILTER(WHERE NOT target_covered) AS incomplete_target_coverage FROM riskset''').df().iloc[0].to_dict()
        if c.execute('SELECT count(*)-count(DISTINCT event_id) FROM events').fetchone()[0]:
            raise ValueError('Duplicate long D5 event IDs')
        event_counts = c.execute('SELECT year(t_start) AS year,count(*) AS events FROM events GROUP BY 1 ORDER BY 1').df().to_dict('records')
        eligible_bounds = c.execute(f'SELECT min(as_of),max(as_of) FROM riskset WHERE {eligible}').fetchone()
    finally:
        builder.close()
    checks = prefix_checks(root, mapping, output/'chanday.parquet', output/'episodes_d5.parquet', output)
    if local.sha256(REPO/'src/ml/local24_data.py') != CALENDAR_SOURCE_SHA256:
        raise ValueError('Calendar source changed while building; validation must be repeated on one source version')
    metadata = {
        'schema_version': 'local24.history20192020.canonicalshort.v1',
        'status': 'validated_for_predeclared_training_history_ablation_only',
        'horizon_h': 24, 'origin': str(START), 'archive_cutoff': str(END),
        'timezone': 'Europe/Moscow naive archive wall time', 'feature_names': local.FEATURE_NAMES,
        'row_file': 'dataset.parquet', 'event_file': 'events.parquet', 'mapping_file': 'mapping.parquet',
        'coverage_file': 'coverage.parquet', 'forecast_calendar_file': 'forecast_days.parquet',
        'counts': {k: int(v) for k,v in counts.items()}, 'exclusion_counts_overlap': True,
        'eligible_as_of_bounds': [str(x) for x in eligible_bounds], 'events_by_year': event_counts,
        'daily_by_year': daily_stats, 'chronology': chronology, 'prefix_checks': checks,
        'failure_values': list(F.D5_VALUES), 'episode_order': F.ORDER,
        'target': 'New observed D5 onset in (t,t+24h], duration strictly >1h; confirmation onset+3601s; journal-defined, not verified physical failure.',
        'short_features': 'Censor ends; exact tuple dedup; require 0<observed closed duration<=1h. Same canonical short definition as sensor-v3 control, kept under short_7d/short_28d names.',
        'identity_limit': 'Current September2026 product channel→picket→collector mapping assumed historically stable; no effective-dated registry. Unknown/no-picket/ambiguous channels excluded.',
        'gap_policy': 'Only raw 2019 and 2020 read. No 2021 rows, episodes, warmup, or state bridge. Canonical 2022+ control remains separate and unchanged.',
        'left_boundary': 'No pre2019 history available; first observation does not prove installation date or physical fault onset. Same exposure and past archive coverage gates as Local24.',
        'right_boundary': 'Ends censored at2020-12-31 23:59:59; target+25h completeness rejects the tail before the excluded2021 gap.',
        'resources': {'threads': 4, 'memory_limit': '8GB'},
        'code_sha256': {str(p.relative_to(REPO)):local.sha256(p) for p in
            (Path(__file__),REPO/'src/ml/local24_data.py',REPO/'src/ml/failure_defs.py',REPO/'src/ml/daily.py')},
        'source_sha256': {'mapping': local.sha256(mapping_path), 'daily_cache': local.sha256(daily_source)},
        'artifact_sha256': {p.name: local.sha256(p) for p in sorted(output.glob('*.parquet'))},
    }
    (output/'metadata.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2, default=str)+'\n')
    print(json.dumps({'output': str(output), 'counts': metadata['counts'], 'prefix_checks': checks}, ensure_ascii=False), flush=True)
    return metadata


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--output', type=Path)
    args = p.parse_args()
    root = args.root.resolve()
    build(root, (args.output or root/'data/03_processed/local24_history_20192020_20260923').resolve())
