-- 002_geo.sql — геосхема карты объектов Москоллектора (PostgreSQL 14+ / PostGIS 3+)
--
-- Откуда взято:
--   * реестр объектов с GUID + внешним кодом, пара "полное/краткое имя", разделение слоёв
--     по типу геометрии, статусная раскраска — ГИС-портал «Арктик СПГ 2»
--     (ТЗ разд. 4.3.1.1, ДТЗ табл. 2-10, лист Interfaces справочника);
--   * узлы и рёбра как топологическая модель сети — ZuluGIS 8.0;
--   * таблица объектов без координат — наше добавление, у Арктик СПГ 2 такой проблемы
--     не было (исходником были DWG генпланов, там координаты есть по определению).
--
-- Решения по координатам:
--   Храним всё в EPSG:4326. Тайлы отдаём в 3857 через ST_Transform.
--   Длины и площади считаем через geography (ST_Length(geom::geography)), НЕ по 3857:
--   на широте Москвы 55.75° масштабный коэффициент Web Mercator 1/cos(55.75°) ≈ 1.77,
--   то есть длина участка по проекции завышена почти вдвое. ТЗ Арктик СПГ 2 про это
--   предупреждает прямым текстом, и мы на эти грабли не наступаем.

-- Порядок накатывания: после 001_assets.sql (нужен ref.district), до 003_permits.sql.

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE SCHEMA IF NOT EXISTS geo;

-- ---------------------------------------------------------------------------
-- Справочники
-- ---------------------------------------------------------------------------

-- Тип объекта. Аналог классификатора из листа Interfaces («Водопровод подземный»,
-- «Кабельная линия 10кВ», «Узел диагностики» и ещё 27 позиций), переписанный под
-- коллекторы. Один тип = один тип геометрии, как у них.
CREATE TABLE geo.object_kind (
    code            text PRIMARY KEY,              -- 'collector_section', 'chamber', ...
    name_full       text NOT NULL,                 -- полное имя: в инфоокно и отчёты
    name_short      text NOT NULL,                 -- краткое: подпись на карте
    geom_type       text NOT NULL
        CHECK (geom_type IN ('POINT', 'LINESTRING', 'POLYGON')),
    is_network_node boolean NOT NULL DEFAULT false, -- узел графа сети (камера, вентшахта)
    is_network_edge boolean NOT NULL DEFAULT false  -- ребро графа (участок коллектора)
);

COMMENT ON TABLE geo.object_kind IS
    'Классификатор типов объектов. Плоский список, как у Арктик СПГ 2 — иерархию типов '
    'не заводим, пока её никто не попросил.';

INSERT INTO geo.object_kind (code, name_full, name_short, geom_type, is_network_node, is_network_edge) VALUES
    ('collector_section', 'Участок коллектора',        'Участок',   'LINESTRING', false, true),
    ('chamber',           'Камера коллектора',          'Камера',    'POINT',      true,  false),
    ('vent_shaft',        'Вентиляционная шахта',       'Вентшахта', 'POINT',      true,  false),
    ('hatch',             'Люк',                        'Люк',       'POINT',      true,  false),
    ('pump_station',      'Насосная станция',           'Насосная',  'POINT',      true,  false),
    ('sensor',            'Датчик СМВУ',                'Датчик',    'POINT',      false, false),
    ('district',          'Район эксплуатации',         'Район',     'POLYGON',    false, false),
    ('brigade_zone',      'Зона ответственности бригады','Зона',     'POLYGON',    false, false);

-- Слой карты. Нужен фронту, чтобы построить дерево слоёв и запомнить порядок:
-- ПМИ проверяет не только состав, но и ПОРЯДОК слоёв («Состав и порядок слоёв
-- соответствует русскоязычной версии»), значит порядок — часть требования.
CREATE TABLE geo.map_layer (
    code           text PRIMARY KEY,
    parent_code    text REFERENCES geo.map_layer(code),
    name           text NOT NULL,
    sort_order     int  NOT NULL,
    visible_by_default boolean NOT NULL DEFAULT true,
    kind_filter    text[]                          -- какие object_kind.code попадают в слой
);

INSERT INTO geo.map_layer (code, parent_code, name, sort_order, visible_by_default, kind_filter) VALUES
    ('network',      NULL,      'Сеть коллекторов',           10, true,  NULL),
    ('sections',     'network', 'Участки коллектора',          11, true,  ARRAY['collector_section']),
    ('chambers',     'network', 'Камеры',                      12, true,  ARRAY['chamber']),
    ('vent_shafts',  'network', 'Вентшахты',                   13, true,  ARRAY['vent_shaft']),
    ('hatches',      'network', 'Люки',                        14, false, ARRAY['hatch']),
    ('pumps',        'network', 'Насосные',                    15, true,  ARRAY['pump_station']),
    ('equipment',    NULL,      'Оборудование',                20, true,  NULL),
    ('sensors',      'equipment','Датчики СМВУ',               21, true,  ARRAY['sensor']),
    ('thematic',     NULL,      'Тематические карты (риск)',   30, false, NULL),
    ('zoning',       NULL,      'Зонирование',                 40, false, NULL),
    ('districts',    'zoning',  'Границы районов',             41, false, ARRAY['district']),
    ('brigades',     'zoning',  'Зоны бригад',                 42, false, ARRAY['brigade_zone']);

