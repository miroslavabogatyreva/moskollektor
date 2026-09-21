SET max_parallel_workers_per_gather = 0;
SET work_mem = '1GB';
\timing on
WITH w AS (
  SELECT r.channel_id, r.read_time, r.journal_id, r.value_text
  FROM smvu.reading r
  WHERE r.read_time >= '2025-07-01' AND r.read_time < '2026-07-02'
    AND r.channel_id IN (SELECT channel_id FROM smvu.channel WHERE sensor_kind='Газовый датчик')),
m AS (
  SELECT channel_id, read_time, journal_id, (value_text='Неопределен') AS u,
         min(CASE WHEN value_text='Неисправен' THEN read_time END)
           OVER (PARTITION BY channel_id ORDER BY read_time DESC, journal_id DESC
                 ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS next_fault,
         row_number() OVER (PARTITION BY channel_id ORDER BY read_time, journal_id)
       - row_number() OVER (PARTITION BY channel_id, (value_text='Неопределен')
                            ORDER BY read_time, journal_id) AS grp
  FROM w),
r AS (
  SELECT channel_id, grp, max(read_time) AS t1,
         (array_agg(next_fault ORDER BY read_time DESC, journal_id DESC))[1] AS nf
  FROM m WHERE u AND read_time < '2026-07-01' GROUP BY channel_id, grp)
SELECT count(*) AS runs, count(DISTINCT channel_id) AS channels,
       count(*) FILTER (WHERE nf IS NOT NULL) AS with_next_fault,
       count(DISTINCT channel_id) FILTER (WHERE nf <= t1+interval '24 hour') AS ch_with_hit,
       count(*) FILTER (WHERE nf <= t1+interval '1 min')  AS le_1min,
       count(*) FILTER (WHERE nf <= t1+interval '5 min')  AS le_5min,
       count(*) FILTER (WHERE nf <= t1+interval '1 hour') AS le_1h,
       count(*) FILTER (WHERE nf <= t1+interval '24 hour') AS le_24h,
       percentile_disc(0.10) WITHIN GROUP (ORDER BY extract(epoch FROM nf-t1)) AS p10_sec,
       percentile_disc(0.50) WITHIN GROUP (ORDER BY extract(epoch FROM nf-t1)) AS p50_sec,
       percentile_disc(0.90) WITHIN GROUP (ORDER BY extract(epoch FROM nf-t1)) AS p90_sec
FROM r;
