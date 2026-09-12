-- =====================================================================
-- Реестр оборудования АО «Москоллектор» — мастер-данные ТОиР
-- PostgreSQL 14+
--
-- Схема собрана по образцу модуля SAP PM (ТОРО) проекта «Арктик СПГ 2»:
--   * техническое место (ТМ / Functional Location) = место, где что-то стоит;
--   * единица оборудования (ЕО / Equipment) = физический предмет, который
--     можно снять и поставить в другое место;
--   * точка измерения / счётчик = меняющийся во времени параметр объекта;
--   * каталоги кодов ТОРО (ISO 14224) = узел / повреждение / причина / работа.
--
-- Источники: «Основные данные модуля SAP ТОРО.xlsx»,
--   ОИ_Создание, изменение и удаление объектов БДО v1.2,
--   Технический проект КСУ ТОиР v2.0 (таблица 20 «Справочники и основные данные»),
--   КСУ ТОиР_Описание настроек v2.4 (встроенные xlsx: виды объектов, каталоги).
--
-- Соглашения:
--   * все справочники в схеме ref, мастер-данные в схеме asset,
--     транзакционные документы в схеме maint, служебное — в схеме load;
--   * код объекта (business key) везде текстовый и уникальный,
--     суррогатный id — bigint GENERATED ALWAYS AS IDENTITY;
--   * историчность ведём отдельными таблицами *_history, а не SCD2 в основной
--     записи — так же, как SAP ведёт историю монтажа в отдельной таблице EQUZ.
-- =====================================================================

CREATE SCHEMA IF NOT EXISTS ref;
CREATE SCHEMA IF NOT EXISTS asset;
CREATE SCHEMA IF NOT EXISTS maint;
CREATE SCHEMA IF NOT EXISTS load;


-- ---------------------------------------------------------------------
-- 1. СПРАВОЧНИКИ НСИ
-- ---------------------------------------------------------------------

-- Район эксплуатации. У «Арктик СПГ 2» это был «Завод расположения
-- технических объектов» (SWERK = 0030, один на всё предприятие).
-- У нас районов много, поэтому справочник настоящий, а не из одной строки.
CREATE TABLE ref.district (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code            char(2)      NOT NULL UNIQUE,  -- '03'
    name            text         NOT NULL,         -- 'Северный район'
    address         text,                          -- адрес РЭУ
    is_active       boolean      NOT NULL DEFAULT true
);
COMMENT ON TABLE  ref.district IS 'Район эксплуатации (аналог SAP «Завод расположения технических объектов», поле SWERK)';
COMMENT ON COLUMN ref.district.code IS 'Двузначный код, второй сегмент кода технического места';

-- Группа планирования ТОиР: кто отвечает за планирование работ по объекту.
-- В SAP через неё резали полномочия: пользователь видит только «свои» ТМ и ЕО.
CREATE TABLE ref.planner_group (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code            varchar(3)   NOT NULL UNIQUE,  -- 'M01'
    name            text         NOT NULL,
    district_id     bigint       REFERENCES ref.district(id)
);
COMMENT ON TABLE ref.planner_group IS 'Группа планирования ТОиР (SAP INGRP). Используется для разграничения полномочий';

-- Рабочее место = бригада или подрядная организация.
-- В SAP разделяется по «виду рабочего места»: бригады исполнителей / подрядчики.
CREATE TABLE ref.work_center (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code            varchar(8)   NOT NULL UNIQUE,  -- 'BR-VENT1'
    name            text         NOT NULL,
    kind            text         NOT NULL CHECK (kind IN ('own_crew', 'contractor')),
    district_id     bigint       REFERENCES ref.district(id),
    responsible_fio text
);
COMMENT ON TABLE ref.work_center IS 'Рабочее место ТОиР: бригада исполнителей или подрядная организация (SAP ARBPL)';
COMMENT ON COLUMN ref.work_center.kind IS 'own_crew = собственная бригада, contractor = подрядчик';

-- Вид технического объекта (SAP EQART). Главный функциональный классификатор.
-- В «Арктик СПГ 2» это 329 четырёхбуквенных кодов, первые две буквы — семейство:
--   DE* = детекторы (DESM smoke, DEGD gas, DETD temperature, DEFI fire),
--   PU* = насосы (PUCE центробежный, PUDI мембранный),
--   AT* = вентиляция/HVAC (ATFA вентилятор приточно-вытяжной, ATSE дымоудаление),
--   FF* = пожаротушение, TX* = преобразователи, SW* = датчики-реле, VA* = арматура.
CREATE TABLE ref.object_kind (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code            varchar(4)   NOT NULL UNIQUE,  -- 'DESM'
    family_code     varchar(2)   NOT NULL,         -- 'DE'
    name_ru         text         NOT NULL,         -- 'Датчик дымовой'
    name_en         text,                          -- 'DET.EQ.SMOKE DETECT'
    -- группа кодов каталога повреждений по умолчанию для этого вида объекта
    default_damage_group varchar(8),
    is_active       boolean      NOT NULL DEFAULT true
);
COMMENT ON TABLE  ref.object_kind IS 'Вид технического объекта — функциональный классификатор ТМ и ЕО (SAP EQART)';
COMMENT ON COLUMN ref.object_kind.family_code IS 'Две первые буквы кода = семейство: DE детекторы, PU насосы, AT вентиляция, FF пожаротушение';
COMMENT ON COLUMN ref.object_kind.default_damage_group IS 'Группа кодов повреждений, подставляемая при регистрации дефекта (ref.catalog_code.code_group)';

-- Тип технического места (SAP FLTYP). Определяет, какие поля обязательны
-- и кто имеет право создавать запись.
-- У «Арктик СПГ 2»: 0 — узловые ТМ (руками), 1..4 — ТМ-теги (только из EDW).
CREATE TABLE ref.floc_type (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code            char(1)      NOT NULL UNIQUE,  -- '0'
    name            text         NOT NULL,         -- 'Узловое ТМ (структура)'
    manual_create_allowed boolean NOT NULL DEFAULT true
);
COMMENT ON TABLE  ref.floc_type IS 'Тип технического места (SAP FLTYP): определяет обязательные поля и способ создания';
COMMENT ON COLUMN ref.floc_type.manual_create_allowed IS 'false = запись создаётся только интеграцией (в АСПГ2 так были закрыты ТМ-теги из EDW)';

