-- История D5 для порядка обхода. Вес не является вероятностью участка.
-- Неоднозначные участки не получают коллектор по большинству каналов.
CREATE OR REPLACE VIEW pred.section_object AS
SELECT c.section_id, min(p.object_id) AS object_id,
       count(*) FILTER (WHERE c.is_active) AS channels_cnt
  FROM smvu.channel c
  JOIN smvu.object_tree n ON n.object_id = c.object_id
  JOIN smvu.object_tree p ON p.object_id = n.parent_id AND p.level = 2
 WHERE c.section_id IS NOT NULL
 GROUP BY c.section_id
 HAVING count(DISTINCT p.object_id) = 1;
COMMENT ON VIEW pred.section_object IS
 'Однозначная привязка участка к реальному коллектору; спорные участки исключены и показаны отдельно на карте';

-- Общий источник для веса и карточки: открытый эпизод учитывается только
-- после подтверждения >1ч. Будущее закрытие не удлиняет историю за окном.
CREATE VIEW pred.weight_failure_episode AS
WITH bounds AS (
 SELECT w.date_from, w.date_to,
        least((w.date_to + 1)::timestamp AT TIME ZONE 'Europe/Moscow',
              (SELECT max(read_time) FROM smvu.reading)) AS cutoff
 FROM pred.weight_window() w
)
SELECT e.channel_id, e.section_id, e.started_at, e.ended_at,
       least(coalesce(e.ended_at,b.cutoff),b.cutoff) AS observed_end,
       (timezone('Europe/Moscow',e.started_at)::date BETWEEN b.date_from AND b.date_to)
          AS in_window
 FROM smvu.model_failure_episode e CROSS JOIN bounds b
 WHERE least(coalesce(e.ended_at,b.cutoff),b.cutoff)-e.started_at > interval '1 hour';

CREATE OR REPLACE VIEW pred.section_weight AS
WITH settings AS (
 SELECT coalesce(max(value) FILTER (WHERE key='forecast_weight_alpha'),1.0) AS alpha
 FROM ref.app_setting
), episodes AS (
 SELECT section_id, count(*) FILTER (WHERE in_window) AS episodes_cnt,
        count(*) FILTER (WHERE NOT in_window) AS excluded_cnt
 FROM pred.weight_failure_episode GROUP BY section_id
), totals AS (
 SELECT s.object_id,count(*) AS sections_cnt,sum(coalesce(e.episodes_cnt,0)) AS total
 FROM pred.section_object s LEFT JOIN episodes e USING(section_id) GROUP BY s.object_id
)
SELECT s.section_id,s.object_id,coalesce(e.episodes_cnt,0)::bigint AS episodes_cnt,
       coalesce(e.excluded_cnt,0)::bigint AS excluded_cnt,t.sections_cnt,
       ((coalesce(e.episodes_cnt,0)+v.alpha)/(t.total+v.alpha*t.sections_cnt))::numeric AS weight
 FROM pred.section_object s JOIN totals t USING(object_id)
 LEFT JOIN episodes e USING(section_id) CROSS JOIN settings v;
COMMENT ON VIEW pred.section_weight IS
 'Приоритет обхода по подтвержденным эпизодам D5 в историческом окне; сглаженная доля, не локальная вероятность отказа';
