-- 038. Вес участка и карточка участка — на эпизодах модели D5, а не на экранных.
-- Задача 3.17 плана, MOS-153. Продолжение 032_section_weight_window.sql (MOS-159).
--
-- ЗАЧЕМ. До этой миграции проект считал отказ тремя способами. На проверочном окне
-- 01.04–30.06.2026 (замер на стенде 22.09.2026):
--   экранный smvu.fault_episode, закрытый, длиннее часа, любое значение — 923 эпизода,
--     из них «Неисправен» 587 и «Неопределен» 336. На нём стояли вес участка
--     (pred.section_weight) и карточка участка (GET /api/objects/{id}/channels);
--   узкий — только «Неисправен» — 587, справочная строка code/check_metrics.py;
--   D5 модели v3 (contracts/failure.v3.json, smvu.model_failure_episode) — 688:
--     «Неисправен» 637, «Много неисправных устройств» 50, «Батарея неисправна» 1.
-- Модель учится на D5, М-18…М-20 меряются на D5, а вес, которым worker выбирает
-- худшие участки коллектора для заявки (035), стоял на экранном определении.
-- Решение оркестратора 22.09.2026: одно определение — D5.
--
-- ОДНО МЕСТО ДЛЯ ПРЕДИКАТА. smvu.model_failure_event — эпизоды, которые модель
-- считает отказом: значение из smvu.model_failure_value той же версии модели,
-- длиннее min_duration_seconds строго, открытый эпизод считается (так в контракте:
-- "open_episode": "counted"). Словарь и порог берутся из таблицы 030, а не пишутся
-- числами здесь: что таблица совпадает с contracts/failure.v3.json, проверяет
-- code/tests/test_model_failure.py. code/model_failure.py:ЭПИЗОДЫ_МОДЕЛИ берёт
-- те же значения из самого контракта — это второе чтение того же файла, а не второе
-- определение.
--
-- ЧТО ИЗМЕНИТСЯ В ЧИСЛАХ. В окне веса 2022-04-01 … 2026-03-31 у участков из свёртки
-- эпизодов D5 3 675 на 679 участках вместо 10 726 экранных на 1 105; участков
-- с отказами только вне окна (excluded_cnt > 0, episodes_cnt = 0) — 342 вместо 278.
-- Две трети экранных эпизодов построены на «Неопределен», которого в D5 нет.
--
-- ЧЕГО НЕ ДЕЛАЕМ. smvu.fault_episode и его построитель остаются как есть: после
-- этой миграции таблицу читает только backend/app/worker/features.py на пути
-- заглушки (у v3 прогноз приходит готовым из score.json). Колонки представления
-- те же, что в 028 и 032, поэтому CREATE OR REPLACE VIEW, и читатели
-- (backend/app/worker/publish.py, backend/app/domain/order_rules.py,
-- code/check_spread.py) правки не требуют.

CREATE VIEW smvu.model_failure_event AS
SELECT e.episode_id, e.channel_id, e.section_id, e.started_at, e.ended_at, e.fault_value
  FROM smvu.model_failure_episode e
  JOIN smvu.model_failure_value v
    ON v.fault_value = e.fault_value AND v.model_version = e.model_version
 WHERE e.ended_at IS NULL
    OR e.ended_at - e.started_at > make_interval(secs => v.min_duration_seconds);

COMMENT ON VIEW smvu.model_failure_event IS
    'Отказ в определении модели v3 (D5): значение и порог из smvu.model_failure_value, открытый эпизод считается. Одно определение для веса участка, карточки участка и метрик М-18…М-20 (MOS-153)';