-- Уровень риска. Шкала из трёх состояний взята у тематической карты «Статус строительства
-- объектов 3-го уровня» (не начато / в процессе / завершено), четвёртое состояние
-- 'no_data' добавлено нами: без него объект без прогноза покрасится как низкорисковый
-- и потеряется.
-- Цвета тут заданы как дефолт, финальную палитру утверждаем в альбоме отчётных форм —
-- у Арктик СПГ 2 цвета тоже не в ТЗ, а в альбоме.
CREATE TABLE geo.risk_level (
    code       text PRIMARY KEY,
    name       text NOT NULL,
    color_hex  text NOT NULL,
    sort_order int  NOT NULL
);

INSERT INTO geo.risk_level (code, name, color_hex, sort_order) VALUES
    ('low',     'Низкий риск',  '#2E7D32', 1),
    ('medium',  'Средний риск', '#F9A825', 2),
    ('high',    'Высокий риск', '#C62828', 3),
    ('no_data', 'Нет данных',   '#9E9E9E', 4);

-- Жизненный цикл объекта. Механизм «зелёный = актуальный, серый = архив/перспектива»
-- из ТЗ Арктик СПГ 2: объект в статусе planned или decommissioned рисуется серым
-- ПОВЕРХ шкалы риска, независимо от скоринга.
CREATE TABLE geo.lifecycle_status (
    code      text PRIMARY KEY,
    name      text NOT NULL,
    is_active boolean NOT NULL    -- false → рисуем серым
);

INSERT INTO geo.lifecycle_status (code, name, is_active) VALUES
    ('planned',        'Планируется',            false),
    ('in_service',     'В эксплуатации',         true),
    ('decommissioned', 'Выведен из эксплуатации', false);

-- ---------------------------------------------------------------------------
-- Реестр пространственных объектов
-- ---------------------------------------------------------------------------

