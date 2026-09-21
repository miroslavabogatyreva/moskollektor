WITH src AS (
  SELECT DISTINCT r.channel_id ch, r.read_time ts, r.journal_id ev, r.value_text val
    FROM smvu.reading r
   WHERE r.read_time >= TIMESTAMPTZ '2026-03-01 00:00:00+03'
     AND r.read_time <  TIMESTAMPTZ '2026-07-01 00:00:00+03'),
w AS (SELECT ch, ts, ev, val,
             lead(ts) OVER (PARTITION BY ch ORDER BY ts, ev) AS next_ts,
             (val = 'Неисправен') AS b
        FROM src),
w2 AS (SELECT ch, ts, ev, next_ts, b,
              (b AND NOT coalesce(lag(b) OVER (PARTITION BY ch ORDER BY ts, ev), false))::int AS st
         FROM w),
r2 AS (SELECT ch, ts, next_ts, b,
              sum(st) OVER (PARTITION BY ch ORDER BY ts, ev ROWS UNBOUNDED PRECEDING) AS run
         FROM w2),
ep AS (SELECT ch, min(ts) AS t_start, max(next_ts) AS t_end
         FROM r2 WHERE b GROUP BY ch, run),
fail AS (SELECT e.ch, e.t_start,
                coalesce(e.t_end, TIMESTAMPTZ '2026-06-30 23:59:59+03') - e.t_start AS dur
           FROM ep e
          WHERE e.t_start >= TIMESTAMPTZ '2026-04-01 00:00:00+03'
            AND e.t_start <  TIMESTAMPTZ '2026-07-01 00:00:00+03'
            AND e.t_end IS NOT NULL
            AND coalesce(e.t_end, TIMESTAMPTZ '2026-06-30 23:59:59+03') - e.t_start > interval '1 hour'),
keyed AS (SELECT coalesce('obj:'||t2.object_id::text, 'ch:'||f.ch::text) AS grp, f.t_start
            FROM fail f JOIN smvu.channel c ON c.channel_id = f.ch
            LEFT JOIN smvu.object_tree t3 ON t3.object_id = c.object_id
            LEFT JOIN smvu.object_tree t2 ON t2.object_id = t3.parent_id),
kk AS (SELECT grp, t_start, lag(t_start) OVER (PARTITION BY grp ORDER BY t_start) AS prev
         FROM keyed WHERE grp IS NOT NULL)
SELECT 'A наше: Неисправен, закрытые, коллектор, 10 мин' AS вариант,
       (SELECT count(*) FROM fail) AS эпизодов,
       (SELECT count(DISTINCT ch) FROM fail) AS каналов,
       count(*) FILTER (WHERE prev IS NULL OR t_start - prev > interval '10 minutes') AS инцидентов
  FROM kk;
WITH src AS (
  SELECT DISTINCT r.channel_id ch, r.read_time ts, r.journal_id ev, r.value_text val
    FROM smvu.reading r
   WHERE r.read_time >= TIMESTAMPTZ '2026-03-01 00:00:00+03'
     AND r.read_time <  TIMESTAMPTZ '2026-07-01 00:00:00+03'),
w AS (SELECT ch, ts, ev, val,
             lead(ts) OVER (PARTITION BY ch ORDER BY ts, ev) AS next_ts,
             (val IN ('Неисправен','Батарея неисправна','Много неисправных устройств','Не определено')) AS b
        FROM src),
w2 AS (SELECT ch, ts, ev, next_ts, b,
              (b AND NOT coalesce(lag(b) OVER (PARTITION BY ch ORDER BY ts, ev), false))::int AS st
         FROM w),
r2 AS (SELECT ch, ts, next_ts, b,
              sum(st) OVER (PARTITION BY ch ORDER BY ts, ev ROWS UNBOUNDED PRECEDING) AS run
         FROM w2),
ep AS (SELECT ch, min(ts) AS t_start, max(next_ts) AS t_end
         FROM r2 WHERE b GROUP BY ch, run),
