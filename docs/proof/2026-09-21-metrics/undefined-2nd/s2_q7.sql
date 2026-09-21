SET max_parallel_workers_per_gather = 0;
\timing on
-- «Не определено»: у каких типов, сколько каналов, есть ли у них отказы
SELECT coalesce(c.sensor_kind,'(без типа)') AS sensor_kind,
       count(DISTINCT r.channel_id) AS channels, count(*) AS rows_all,
       count(*) FILTER (WHERE r.read_time>='2025-07-01' AND r.read_time<'2026-07-01') AS rows_win
FROM smvu.reading r JOIN smvu.channel c USING (channel_id)
WHERE r.value_text='Не определено'
GROUP BY 1 ORDER BY 3 DESC;
