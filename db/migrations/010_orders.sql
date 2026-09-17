-- 010. Модуль автозаявок: реестр объектов ТОиР и одна заявка на объект в сутки.
-- Задачи Q6.1 (MOS-56) и Q6.3 (MOS-58), приёмка М-09…М-13.
--
-- ЧТО ЗДЕСЬ НЕ ДЕЛАЕТСЯ И ПОЧЕМУ. Задача Q6.1 просила завести в maint.notification
-- колонки forecast_id и due_at и внешний ключ на pred.forecast. Заводить нечего:
-- forecast_id и due_at лежат в 001_assets.sql (строки 632 и 639), ключ
-- notif_forecast_fk стоит в 005_xref.sql:119, индекс ix_notif_forecast —
-- в 001_assets.sql:641. Проверено 17.09.2026 на стенде 135.106.216.101 запросом
-- к information_schema.columns и pg_constraint.
--
-- ЧЕГО НЕ ХВАТАЛО НА САМОМ ДЕЛЕ. Заявку нельзя было завести физически. У
-- maint.notification стоит CHECK (func_location_id IS NOT NULL OR equipment_id
-- IS NOT NULL), а реестра объектов ТОиР в базе нет вовсе: 17.09.2026 на стенде
-- asset.func_location — 0 строк, asset.equipment — 0, ref.object_xref.func_location_id
-- заполнен у 0 из 3 173 участков, ref.floc_type, ref.floc_structure, ref.district,
-- ref.criticality, ref.order_type, ref.activity_type и ref.priority — все по 0.
--
-- РЕЕСТР СИНТЕТИЧЕСКИЙ, И ЭТО СОГЛАСОВАННОЕ РЕШЕНИЕ, А НЕ ОБХОД. Своего реестра
-- заказчик не даст: вопрос Ф-84 он 17.09.2026 закрыл словами «Нет, они несут
-- второстепенную роль», а по недостающим источникам ответил «для синтетических
-- макетов достаточно симуляции» (docs/meetings/2026-09-17-эксперты.md). Поэтому
-- дерево технических мест выводится из единственного, что у нас есть, —
-- из ref.object_xref.smvu_key вида «коллектор:пикет». Связь восстанавливается
-- из ключа детерминированно, ручной таблицы соответствия нет.
--
-- ПОВТОРНЫЙ ЗАПУСК БЕЗОПАСЕН. Все вставки идут с ON CONFLICT DO NOTHING, все
-- обновления — по совпадению кода. Файл можно прогнать руками ещё раз:
--   docker compose exec -T db psql -U moskollektor -d moskollektor \
--       -f /dev/stdin < db/migrations/010_orders.sql
--
-- И ЭТО НЕ ПРИДИРКА, А НЕОБХОДИМОСТЬ. Накатчик backend/app/migrate.py выполняет
-- файл один раз, а часть 2 читает ref.object_xref — таблицу, которую наполняет
-- не миграция, а заливка выгрузки (backend/app/ingest/tag_to_section.py).
-- На ЧИСТОЙ базе, где миграции идут до заливки, эта часть вставит ноль строк
-- и промолчит. Признак такой базы — расхождение пары чисел «участков в
-- object_xref» и «участков с func_location_id»; его ловит самопроверка
-- backend/app/domain/order_rules.py, и лечится оно повторным прогоном файла
-- руками после заливки.

-- =====================================================================
-- ЧАСТЬ 1. Справочники ТОиР
-- =====================================================================

-- Район эксплуатации один: в дереве объектов заказчика (smvu.object_tree)
-- первый уровень — единственный узел «Район по эксплуатации».
INSERT INTO ref.district (code, name) VALUES
    ('01', 'Район по эксплуатации')
ON CONFLICT (code) DO NOTHING;

INSERT INTO ref.floc_type (code, name, manual_create_allowed) VALUES
    ('0', 'Узловое ТМ (структура)',              true),
    ('U', 'Участок коллектора (коллектор:пикет)', false)
ON CONFLICT (code) DO NOTHING;

-- Маска четырёхуровневая, а не пятиуровневая из комментария в 001_assets.sql:
-- пятый уровень — функциональное место конкретного объекта, а объектов
-- заказчик не дал. Глубже участка мы не спускаемся.
INSERT INTO ref.floc_structure (code, mask, level_count, level_lengths, separator) VALUES
    ('MK01', 'XX.NN.XNNNN.NNNN', 4, '{2,2,5,4}', '.')
ON CONFLICT (code) DO NOTHING;

-- Класс критичности участка. Смысл каждого кода — в разделе «Критичность»
-- файла docs/order-rules.md; здесь только справочник, раскладывает участки
-- по классам backend/app/domain/order_rules.py.
INSERT INTO ref.criticality (code, name, sort_order, default_strategy) VALUES
    ('A', 'Критичное: пожарная или газовая охрана',        1, 'ppr'),
    ('B', 'Важное: охранная подсистема или диспетчерский контроль', 2, 'fts'),
    ('C', 'Обычное: температура, диагностика, без типа',   3, 'fts')