-- Тип единицы оборудования (SAP EQTYP). Режет диапазоны номеров ЕО
-- и набор обязательных полей.
CREATE TABLE ref.equipment_type (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code            char(1)      NOT NULL UNIQUE,  -- 'M'
    name            text         NOT NULL,         -- 'Оборудование инженерных коллекторов'
    number_range_from bigint     NOT NULL,         -- 100000000
    number_range_to   bigint     NOT NULL          -- 199999999
);
COMMENT ON TABLE ref.equipment_type IS 'Тип единицы оборудования (SAP EQTYP): диапазон номеров + обязательные поля';

-- Код ABC = степень критичности технического объекта (SAP ABCKZ).
-- В АСПГ2 это был отдельный справочник-настройка, значение 'I' в примерах.
-- Критичность там считали матрицей вероятность x серьёзность (процесс PM.01.02.005).
CREATE TABLE ref.criticality (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code            char(1)      NOT NULL UNIQUE,  -- 'A'
    name            text         NOT NULL,         -- 'Критичное'
    sort_order       smallint    NOT NULL,
    -- стратегия ТОиР по умолчанию для этого класса критичности:
    -- ППР / ФТС (по фактическому техническому состоянию) / до отказа
    default_strategy text        CHECK (default_strategy IN ('ppr', 'fts', 'run_to_failure'))
);
COMMENT ON TABLE  ref.criticality IS 'Код ABC — степень критичности технического объекта (SAP ABCKZ)';
COMMENT ON COLUMN ref.criticality.default_strategy IS 'Стратегия по PM.01.02.006: ППР, по фактическому состоянию (ФТС) или до отказа';

-- Местоположение (SAP STORT). В АСПГ2 это был плоский список из 301 строки
-- вида «0-HVC-001A | Венткамера № 1» в привязке к заводу.
CREATE TABLE ref.location (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    district_id     bigint       NOT NULL REFERENCES ref.district(id),
    code            varchar(20)  NOT NULL,         -- 'K127-KAM-0415'
    name            text         NOT NULL,         -- 'Камера К-127/415'
    lat             numeric(9,6),
    lon             numeric(9,6),
    UNIQUE (district_id, code)
);
COMMENT ON TABLE  ref.location IS 'Местоположение технического объекта (SAP STORT). Дополнено координатами — нужны для карты диспетчера';

-- Изготовитель. В SAP это было просто текстовое поле HERST, но мы уже знаем,
-- что «ООО Электродвигатель-НК» и «ООО "Электродвигатель-НК"» дадут дубли,
-- поэтому выносим в справочник. Правила записи — из Методики ведения
-- справочника «Номенклатура», п. «по производителю».
CREATE TABLE ref.manufacturer (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name            text         NOT NULL UNIQUE,  -- 'ООО "Электродвигатель-НК", Нижнекамск'
    country_iso2    char(2)      NOT NULL DEFAULT 'RU',
    -- ссылка на эталон, если запись признана дублем (термин «Эталон» из Методики)
    master_id       bigint       REFERENCES ref.manufacturer(id)
);
COMMENT ON COLUMN ref.manufacturer.master_id IS 'Дубль ссылается на эталонную позицию — механизм из Методики ведения справочника «Номенклатура»';

-- Приоритет сообщения/заказа ТОРО (SAP PRIOK).
CREATE TABLE ref.priority (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code            char(1)      NOT NULL UNIQUE,  -- '1'
    name            text         NOT NULL,         -- 'Аварийный'
    response_hours  integer      NOT NULL          -- нормативный срок реакции
);

-- Вид заказа ТОРО (SAP AUART). У АСПГ2: CORR, PREV, IMOW, PRDM, REFB.
CREATE TABLE ref.order_type (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code            varchar(4)   NOT NULL UNIQUE,  -- 'PREV'
    name            text         NOT NULL          -- 'Планово-профилактические работы'
);

-- Вид работ ТОиР (SAP ILART). У АСПГ2 14 значений: POC обходы, PIN осмотр,
-- PCA калибровка, PCM диагностика, PSE ТО, RMD текущий ремонт, OVH капремонт и т.д.
CREATE TABLE ref.activity_type (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code            varchar(3)   NOT NULL UNIQUE,  -- 'POC'
    name            text         NOT NULL          -- 'Проверки/Обходы'
);

-- Допустимые пары «вид заказа — вид работ». В АСПГ2 это отдельная таблица
-- настройки на 99 строк: не каждый вид работ разрешён в каждом виде заказа.
CREATE TABLE ref.order_type_activity (
    order_type_id    bigint NOT NULL REFERENCES ref.order_type(id),
    activity_type_id bigint NOT NULL REFERENCES ref.activity_type(id),
    PRIMARY KEY (order_type_id, activity_type_id)
);

-- Каталоги кодов ТОРО. В SAP это четыре каталога по ISO 14224:
--   B (в АСПГ2 буква R) — узел объекта (object part),
--   C (буква S) — вид повреждения (damage),
--   5 (буква T) — причина (cause),
--   A — выполненное мероприятие (activity),
--   U — режим отказа (failure mode).
-- Ведём все в одной таблице: каталог -> группа кодов -> код.
CREATE TABLE ref.catalog_code (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    catalog         char(1)      NOT NULL CHECK (catalog IN ('B','C','5','A','U')),
    code_group      varchar(10)  NOT NULL,         -- 'DGFSMOKD'
    group_name      text         NOT NULL,         -- 'Датчик дымовой'
    code            varchar(6)   NOT NULL,         -- 'DCON'
    code_name       text         NOT NULL,         -- 'Загрязнение'
    is_active       boolean      NOT NULL DEFAULT true,
    UNIQUE (catalog, code_group, code)
);
COMMENT ON TABLE  ref.catalog_code IS 'Каталоги кодов ТОРО (ISO 14224): B узел, C повреждение, 5 причина, A мероприятие, U режим отказа';
COMMENT ON COLUMN ref.catalog_code.catalog IS 'B=узел объекта, C=вид повреждения, 5=причина отказа, A=выполненное мероприятие, U=режим отказа';

