SET max_parallel_workers_per_gather = 0;
\timing on
-- чем начинается эпизод: берём значение записи журнала в момент started_at
WITH e AS (
  SELECT e.episode_id, e.channel_id, e.started_at, c.sensor_kind
  FROM smvu.fault_episode e JOIN smvu.channel c USING (channel_id)),
v AS (
  SELECT e.*, (SELECT r.value_text FROM smvu.reading r
               WHERE r.channel_id=e.channel_id AND r.read_time=e.started_at
                 AND r.value_text IN ('Неопределен','Неисправен') LIMIT 1) AS start_val
  FROM e)
SELECT coalesce(start_val,'(не нашлось)') AS start_val,
       count(*) AS episodes,
       round(100.0*count(*)/sum(count(*)) OVER (),2) AS pct,
       count(*) FILTER (WHERE sensor_kind IN ('Тепловой датчик','Датчик температуры')) AS heat_temp
FROM v GROUP BY 1 ORDER BY 2 DESC;