ON CONFLICT (code) DO NOTHING;

-- Вид заказа ТОиР. Автозаявка всегда превентивная — в этом вся её суть,
-- поэтому CORR заведён не ради модуля прогноза, а чтобы у превентивной работы
-- было с чем контрастировать в интерфейсе.
INSERT INTO ref.order_type (code, name) VALUES
    ('PREV', 'Планово-профилактические работы'),
    ('CORR', 'Аварийно-восстановительные работы')
ON CONFLICT (code) DO NOTHING;

-- Вид работ. Три кода из списка АСПГ2, и различает их выезд: обход — мимо,
-- осмотр — глазами на месте, диагностика — с прибором и с проверкой канала связи.
INSERT INTO ref.activity_type (code, name) VALUES
    ('PCM', 'Диагностика и проверка канала связи'),
    ('PIN', 'Осмотр на месте'),
    ('POC', 'Обход по маршруту')
ON CONFLICT (code) DO NOTHING;

INSERT INTO ref.order_type_activity (order_type_id, activity_type_id)
SELECT o.id, a.id
  FROM ref.order_type o, ref.activity_type a
 WHERE o.code = 'PREV' AND a.code IN ('PCM', 'PIN', 'POC')
ON CONFLICT DO NOTHING;

-- Приоритет. response_hours — нормативный срок реакции; код '1' оставлен
-- аварийным работам и автозаявкам не достаётся никогда: превентивная работа,
-- которую надо начать через 4 часа, называется аварийной.
INSERT INTO ref.priority (code, name, response_hours) VALUES
    ('1', 'Аварийный', 4),
    ('2', 'Высокий',   24),
    ('3', 'Средний',   72),
    ('4', 'Низкий',    168)
ON CONFLICT (code) DO NOTHING;

-- =====================================================================
-- ЧАСТЬ 2. Технические места из ref.object_xref
-- =====================================================================
-- Четыре уровня, каждый следующий ссылается на предыдущий через parent_id:
--   1  МК                   предприятие
--   2  МК.01                район эксплуатации
--   3  МК.01.K0889          коллектор, 30 штук
--   4  МК.01.K0889.0001     участок «коллектор:пикет», 3 173 штуки
-- Номера в коде дополнены нулями слева до четырёх знаков, чтобы коды
-- сортировались как числа: без этого «K0889.0010» встал бы перед «K0889.0002».

INSERT INTO asset.func_location (code, name, floc_type_id, structure_id,
                                 hierarchy_level, district_id, source_system)
SELECT 'МК', 'АО «Москоллектор»', t.id, s.id, 1, d.id, 'migration'
  FROM ref.floc_type t, ref.floc_structure s, ref.district d
 WHERE t.code = '0' AND s.code = 'MK01' AND d.code = '01'
ON CONFLICT (code) DO NOTHING;

INSERT INTO asset.func_location (code, name, floc_type_id, structure_id,
                                 hierarchy_level, parent_id, district_id, source_system)
SELECT 'МК.01', 'Район по эксплуатации', t.id, s.id, 2, p.id, d.id, 'migration'
  FROM ref.floc_type t, ref.floc_structure s, ref.district d, asset.func_location p
 WHERE t.code = '0' AND s.code = 'MK01' AND d.code = '01' AND p.code = 'МК'
ON CONFLICT (code) DO NOTHING;

-- Коллекторы. DISTINCT по первой части ключа: в выгрузке 30 коллекторов
-- на 3 173 участка.
INSERT INTO asset.func_location (code, name, floc_type_id, structure_id,
                                 hierarchy_level, parent_id, district_id, source_system)
SELECT DISTINCT
       'МК.01.K' || lpad(split_part(x.smvu_key, ':', 1), 4, '0'),
       'Коллектор ' || split_part(x.smvu_key, ':', 1),
       t.id, s.id, 3, p.id, d.id, 'migration'
  FROM ref.object_xref x, ref.floc_type t, ref.floc_structure s,
       ref.district d, asset.func_location p
 WHERE x.smvu_key IS NOT NULL
   AND t.code = '0' AND s.code = 'MK01' AND d.code = '01' AND p.code = 'МК.01'
ON CONFLICT (code) DO NOTHING;

-- Участки. Длина 10 метров, а не 100: заказчик назвал размер пикета
-- 17.09.2026 («10 м»), разбор — docs/meetings/2026-09-17-эксперты.md разд. 4.
--
-- Класс критичности берётся из системы, к которой относятся каналы участка.
-- Это классификация самого заказчика (колонка smvu.channel.system_kind из его
-- справочника), а не наша выдумка. Участок без каналов и участок с одними
-- температурными датчиками попадают в класс C — самый терпимый порог.
INSERT INTO asset.func_location (code, name, floc_type_id, structure_id,
                                 hierarchy_level, parent_id, district_id,
                                 criticality_id, picket_from, picket_to, length_m,
                                 inventory_no, source_system)