-- Профиль каталога: какие группы кодов разрешены для конкретного вида объекта.
-- В АСПГ2 таблица «Catalog profiles» на 9280 строк отвечала ровно на этот вопрос:
-- если объект — дымовой датчик, показывай только 10 кодов повреждений, а не 1237.
CREATE TABLE ref.catalog_profile (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code            varchar(8)   NOT NULL,         -- 'SD001'
    name            text         NOT NULL,         -- 'Датчики дымовые — все типы'
    catalog         char(1)      NOT NULL,
    code_group      varchar(10)  NOT NULL,
    UNIQUE (code, catalog, code_group)
);
COMMENT ON TABLE ref.catalog_profile IS 'Профиль каталога: ограничивает список кодов, предлагаемых оператору для данного вида объекта';


-- ---------------------------------------------------------------------
-- 2. МАСТЕР-ДАННЫЕ: ТЕХНИЧЕСКИЕ МЕСТА И ОБОРУДОВАНИЕ
-- ---------------------------------------------------------------------

-- Индикатор структуры кода ТМ (SAP TPLKZ). В АСПГ2 было шесть индикаторов
-- AL2A..AL2F. Индикатор задаёт маску: сколько уровней, какой длины,
-- какие разделители. Маска АСПГ2: XXXXN.NN.XXXXXXXXX... на 3 уровня,
-- пример кода — ALNG2.30.115-HV-00043-A03SB0B0.
CREATE TABLE ref.floc_structure (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code            varchar(4)   NOT NULL UNIQUE,  -- 'MK01'
    mask            text         NOT NULL,         -- 'XX.NN.XXXX.NNNN.XXXXXX'
    level_count     smallint     NOT NULL,         -- 5
    -- длины сегментов по уровням, слева направо
    level_lengths   smallint[]   NOT NULL,         -- '{2,2,4,4,6}'
    separator       char(1)      NOT NULL DEFAULT '.'
);
COMMENT ON TABLE ref.floc_structure IS 'Индикатор структуры ТМ (SAP TPLKZ): маска кода, число уровней, длина сегмента, разделитель';

-- Техническое место: постоянное место, где что-то работает.
-- Иерархия для Москоллектора (5 уровней, по образцу Maintenance Plant Hierarchy
-- Ямал СПГ: Plant -> Sector -> Process Unit -> FL -> Equipment):
--   1  МК                предприятие
--   2  МК.03             район эксплуатации
--   3  МК.03.K127        коллектор (трасса)
--   4  МК.03.K127.0415   пикет / камера
--   5  МК.03.K127.0415.VSH001  функциональное место конкретного объекта
CREATE TABLE asset.func_location (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code                varchar(40)  NOT NULL UNIQUE,
    name                text         NOT NULL,
    floc_type_id        bigint       NOT NULL REFERENCES ref.floc_type(id),
    structure_id        bigint       NOT NULL REFERENCES ref.floc_structure(id),
    hierarchy_level     smallint     NOT NULL CHECK (hierarchy_level BETWEEN 1 AND 5),
    parent_id           bigint       REFERENCES asset.func_location(id),
    object_kind_id      bigint       REFERENCES ref.object_kind(id),
    district_id         bigint       NOT NULL REFERENCES ref.district(id),
    location_id         bigint       REFERENCES ref.location(id),
    planner_group_id    bigint       REFERENCES ref.planner_group(id),
    -- «ответственное рабочее место» — подразделение, отвечающее за эксплуатацию;
    -- в SAP копируется в сообщение и заказ ТОРО автоматически
    resp_work_center_id bigint       REFERENCES ref.work_center(id),
    criticality_id      bigint       REFERENCES ref.criticality(id),
    catalog_profile     varchar(8),
    -- даты и паспортные данные самого места (в SAP они есть и у ТМ, и у ЕО)
    in_service_from     date,                       -- «В эксплуатации С», SAP INBDT
    inventory_no        varchar(25),                -- инвентарный номер
    -- географическая привязка отрезка коллектора
    picket_from         integer,                    -- ПК начала
    picket_to           integer,                    -- ПК конца
    length_m            numeric(10,2),
    -- статусы. В SAP их два: системный (СОЗД/УСТН/МТКУ) и пользовательский.
    system_status       text         NOT NULL DEFAULT 'CREATED'
                        CHECK (system_status IN ('CREATED','INSTALLED','INACTIVE','DELETION_FLAG')),
    user_status         text,
    deletion_flag       boolean      NOT NULL DEFAULT false,
    created_at          timestamptz  NOT NULL DEFAULT now(),
    updated_at          timestamptz  NOT NULL DEFAULT now(),
    source_system       text         NOT NULL DEFAULT 'manual'
);
COMMENT ON TABLE  asset.func_location IS 'Техническое место (SAP IFLOT): постоянное во времени место установки оборудования';
COMMENT ON COLUMN asset.func_location.code IS 'Код по маске индикатора структуры, например МК.03.K127.0415.VSH001';
COMMENT ON COLUMN asset.func_location.hierarchy_level IS '1 предприятие, 2 район, 3 коллектор, 4 пикет/камера, 5 функциональное место объекта';
COMMENT ON COLUMN asset.func_location.deletion_flag IS 'Метка удаления (SAP МТКУ): объект исключается из списков, на него нельзя создать сообщение и заказ';
COMMENT ON COLUMN asset.func_location.source_system IS 'Откуда пришла запись: manual, smvu, ods, arm_control, migration';

CREATE INDEX ix_floc_parent   ON asset.func_location(parent_id);
CREATE INDEX ix_floc_district ON asset.func_location(district_id);
CREATE INDEX ix_floc_kind     ON asset.func_location(object_kind_id);

