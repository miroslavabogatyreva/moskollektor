SET max_parallel_workers_per_gather = 0;
SET work_mem = '1GB';
\timing on
WITH w AS (
  -- поток окна плюс сутки хвоста: хвост нужен, чтобы отказ 30.06 вечером
  -- нашёлся для события 30.06 утром. События берём только из окна.
  SELECT channel_id, read_time, journal_id, value_text
  FROM smvu.reading
  WHERE read_time >= '2025-07-01' AND read_time < '2026-07-02'),
m AS (
  SELECT channel_id, read_time, journal_id, value_text,
         (value_text='Неопределен') AS u,
         lead(value_text) OVER wnd AS nxt,
         min(CASE WHEN value_text='Неисправен' THEN read_time END)
           OVER (PARTITION BY channel_id ORDER BY read_time DESC, journal_id DESC
                 ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS next_fault,
         row_number() OVER wnd
       - row_number() OVER (PARTITION BY channel_id, (value_text='Неопределен')
                            ORDER BY read_time, journal_id) AS grp
  FROM w
  WINDOW wnd AS (PARTITION BY channel_id ORDER BY read_time, journal_id)),
lvl1 AS (
  SELECT channel_id, grp, u,
         count(*) AS cnt,
         max(read_time) AS t1,
         (array_agg(nxt        ORDER BY read_time DESC, journal_id DESC))[1] AS closed_by,
         (array_agg(next_fault ORDER BY read_time DESC, journal_id DESC))[1] AS nf_end,
         count(*) FILTER (WHERE next_fault <= read_time + interval '24 hour') AS k_rows
  FROM m
  WHERE read_time < '2026-07-01'                 -- отсекает хвостовые сутки
    AND value_text IS DISTINCT FROM 'Неисправен' -- отсекает сам отказ из базовой группы
  GROUP BY channel_id, grp, u)
SELECT 'A' AS tag, coalesce(c.sensor_kind,'(без типа)') AS a,
       coalesce(l.closed_by,'(конец окна)') AS b,
       count(*)::text AS c, ''::text AS d, ''::text AS e, ''::text AS f
FROM lvl1 l JOIN smvu.channel c USING (channel_id) WHERE l.u
GROUP BY 1,2,3
UNION ALL
SELECT 'B', l.channel_id::text, coalesce(c.sensor_kind,'(без типа)'),
       count(*) FILTER (WHERE l.u)::text,
       count(*) FILTER (WHERE l.u AND l.nf_end <= l.t1 + interval '24 hour')::text,
       coalesce(sum(l.cnt) FILTER (WHERE NOT l.u),0)::text,
       coalesce(sum(l.k_rows) FILTER (WHERE NOT l.u),0)::text
FROM lvl1 l JOIN smvu.channel c USING (channel_id)
GROUP BY 1,2,3;