fail AS (SELECT e.ch, e.t_start,
                coalesce(e.t_end, TIMESTAMPTZ '2026-06-30 23:59:59+03') - e.t_start AS dur
           FROM ep e
          WHERE e.t_start >= TIMESTAMPTZ '2026-04-01 00:00:00+03'
            AND e.t_start <  TIMESTAMPTZ '2026-07-01 00:00:00+03'
            AND e.t_end IS NOT NULL
            AND coalesce(e.t_end, TIMESTAMPTZ '2026-06-30 23:59:59+03') - e.t_start > interval '1 hour'),
keyed AS (SELECT coalesce('obj:'||t2.object_id::text, 'ch:'||f.ch::text) AS grp, f.t_start
            FROM fail f JOIN smvu.channel c ON c.channel_id = f.ch
            LEFT JOIN smvu.object_tree t3 ON t3.object_id = c.object_id
            LEFT JOIN smvu.object_tree t2 ON t2.object_id = t3.parent_id),
kk AS (SELECT grp, t_start, lag(t_start) OVER (PARTITION BY grp ORDER BY t_start) AS prev
         FROM keyed WHERE grp IS NOT NULL)
SELECT 'B +словарь D5' AS вариант,
       (SELECT count(*) FROM fail) AS эпизодов,
       (SELECT count(DISTINCT ch) FROM fail) AS каналов,
       count(*) FILTER (WHERE prev IS NULL OR t_start - prev > interval '10 minutes') AS инцидентов
  FROM kk;
WITH src AS (
  SELECT DISTINCT r.channel_id ch, r.read_time ts, r.journal_id ev, r.value_text val
    FROM smvu.reading r
   WHERE r.read_time >= TIMESTAMPTZ '2026-03-01 00:00:00+03'
     AND r.read_time <  TIMESTAMPTZ '2026-07-01 00:00:00+03'),
w AS (SELECT ch, ts, ev, val,
             lead(ts) OVER (PARTITION BY ch ORDER BY ts, ev) AS next_ts,
             (val IN ('Неисправен','Батарея неисправна','Много неисправных устройств','Не определено')) AS b
        FROM src),
w2 AS (SELECT ch, ts, ev, next_ts, b,
              (b AND NOT coalesce(lag(b) OVER (PARTITION BY ch ORDER BY ts, ev), false))::int AS st
         FROM w),
r2 AS (SELECT ch, ts, next_ts, b,
              sum(st) OVER (PARTITION BY ch ORDER BY ts, ev ROWS UNBOUNDED PRECEDING) AS run
         FROM w2),
ep AS (SELECT ch, min(ts) AS t_start, max(next_ts) AS t_end
         FROM r2 WHERE b GROUP BY ch, run),
fail AS (SELECT e.ch, e.t_start,
                coalesce(e.t_end, TIMESTAMPTZ '2026-06-30 23:59:59+03') - e.t_start AS dur
           FROM ep e
          WHERE e.t_start >= TIMESTAMPTZ '2026-04-01 00:00:00+03'
            AND e.t_start <  TIMESTAMPTZ '2026-07-01 00:00:00+03'
            AND true
            AND coalesce(e.t_end, TIMESTAMPTZ '2026-06-30 23:59:59+03') - e.t_start > interval '1 hour'),
keyed AS (SELECT coalesce('obj:'||t2.object_id::text, 'ch:'||f.ch::text) AS grp, f.t_start
            FROM fail f JOIN smvu.channel c ON c.channel_id = f.ch
            LEFT JOIN smvu.object_tree t3 ON t3.object_id = c.object_id
            LEFT JOIN smvu.object_tree t2 ON t2.object_id = t3.parent_id),
kk AS (SELECT grp, t_start, lag(t_start) OVER (PARTITION BY grp ORDER BY t_start) AS prev
         FROM keyed WHERE grp IS NOT NULL)
SELECT 'C +незакрытые эпизоды' AS вариант,
       (SELECT count(*) FROM fail) AS эпизодов,
       (SELECT count(DISTINCT ch) FROM fail) AS каналов,
       count(*) FILTER (WHERE prev IS NULL OR t_start - prev > interval '10 minutes') AS инцидентов
  FROM kk;
