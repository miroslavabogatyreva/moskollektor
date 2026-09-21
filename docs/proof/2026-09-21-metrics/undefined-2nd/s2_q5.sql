SET max_parallel_workers_per_gather = 0;
\timing on
WITH p AS (
  SELECT channel_id,
         count(*) FILTER (WHERE value_text='Неопределен') AS und_all,
         count(*) FILTER (WHERE value_text='Неисправен')  AS flt_all
  FROM smvu.reading WHERE value_text IN ('Неопределен','Неисправен')
  GROUP BY channel_id),
park AS (SELECT coalesce(sensor_kind,'(без типа)') AS sk, count(*) AS ch_in_park
         FROM smvu.channel GROUP BY 1),
agg AS (
  SELECT coalesce(c.sensor_kind,'(без типа)') AS sk,
         count(*) FILTER (WHERE p.und_all>0) AS ch_with_und,
         count(*) FILTER (WHERE p.und_all>0 AND p.flt_all=0) AS ch_never,
         sum(p.und_all) FILTER (WHERE p.und_all>0 AND p.flt_all=0) AS und_rows_never
  FROM p JOIN smvu.channel c USING (channel_id) GROUP BY 1)
SELECT a.sk, park.ch_in_park, a.ch_with_und, a.ch_never,
       round(100.0*a.ch_never/nullif(a.ch_with_und,0),1) AS pct_never,
       a.und_rows_never
FROM agg a JOIN park USING (sk) ORDER BY a.ch_with_und DESC;
