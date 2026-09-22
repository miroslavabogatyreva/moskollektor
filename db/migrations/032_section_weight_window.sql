-- 032. Вес участка — на периоде обучения модели, а не «всё, кроме 2021 года».
-- Задача 3.18 плана, MOS-159. Продолжение 028_section_weight.sql (MOS-150).
--
-- ЗАЧЕМ. pred.section_weight разносит вероятность коллектора по участкам долей
-- отказов участка. Модель v3 училась на 2022-04-01 … 2026-03-31: в репозитории
-- ML это src/ml/config.py (DATE_START = 2022-04-01, разрез train до 2025-12-31
-- и valid 2026-01-01 … 2026-03-31) и autoresearch_v3/prepare_data.py
-- (TRAIN_FROM = 2022-04-01, SEAL_DAY = 2026-03-31). Николай попросил брать для
-- веса тот же период (MOS-74, комментарий 14492). 028 вместо этого выбрасывал
-- один 2021 год и оставлял 2019, 2020 и январь–март 2022, которых у модели нет;
-- до 2022-04 парк другой — там же, в config.py, это названо «граница смены парка».
-- Замер на стенде по smvu.fault_episode с условиями самого представления
-- (участок известен, эпизод закрыт, длиннее часа): всего 24 414 эпизодов
-- на 1 389 участках, без 2021 — 19 810 на 1 354, в окне модели — 10 726 на 1 105.
--
-- ПОЧЕМУ НОВАЯ МИГРАЦИЯ, А НЕ ПРАВКА 028. 028 накатана на стенд 21.09.2026,
-- а backend/app/migrate.py сверяет sha256 накатанного файла и роняет накат —
-- вместе с ним не поднимаются api и worker.
--
-- ОКНО — ДВЕ НАСТРОЙКИ, ОБЕ ГРАНИЦЫ ВКЛЮЧИТЕЛЬНЫ. Колонка ref.app_setting.value
-- числовая, поэтому дата лежит числом ГГГГММДД: 20220401 читается глазами так же,
-- как «2022-04-01», и менять тип колонки ради двух строк не нужно. Правка — тот же
-- PUT /api/settings/{key}; backend/app/api/settings.py не пустит несуществующую
-- дату и «с» позже «по». Дата эпизода — московская, timezone('Europe/Moscow', …),
-- а не по поясу сеанса: так же режет суточная свёртка (022), и ответ не зависит
-- от того, кто и откуда подключился. Верхняя граница — 2026-03-31, а не конец
-- архива: апрель–июнь 2026 у Николая — проверочное окно (test), и отказы оттуда в весе
-- подсказали бы разносу то, что модель на проверке видеть не должна.
--
-- forecast_weight_exclude_year УДАЛЯЕТСЯ, а не остаётся «на всякий случай».
-- Её больше никто не читает: и представление, и карточка участка
-- (backend/app/api/objects.py, GET /api/objects/{id}/channels) переходят на окно.
-- Строка, которую администратор правит в экране настроек и которая ни на что
-- не влияет, хуже отсутствующей. 2021 год лежит вне окна целиком, так что
-- просьба заказчика от 19.09.2026 исключить его выполняется и дальше.
--
-- excluded_cnt ТЕПЕРЬ — ЭПИЗОДЫ ВНЕ ОКНА, с обеих сторон. Участков, у которых
-- отказы есть, но все вне окна, в представлении станет 278 вместо 35; по всем
-- участкам их 284, но 6 участков (559, 564, 567, 570, 574, 579) не попали
-- в feat.section_daily, и представление их не берёт по построению. Для них «не
-- отказывал» и «отказы вне периода модели» дают одинаковую минимальную долю,
-- и карточка участка должна уметь это объяснить — отсюда колонка. Имя и тип
-- колонок те же, что в 028, поэтому CREATE OR REPLACE VIEW, и читатели
-- (backend/app/worker/publish.py, backend/app/domain/order_rules.py,
-- code/check_spread.py) правки не требуют.
--
-- ОТКРЫТЫЙ ВОПРОС, НЕ РЕШЁННЫЙ ЗДЕСЬ НАРОЧНО. Метка модели v3 — не
-- smvu.fault_episode, а словарь D5 (contracts/failure.v3.json, эпизоды
-- в smvu.model_failure_episode из 030). Вес и после этой миграции считается
-- по экранным эпизодам: MOS-133 оставил читателей smvu.fault_episode на месте,
-- а переключение источника веса — отдельное решение с Николаем, не побочный
-- эффект смены периода. Записан в docs/plan.md, строка 3.18.

INSERT INTO ref.app_setting (key, value, unit) VALUES
    ('forecast_weight_window_from', 20220401, 'ГГГГММДД'),
    ('forecast_weight_window_to',   20260331, 'ГГГГММДД');

DELETE FROM ref.app_setting WHERE key = 'forecast_weight_exclude_year';

-- Одна точка чтения окна для представления и карточки участка: если границы
-- прочитать в двух местах по-разному, счётчик карточки и вес участка разойдутся
-- молча — ровно такую ловушку MOS-153 нашла у определения отказа.
-- Умолчания в coalesce те же, что в сиде выше: без строки настройки окно
-- остаётся окном модели, а не превращается в «всё» или «ничего».
CREATE FUNCTION pred.weight_window(OUT date_from date, OUT date_to date)
LANGUAGE sql STABLE AS $$
    SELECT to_date(coalesce(max(value) FILTER (
                       WHERE key = 'forecast_weight_window_from'), 20220401)::bigint::text,
                   'YYYYMMDD'),
           to_date(coalesce(max(value) FILTER (
                       WHERE key = 'forecast_weight_window_to'), 20260331)::bigint::text,
                   'YYYYMMDD')
      FROM ref.app_setting
$$;

COMMENT ON FUNCTION pred.weight_window() IS
    'Окно истории отказов для веса участка и карточки участка: ref.app_setting forecast_weight_window_from/to (ГГГГММДД), обе границы включительны, дата московская';

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
      FROM smvu.fault_episode e CROSS JOIN pred.weight_window() w
     WHERE e.section_id IS NOT NULL
       AND e.ended_at IS NOT NULL
       AND e.ended_at - e.started_at > interval '1 hour'
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
    'Доля вероятности объекта, приходящаяся на участок: (отказы в окне pred.weight_window()+alpha)/(отказы объекта+alpha*N). Сумма weight внутри object_id равна 1 тождественно. excluded_cnt — эпизоды вне окна. Только участки, попавшие в feat.section_daily';
COMMENT ON COLUMN pred.section_weight.excluded_cnt IS
    'Эпизоды участка вне окна pred.weight_window() (до и после): отличает «отказы были, но вне периода модели» от «не отказывал»';
