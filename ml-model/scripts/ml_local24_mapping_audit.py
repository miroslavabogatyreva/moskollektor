#!/usr/bin/env python3
"""Read-only spatial/identity audit of the frozen Local24 real-data snapshot.

Writes aggregate JSON only, never journals, dictionaries, or processed datasets.
Run with native Python; DuckDB is limited to 3 threads and 6 GB. --product-root
must contain backend/app/ingest/tag_to_section.py, the actual product parser.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile

import duckdb
import pandas as pd


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def temporal_probe(mapping, paths):
    """Exercise actual builder at two cutoffs on real event-rich sections.

    Bypass only its constructor resource defaults, retaining its actual SQL.
    This tests cutoff consistency, not historical registry correctness.
    """
    source = Path(__file__).resolve().parents[1] / 'src/ml/local24_data.py'
    spec = importlib.util.spec_from_file_location('audited_local24_data', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    events = pd.read_parquet(paths['events'])
    selected = (events.groupby(['collector_id', 'section_id']).size().rename('n').reset_index()
                .sort_values(['collector_id', 'n', 'section_id'], ascending=[True, False, True])
                .drop_duplicates('collector_id').section_id.tolist())
    results = []
    for cutoff in ('2025-07-01 00:00:00', '2026-06-30 23:59:59'):
        builder = module.Local24Data.__new__(module.Local24Data)
        builder.cutoff = pd.Timestamp(cutoff).to_pydatetime()
        builder.tempdir = tempfile.TemporaryDirectory(prefix='local24-mapping-audit-')
        builder.con = duckdb.connect()
        builder.con.execute("SET threads=3; SET memory_limit='6GB'; SET preserve_insertion_order=false")
        builder.con.execute(f'SET temp_directory={module._literal(builder.tempdir.name)}')
        builder.con.register('mapping_input', mapping[mapping.section_id.isin(selected)])
        builder.con.read_parquet(str(paths['daily'])).create_view('daily_input')
        builder.con.read_parquet(str(paths['episodes'])).create_view('episodes_input')
        try:
            builder._prepare()
            results.append(builder.build_features('2025-07-01').sort_values('section_id').reset_index(drop=True))
        finally:
            builder.close()
    pd.testing.assert_frame_equal(results[0], results[1], check_exact=True)
    return {'as_of': '2025-07-01T00:00:00', 'cutoffs': ['2025-07-01T00:00:00', '2026-06-30T23:59:59'],
            'sections': len(selected), 'features': len(module.FEATURE_NAMES), 'exactly_equal': True,
            'builder_source_sha256': digest(source),
            'scope': 'Actual current Local24Data SQL; real event-rich section per represented collector. Static mapping reused in both; no proof of historical identity.'}


def audit(root, product_root):
    data = root / 'data'
    paths = {
        'mapping': data / '03_processed/local24_20260923/mapping.parquet',
        'daily': data / '03_processed/ml_20260915/chanday.parquet',
        'episodes': data / '03_processed/faildef_20260915/D5/episodes_ext.parquet',
        'events': data / '03_processed/local24_20260923/events.parquet',
        'dataset': data / '03_processed/local24_20260923/dataset.parquet',
        'coverage': data / '03_processed/local24_20260923/coverage.parquet',
        'old_dictionary': data / '01_raw/dataset_20260915/справочник_каналов_датчиков.csv',
        'new_dictionary': data / '01_raw/dataset_update_20260916/справочник_каналов_датчиков.csv',
        'objects': data / '01_raw/dataset_20260915/справочник_объектов_диспетчер.csv',
        'product_parser': product_root / 'backend/app/ingest/tag_to_section.py',
    }
    spec = importlib.util.spec_from_file_location('audited_product_parser', paths['product_parser'])
    parser = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parser)
    old = pd.read_csv(paths['old_dictionary']).set_index('ид_канала_данных').sort_index()
    new = pd.read_csv(paths['new_dictionary']).set_index('ид_канала_данных').sort_index()
    objects = pd.read_csv(paths['objects']).set_index('ид_объект')
    mapping = pd.read_parquet(paths['mapping'])
    key = lambda row: parser.section_key(row['тег_инженерной_системы'], row['название_датчика'])
    common = old.index.intersection(new.index)
    old_keys, new_keys = old.apply(key, axis=1), new.apply(key, axis=1)
    spatial = mapping.merge(new.reset_index(), left_on='ch', right_on='ид_канала_данных', validate='one_to_one')
    spatial['picket'] = spatial['название_датчика'].map(parser.picket_of)
    spatial['parsed_key'] = [None if k is None else f'{k[0]}:{k[1]}' for k in spatial.apply(key, axis=1)]
    spatial['tree_collector'] = spatial['ид_объект'].map(objects['родитель'])
    spatial['multi_pk'] = spatial['название_датчика'].map(lambda x: len(parser.PICKET.findall(x)) > 1)
    spatial['offset_ge10'] = spatial['название_датчика'].map(
        lambda x: bool((m := parser.OFFSET.search(x)) and float(m.group(2).replace(',', '.')) >= 10))
    c = duckdb.connect()
    c.execute("SET threads=3; SET memory_limit='6GB'; SET preserve_insertion_order=false")
    for name in ('mapping', 'daily', 'episodes', 'events', 'dataset', 'coverage'):
        c.read_parquet(str(paths[name])).create_view(name)
    c.register('spatial', spatial)
    def rows(sql):
        result = c.execute(sql)
        return [dict(zip([x[0] for x in result.description], r)) for r in result.fetchall()]
    def one(sql):
        return rows(sql)[0]
    c.execute('''CREATE TEMP TABLE status AS SELECT section_id,
        count(DISTINCT collector_id)=1 AND count(collector_id)=count(*) AS resolved
        FROM mapping WHERE section_id IS NOT NULL GROUP BY section_id''')
    c.execute('''CREATE TEMP VIEW resolved_mapping AS SELECT m.* FROM mapping m
        JOIN status USING(section_id) WHERE resolved''')
    reason = """CASE WHEN m.ch IS NULL THEN 'unknown_channel' WHEN m.section_id IS NULL THEN 'no_picket'
        WHEN NOT s.resolved THEN 'ambiguous_section' ELSE 'resolved' END"""
    joins = 'LEFT JOIN mapping m USING(ch) LEFT JOIN status s USING(section_id)'
    out = {
        'schema': 'local24.mapping-audit.v1',
        'resources': {'threads': 3, 'memory_limit': '6GB'},
        'sources': {k: {'relative_path': str(v.relative_to(root)) if v.is_relative_to(root) else str(v),
                        'sha256': digest(v)} for k, v in paths.items()},
        'dictionaries': {
            'old_channels': len(old), 'new_channels': len(new),
            'added_ids': len(new.index.difference(old.index)), 'removed_ids': len(old.index.difference(new.index)),
            'changed_common_fields': {col: int((old.loc[common, col] != new.loc[common, col]).sum()) for col in old.columns},
            'changed_parsed_keys': int((old_keys.loc[common].fillna('') != new_keys.loc[common].fillna('')).sum()),
            'new_dictionary_object_nulls': int(new['ид_объект'].isna().sum()),
            'collector_parent_mismatches': int((spatial['collector_id'] != spatial['tree_collector']).sum()),
            'missing_object_ids': int((~new['ид_объект'].isin(objects.index)).sum()),
            'object_levels': objects['иерархия_уровень'].value_counts().sort_index().to_dict(),
        },
        'mapping': one('''SELECT count(*) AS n_rows,count(DISTINCT ch) AS channels,
            count(*) FILTER(WHERE section_id IS NULL) AS no_picket_channels,
            count(DISTINCT section_id) AS product_sections,count(DISTINCT collector_id) AS collectors
            FROM mapping'''),
        'daily_identity': one('''SELECT min(d) AS first_day,max(d) AS last_day,count(*) AS n_rows,
            count(DISTINCT ch) AS channels,count(*)-count(DISTINCT (ch,d)) AS duplicate_channel_days FROM daily'''),
    }
    out['mapping']['ambiguous_sections'] = rows('''SELECT section_id,count(*) AS channels,
        count(DISTINCT collector_id) AS collectors FROM mapping JOIN status USING(section_id)
        WHERE NOT resolved GROUP BY section_id ORDER BY section_id''')
    out['mapping']['parsed_key_bijection_violations'] = one('''SELECT
        (SELECT count(*) FROM (SELECT section_id FROM spatial WHERE section_id IS NOT NULL GROUP BY 1
            HAVING count(DISTINCT parsed_key)<>1)) AS section_to_keys,
        (SELECT count(*) FROM (SELECT parsed_key FROM spatial WHERE parsed_key IS NOT NULL GROUP BY 1
            HAVING count(DISTINCT section_id)<>1)) AS key_to_sections''')
    c.execute('''CREATE TEMP VIEW multinode_sections AS SELECT section_id FROM spatial
        WHERE section_id IS NOT NULL GROUP BY section_id HAVING count(DISTINCT "ид_объект")>1''')
    c.execute('''CREATE TEMP VIEW split_groups AS SELECT collector_id,picket FROM spatial
        WHERE section_id IS NOT NULL GROUP BY 1,2 HAVING count(DISTINCT section_id)>1''')
    out['spatial_ambiguity_candidates'] = {
        'multinode_sections': one('''SELECT count(DISTINCT section_id) AS sections,count(*) AS channels
            FROM spatial JOIN multinode_sections USING(section_id)'''),
        'same_collector_picket_split': one('''SELECT count(DISTINCT (collector_id,picket)) AS groups_count,
            count(DISTINCT section_id) AS sections,count(*) AS channels
            FROM spatial JOIN split_groups USING(collector_id,picket)'''),
        'text_geometry': one('''SELECT count(*) FILTER(WHERE multi_pk) AS multi_pk_channels,
            count(DISTINCT section_id) FILTER(WHERE multi_pk) AS multi_pk_sections,
            count(*) FILTER(WHERE offset_ge10) AS offset_ge10_channels FROM spatial'''),
        'events_in_multinode_sections': one('''SELECT count(*) AS events,count(DISTINCT section_id) AS sections
            FROM events JOIN multinode_sections USING(section_id)'''),
        'events_on_multirange_channels': one('''SELECT count(*) AS events,count(DISTINCT e.channel_id) AS channels
            FROM events e JOIN spatial s ON e.channel_id=s.ch WHERE s.multi_pk'''),
    }
    out['section_flags'] = rows('''SELECT section_id,count(*) AS channel_count,
        count(DISTINCT "ид_объект") AS tree_node_count,
        count(DISTINCT collector_id) AS collector_count,
        count(*) FILTER(WHERE collector_id IS NULL) AS missing_collector_count,
        count(*) FILTER(WHERE multi_pk) AS multi_pk_channel_count,
        count(*) FILTER(WHERE offset_ge10) AS offset_ge10_channel_count,
        bool_or(g.picket IS NOT NULL) AS shares_collector_picket_with_other_product_section
        FROM spatial s LEFT JOIN split_groups g USING(collector_id,picket)
        WHERE section_id IS NOT NULL GROUP BY section_id ORDER BY section_id''')
    for row in out['section_flags']:
        row['ambiguous_collector'] = row['collector_count'] != 1 or row['missing_collector_count'] > 0
        if row['ambiguous_collector']:
            status = 'ambiguous_collector_excluded'
        elif row['multi_pk_channel_count'] or row['offset_ge10_channel_count']:
            status = 'range_or_offset_lower_localization_confidence'
        elif row['tree_node_count'] > 1 or row['shares_collector_picket_with_other_product_section']:
            status = 'topology_review_required'
        else:
            status = 'current_mapping_physical_location_unverified'
        row['localization_status'] = status
    out['section_flags_summary'] = pd.Series([r['localization_status'] for r in out['section_flags']]).value_counts().sort_index().to_dict()
    yearly = []
    activity = []
    out['journal_sources'] = []
    for path in sorted((data / '02_interim/mk/parquet').glob('j20??.parquet')):
        year = int(path.stem[1:])
        c.read_parquet(str(path)).create_view('journal', replace=True)
        c.execute('''CREATE OR REPLACE TEMP TABLE journal_channel AS SELECT ch,count(*) AS n,
            min(ts) AS first_ts,max(ts) AS last_ts FROM journal GROUP BY ch''')
        yearly.extend(rows(f'''SELECT {year} AS year,{reason} AS mapping_reason,sum(n) AS journal_rows,
            count(*) AS channels,min(first_ts) AS first_ts,max(last_ts) AS last_ts
            FROM journal_channel j {joins} GROUP BY mapping_reason ORDER BY mapping_reason'''))
        frame = c.execute('SELECT * FROM journal_channel').df()
        frame['year'] = year
        activity.append(frame)
        # Hashing multi-GB raw journals is optional overhead; record immutable file size and
        # footer row count, while the small identity/label artifacts above have full hashes.
        out['journal_sources'].append({'relative_path': str(path.relative_to(root)),
            'size_bytes': path.stat().st_size, 'rows': int(frame.n.sum())})
    out['journal_mapping_coverage'] = yearly
    active = pd.concat(activity, ignore_index=True)
    returns = active.sort_values(['ch', 'year']).assign(previous_year=lambda x: x.groupby('ch')['year'].shift())
    returned = returns[returns.year-returns.previous_year > 1]
    known = set(mapping.ch)
    out['identity_lifetime_proxies'] = {
        'journal_channels': int(active.ch.nunique()),
        'journal_channels_absent_from_current_dictionary': len(set(active.ch)-known),
        'channels_with_a_full_calendar_year_absent_then_returning': int(returned.ch.nunique()),
        'returning_channels_in_current_dictionary': len(set(returned.ch)&known),
        'interpretation': 'Activity gaps are NOT proof of reassignment or reuse; no versioned registry exists in these inputs.',
    }
    out['episodes'] = one('''SELECT count(*) AS n_rows,min(t_start) AS first_onset,max(t_start) AS last_onset,
        count(*)-count(DISTINCT (ch,t_start)) AS same_onset_extra_rows,
        count(*)-count(DISTINCT (ch,t_start,t_end,close_val)) AS exact_tuple_extra_rows,
        count(*) FILTER(WHERE t_end=t_start) AS zero_duration_rows,
        count(*) FILTER(WHERE t_end<t_start) AS negative_duration_rows FROM episodes''')
    out['episodes']['exact_full_row_extra_rows'] = c.execute('SELECT (SELECT count(*) FROM episodes)-count(*) FROM (SELECT DISTINCT * FROM episodes)').fetchone()[0]
    out['episodes']['onset_conflicts'] = one('''SELECT count(*) AS duplicate_groups,
        count(*) FILTER(WHERE variants>1) AS different_end_close_groups,
        count(*) FILTER(WHERE n_long=1) AS groups_with_one_long,
        count(*) FILTER(WHERE n_long>1) AS groups_with_multiple_long FROM (
        SELECT ch,t_start,count(*) AS n,count(DISTINCT (t_end,close_val)) AS variants,
        count(*) FILTER(WHERE coalesce(t_end,TIMESTAMP '2026-06-30 23:59:59')>t_start+INTERVAL 1 HOUR) AS n_long
        FROM episodes GROUP BY 1,2 HAVING count(*)>1)''')
    out['episodes']['positive_duration_onset_conflicts'] = c.execute('''SELECT count(*) FROM (
        SELECT ch,t_start FROM (SELECT DISTINCT ch,t_start,t_end,close_val FROM episodes WHERE t_end>t_start)
        GROUP BY 1,2 HAVING count(*)>1)''').fetchone()[0]
    out['long_event_mapping_coverage'] = rows(f'''SELECT {reason} AS mapping_reason,count(*) AS events,
        count(DISTINCT e.ch) AS channels FROM episodes e {joins}
        WHERE t_start>=TIMESTAMP '2022-04-01' AND
        coalesce(t_end,TIMESTAMP '2026-06-30 23:59:59')>t_start+INTERVAL 1 HOUR
        GROUP BY mapping_reason ORDER BY mapping_reason''')
    out['long_event_id_check'] = one('''SELECT count(*) AS events,count(DISTINCT event_id) AS distinct_ids FROM events''')
    out['event_eligibility'] = rows('''WITH matched AS (SELECT e.event_id,c.* FROM events e LEFT JOIN coverage c
        ON c.section_id=e.section_id AND c.as_of=date_trunc('day',e.t_start)-
            CASE WHEN e.t_start=date_trunc('day',e.t_start) THEN INTERVAL 1 DAY ELSE INTERVAL 0 DAY END)
        SELECT exposed,ongoing_d5,past_covered,target_covered,count(*) AS events
        FROM matched GROUP BY ALL ORDER BY ALL''')
    out['dataset_check'] = one('''SELECT count(*) AS rows_count,sum(y) AS positive_section_days,
        sum(n_events) AS eligible_events,count(*)-count(DISTINCT (section_id,as_of)) AS duplicate_keys FROM dataset''')
    c.execute('''CREATE TEMP TABLE short_raw AS SELECT ch,section_id,t_start,t_end,close_val
        FROM episodes JOIN resolved_mapping USING(ch)
        WHERE t_start>=DATE '2022-01-01' AND t_end<=TIMESTAMP '2026-06-30 23:59:59'
            AND t_end<=t_start+INTERVAL 1 HOUR''')
    out['short_feature_audit'] = one('''SELECT count(*) AS original_rows,
        count(*)-count(DISTINCT (ch,t_start,t_end,close_val)) AS exact_tuple_excess,
        count(*) FILTER(WHERE t_end=t_start) AS zero_duration_rows,
        count(DISTINCT (ch,t_start,t_end,close_val)) FILTER(WHERE t_end>t_start) AS distinct_positive_rows,
        count(DISTINCT section_id) FILTER(WHERE t_end=t_start) AS sections_with_zero_duration,
        count(DISTINCT (section_id,t_end::DATE)) FILTER(WHERE t_end=t_start) AS section_days_with_zero_duration
        FROM short_raw''')
    out['short_feature_audit']['eligible_rows_with_zero_duration_in_prior28d'] = c.execute('''SELECT count(*)
        FROM dataset d WHERE EXISTS(SELECT 1 FROM short_raw s WHERE s.section_id=d.section_id
            AND s.t_end=s.t_start AND s.t_end::DATE>=d.as_of::DATE-28 AND s.t_end::DATE<d.as_of::DATE)''').fetchone()[0]
    out['join_cardinality'] = one('''WITH once AS(SELECT d.* FROM daily d JOIN resolved_mapping m USING(ch)
        WHERE d.d>=DATE '2022-04-01' AND d.d<=DATE '2026-06-30'),
        twice AS(SELECT d.* FROM once d JOIN resolved_mapping m USING(ch))
        SELECT (SELECT count(*) FROM once) AS once_rows,(SELECT count(*) FROM twice) AS twice_rows,
        (SELECT sum(n) FROM once) AS once_readings,(SELECT sum(n) FROM twice) AS twice_readings''')
    c.close()
    out['temporal_query_probe'] = temporal_probe(mapping, paths)
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--product-root', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--flags-output', type=Path,
                   help='Local numeric section flags; defaults to root/data/03_processed/local24_mapping_audit_20260923/mapping-flags.json')
    args = p.parse_args()
    result = audit(args.root.resolve(), args.product_root.resolve())
    flags_path = args.flags_output or args.root / 'data/03_processed/local24_mapping_audit_20260923/mapping-flags.json'
    if flags_path.resolve() == args.output.resolve():
        raise ValueError('report and flags output paths must differ')
    flags = {'schema': 'local24.mapping-flags.v1',
             'mapping_sha256': result['sources']['mapping']['sha256'],
             'current_mapping_assumed_historically_stable': True,
             'historical_identity_verified': False, 'physical_location_verified': False,
             'confidence_is_calibrated_probability': False,
             'no_picket_channels_excluded_from_section_flags': result['mapping']['no_picket_channels'],
             'sections': result.pop('section_flags')}
    flags_path.parent.mkdir(parents=True, exist_ok=True)
    flags_path.write_text(json.dumps(flags, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    result['section_flags_artifact'] = {'path': str(flags_path.relative_to(args.root)) if flags_path.is_relative_to(args.root) else str(flags_path),
                                       'sha256': digest(flags_path), 'tracked_in_git': False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str, allow_nan=False)+'\n')
    print(json.dumps({'output': str(args.output), 'dataset_check': result['dataset_check'],
                      'short_feature_audit': result['short_feature_audit']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