CREATE OR REPLACE VIEW pred.section_weight AS
WITH nastroiki AS (
    SELECT coalesce(max(value) FILTER (WHERE key = 'forecast_weight_alpha'), 1.0) AS alpha
      FROM ref.app_setting
), uchastki AS (
    SELECT so.section_id, so.object_id
      FROM pred.section_object so
     WHERE EXISTS (SELECT 1 FROM feat.section_daily d WHERE d.section_id = so.section_id)
), epizody AS (
    SELECT e.section_id,
           count(*) FILTER (WHERE timezone('Europe/Moscow', e.started_at)::date
                                  BETWEEN w.date_from AND w.date_to) AS episodes_cnt,
           count(*) FILTER (WHERE timezone('Europe/Moscow', e.started_at)::date
                                  NOT BETWEEN w.date_from AND w.date_to) AS excluded_cnt
      FROM smvu.model_failure_event e CROSS JOIN pred.weight_window() w
     WHERE e.section_id IS NOT NULL
     GROUP BY e.section_id
), po_obiektu AS (
    SELECT u.object_id,
           count(*) AS sections_cnt,
           sum(coalesce(ep.episodes_cnt, 0)) AS object_episodes
      FROM uchastki u LEFT JOIN epizody ep ON ep.section_id = u.section_id
     GROUP BY u.object_id
)
SELECT u.section_id,
       u.object_id,
       coalesce(ep.episodes_cnt, 0)::bigint AS episodes_cnt,
       coalesce(ep.excluded_cnt, 0)::bigint AS excluded_cnt,
       o.sections_cnt,
       -- Формула та же, что в 028: сглаживание Лапласа, сумма внутри объекта — 1.
       ((coalesce(ep.episodes_cnt, 0) + n.alpha)
        / (o.object_episodes + n.alpha * o.sections_cnt))::numeric AS weight
  FROM uchastki u
  CROSS JOIN nastroiki n
  JOIN po_obiektu o ON o.object_id = u.object_id
  LEFT JOIN epizody ep ON ep.section_id = u.section_id;

COMMENT ON VIEW pred.section_weight IS
    'Доля вероятности объекта, приходящаяся на участок: (отказы D5 в окне pred.weight_window()+alpha)/(отказы объекта+alpha*N). Отказ — smvu.model_failure_event (038, MOS-153). Сумма weight внутри object_id равна 1 тождественно. excluded_cnt — эпизоды D5 вне окна. Только участки, попавшие в feat.section_daily';
COMMENT ON COLUMN pred.section_weight.excluded_cnt IS
    'Эпизоды D5 участка вне окна pred.weight_window() (до и после): отличает «отказы были, но вне периода модели» от «не отказывал»';

-- Самопроверка накатом. На пустой базе проверять нечего. На залитой — эпизоды
-- модели есть, значит представление обязано их видеть, а вес — сходиться в единицу:
-- представление, которое молча отдаёт ноль отказов, делает вес ровным 1/N у всех
-- участков и проходит любую проверку «накатилось без ошибок».
DO $$
DECLARE эпизодов bigint; отказов bigint; худшее numeric;
BEGIN
  SELECT count(*) INTO эпизодов FROM smvu.model_failure_episode;
  IF эпизодов = 0 THEN
    RAISE NOTICE '038: smvu.model_failure_episode пуста, вес проверит первый накат после заливки';
    RETURN;
  END IF;
  SELECT count(*) INTO отказов FROM smvu.model_failure_event;
  ASSERT отказов > 0, format(
    'эпизодов модели %s, а smvu.model_failure_event видит 0 — версия модели или словарь '
    'в smvu.model_failure_value разошлись с эпизодами', эпизодов);
  SELECT max(abs(s - 1)) INTO худшее
    FROM (SELECT sum(weight) AS s FROM pred.section_weight GROUP BY object_id) q;
  ASSERT coalesce(худшее, 0) < 1e-9, format('сумма весов объекта уехала на %s', худшее);
  RAISE NOTICE '038: эпизодов модели %, из них отказов D5 %, сумма весов сходится (отклонение %)',
    эпизодов, отказов, coalesce(худшее, 0);
END $$;