WITH src AS (
  SELECT DISTINCT r.channel_id ch, r.read_time ts, r.journal_id ev, r.value_text val
    FROM smvu.reading r
   WHERE r.read_time >= TIMESTAMPTZ '2026-03-01 00:00:00+03'
     AND r.read_time <  TIMESTAMPTZ '2026-07-01 00:00:00+03'),
w AS (SELECT ch, ts, ev, val,
             lead(ts) OVER (PARTITION BY ch ORDER BY ts, ev) AS next_ts,
             (val IN ('Неисправен','Батарея неисправна','Много неисправных устройств','Не определено')) AS b
        FROM src),
w2 AS (SELECT ch, ts, ev, next_ts, b,
              (b AND NOT coalesce(lag(b) OVER (PARTITION BY ch ORDER BY ts, ev), false))::int AS st
         FROM w),
r2 AS (SELECT ch, ts, next_ts, b,
              sum(st) OVER (PARTITION BY ch ORDER BY ts, ev ROWS UNBOUNDED PRECEDING) AS run
         FROM w2),
ep AS (SELECT ch, min(ts) AS t_start, max(next_ts) AS t_end
         FROM r2 WHERE b GROUP BY ch, run),
fail AS (SELECT e.ch, e.t_start,
                coalesce(e.t_end, TIMESTAMPTZ '2026-06-30 23:59:59+03') - e.t_start AS dur
           FROM ep e
          WHERE e.t_start >= TIMESTAMPTZ '2026-04-01 00:00:00+03'
            AND e.t_start <  TIMESTAMPTZ '2026-07-01 00:00:00+03'
            AND true
            AND coalesce(e.t_end, TIMESTAMPTZ '2026-06-30 23:59:59+03') - e.t_start > interval '1 hour'),
keyed AS (SELECT nullif(split_part(c.tag,'-',1),'') AS grp, f.t_start
            FROM fail f JOIN smvu.channel c ON c.channel_id = f.ch
            LEFT JOIN smvu.object_tree t3 ON t3.object_id = c.object_id
            LEFT JOIN smvu.object_tree t2 ON t2.object_id = t3.parent_id),
kk AS (SELECT grp, t_start, lag(t_start) OVER (PARTITION BY grp ORDER BY t_start) AS prev
         FROM keyed WHERE grp IS NOT NULL)
SELECT 'D +ключ pfx вместо коллектора' AS вариант,
       (SELECT count(*) FROM fail) AS эпизодов,
       (SELECT count(DISTINCT ch) FROM fail) AS каналов,
       count(*) FILTER (WHERE prev IS NULL OR t_start - prev > interval '10 minutes') AS инцидентов
  FROM kk;
WITH src AS (
  SELECT DISTINCT r.channel_id ch, r.read_time ts, r.journal_id ev, r.value_text val
    FROM smvu.reading r
   WHERE r.read_time >= TIMESTAMPTZ '2026-03-01 00:00:00+03'
     AND r.read_time <  TIMESTAMPTZ '2026-07-01 00:00:00+03'),
w AS (SELECT ch, ts, ev, val,
             lead(ts) OVER (PARTITION BY ch ORDER BY ts, ev) AS next_ts,
             (val IN ('Неисправен','Батарея неисправна','Много неисправных устройств','Не определено')) AS b
        FROM src),
w2 AS (SELECT ch, ts, ev, next_ts, b,
              (b AND NOT coalesce(lag(b) OVER (PARTITION BY ch ORDER BY ts, ev), false))::int AS st
         FROM w),
r2 AS (SELECT ch, ts, next_ts, b,
              sum(st) OVER (PARTITION BY ch ORDER BY ts, ev ROWS UNBOUNDED PRECEDING) AS run
         FROM w2),
ep AS (SELECT ch, min(ts) AS t_start, max(next_ts) AS t_end
         FROM r2 WHERE b GROUP BY ch, run),
