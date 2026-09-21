SET max_parallel_workers_per_gather = 0;
\timing on
-- эпизоды: на чём построены. Значение отказа берём из первой записи эпизода.
SELECT count(*) AS episodes_total,
       count(*) FILTER (WHERE c.sensor_kind IN ('Тепловой датчик','Датчик температуры')) AS ep_heat_temp
FROM smvu.fault_episode e JOIN smvu.channel c USING (channel_id);
-- разрез по типу датчика
SELECT c.sensor_kind, count(*) AS episodes, count(DISTINCT e.channel_id) AS channels,
       min(e.started_at)::date AS first_ep, max(e.started_at)::date AS last_ep
FROM smvu.fault_episode e JOIN smvu.channel c USING (channel_id)
GROUP BY 1 ORDER BY 2 DESC;