-- Единица оборудования: физический предмет, который обслуживают и ремонтируют
-- автономно. Ставится на техническое место, может быть снята и переставлена.
-- Датчик СМВУ, насос, вентилятор, люк, запорная арматура — всё это ЕО.
CREATE TABLE asset.equipment (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- номер ЕО из диапазона типа, в SAP это 18-значная строка с ведущими нулями
    equipment_no        varchar(18)  NOT NULL UNIQUE,
    name                text         NOT NULL,
    equipment_type_id   bigint       NOT NULL REFERENCES ref.equipment_type(id),
    object_kind_id      bigint       NOT NULL REFERENCES ref.object_kind(id),
    -- текущее место монтажа; история монтажей — в asset.equipment_install_history
    func_location_id    bigint       REFERENCES asset.func_location(id),
    -- узловое подчинение: датчик может висеть на щите, щит — на вентшахте
    parent_equipment_id bigint       REFERENCES asset.equipment(id),
    district_id         bigint       NOT NULL REFERENCES ref.district(id),
    location_id         bigint       REFERENCES ref.location(id),
    planner_group_id    bigint       REFERENCES ref.planner_group(id),
    resp_work_center_id bigint       REFERENCES ref.work_center(id),
    criticality_id      bigint       REFERENCES ref.criticality(id),
    catalog_profile     varchar(8),

    -- ---- паспортные данные (в SAP вкладка «Общее» + «Изготовитель») ----
    valid_from          date         NOT NULL,      -- «Начало срока действия», SAP DATAB
    in_service_from     date,                       -- «В эксплуатации С», SAP INBDT
    purchase_date       date,                       -- «Дата покупки», SAP ANSDT
    purchase_value      numeric(15,2),              -- «Закупочная стоимость», SAP ANSWT
    currency            char(3)      DEFAULT 'RUB',
    manufacturer_id     bigint       REFERENCES ref.manufacturer(id),  -- SAP HERST
    manufacturer_country char(2),                   -- SAP HERLD
    model_no            varchar(30),                -- «Номер модели», SAP TYPBZ
    manuf_part_no       varchar(30),                -- «Номер детали изготовителя», SAP MAPAR
    serial_no           varchar(30),                -- «Серийный номер», SAP SERGE
    build_year          smallint,                   -- SAP BAUJJ
    build_month         smallint CHECK (build_month BETWEEN 1 AND 12),  -- SAP BAUMM
    construction_type   varchar(18),                -- «Тип конструкции» = модель, SAP SUBMT
    size_dim            varchar(18),                -- «Величина/размер», SAP GROES
    weight              numeric(13,3),              -- SAP BRGEW
    weight_uom          varchar(3),                 -- SAP GEWEI
    inventory_no        varchar(25),                -- SAP INVNR
    -- нормативный срок службы: нужен модели износа, в SAP отдельного поля нет
    service_life_years  smallint,

    -- ---- статусы ----
    system_status       text         NOT NULL DEFAULT 'CREATED'
                        CHECK (system_status IN ('CREATED','INSTALLED','DISMANTLED','IN_REPAIR','WRITTEN_OFF','DELETION_FLAG')),
    user_status         text,
    deletion_flag       boolean      NOT NULL DEFAULT false,

    created_at          timestamptz  NOT NULL DEFAULT now(),
    updated_at          timestamptz  NOT NULL DEFAULT now(),
    source_system       text         NOT NULL DEFAULT 'manual',
    -- идентификатор объекта в системе-источнике (ID датчика СМВУ, номер по реестру ОЭ)
    source_key          text
);
COMMENT ON TABLE  asset.equipment IS 'Единица оборудования (SAP EQUI): физический объект, обслуживаемый и ремонтируемый автономно';
COMMENT ON COLUMN asset.equipment.construction_type IS 'Тип конструкции = модель. В SAP через него привязывались общие нормативы обслуживания на всю модель (монтажный узел ТОРО)';
COMMENT ON COLUMN asset.equipment.source_key IS 'ID объекта в системе-источнике: ID датчика СМВУ, номер по реестру оборудования ОЭ';

CREATE INDEX ix_eq_floc     ON asset.equipment(func_location_id);
CREATE INDEX ix_eq_parent   ON asset.equipment(parent_equipment_id);
CREATE INDEX ix_eq_kind     ON asset.equipment(object_kind_id);
CREATE INDEX ix_eq_source   ON asset.equipment(source_system, source_key);

-- История монтажа и демонтажа. Это отдельная таблица, потому что так же
-- устроен SAP: основная запись ЕО хранит текущее место, а вся история
-- перестановок лежит в таблице истории использования (EQUZ/ILOA).
-- В инструкции ОИ_БДО это операция «Изменить место монтажа»: указываются
-- дата и время демонтажа, потом дата и время монтажа на новое место.
CREATE TABLE asset.equipment_install_history (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    equipment_id        bigint       NOT NULL REFERENCES asset.equipment(id),
    func_location_id    bigint       REFERENCES asset.func_location(id),
    parent_equipment_id bigint       REFERENCES asset.equipment(id),
    installed_at        timestamptz  NOT NULL,
    dismantled_at       timestamptz,
    -- заказ ТОРО, в рамках которого произошла перестановка (если была)
    order_id            bigint,
    reason              text,
    created_by          text,
    created_at          timestamptz  NOT NULL DEFAULT now(),
    CHECK (dismantled_at IS NULL OR dismantled_at >= installed_at)
);
COMMENT ON TABLE asset.equipment_install_history IS 'История перемещений ЕО: где стояла, с какого по какое время. Аналог истории использования оборудования в SAP';

CREATE INDEX ix_instl_eq ON asset.equipment_install_history(equipment_id, installed_at DESC);

-- Классификация: дополнительные атрибуты, которых нет в постоянном наборе полей.
-- В SAP это связка «класс — признак — значение» (транзакции CL03, CT04).
-- Класс вида 002 присваивается оборудованию, 003 — техническим местам.
CREATE TABLE ref.characteristic (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code            varchar(30)  NOT NULL UNIQUE,  -- 'DIAMETR_NOM'
    name            text         NOT NULL,         -- 'Диаметр номинальный'
    data_type       text         NOT NULL CHECK (data_type IN ('num','char','date','bool')),
    uom             varchar(10),                   -- 'мм'
    decimals        smallint     NOT NULL DEFAULT 0,
    -- допустимые значения для типа char; NULL = свободный ввод
    allowed_values  text[]
);
COMMENT ON TABLE ref.characteristic IS 'Признак класса (SAP CABN): дополнительный атрибут технического объекта';

CREATE TABLE ref.object_class (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code            varchar(18)  NOT NULL UNIQUE,
    name            text         NOT NULL,
    class_kind      char(3)      NOT NULL CHECK (class_kind IN ('002','003')),
    parent_id       bigint       REFERENCES ref.object_class(id)
);
COMMENT ON COLUMN ref.object_class.class_kind IS '002 = класс для единиц оборудования, 003 = класс для технических мест (нумерация SAP)';

