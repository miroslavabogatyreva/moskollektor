#!/usr/bin/env python3
"""Read-only aggregate target audit; no relabeling/training. <=3 threads, <=6GB."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile

import duckdb


def audit(root):
    root = Path(root)
    folder = root / 'data/03_processed/local24_20260923'
    sources = {n: folder / (n + '.parquet') for n in ('events', 'coverage', 'mapping', 'forecast_days')}
    sources['episodes'] = root / 'data/03_processed/faildef_20260915/D5/episodes_ext.parquet'
    with tempfile.TemporaryDirectory(prefix='target-audit-') as tmp, duckdb.connect() as c:
        c.execute("SET threads=3; SET memory_limit='6GB'; SET preserve_insertion_order=false")
        c.execute("SET temp_directory=?", [tmp])
        for name, path in sources.items():
            literal = str(path).replace("'", "''")
            c.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{literal}')")

        def rows(sql):
            result = c.execute(sql)
            return [dict(zip([x[0] for x in result.description], row)) for row in result.fetchall()]

        c.execute('''CREATE TABLE event_audit AS SELECT e.*,
            date_trunc('day',t_start)-CASE WHEN t_start=date_trunc('day',t_start)
                THEN INTERVAL 1 DAY ELSE INTERVAL 0 DAY END AS origin,
            lag(t_start) OVER(PARTITION BY channel_id ORDER BY t_start) AS prev_start,
            lag(t_end) OVER(PARTITION BY channel_id ORDER BY t_start) AS prev_end
            FROM events e''')
        c.execute('''CREATE TABLE event_coverage AS SELECT e.*,v.exposed,v.ongoing_d5,
            v.past_covered,v.target_covered, cal.as_of IS NOT NULL AS calendar_included,
            coalesce(exposed AND NOT ongoing_d5 AND past_covered AND target_covered,false) AS eligible
            FROM event_audit e LEFT JOIN coverage v ON v.section_id=e.section_id AND v.as_of=e.origin
            LEFT JOIN forecast_days cal ON cal.as_of=e.origin''')
        result = {'scope': 'D5 operational journal onsets; no independently verified hardware outcome',
                  'resources': {'threads': 3, 'memory_limit': '6GB'},
                  'labels_unchanged': True, 'independent_holdout_available': False}
        result['source_range'] = rows('''SELECT min(t_start) AS first_onset,max(t_start) AS last_onset,
            count(*) AS all_episode_rows,count(*) FILTER(WHERE t_end IS NULL) AS open_episodes,
            count(*) FILTER(WHERE dur_h>1) AS stored_duration_gt1 FROM episodes''')[0]
        result['event_integrity'] = rows('''SELECT count(*) AS events,count(DISTINCT event_id) AS event_ids,
            count(DISTINCT channel_id) AS channels,count(DISTINCT section_id) AS sections,
            count(*) FILTER(WHERE t_end IS NULL) AS open_events,
            count(*) FILTER(WHERE prev_end>t_start) AS overlapping_same_channel,
            count(*) FILTER(WHERE prev_end=t_start) AS exact_touch_same_channel,
            count(*) FILTER(WHERE prev_start IS NOT NULL) AS repeated_channel_onsets,
            count(*) FILTER(WHERE t_start-prev_start<=INTERVAL 24 HOUR) AS previous_onset_within24h,
            count(*) FILTER(WHERE t_start-prev_end BETWEEN INTERVAL 0 SECOND AND INTERVAL 1 HOUR) AS recovery_gap_le1h,
            count(*) FILTER(WHERE t_start-prev_end BETWEEN INTERVAL 0 SECOND AND INTERVAL 24 HOUR) AS recovery_gap_le24h,
            count(*) FILTER(WHERE t_start-prev_end BETWEEN INTERVAL 0 SECOND AND INTERVAL 7 DAY) AS recovery_gap_le7d
            FROM event_audit''')[0]
        assert result['event_integrity']['events'] == result['event_integrity']['event_ids']
        result['duration_sensitivity'] = rows('''SELECT threshold_h,
            count(*) FILTER(WHERE coalesce(t_end,TIMESTAMP '2026-06-30 23:59:59')-t_start>threshold_h*INTERVAL 1 HOUR) AS events
            FROM events CROSS JOIN (VALUES (1),(2),(6),(24)) t(threshold_h) GROUP BY 1 ORDER BY 1''')
        result['closing_values'] = rows('''SELECT coalesce(p.close_val,'<open>') AS closing_value,count(*) AS events
            FROM events e JOIN episodes p ON p.ch=e.channel_id AND p.t_start=e.t_start
                AND p.t_end IS NOT DISTINCT FROM e.t_end
            GROUP BY 1 ORDER BY 2 DESC,1''')
        result['coverage_by_quarter'] = rows('''SELECT year(t_start) AS year,quarter(t_start) AS quarter,
            count(*) AS total_events,count(*) FILTER(WHERE eligible) AS eligible_events,
            count(*) FILTER(WHERE calendar_included) AS events_on_evaluation_calendar,
            count(*) FILTER(WHERE ongoing_d5) AS excluded_ongoing,
            count(*) FILTER(WHERE NOT coalesce(exposed,false)) AS excluded_no_exposure,
            count(*) FILTER(WHERE NOT coalesce(past_covered,false)) AS excluded_past,
            count(*) FILTER(WHERE NOT coalesce(target_covered,false)) AS excluded_target,
            count(*) FILTER(WHERE eligible AND NOT is_repeat) AS eligible_first_observed,
            count(DISTINCT (section_id,origin)) FILTER(WHERE eligible) AS eligible_positive_section_days
            FROM event_coverage GROUP BY 1,2 ORDER BY 1,2''')
        result['coverage_totals'] = rows('''SELECT count(*) AS events,count(*) FILTER(WHERE eligible) AS eligible,
            count(*) FILTER(WHERE ongoing_d5) AS ongoing,
            count(*) FILTER(WHERE ongoing_d5 AND exposed AND past_covered AND target_covered) AS only_ongoing_exclusion,
            count(*) FILTER(WHERE ongoing_d5 AND exposed AND past_covered AND target_covered AND EXISTS(
                SELECT 1 FROM episodes p WHERE p.ch=event_coverage.channel_id
                AND p.t_start<=origin AND (p.t_end IS NULL OR p.t_end>origin))) AS own_channel_ongoing_exclusion
            FROM event_coverage''')[0]
        # Interval union: one episode continues until all included channels recover.
        # Additional 1h/24h healing gaps are sensitivity choices, NOT adopted labels.
        result['grouping_sensitivity'] = []
        for unit in ('channel_id', 'section_id'):
            for gap in (0, 1, 6, 24):
                q = f'''WITH prior AS (SELECT *,max(coalesce(t_end,TIMESTAMP '2026-06-30 23:59:59'))
                    OVER(PARTITION BY {unit} ORDER BY t_start,event_id ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS prior_end FROM events),
                    grouped AS (SELECT *,sum(CASE WHEN prior_end IS NULL OR t_start>prior_end+INTERVAL '{gap} HOUR' THEN 1 ELSE 0 END)
                    OVER(PARTITION BY {unit} ORDER BY t_start,event_id) AS grp FROM prior),
                    sizes AS (SELECT {unit},grp,min(t_start) AS onset,count(*) AS n,count(DISTINCT channel_id) AS channels FROM grouped GROUP BY 1,2)
                    SELECT count(*) AS grouped_events,sum(n) AS original_events,max(n) AS largest_group,
                    count(*) FILTER(WHERE n>1) AS multiple_groups,sum(n) FILTER(WHERE n>1) AS events_in_multiple_groups,
                    count(*) FILTER(WHERE onset>='2026-04-01') AS q2_grouped_events FROM sizes'''
                result['grouping_sensitivity'].append(dict(unit=unit,recovery_gap_hours=gap,**rows(q)[0]))
        # Temporal co-occurrence only: not a claim about common physical root cause.
        result['collector_cooccurrence_sensitivity'] = []
        for minutes in (10, 60):
            c.execute(f'''CREATE OR REPLACE TABLE collector_clusters AS WITH lagged AS (
                SELECT *,lag(t_start) OVER(PARTITION BY collector_id ORDER BY t_start,event_id) AS prev FROM events),
                grouped AS (SELECT *,sum(CASE WHEN prev IS NULL OR t_start-prev>INTERVAL '{minutes} MINUTE' THEN 1 ELSE 0 END)
                    OVER(PARTITION BY collector_id ORDER BY t_start,event_id) AS grp FROM lagged)
                SELECT collector_id,grp,min(t_start) AS onset,max(t_start) AS last_onset,
                count(*) AS n,count(DISTINCT channel_id) AS channels,count(DISTINCT section_id) AS sections
                FROM grouped GROUP BY 1,2''')
            cluster = rows('''SELECT count(*) AS clusters,sum(n) AS channel_events,
                count(*) FILTER(WHERE channels>=5) AS clusters_ge5_channels,sum(n) FILTER(WHERE channels>=5) AS events_in_ge5_clusters,
                count(*) FILTER(WHERE channels>=10) AS clusters_ge10_channels,sum(n) FILTER(WHERE channels>=10) AS events_in_ge10_clusters,
                max(channels) AS largest_channel_cluster,max(sections) AS largest_section_cluster,
                count(*) FILTER(WHERE last_onset-onset>INTERVAL 24 HOUR) AS transitive_clusters_spanning_gt24h FROM collector_clusters''')[0]
            result['collector_cooccurrence_sensitivity'].append(dict(chained_gap_minutes=minutes, **cluster))
        result['collector_cooccurrence'] = result['collector_cooccurrence_sensitivity'][-1]
        result['concentration'] = rows('''WITH s AS (SELECT section_id,count(*) AS n FROM events GROUP BY 1),
            ranked AS(SELECT *,row_number() OVER(ORDER BY n DESC,section_id) AS rank FROM s)
            SELECT count(*) AS sections_with_events,sum(n) AS events,
            sum(n) FILTER(WHERE rank<=10) AS events_top10_sections,
            sum(n) FILTER(WHERE rank<=50) AS events_top50_sections FROM ranked''')[0]
        result['first_observed_left_censor'] = rows('''SELECT count(*) FILTER(WHERE NOT e.channel_is_repeat) AS first_channel_labels,
            count(*) FILTER(WHERE NOT e.channel_is_repeat AND EXISTS(SELECT 1 FROM episodes p
                WHERE p.ch=e.channel_id AND p.t_start<e.t_start AND p.dur_h>1)) AS first_channel_labels_with_earlier_qualified_history
            FROM events e''')[0]
        result['first_observed_left_censor'].update(rows('''SELECT
            count(*) FILTER(WHERE NOT e.is_repeat) AS first_section_event_labels,
            count(*) FILTER(WHERE NOT e.is_repeat AND EXISTS(SELECT 1 FROM episodes p
                JOIN mapping m ON m.ch=p.ch WHERE m.section_id=e.section_id
                AND p.t_start<e.t_start AND p.dur_h>1)) AS first_section_labels_with_earlier_qualified_history
            FROM events e''')[0])
        assert sum(x['events'] for x in result['closing_values']) == result['event_integrity']['events']
        assert result['duration_sensitivity'][0]['events'] == result['event_integrity']['events']
        assert result['collector_cooccurrence']['channel_events'] == result['event_integrity']['events']
        assert all(x['original_events'] == result['event_integrity']['events'] for x in result['grouping_sensitivity'])
        result['sources'] = {n: {'path':str(p.relative_to(root)), 'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for n,p in sources.items()}
        return result


def verify_flag_fix(root):
    """Rebuild from real source inputs; compare every accepted row and field."""
    import pandas as pd
    from ml.local24_data import Local24Data, CUTOFF, FEATURE_NAMES

    class BoundedBuilder(Local24Data):
        def _prepare(self):
            self.con.execute("SET threads=3; SET memory_limit='6GB'")
            super()._prepare()

    root = Path(root)
    base = root / 'data/03_processed/local24_20260923'
    builder = BoundedBuilder(pd.read_parquet(base/'mapping.parquet'),
        root/'data/03_processed/ml_20260915/chanday.parquet',
        root/'data/03_processed/faildef_20260915/D5/episodes_ext.parquet')
    try:
        c = builder.riskset(CUTOFF.replace(hour=0, minute=0, second=0))
        old = str(base/'events.parquet').replace("'", "''")
        c.execute(f"CREATE VIEW old_events AS SELECT * FROM read_parquet('{old}')")
        event_difference = c.execute("""SELECT count(*) FROM (
            (SELECT * EXCLUDE(is_repeat,channel_is_repeat) FROM events EXCEPT ALL
             SELECT * EXCLUDE(is_repeat,channel_is_repeat) FROM old_events)
            UNION ALL
            (SELECT * EXCLUDE(is_repeat,channel_is_repeat) FROM old_events EXCEPT ALL
             SELECT * EXCLUDE(is_repeat,channel_is_repeat) FROM events))""").fetchone()[0]
        changes = c.execute("""SELECT count(*),
            count(*) FILTER(WHERE e.is_repeat IS DISTINCT FROM o.is_repeat),
            count(*) FILTER(WHERE e.channel_is_repeat IS DISTINCT FROM o.channel_is_repeat)
            FROM events e JOIN old_events o USING(event_id)""").fetchone()
        old = str(base/'dataset.parquet').replace("'", "''")
        c.execute(f"CREATE VIEW old_dataset AS SELECT * FROM read_parquet('{old}')")
        c.execute("""CREATE VIEW rebuilt_dataset AS SELECT * FROM riskset
            WHERE exposed AND NOT ongoing_d5 AND past_covered AND target_covered""")
        comparisons = ' OR '.join(f'r.{k} IS DISTINCT FROM d.{k}'
            for k in ['collector_id','y','n_events'] + FEATURE_NAMES)
        different_rows = c.execute(f"""SELECT count(*) FROM rebuilt_dataset r FULL OUTER JOIN
            old_dataset d USING(section_id,as_of) WHERE r.section_id IS NULL OR
            d.section_id IS NULL OR {comparisons}""").fetchone()[0]
        rows = c.execute('SELECT count(*) FROM rebuilt_dataset').fetchone()[0]
        assert event_difference == different_rows == 0
        return dict(events_compared=changes[0], section_repeat_flags_changed=changes[1],
            channel_repeat_flags_changed=changes[2], event_nonflag_differences=event_difference,
            dataset_rows_compared=rows, dataset_row_or_field_differences=different_rows,
            original_files_unchanged=True)
    finally:
        builder.close()


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--verify-rebuild',action='store_true')
    args=parser.parse_args()
    report=audit(args.root)
    if args.verify_rebuild:
        report['repeat_fix_rebuild'] = verify_flag_fix(args.root)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str)+'\n')
    print(json.dumps({k:report[k] for k in ('event_integrity','coverage_totals','collector_cooccurrence')},indent=2))
