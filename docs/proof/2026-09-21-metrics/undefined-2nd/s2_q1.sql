SET max_parallel_workers_per_gather = 0;
\timing on
-- A. по каналам: сколько записей каждого значения, окно и весь архив
WITH p AS (
  SELECT channel_id,
         count(*) FILTER (WHERE value_text='Неопределен') AS und_all,
         count(*) FILTER (WHERE value_text='Неисправен')  AS flt_all,
         count(*) FILTER (WHERE value_text='Неопределен' AND read_time>='2025-07-01' AND read_time<'2026-07-01') AS und_win,
         count(*) FILTER (WHERE value_text='Неисправен'  AND read_time>='2025-07-01' AND read_time<'2026-07-01') AS flt_win
  FROM smvu.reading
  WHERE value_text IN ('Неопределен','Неисправен')
  GROUP BY channel_id)
SELECT
  (SELECT count(*) FROM smvu.channel) AS channels_total,
  count(*) FILTER (WHERE und_all>0) AS ch_und_all,
  count(*) FILTER (WHERE und_all>0 AND flt_all=0) AS ch_und_no_fault_ever,
  sum(und_all) FILTER (WHERE und_all>0 AND flt_all=0) AS und_rows_in_those,
  count(*) FILTER (WHERE und_win>0) AS ch_und_win,
  count(*) FILTER (WHERE und_win>0 AND flt_win=0) AS ch_und_no_fault_win,
  count(*) FILTER (WHERE und_win>=10 AND flt_win>=3) AS ch_candidates
FROM p;