fail AS (SELECT e.ch, e.t_start,
                coalesce(e.t_end, TIMESTAMPTZ '2026-06-30 23:59:59+03') - e.t_start AS dur
           FROM ep e
          WHERE e.t_start >= TIMESTAMPTZ '2026-04-01 00:00:00+03'
            AND e.t_start <  TIMESTAMPTZ '2026-07-01 00:00:00+03'
            AND true
            AND coalesce(e.t_end, TIMESTAMPTZ '2026-06-30 23:59:59+03') - e.t_start > interval '1 hour'),
keyed AS (SELECT nullif(split_part(c.tag,'-',1),'') AS grp, f.t_start
            FROM fail f JOIN smvu.channel c ON c.channel_id = f.ch
            LEFT JOIN smvu.object_tree t3 ON t3.object_id = c.object_id
            LEFT JOIN smvu.object_tree t2 ON t2.object_id = t3.parent_id),
kk AS (SELECT grp, t_start, lag(t_start) OVER (PARTITION BY grp ORDER BY t_start) AS prev
         FROM keyed WHERE grp IS NOT NULL)
SELECT 'E его: +окно склейки 60 мин' AS вариант,
       (SELECT count(*) FROM fail) AS эпизодов,
       (SELECT count(DISTINCT ch) FROM fail) AS каналов,
       count(*) FILTER (WHERE prev IS NULL OR t_start - prev > interval '60 minutes') AS инцидентов
  FROM kk;
WITH src AS (
  SELECT DISTINCT r.channel_id ch, r.read_time ts, r.journal_id ev, r.value_text val
    FROM smvu.reading r
   WHERE r.read_time >= TIMESTAMPTZ '2026-03-01 00:00:00+03'
     AND r.read_time <  TIMESTAMPTZ '2026-07-01 00:00:00+03'),
w AS (SELECT ch, ts, ev, val,
             lead(ts) OVER (PARTITION BY ch ORDER BY ts, ev) AS next_ts,
             (val = 'Неисправен') AS b
        FROM src),
w2 AS (SELECT ch, ts, ev, next_ts, b,
              (b AND NOT coalesce(lag(b) OVER (PARTITION BY ch ORDER BY ts, ev), false))::int AS st
         FROM w),
r2 AS (SELECT ch, ts, next_ts, b,
              sum(st) OVER (PARTITION BY ch ORDER BY ts, ev ROWS UNBOUNDED PRECEDING) AS run
         FROM w2),
ep AS (SELECT ch, min(ts) AS t_start, max(next_ts) AS t_end
         FROM r2 WHERE b GROUP BY ch, run),
fail AS (SELECT e.ch, e.t_start,
                coalesce(e.t_end, TIMESTAMPTZ '2026-06-30 23:59:59+03') - e.t_start AS dur
           FROM ep e
          WHERE e.t_start >= TIMESTAMPTZ '2026-04-01 00:00:00+03'
            AND e.t_start <  TIMESTAMPTZ '2026-07-01 00:00:00+03'
            AND true
            AND coalesce(e.t_end, TIMESTAMPTZ '2026-06-30 23:59:59+03') - e.t_start > interval '1 hour'),
keyed AS (SELECT nullif(split_part(c.tag,'-',1),'') AS grp, f.t_start
            FROM fail f JOIN smvu.channel c ON c.channel_id = f.ch
            LEFT JOIN smvu.object_tree t3 ON t3.object_id = c.object_id
            LEFT JOIN smvu.object_tree t2 ON t2.object_id = t3.parent_id),
kk AS (SELECT grp, t_start, lag(t_start) OVER (PARTITION BY grp ORDER BY t_start) AS prev
         FROM keyed WHERE grp IS NOT NULL)
SELECT 'F контроль: Неисправен, незакрытые, pfx, 60 мин' AS вариант,
       (SELECT count(*) FROM fail) AS эпизодов,
       (SELECT count(DISTINCT ch) FROM fail) AS каналов,
       count(*) FILTER (WHERE prev IS NULL OR t_start - prev > interval '60 minutes') AS инцидентов
  FROM kk;
