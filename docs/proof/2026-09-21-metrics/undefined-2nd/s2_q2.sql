SET max_parallel_workers_per_gather = 0;
SET work_mem = '1GB';
\timing on
WITH w AS MATERIALIZED (
  SELECT channel_id, read_time, journal_id, value_text
  FROM smvu.reading
  WHERE read_time >= '2025-07-01' AND read_time < '2026-07-01'),
m AS (
  SELECT channel_id, read_time, journal_id,
         (value_text='Неопределен') AS u,
         lead(value_text) OVER (PARTITION BY channel_id ORDER BY read_time, journal_id) AS nxt,
         row_number() OVER (PARTITION BY channel_id ORDER BY read_time, journal_id)
       - row_number() OVER (PARTITION BY channel_id, (value_text='Неопределен') ORDER BY read_time, journal_id) AS grp
  FROM w),
r AS (
  SELECT channel_id, grp, count(*) AS len,
         (array_agg(nxt ORDER BY read_time DESC, journal_id DESC))[1] AS closed_by
  FROM m WHERE u GROUP BY channel_id, grp)
SELECT count(*) AS runs, count(DISTINCT channel_id) AS channels,
       sum(len) AS rows_in_runs,
       percentile_disc(0.5) WITHIN GROUP (ORDER BY len) AS median_len,
       percentile_disc(0.9) WITHIN GROUP (ORDER BY len) AS p90_len,
       max(len) AS max_len
FROM r;
