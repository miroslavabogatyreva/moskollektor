SET max_parallel_workers_per_gather = 0;
\timing on
-- 0.1 два написания: весь архив и окно
SELECT value_text,
       count(*) AS rows_all,
       count(*) FILTER (WHERE read_time >= '2025-07-01' AND read_time < '2026-07-01') AS rows_win,
       count(DISTINCT channel_id) AS ch_all
FROM smvu.reading
WHERE value_text IN ('Неопределен','Не определено','Неисправен')
GROUP BY 1 ORDER BY 1;