CREATE TABLE ref.class_characteristic (
    class_id          bigint  NOT NULL REFERENCES ref.object_class(id),
    characteristic_id bigint  NOT NULL REFERENCES ref.characteristic(id),
    is_required       boolean NOT NULL DEFAULT false,
    sort_order        smallint,
    PRIMARY KEY (class_id, characteristic_id)
);

CREATE TABLE asset.equipment_characteristic (
    equipment_id      bigint NOT NULL REFERENCES asset.equipment(id) ON DELETE CASCADE,
    characteristic_id bigint NOT NULL REFERENCES ref.characteristic(id),
    value_num         numeric(20,6),
    value_char        text,
    value_date        date,
    value_bool        boolean,
    PRIMARY KEY (equipment_id, characteristic_id)
);
COMMENT ON TABLE asset.equipment_characteristic IS 'Значения признаков классификации для ЕО (SAP AUSP)';


-- ---------------------------------------------------------------------
-- 3. ТОЧКИ ИЗМЕРЕНИЯ, СЧЁТЧИКИ, НАРАБОТКА
-- ---------------------------------------------------------------------

-- Точка измерения регистрирует меняющийся во времени параметр объекта.
-- Если параметр только растёт (наработка, пробег), точка помечается как счётчик.
-- В SAP точки создаются для двух типов объектов: IFL (техместо) и IEQ (оборудование).
CREATE TABLE asset.measuring_point (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    point_no            bigint       NOT NULL UNIQUE,
    name                text         NOT NULL,      -- 'Наработка вентилятора'
    equipment_id        bigint       REFERENCES asset.equipment(id),
    func_location_id    bigint       REFERENCES asset.func_location(id),
    characteristic_id   bigint       NOT NULL REFERENCES ref.characteristic(id),
    is_counter          boolean      NOT NULL DEFAULT false,
    -- «Базисная выработка» — плановая годовая наработка, нужна планам ППР по счётчику
    annual_estimate     numeric(20,6),
    -- «Метка переполнения счётчика»
    overflow_value      numeric(20,6),
    lower_limit         numeric(20,6),
    upper_limit         numeric(20,6),
    is_active           boolean      NOT NULL DEFAULT true,
    CHECK (equipment_id IS NOT NULL OR func_location_id IS NOT NULL)
);
COMMENT ON TABLE  asset.measuring_point IS 'Точка измерения / счётчик (SAP IMPTT)';
COMMENT ON COLUMN asset.measuring_point.annual_estimate IS 'Базисная выработка: плановая годовая наработка, по ней считаются сроки ППР по счётчику';
COMMENT ON COLUMN asset.measuring_point.is_active IS 'Деактивируется при замене оборудования, чтобы по точке больше не вводили данные';

-- Документ измерения: одно снятое значение. Сюда же ложатся результаты
-- проверок СМВУ, если завести точку измерения «результат проверки датчика».
CREATE TABLE asset.measurement (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    point_id        bigint       NOT NULL REFERENCES asset.measuring_point(id),
    measured_at     timestamptz  NOT NULL,
    value_num       numeric(20,6),
    value_text      text,
    -- разница с предыдущим значением для счётчиков (SAP хранит и то, и другое)
    delta_num       numeric(20,6),
    is_out_of_limit boolean      NOT NULL DEFAULT false,
    recorded_by     text,
    source_system   text         NOT NULL DEFAULT 'manual'
);
CREATE INDEX ix_meas_point_time ON asset.measurement(point_id, measured_at DESC);
COMMENT ON TABLE asset.measurement IS 'Документ измерения (SAP IMRG): наработка, температура, результат проверки датчика СМВУ';


-- ---------------------------------------------------------------------
-- 4. НОРМАТИВЫ: СТРАТЕГИИ, ТЕХКАРТЫ, ПЛАНЫ ППР
-- ---------------------------------------------------------------------

-- Стратегия предупредительного ТОРО задаёт набор циклов: раз в месяц,
-- раз в квартал, раз в год. Один раз описали — присвоили многим техкартам и планам.
CREATE TABLE maint.strategy (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code            varchar(6)   NOT NULL UNIQUE,  -- 'MK0001'
    name            text         NOT NULL,
    scheduling_kind text         NOT NULL CHECK (scheduling_kind IN ('time','counter')),
    unit            varchar(4)   NOT NULL          -- 'ДЕН', 'МЕС', 'ЧАС'
);
COMMENT ON TABLE maint.strategy IS 'Стратегия предупредительного ТОРО (SAP T351): набор пакетов мероприятий с периодичностью';

CREATE TABLE maint.strategy_package (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    strategy_id     bigint       NOT NULL REFERENCES maint.strategy(id),
    package_no      smallint     NOT NULL,
    cycle_length    numeric(10,2) NOT NULL,        -- 'Длина цикла'
    unit            varchar(4)   NOT NULL,
    cycle_text      text         NOT NULL,         -- 'Ежеквартальный осмотр'
    short_name      varchar(10),
    hierarchy_level smallint,                      -- «Иерархия» пакетов
    UNIQUE (strategy_id, package_no)
);

-- Технологическая карта: что именно делать, сколько это стоит по трудоёмкости,
-- какие материалы нужны. Привязывается к ЕО, к ТМ или к типу конструкции (модели).
CREATE TABLE maint.task_list (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    group_key       varchar(8)   NOT NULL,         -- ключ группы техкарт
    group_counter   smallint     NOT NULL,         -- счётчик группы, 1,2,3...
    name            text         NOT NULL,
    -- одна из трёх привязок
    equipment_id        bigint   REFERENCES asset.equipment(id),
    func_location_id    bigint   REFERENCES asset.func_location(id),
    construction_type   varchar(18),               -- общая инструкция на модель
    work_center_id  bigint       REFERENCES ref.work_center(id),
    planner_group_id bigint      REFERENCES ref.planner_group(id),
    strategy_id     bigint       REFERENCES maint.strategy(id),
    valid_from      date         NOT NULL,
    -- статус: 4 = общее деблокирование, только тогда техкарту можно брать в заказ
    status          char(1)      NOT NULL DEFAULT '1',
    deletion_flag   boolean      NOT NULL DEFAULT false,
    UNIQUE (group_key, group_counter)
);
COMMENT ON TABLE  maint.task_list IS 'Технологическая карта ТОиР (SAP PLKO): норматив на работу';
COMMENT ON COLUMN maint.task_list.status IS 'Статус техкарты. 4 = общее деблокирование — только с ним техкарта попадает в заказ ТОРО';