-- Одна таблица на все типы геометрии. У Арктик СПГ 2 шесть слоёв («Объекты уровня 3
-- (точечные)», «(линейные)», «(площадные)» и то же для уровня 2) — это ограничение
-- ArcGIS, там класс объектов не смешивает геометрии. PostGIS такого не требует,
-- поэтому таблица одна, а слои — представления над ней (см. ниже).
CREATE TABLE geo.geo_object (
    object_id     uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    kind_code     text NOT NULL REFERENCES geo.object_kind(code),

    -- Пара имён, как в справочнике объектов Арктик СПГ 2 (лист Sites):
    -- полное идёт в инфоокно и отчёты, краткое — в подпись на карте, где мало места.
    name_full     text NOT NULL,
    name_short    text NOT NULL,

    -- Внешние ключи. У них было два: GUID (внутренний) и код титула (отраслевой).
    -- У нас: object_id и инвентарный номер из реестра оборудования ОЭ.
    inventory_no  text,                          -- номер по реестру ОЭ
    -- Идентификатор в выгрузке СМВУ = smvu.channel.tag, тег инженерной системы
    -- вида «847-11.1.131.2.». Внешнего ключа нет намеренно: реестр объектов грузится
    -- из ОЭ раньше первой выгрузки СМВУ, и ключ в ещё пустой справочник каналов
    -- уронил бы загрузку реестра.
    -- Участок с датчиком связывает ref.object_xref, а не эта колонка.
    smvu_id       text,

    -- «Старое наименование» из листа Sites: как объект назывался в исходных данных.
    -- Без этой колонки мы не сопоставим адрес из выгрузки СМВУ с объектом реестра.
    source_name   text,
    source_address text,                         -- исходный адрес текстом, до геокодирования

    district_code char(2) REFERENCES ref.district(code),  -- район эксплуатации (аналог «Проекта»)
    contractor    text,                          -- подрядная организация (из АРМ-Контроль)

    lifecycle_code text NOT NULL DEFAULT 'in_service' REFERENCES geo.lifecycle_status(code),

    -- Геометрия. Тип контролируется триггером против object_kind.geom_type —
    -- одной CHECK-констрейнтой это не выразить, нужен доступ к справочнику.
    geom          geometry(Geometry, 4326) NOT NULL,

    -- Ссылка во внешнюю систему. Механика сборки URL по шаблону — оттуда же:
    -- у них https://assai.novatek.ru/.../{proj}/ASIT/UOGCF/{title_code},
    -- у нас подставляем district_code и inventory_no в шаблон АРМ-Контроль.
    external_url  text,

    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX geo_object_geom_idx ON geo.geo_object USING gist (geom);
CREATE INDEX geo_object_kind_idx ON geo.geo_object (kind_code);
CREATE INDEX geo_object_district_idx ON geo.geo_object (district_code);
CREATE UNIQUE INDEX geo_object_smvu_idx ON geo.geo_object (smvu_id) WHERE smvu_id IS NOT NULL;

-- Тип геометрии должен совпадать с тем, что объявлено у типа объекта.
-- Иначе «люк» окажется полигоном, и слой точек его не покажет.
CREATE OR REPLACE FUNCTION geo.geo_object_check_geom_type() RETURNS trigger AS $$
DECLARE
    expected text;
BEGIN
    SELECT geom_type INTO expected FROM geo.object_kind WHERE code = NEW.kind_code;
    IF GeometryType(NEW.geom) <> expected THEN
        RAISE EXCEPTION 'Тип геометрии % не совпадает с типом % для kind_code=%',
            GeometryType(NEW.geom), expected, NEW.kind_code;
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER geo_object_check_geom_type_trg
    BEFORE INSERT OR UPDATE ON geo.geo_object
    FOR EACH ROW EXECUTE FUNCTION geo.geo_object_check_geom_type();

-- ---------------------------------------------------------------------------
-- Сетевая топология (модель ZuluGIS: узлы и рёбра)
-- ---------------------------------------------------------------------------

-- «Топологическая модель сети представляет собой граф: узлы — колодцы, источники,
-- задвижки...; рёбра — линейные объекты: кабели, трубопроводы...» (ZuluGIS 8.0).
-- У Арктик СПГ 2 этого нет вообще, объекты там независимы. Нам нужно, чтобы отвечать
-- на «какой участок отсекается при аварии в камере N».
CREATE TABLE geo.network_edge (
    edge_id      uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    object_id    uuid NOT NULL UNIQUE REFERENCES geo.geo_object(object_id) ON DELETE CASCADE,
    from_node_id uuid NOT NULL REFERENCES geo.geo_object(object_id),
    to_node_id   uuid NOT NULL REFERENCES geo.geo_object(object_id),
    picket_from  numeric(10,2),     -- пикет начала участка
    picket_to    numeric(10,2),     -- пикет конца
    CHECK (from_node_id <> to_node_id)
);

CREATE INDEX network_edge_from_idx ON geo.network_edge (from_node_id);
CREATE INDEX network_edge_to_idx   ON geo.network_edge (to_node_id);

COMMENT ON TABLE geo.network_edge IS
    'Рёбра графа сети. object_id указывает на линейный объект в geo_object, '
    'from/to — на точечные объекты-узлы (камеры, вентшахты).';

-- ponytail: связность считаем рекурсивным CTE по этой таблице.
-- Если обходы графа станут горячими — pgRouting, но пока 825 км это тысячи рёбер,
-- рекурсивный CTE справится.

-- ---------------------------------------------------------------------------
-- Риск: результат ML-скоринга, который раскрашивает карту
-- ---------------------------------------------------------------------------

-- Аналог тематической карты по статусу, только вместо стадии стройки — уровень риска.
-- ZuluGIS называет это «тематическая карта по результатам расчётов».
CREATE TABLE geo.object_risk (
    object_id     uuid NOT NULL REFERENCES geo.geo_object(object_id) ON DELETE CASCADE,
    risk_kind     text NOT NULL
        CHECK (risk_kind IN ('sensor_failure', 'fire', 'unauthorized_access', 'wear')),
    risk_code     text NOT NULL REFERENCES geo.risk_level(code),
    score         numeric(5,4),                   -- сырой скоринг модели, 0..1
    horizon_hours int,                            -- горизонт прогноза, целевой >= 24
    calculated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (object_id, risk_kind)
);

CREATE INDEX object_risk_level_idx ON geo.object_risk (risk_kind, risk_code);

-- ---------------------------------------------------------------------------
-- Миграция: объекты, которым не нашли координату
-- ---------------------------------------------------------------------------

-- Наше добавление. В выгрузках СМВУ адрес — это текст, координат нет.
-- Правило: объект без координаты НЕ попадает в geo_object с точкой (0,0) и НЕ исчезает.
-- Он живёт здесь, пока диспетчер не разберёт. Это перенос принципа ТЗ
-- «не допускается потеря данных, размещённых в Системе».
CREATE TABLE geo.geo_unresolved (
    unresolved_id  bigserial PRIMARY KEY,
    source_system  text NOT NULL,                 -- 'smvu', 'ods', 'arm_control', 'oe_registry'
    source_row_ref text,                          -- чем строка идентифицируется в источнике
    raw_address    text NOT NULL,
    smvu_id        text,
    geocoded_point geometry(Point, 4326),         -- что вернул геокодер, если вернул
    geocode_score  numeric(4,3),                  -- качество совпадения, 0..1
    nearest_object_id uuid REFERENCES geo.geo_object(object_id),
    nearest_dist_m numeric(10,2),
    reason         text NOT NULL,                 -- 'no_geocode', 'low_score', 'no_object_within_radius', 'ambiguous'
    resolved_at    timestamptz,
    resolved_by    text,
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX geo_unresolved_open_idx ON geo.geo_unresolved (source_system, reason)
    WHERE resolved_at IS NULL;

-- Привязка геокодированной точки к ближайшему участку коллектора.
-- Радиус 50 м — калибровочная ручка: для плотной застройки в центре его придётся
-- уменьшать, для окраин увеличивать. Значение подбирается по проценту привязки
-- на реальной выгрузке, не по теории.
CREATE OR REPLACE FUNCTION geo.snap_to_network(p_point geometry, p_radius_m numeric DEFAULT 50)
RETURNS TABLE (object_id uuid, dist_m numeric) AS $$
    SELECT o.object_id,
           ST_Distance(o.geom::geography, p_point::geography)::numeric AS dist_m
    FROM geo.geo_object o
    JOIN geo.object_kind k ON k.code = o.kind_code
    WHERE k.is_network_edge
      AND o.lifecycle_code = 'in_service'
      AND ST_DWithin(o.geom::geography, p_point::geography, p_radius_m)
    ORDER BY dist_m
    LIMIT 1;
$$ LANGUAGE sql STABLE;

-- ---------------------------------------------------------------------------
-- Представления: слои карты
-- ---------------------------------------------------------------------------

-- Фронт запрашивает слой по коду, получает GeoJSON. Разделение на точки/линии/полигоны
-- живёт здесь, а не в хранении.
CREATE OR REPLACE VIEW geo.v_map_feature AS
SELECT
    o.object_id,
    o.kind_code,
    l.code            AS layer_code,
    o.name_full,
    o.name_short,
    o.inventory_no,
    o.district_code,
    o.contractor,
    o.lifecycle_code,
    o.external_url,
    -- Цвет: серый для неактивных объектов поверх шкалы риска, иначе цвет уровня риска,
    -- иначе серый «нет данных».
    CASE WHEN NOT s.is_active THEN '#9E9E9E'
         ELSE COALESCE(rl.color_hex, '#9E9E9E') END AS color_hex,
    r.risk_kind,
    r.risk_code,
    r.score,
    o.geom
FROM geo.geo_object o
JOIN geo.object_kind k        ON k.code = o.kind_code
JOIN geo.lifecycle_status s   ON s.code = o.lifecycle_code
JOIN geo.map_layer l          ON o.kind_code = ANY (l.kind_filter)
LEFT JOIN geo.object_risk r   ON r.object_id = o.object_id
LEFT JOIN geo.risk_level rl   ON rl.code = r.risk_code;

-- Быстрый отчёт по выделенной области. Идея прямо из ZuluGIS:
--   «Количество потребителей 23 / Количество колодцев 23 / Протяжённость сетей 1381.64 м».
-- Длина считается через geography, а не по проекции — см. шапку файла.
CREATE OR REPLACE FUNCTION geo.area_summary(p_area geometry(Polygon, 4326))
RETURNS TABLE (
    chambers_cnt   bigint,
    vent_shafts_cnt bigint,
    hatches_cnt    bigint,
    sensors_cnt    bigint,
    network_len_m  numeric,
    high_risk_cnt  bigint
) AS $$
    SELECT
        count(*) FILTER (WHERE o.kind_code = 'chamber'),
        count(*) FILTER (WHERE o.kind_code = 'vent_shaft'),
        count(*) FILTER (WHERE o.kind_code = 'hatch'),
        count(*) FILTER (WHERE o.kind_code = 'sensor'),
        COALESCE(sum(ST_Length(o.geom::geography))
                 FILTER (WHERE o.kind_code = 'collector_section'), 0)::numeric(12,2),
        count(DISTINCT r.object_id) FILTER (WHERE r.risk_code = 'high')
    FROM geo.geo_object o
    LEFT JOIN geo.object_risk r ON r.object_id = o.object_id
    WHERE o.lifecycle_code = 'in_service'
      AND ST_Intersects(o.geom, p_area);
$$ LANGUAGE sql STABLE;
