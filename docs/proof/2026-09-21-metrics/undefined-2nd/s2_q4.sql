SET max_parallel_workers_per_gather = 0;
SET work_mem = '512MB';
\timing on
-- Газовый датчик: промежуток от конца серии «Неопределен» до ближайшего «Неисправен»
WITH ch AS (SELECT channel_id FROM smvu.channel WHERE sensor_kind='Газовый датчик'),
w AS MATERIALIZED (
  SELECT r.channel_id, r.read_time, r.journal_id, r.value_text
  FROM smvu.reading r JOIN ch USING (channel_id)
  WHERE r.read_time >= '2025-07-01' AND r.read_time < '2026-07-01'),
m AS (SELECT channel_id, read_time, journal_id, (value_text='Неопределен') AS u,
        lead(value_text) OVER (PARTITION BY channel_id ORDER BY read_time, journal_id) AS nxt,
        row_number() OVER (PARTITION BY channel_id ORDER BY read_time, journal_id)
      - row_number() OVER (PARTITION BY channel_id, (value_text='Неопределен') ORDER BY read_time, journal_id) AS grp
      FROM w),
r AS (SELECT channel_id, grp, count(*) AS len, max(read_time) AS t1,
        (array_agg(nxt ORDER BY read_time DESC, journal_id DESC))[1] AS closed_by
      FROM m WHERE u GROUP BY channel_id, grp),
g AS (SELECT r.*, (SELECT min(f.read_time) FROM smvu.reading f
                   WHERE f.channel_id=r.channel_id AND f.value_text='Неисправен'
                     AND f.read_time > r.t1 AND f.read_time < '2026-07-02') AS f1
      FROM r)
SELECT count(*) AS runs, count(DISTINCT channel_id) AS channels,
       count(*) FILTER (WHERE f1 IS NOT NULL) AS with_fault_ever,
       count(*) FILTER (WHERE f1 <= t1 + interval '5 min')  AS le_5min,
       count(*) FILTER (WHERE f1 <= t1 + interval '1 hour') AS le_1h,
       count(*) FILTER (WHERE f1 <= t1 + interval '24 hour') AS le_24h,
       percentile_disc(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM f1-t1)) AS median_gap_sec,
       percentile_disc(0.25) WITHIN GROUP (ORDER BY extract(epoch FROM f1-t1)) AS p25_gap_sec,
       percentile_disc(0.75) WITHIN GROUP (ORDER BY extract(epoch FROM f1-t1)) AS p75_gap_sec
FROM g;
SELECT closed_by, count(*) FROM (
  SELECT (array_agg(nxt ORDER BY read_time DESC, journal_id DESC))[1] AS closed_by
  FROM (SELECT channel_id, read_time, journal_id, (value_text='Неопределен') AS u,
          lead(value_text) OVER (PARTITION BY channel_id ORDER BY read_time, journal_id) AS nxt,
          row_number() OVER (PARTITION BY channel_id ORDER BY read_time, journal_id)
        - row_number() OVER (PARTITION BY channel_id, (value_text='Неопределен') ORDER BY read_time, journal_id) AS grp
        FROM (SELECT r.channel_id, r.read_time, r.journal_id, r.value_text
              FROM smvu.reading r JOIN smvu.channel c USING (channel_id)
              WHERE c.sensor_kind='Газовый датчик'
                AND r.read_time >= '2025-07-01' AND r.read_time < '2026-07-01') x) y
  WHERE u GROUP BY channel_id, grp) z
GROUP BY 1 ORDER BY 2 DESC;