CREATE TABLE maint.task_list_operation (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    task_list_id    bigint       NOT NULL REFERENCES maint.task_list(id) ON DELETE CASCADE,
    operation_no    varchar(4)   NOT NULL,         -- '0010'
    name            text         NOT NULL,
    work_center_id  bigint       REFERENCES ref.work_center(id),
    -- управляющий ключ: PMIN внутренний персонал, PMSV подрядчик с договором,
    -- PMEX подрядчик без договора, PMYY информационная операция без подтверждения
    control_key     varchar(4)   NOT NULL DEFAULT 'PMIN',
    activity_type_id bigint      REFERENCES ref.activity_type(id),
    work_qty        numeric(10,2),                 -- трудоёмкость
    work_uom        varchar(4)   DEFAULT 'ЧАС',
    duration        numeric(10,2),
    duration_uom    varchar(4)   DEFAULT 'Ч',
    capacity_count  smallint     DEFAULT 1,        -- сколько человек
    UNIQUE (task_list_id, operation_no)
);

CREATE TABLE maint.task_list_material (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    operation_id    bigint       NOT NULL REFERENCES maint.task_list_operation(id) ON DELETE CASCADE,
    material_code   varchar(18)  NOT NULL,
    qty             numeric(13,3) NOT NULL,
    uom             varchar(4)   NOT NULL
);

-- План предупредительного ТОиР: связывает объект, техкарту и периодичность.
-- При наступлении даты или значения счётчика система порождает заказ ТОРО.
CREATE TABLE maint.plan (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    plan_no         varchar(12)  NOT NULL UNIQUE,
    name            text         NOT NULL,
    plan_kind       text         NOT NULL CHECK (plan_kind IN ('single_cycle','strategy')),
    strategy_id     bigint       REFERENCES maint.strategy(id),
    -- для плана отдельного цикла
    cycle_length    numeric(10,2),
    cycle_unit      varchar(4),
    measuring_point_id bigint    REFERENCES asset.measuring_point(id),
    -- параметры календарного планирования (все из ОИ_БДН)
    shift_factor_late   numeric(5,2),   -- КСС при запоздалом выполнении
    tolerance_late      smallint,       -- ДопОтклн (+), %
    shift_factor_early  numeric(5,2),   -- КСС при досрочном выполнении
    tolerance_early     smallint,       -- ДопОтклн (-), %
    cycle_modifier      numeric(5,2) NOT NULL DEFAULT 1.0,  -- коэффициент изменения цикла
    call_horizon        smallint,       -- горизонт открытия, %
    scheduling_period   smallint,       -- интервал отзывов
    scheduling_unit     varchar(4),
    cycle_start         date,
    call_after_completion boolean NOT NULL DEFAULT false,
    is_active           boolean  NOT NULL DEFAULT true,
    deletion_flag       boolean  NOT NULL DEFAULT false,
    CHECK (plan_kind = 'strategy' AND strategy_id IS NOT NULL
        OR plan_kind = 'single_cycle' AND cycle_length IS NOT NULL)
);
COMMENT ON TABLE  maint.plan IS 'План предупредительного ТОиР (SAP MPLA)';
COMMENT ON COLUMN maint.plan.cycle_modifier IS 'Коэффициент изменения цикла: растягивает или сжимает периодичность стратегии индивидуально для этого плана';
COMMENT ON COLUMN maint.plan.call_horizon IS 'Горизонт открытия, %: за сколько от цикла сгенерировать заказ заранее';

CREATE TABLE maint.plan_item (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    plan_id             bigint   NOT NULL REFERENCES maint.plan(id) ON DELETE CASCADE,
    item_no             smallint NOT NULL,
    name                text     NOT NULL,
    func_location_id    bigint   REFERENCES asset.func_location(id),
    equipment_id        bigint   REFERENCES asset.equipment(id),
    order_type_id       bigint   NOT NULL REFERENCES ref.order_type(id),
    activity_type_id    bigint   NOT NULL REFERENCES ref.activity_type(id),
    resp_work_center_id bigint   REFERENCES ref.work_center(id),
    priority_id         bigint   REFERENCES ref.priority(id),
    task_list_id        bigint   REFERENCES maint.task_list(id),
    UNIQUE (plan_id, item_no),
    CHECK (func_location_id IS NOT NULL OR equipment_id IS NOT NULL)
);


-- ---------------------------------------------------------------------
-- 5. ИСТОРИЯ ОТКАЗОВ И РЕМОНТОВ
-- ---------------------------------------------------------------------

-- Сообщение ТОиР фиксирует, что с объектом что-то не так. Это первичный документ
-- для истории отказов: именно отсюда ML-модель берёт размеченные события.
-- В АСПГ2 было четыре вида: WR запрос на работу, AR отчёт о работах ППР,
-- IR запрос на анализ первопричин (RCA), MD запрос на изменение мастер-данных.
CREATE TABLE maint.notification (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    notification_no     varchar(12)  NOT NULL UNIQUE,
    notification_kind   varchar(2)   NOT NULL,      -- 'WR','AR','IR','MD'
    subject             text         NOT NULL,
    long_text           text,
    func_location_id    bigint       REFERENCES asset.func_location(id),
    equipment_id        bigint       REFERENCES asset.equipment(id),
    priority_id         bigint       REFERENCES ref.priority(id),
    resp_work_center_id bigint       REFERENCES ref.work_center(id),
    planner_group_id    bigint       REFERENCES ref.planner_group(id),
    reported_at         timestamptz  NOT NULL,      -- когда заметили
    malfunction_start   timestamptz,                -- начало неисправности
    malfunction_end     timestamptz,                -- конец неисправности
    downtime_hours      numeric(10,2),              -- простой объекта
    -- режим отказа по каталогу U (ISO 14224): FTO, FTC, NOO, SPO, VIB, ...
    failure_mode_code   varchar(6),
    status              text         NOT NULL DEFAULT 'OPEN'
                        CHECK (status IN ('OPEN','IN_PROCESS','COMPLETED','CANCELLED')),
    created_by          text,
    created_at          timestamptz  NOT NULL DEFAULT now(),
    source_system       text         NOT NULL DEFAULT 'manual',
    source_key          text,
    CHECK (func_location_id IS NOT NULL OR equipment_id IS NOT NULL)
);
COMMENT ON TABLE  maint.notification IS 'Сообщение ТОиР (SAP QMEL): зафиксированное отклонение состояния объекта. Основа истории отказов';
COMMENT ON COLUMN maint.notification.failure_mode_code IS 'Режим отказа по каталогу U: NOO нет сигнала, SPO ложное срабатывание, FTO не открылось, VIB вибрация';
COMMENT ON COLUMN maint.notification.source_key IS 'ID исходной записи: сработка СМВУ, запись журнала ОДС';

