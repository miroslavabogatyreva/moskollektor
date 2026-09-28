-- Очередь датчиков внутри коллектора: какая доля отказов приходится на первые N
-- каналов, если ставить их по числу отказов за 365 суток до момента отказа.
-- Задача MOS-217, вариант «ранг внутри коллектора». Тот же замер для участков
-- сделан 23.09.2026 (docs/for-ml-team.md, «Порядок объезда внутри коллектора»):
-- первые 10 участков — около 6 % коллектора и 22 % отказов.
--
-- Цель — метка модели D5 (contracts/failure.v3.json): четыре значения словаря,
-- эпизод длиннее часа или незакрытый, как в docs/proof/2026-09-22-metrics-d5/export_d5.sql.
-- Отказ окна — эпизод, начавшийся 01.04–30.06.2026, если у того же канала
-- за 60 минут до него не начинался другой эпизод (склейка 60 минут).
--
-- Очередь строится на момент отказа только по прошлому: эпизоды канала
-- за [t − 365 сут, t). Сам отказ в историю не входит. Кандидаты — активные
-- каналы коллектора из smvu.channel_collector. При равенстве отказавший канал
-- ставим в конец (оценка пессимистичная), канал без истории — на последнее место.
-- Случайная очередь дала бы долю отказов, равную доле коллектора.
--
-- Запуск на стенде, только чтение:
--   docker exec -i moskollektor-db-1 psql -U moskollektor -d moskollektor \
--     < analysis/sensor_queue.sql

BEGIN READ ONLY;

WITH эпизод AS (
    SELECT e.channel_id, cc.collector_id, e.started_at
      FROM smvu.model_failure_episode e
      JOIN smvu.channel_collector cc USING (channel_id)
     WHERE e.model_version = 'lgbm-v3-bag-2026.09.21'
       AND e.fault_value = ANY (ARRAY['Неисправен', 'Батарея неисправна',
                                      'Много неисправных устройств', 'Не определено'])
       AND (e.ended_at IS NULL OR e.ended_at - e.started_at > make_interval(secs => 3600))
),
отказ AS (
    SELECT channel_id, collector_id, started_at AS t
      FROM (SELECT эпизод.*,
                   lag(started_at) OVER (PARTITION BY channel_id ORDER BY started_at) AS пред
              FROM эпизод) x
     WHERE started_at >= '2026-04-01 00:00:00+03' AND started_at < '2026-07-01 00:00:00+03'
       AND (пред IS NULL OR started_at - пред >= interval '60 minutes')
),
история AS (
    SELECT о.channel_id AS отказ_канал, о.t, э.channel_id, count(*) AS n
      FROM отказ о
      JOIN эпизод э ON э.collector_id = о.collector_id
                   AND э.started_at >= о.t - interval '365 days'
                   AND э.started_at < о.t
     GROUP BY 1, 2, 3
),
размер AS (
    SELECT collector_id, count(*) AS каналов FROM smvu.channel_collector GROUP BY 1
),
ранг AS (
    -- Место отказавшего канала: сколько каналов коллектора отказывали не реже
    -- него, считая его самого. Равные встают впереди — это и есть пессимизм.
    SELECT отказ_канал, t, count(*) FILTER (WHERE n >= свой) AS место
      FROM (SELECT и.*,
                   max(n) FILTER (WHERE channel_id = отказ_канал)
                          OVER (PARTITION BY отказ_канал, t) AS свой
              FROM история и) x
     WHERE свой IS NOT NULL
     GROUP BY 1, 2
),
место AS (
    SELECT о.channel_id, о.t, р.каналов,
           coalesce(c.sensor_kind, '(без типа)') AS тип,
           г.место IS NULL                        AS без_истории,
           coalesce(г.место, р.каналов)           AS место
      FROM отказ о
      JOIN размер р USING (collector_id)
      JOIN smvu.channel c USING (channel_id)
      LEFT JOIN ранг г ON г.отказ_канал = о.channel_id AND г.t = о.t
)
SELECT CASE WHEN grouping(тип) = 1 THEN 'все типы' ELSE тип END AS тип,
       N.n                                                     AS первые_n,
       count(*)                                                AS отказов,
       count(*) FILTER (WHERE место <= N.n)                    AS в_первых_n,
       round(avg((место <= N.n)::int), 3)                      AS доля_отказов,
       round(avg(least(N.n, каналов)::numeric / каналов), 4)   AS доля_коллектора,
       round(avg(без_истории::int), 3)                         AS без_истории
  FROM место CROSS JOIN (VALUES (1), (3), (10), (30)) AS N(n)
 GROUP BY GROUPING SETS ((N.n), (тип, N.n))
HAVING grouping(тип) = 1 OR count(*) >= 20
 ORDER BY grouping(тип) DESC, count(*) DESC, 1, 2;

ROLLBACK;