SELECT 'МК.01.K' || lpad(split_part(x.smvu_key, ':', 1), 4, '0')
            || '.' || lpad(split_part(x.smvu_key, ':', 2), 4, '0'),
       'Коллектор ' || split_part(x.smvu_key, ':', 1)
            || ', пикет ' || split_part(x.smvu_key, ':', 2),
       t.id, s.id, 4, p.id, d.id,
       c.id,
       split_part(x.smvu_key, ':', 2)::integer,
       split_part(x.smvu_key, ':', 2)::integer,
       10.00,
       x.inventory_no,
       'migration'
  FROM ref.object_xref x
  JOIN ref.floc_type t      ON t.code = 'U'
  JOIN ref.floc_structure s ON s.code = 'MK01'
  JOIN ref.district d       ON d.code = '01'
  JOIN asset.func_location p
       ON p.code = 'МК.01.K' || lpad(split_part(x.smvu_key, ':', 1), 4, '0')
  JOIN ref.criticality c
       ON c.code = (SELECT CASE
                        WHEN bool_or(ch.system_kind IN ('Пожарная охрана', 'Газовая охрана'))
                             THEN 'A'
                        WHEN bool_or(ch.system_kind IN ('Охранная подсистема', 'Диспетчерский контроль'))
                             THEN 'B'
                        ELSE 'C' END
                      FROM smvu.channel ch WHERE ch.section_id = x.section_id)
 WHERE x.smvu_key IS NOT NULL
ON CONFLICT (code) DO NOTHING;

-- Обратная связь: участок расчёта знает своё техническое место. Колонка
-- объявлена UNIQUE, поэтому соответствие строго один к одному.
UPDATE ref.object_xref x
   SET func_location_id = f.id
  FROM asset.func_location f
 WHERE f.hierarchy_level = 4
   AND f.code = 'МК.01.K' || lpad(split_part(x.smvu_key, ':', 1), 4, '0')
            || '.' || lpad(split_part(x.smvu_key, ':', 2), 4, '0')
   AND x.func_location_id IS DISTINCT FROM f.id;

-- =====================================================================
-- ЧАСТЬ 3. Одна автозаявка на объект в сутки (Q6.3)
-- =====================================================================
--
-- ПОЧЕМУ УНИКАЛЬНЫЙ ИНДЕКС, А НЕ ПРОВЕРКА В КОДЕ. Расчёт может идти в двух
-- процессах: планировщик по расписанию и рука на сервере. Проверка «а нет ли
-- уже заявки» в Python между SELECT и INSERT оставляет щель, в которую проходит
-- вторая заявка. Уникальный индекс закрывает щель самой базой, а
-- ON CONFLICT DO NOTHING превращает столкновение в тихий пропуск, а не в ошибку.
--
-- СУТКИ МОСКОВСКИЕ, А НЕ UTC. «Одна заявка в сутки» для диспетчера — одна заявка
-- за его рабочий день. В UTC граница суток приходится на 03:00 по Москве,
-- то есть на середину ночной смены: прогон в 02:00 и прогон в 04:00 попали бы
-- в разные сутки и дали бы две заявки на один объект.
--
-- ВЫРАЖЕНИЕ В ИНДЕКСЕ ОБЯЗАНО БЫТЬ IMMUTABLE. Написать reported_at::date нельзя:
-- приведение timestamptz к date зависит от параметра TimeZone сеанса, Постгрес
-- считает его STABLE и в индекс не пускает. А timezone('Europe/Moscow', …)
-- получает зону явным аргументом и объявлена IMMUTABLE — её пустит.
--
-- COALESCE ВОКРУГ ОБОИХ КЛЮЧЕЙ ОБЪЕКТА — не украшение. Объект у заявки задан
-- одной из двух колонок: func_location_id или equipment_id. Наши автозаявки
-- всегда пишут func_location_id, но заявка на единицу оборудования оставит его
-- пустым, а NULL в уникальном индексе не сталкивается ни с чем — и правило
-- «одна в сутки» для таких заявок молча перестало бы работать. Ноль подставлен
-- потому, что ключи обеих таблиц выданы IDENTITY и начинаются с единицы.
--
-- Ручных заявок индекс не касается: у диспетчера есть право завести две заявки
-- на один объект за день, и запрещать ему это модуль прогноза не вправе.

CREATE UNIQUE INDEX IF NOT EXISTS uq_notif_forecast_daily
    ON maint.notification (
        coalesce(func_location_id, 0),
        coalesce(equipment_id, 0),
        (timezone('Europe/Moscow', reported_at)::date))
    WHERE source_system = 'forecast';

COMMENT ON INDEX maint.uq_notif_forecast_daily IS
    'Одна автозаявка на объект в московские сутки. Под этот индекс написан '
    'ON CONFLICT DO NOTHING в backend/app/domain/order_rules.py: правка индекса '
    'без правки INSERT сделает вставку падающей, а не пропускающей';