CREATE INDEX ix_notif_eq   ON maint.notification(equipment_id, reported_at DESC);
CREATE INDEX ix_notif_floc ON maint.notification(func_location_id, reported_at DESC);

-- Позиция сообщения: узел — повреждение — причина — выполненное мероприятие.
-- Это четвёрка кодов ISO 14224, по которой потом строится статистика отказов.
CREATE TABLE maint.notification_item (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    notification_id     bigint       NOT NULL REFERENCES maint.notification(id) ON DELETE CASCADE,
    item_no             smallint     NOT NULL,
    object_part_code    varchar(6),                 -- каталог B, например PSEM «шток»
    damage_code         varchar(6),                 -- каталог C, например DCON «загрязнение»
    cause_code          varchar(6),                 -- каталог 5, например COWT «естественный износ»
    activity_code       varchar(6),                 -- каталог A, например PSE «ТО/восстановительный ремонт»
    item_text           text,
    UNIQUE (notification_id, item_no)
);
COMMENT ON TABLE maint.notification_item IS 'Позиция сообщения: узел объекта, повреждение, причина, выполненное мероприятие (четвёрка кодов ISO 14224)';

-- Заказ ТОиР: документ, по которому работы планируются, исполняются и закрываются.
CREATE TABLE maint.work_order (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_no            varchar(12)  NOT NULL UNIQUE,
    order_type_id       bigint       NOT NULL REFERENCES ref.order_type(id),
    activity_type_id    bigint       NOT NULL REFERENCES ref.activity_type(id),
    subject             text         NOT NULL,
    func_location_id    bigint       REFERENCES asset.func_location(id),
    equipment_id        bigint       REFERENCES asset.equipment(id),
    notification_id     bigint       REFERENCES maint.notification(id),
    plan_id             bigint       REFERENCES maint.plan(id),   -- если заказ породил план ППР
    priority_id         bigint       REFERENCES ref.priority(id),
    resp_work_center_id bigint       REFERENCES ref.work_center(id),
    planner_group_id    bigint       REFERENCES ref.planner_group(id),
    planned_start       timestamptz,
    planned_finish      timestamptz,
    actual_start        timestamptz,
    actual_finish       timestamptz,
    -- «Состояние производственной установки» на время работ
    system_condition    text,
    planned_cost        numeric(15,2),
    actual_cost         numeric(15,2),
    status              text         NOT NULL DEFAULT 'CREATED'
                        CHECK (status IN ('CREATED','RELEASED','CONFIRMED','TECH_COMPLETE','CLOSED','CANCELLED')),
    created_at          timestamptz  NOT NULL DEFAULT now(),
    CHECK (func_location_id IS NOT NULL OR equipment_id IS NOT NULL)
);
COMMENT ON TABLE maint.work_order IS 'Заказ ТОиР (SAP AUFK/AFIH): управление мероприятием — сроки, трудоёмкость, материалы, факт';

CREATE INDEX ix_order_eq   ON maint.work_order(equipment_id, planned_start DESC);
CREATE INDEX ix_order_floc ON maint.work_order(func_location_id, planned_start DESC);

CREATE TABLE maint.work_order_operation (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id            bigint       NOT NULL REFERENCES maint.work_order(id) ON DELETE CASCADE,
    operation_no        varchar(4)   NOT NULL,
    name                text         NOT NULL,
    long_text           text,
    work_center_id      bigint       REFERENCES ref.work_center(id),
    control_key         varchar(4)   NOT NULL DEFAULT 'PMIN',
    activity_type_id    bigint       REFERENCES ref.activity_type(id),
    planned_work        numeric(10,2),
    actual_work         numeric(10,2),
    work_uom            varchar(4)   DEFAULT 'ЧАС',
    capacity_count      smallint     DEFAULT 1,
    -- ссылка на наряд-допуск из АРМ-Контроль
    permit_no           varchar(30),
    deviation_reason    varchar(4),                 -- справочник причин отклонения
    UNIQUE (order_id, operation_no)
);
COMMENT ON COLUMN maint.work_order_operation.permit_no IS 'Номер наряда-допуска. В АСПГ2 допуски вели во внешней системе PTW и связывали с операциями заказа отдельной таблицей';

CREATE TABLE maint.work_order_material (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    operation_id    bigint       NOT NULL REFERENCES maint.work_order_operation(id) ON DELETE CASCADE,
    material_code   varchar(18)  NOT NULL,
    material_name   text,
    qty_planned     numeric(13,3) NOT NULL,
    qty_actual      numeric(13,3),
    uom             varchar(4)   NOT NULL,
    required_on     date
);

-- Замена узла: какую деталь сняли, какую поставили. В SAP это выводится
-- из истории монтажа подчинённых ЕО, но для отчётности удобнее отдельный факт.
CREATE TABLE maint.part_replacement (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id            bigint       NOT NULL REFERENCES maint.work_order(id),
    parent_equipment_id bigint       NOT NULL REFERENCES asset.equipment(id),
    removed_equipment_id bigint      REFERENCES asset.equipment(id),
    installed_equipment_id bigint    REFERENCES asset.equipment(id),
    object_part_code    varchar(6),                 -- каталог B
    material_code       varchar(18),
    replaced_at         timestamptz  NOT NULL,
    -- наработка снятого узла на момент замены
    runtime_at_removal  numeric(20,6),
    runtime_uom         varchar(4)
);
COMMENT ON TABLE maint.part_replacement IS 'Замена узла в составе оборудования: что сняли, что поставили, с какой наработкой';


-- ---------------------------------------------------------------------
-- 6. МИГРАЦИЯ И КАЧЕСТВО ДАННЫХ
-- ---------------------------------------------------------------------

-- Пакет загрузки. В «Арктик СПГ 2» каждая загрузка завершалась подписанным
-- «Протоколом загрузки данных из внешних источников»: сколько записей
-- планировали, сколько загрузили, каким инструментом, какие замечания.
CREATE TABLE load.batch (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    object_name     text         NOT NULL,          -- 'Технические места'
    source_file     text,
    tool            text,                           -- 'excel_template','api','manual'
    planned_rows    integer      NOT NULL,
    loaded_rows     integer      NOT NULL DEFAULT 0,
    failed_rows     integer      NOT NULL DEFAULT 0,
    stage           text         NOT NULL CHECK (stage IN ('test','production')),
    started_at      timestamptz  NOT NULL DEFAULT now(),
    finished_at     timestamptz,
    approved_by     text,
    approved_at     timestamptz
);
COMMENT ON TABLE load.batch IS 'Пакет загрузки мастер-данных. Аналог «Протокола загрузки данных из внешних источников» проекта АСПГ2';

CREATE TABLE load.error (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id        bigint       NOT NULL REFERENCES load.batch(id) ON DELETE CASCADE,
    row_no          integer,
    source_key      text,
    rule_code       text         NOT NULL,          -- 'CODE_MASK','DUPLICATE','REQUIRED','FK_MISSING'
    severity        text         NOT NULL CHECK (severity IN ('error','warning')),
    message         text         NOT NULL,
    payload         jsonb
);
COMMENT ON COLUMN load.error.rule_code IS 'Какое правило нарушено: маска кода, дубль, незаполненный обязательный атрибут, отсутствующая ссылка на справочник';

-- Заявка на изменение мастер-данных. В АСПГ2 ни одно изменение БДО не шло
-- напрямую: инициатор подавал заявку, технический эксперт согласовывал,
-- представитель группы КСУ ТОиР вносил (процесс PM.01.01).
CREATE TABLE load.change_request (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    request_no      varchar(12)  NOT NULL UNIQUE,
    object_type     text         NOT NULL CHECK (object_type IN ('func_location','equipment','measuring_point','reference')),
    action          text         NOT NULL CHECK (action IN ('create','update','delete')),
    target_code     text,
    payload         jsonb        NOT NULL,
    status          text         NOT NULL DEFAULT 'DRAFT'
                    CHECK (status IN ('DRAFT','ON_APPROVAL','REWORK','APPROVED','REJECTED','APPLIED','CANCELLED')),
    initiator       text         NOT NULL,
    approver        text,
    approver_comment text,
    created_at      timestamptz  NOT NULL DEFAULT now(),
    decided_at      timestamptz,
    applied_at      timestamptz
);
COMMENT ON TABLE load.change_request IS 'Заявка на изменение реестра оборудования. Повторяет цепочку PM.01.01: сформировал — согласовал — внёс';


-- ---------------------------------------------------------------------
-- 7. ПРЕДСТАВЛЕНИЯ ДЛЯ ML И ДИСПЕТЧЕРА
-- ---------------------------------------------------------------------

-- Плоская карточка объекта: собирает в одну строку всё, что нужно фичам модели.
CREATE VIEW asset.v_equipment_card AS
SELECT e.id,
       e.equipment_no,
       e.name,
       ok.code                                   AS object_kind,
       ok.family_code                            AS object_family,
       fl.code                                   AS floc_code,
       d.name                                    AS district,
       cr.code                                   AS criticality,
       e.in_service_from,
       -- возраст в годах на текущую дату: базовая фича износа
       EXTRACT(YEAR FROM age(current_date, e.in_service_from))          AS age_years,
       e.service_life_years,
       CASE WHEN e.service_life_years IS NULL OR e.in_service_from IS NULL THEN NULL
            ELSE round(EXTRACT(YEAR FROM age(current_date, e.in_service_from))::numeric
                       / e.service_life_years, 3)
       END                                                              AS wear_ratio,
       m.name                                    AS manufacturer,
       e.model_no,
       e.system_status,
       (SELECT count(*) FROM maint.notification n
         WHERE n.equipment_id = e.id
           AND n.reported_at > now() - interval '365 days')             AS notifications_1y,
       (SELECT max(n.reported_at) FROM maint.notification n
         WHERE n.equipment_id = e.id)                                   AS last_notification_at,
       (SELECT max(o.actual_finish) FROM maint.work_order o
         WHERE o.equipment_id = e.id AND o.status IN ('TECH_COMPLETE','CLOSED')) AS last_repair_at
FROM asset.equipment e
JOIN ref.object_kind ok ON ok.id = e.object_kind_id
JOIN ref.district    d  ON d.id  = e.district_id
LEFT JOIN asset.func_location fl ON fl.id = e.func_location_id
LEFT JOIN ref.criticality     cr ON cr.id = e.criticality_id
LEFT JOIN ref.manufacturer    m  ON m.id  = e.manufacturer_id
WHERE e.deletion_flag = false;

COMMENT ON VIEW asset.v_equipment_card IS 'Карточка оборудования одной строкой: паспорт + возраст + износ + история отказов и ремонтов за год';


-- Развёрнутый путь техместа для дерева в интерфейсе диспетчера.
CREATE RECURSIVE VIEW asset.v_floc_path (id, code, name, level, path, path_names) AS
    SELECT f.id, f.code, f.name, f.hierarchy_level,
           f.code::text, f.name::text
      FROM asset.func_location f
     WHERE f.parent_id IS NULL
    UNION ALL
    SELECT c.id, c.code, c.name, c.hierarchy_level,
           p.path || ' / ' || c.code, p.path_names || ' / ' || c.name
      FROM asset.func_location c
      JOIN asset.v_floc_path p ON p.id = c.parent_id;

COMMENT ON VIEW asset.v_floc_path IS 'Полный путь технического места от предприятия до объекта — для дерева и хлебных крошек';
