-- ============================================================================
-- Схема реестра нарядов-допусков для Москоллектора (АРМ-Контроль -> ML)
--
-- Источник структуры:
--   Ямал СПГ / ЕСНД (Tactise iPTW):
--     - ФТС 1000-Y-000-IM-SPE-00014-00-D_03R, п. 2.2.2 (роли), 3.2 (модуль НД),
--       3.2.2 (жизненный цикл), 3.3 (риски: Группа опасностей -> Опасность -> Мера)
--     - Матрица прав доступа пользователей_Ямал СПГ_ЕСНД.docx, Таблица 3 (переходы статусов)
--     - Техническое Задание Релиз 2.17 (реальные имена полей печатной формы)
--     - 3. База данных опасностей и мер контроля.xlsx (12 групп, ~100 опасностей, 1196 мер)
--   КТК / Пособие по организации работ повышенной опасности (СТП КТК 33.04.2021):
--     - бланк наряда-допуска, 20 разделов
--
-- Целевая СУБД: PostgreSQL 15+ (UNIQUE NULLS NOT DISTINCT), поставка — 18.6.
-- Порядок накатывания: после 002_geo.sql (PostGIS), до 004_events.sql.
-- Назначение: (а) реестр допусков, (б) источник признаков для модели пожарного
-- риска и для фильтра ложных срабатываний дымовых/температурных датчиков СМВУ.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. Справочники
-- ---------------------------------------------------------------------------

CREATE EXTENSION IF NOT EXISTS postgis;   -- geometry в permit.location
CREATE SCHEMA IF NOT EXISTS permit;

-- Типы работ повышенной опасности. Наполняется из work_types.json.

CREATE TABLE permit.permit_work_type (
    code                   text PRIMARY KEY,          -- HOT, GAS, CSE, HGT, ELE, EXC, LFT, OTH, ROUTINE
    name                   text NOT NULL,
    color                  text,                      -- цветовая кодировка бланка: красный/голубой/зелёный/коричневый
    fire_risk              boolean NOT NULL DEFAULT false,  -- тип работ повышает вероятность возгорания
    ignition_source        boolean NOT NULL DEFAULT false,  -- работы сами по себе — источник воспламенения
    detector_inhibit_expected boolean NOT NULL DEFAULT false, -- по регламенту ожидается блокировка датчиков
    gas_test_required      boolean NOT NULL DEFAULT false,
    validity_hours         integer,                   -- срок действия НД: HOT=72, прочие ПО=672 (28 сут), ROUTINE=до конца смены
    reapproval_hours       integer                    -- периодичность переутверждения: 168 (7 сут) для 28-суточных
);

-- Подтипы (HOT-01 электросварка, HOT-02 газорезка ...). Нужны, чтобы отличить
-- сварку от пайки: у них разная интенсивность дыма и тепловыделения.
CREATE TABLE permit.permit_work_subtype (
    code                   text PRIMARY KEY,
    work_type_code         text NOT NULL REFERENCES permit.permit_work_type(code),
    name                   text NOT NULL,
    fire_risk              boolean NOT NULL DEFAULT false,
    smoke_detector_trigger boolean NOT NULL DEFAULT false  -- работа заведомо даёт дым (задымление, битум, сварка)
);

-- Организации: подрядчик, субподрядчик, собственные службы.
CREATE TABLE permit.org (
    id                     bigserial PRIMARY KEY,
    name                   text NOT NULL,
    inn                    text,
    is_contractor          boolean NOT NULL DEFAULT true,
    UNIQUE (name, inn)
);

-- Люди. В ЕСНД это отдельный модуль «Люди».
CREATE TABLE permit.person (
    id                     bigserial PRIMARY KEY,
    last_name              text NOT NULL,
    first_name             text,
    middle_name            text,
    org_id                 bigint REFERENCES permit.org(id),
    position               text,
    -- аттестации: срок действия удостоверений ПТМ/ОТ, группа по электробезопасности
    ptm_valid_until        date,
    ot_valid_until         date,
    electrical_group       smallint
);

-- Роли из ролевой модели ЕСНД (п. 2.2 ФТС + Матрица прав доступа, Таблица 2).
CREATE TABLE permit.permit_role (
    code                   text PRIMARY KEY,
    name                   text NOT NULL,
    can_sign               boolean NOT NULL DEFAULT false
);
-- INSERT:
--   SA  Координатор НД (администратор процесса)
--   SC  Утверждающий
--   AA  Ответственный за подготовку работ
--   PA  Ответственный за проведение работ
--   RPx Ответственный за мех./техн./электр./КИПиА изоляцию
--   IAx Исполнитель по изоляции
--   HSE Представитель службы ПБ и ОТ (только подпись)
--   GAS Ответственный за ГВС (вносит результаты газоанализа)
--   FIRE Представитель ПСС / пожарной службы (только подпись)
--   RESC Представитель ГСВ / аварийно-спасательной службы (только подпись)
--   OBS Наблюдатель

-- ---------------------------------------------------------------------------
-- 2. Пространственная привязка
-- ---------------------------------------------------------------------------
-- В ЕСНД место работ — это четыре поля печатной формы (ТЗ 2.17, п. 3):
--   «Участок», «Производственная система», «Функциональная локация», «Смежный участок».
-- Для Москоллектора: район ОЭ -> коллектор -> участок/камера -> пикет.
-- Ровно эта иерархия должна совпадать с адресацией датчиков СМВУ, иначе
-- корреляцию «сварка рядом с датчиком» не построить.

CREATE TABLE permit.location (
    id                     bigserial PRIMARY KEY,
    parent_id              bigint REFERENCES permit.location(id),
    level                  text NOT NULL,             -- 'district' | 'collector' | 'section' | 'chamber' | 'piket'
    code                   text NOT NULL,             -- инвентарный код, совпадает с кодом в СМВУ
    name                   text,
    address                text,
    geom                   geometry(Geometry, 4326),  -- PostGIS: точка камеры / линия участка коллектора
    UNIQUE (level, code)
);
CREATE INDEX ON permit.location USING gist (geom);
CREATE INDEX ON permit.location (parent_id);

-- ---------------------------------------------------------------------------
-- 3. Наряд-допуск — главная таблица
-- ---------------------------------------------------------------------------
CREATE TABLE permit.permit (
    id                     bigserial PRIMARY KEY,
    number                 text NOT NULL UNIQUE,      -- регистрационный номер из журнала выдачи НД
    work_type_code         text NOT NULL REFERENCES permit.permit_work_type(code),
    work_subtype_code      text REFERENCES permit.permit_work_subtype(code),
    is_routine             boolean NOT NULL DEFAULT false,
    template_id            bigint REFERENCES permit.permit(id), -- шаблон рутинных работ, из которого создан НД

    -- Раздел 1 бланка КТК: место проведения работ
    location_id            bigint NOT NULL REFERENCES permit.location(id),
    adjacent_location_id   bigint REFERENCES permit.location(id),  -- «Смежный участок» из ЕСНД
    location_detail        text,                      -- этаж, ярус, помещение, отметка

    -- Раздел 2: срок действия, с точностью до минуты
    valid_from             timestamptz NOT NULL,      -- поле ЕСНД «Запрошено на:»
    valid_to               timestamptz NOT NULL,      -- поле ЕСНД «Конец:»
    shift_mode             text CHECK (shift_mode IN ('day','night')),  -- п. 2.1 «Режим работы»

    -- Раздел 3: организация, производящая работы
    contractor_org_id      bigint REFERENCES permit.org(id),

    -- Раздел 5: содержание работ
    work_description       text NOT NULL,             -- «Подробное описание работ:»
    equipment_description  text,                      -- «Описание оборудования:»
    work_volume            text,

    -- Раздел 6: техника и инструмент
    vehicles_and_tools     text,

    -- газоанализ
    gas_test_required      boolean NOT NULL DEFAULT false,
    -- изоляция оборудования
    isolation_required     boolean NOT NULL DEFAULT false,
    -- запрос на блокировку систем безопасности (для нас — ключевое поле)
    safety_system_inhibit_requested boolean NOT NULL DEFAULT false,

    -- Раздел 14: целевой инструктаж
    briefing_content       text,

    status                 text NOT NULL,             -- см. permit_status_transition ниже
    created_at             timestamptz NOT NULL DEFAULT now(),
    created_by             bigint REFERENCES permit.person(id),
    closed_at              timestamptz,
    close_reason           text,                      -- причина незавершённости работ

    CHECK (valid_to > valid_from)
);
CREATE INDEX ON permit.permit (location_id, valid_from, valid_to);
CREATE INDEX ON permit.permit (work_type_code, valid_from);
CREATE INDEX ON permit.permit (status);
-- Для оконных запросов «какие НД действовали в момент T»:
CREATE INDEX ON permit.permit USING gist (tstzrange(valid_from, valid_to));

-- Раздел 4 бланка: ответственные лица по ролям.
CREATE TABLE permit.permit_responsible (
    permit_id              bigint NOT NULL REFERENCES permit.permit(id) ON DELETE CASCADE,
    role_code              text NOT NULL REFERENCES permit.permit_role(code),
    person_id              bigint NOT NULL REFERENCES permit.person(id),
    PRIMARY KEY (permit_id, role_code, person_id)
);

-- Раздел 4/19 бланка: состав бригады, включая введённых в течение работ.
CREATE TABLE permit.permit_crew (
    id                     bigserial PRIMARY KEY,
    permit_id              bigint NOT NULL REFERENCES permit.permit(id) ON DELETE CASCADE,
    person_id              bigint NOT NULL REFERENCES permit.person(id),
    profession             text,
    function               text,                      -- «Выполняемая функция»: демонтаж, монтаж, ...
    briefed_at             timestamptz,               -- дата и время целевого инструктажа
    added_at               timestamptz,               -- введён в состав бригады дополнительно
    removed_at             timestamptz,               -- выведен из состава бригады
    UNIQUE (permit_id, person_id)
);

-- Раздел 7 бланка: подписи и согласования.
CREATE TABLE permit.permit_signature (
    id                     bigserial PRIMARY KEY,
    permit_id              bigint NOT NULL REFERENCES permit.permit(id) ON DELETE CASCADE,
    role_code              text NOT NULL REFERENCES permit.permit_role(code),
    person_id              bigint NOT NULL REFERENCES permit.person(id),
    signed_at              timestamptz NOT NULL,
    is_countersign         boolean NOT NULL DEFAULT false,  -- заверение, а не согласование
    comment                text
);
CREATE INDEX ON permit.permit_signature (permit_id, signed_at);

-- ---------------------------------------------------------------------------
-- 4. Жизненный цикл
-- ---------------------------------------------------------------------------
-- Полный набор статусов ЕСНД (ФТС п. 3.2.2 + Матрица прав доступа, Таблица 3):
--   Создан, Согласование, Заверен, Утверждён, Выпущен, В работе,
--   Работа завершена, Работа не завершена, Закрыт: работа завершена,
--   Закрыт: работа не завершена, Приостановлен, Возврат, Возвращён,
--   Просрочен, Архив.

-- Таблица правил для приложения, а не справочник для ключей: журнал переходов
-- ниже на неё не ссылается, потому что from_status у создания NULL, а ключ
-- с NULL в одной из колонок PostgreSQL не проверяет вовсе. actor_role тоже без
-- ключа на permit_role: значение SYSTEM — не роль человека.
CREATE TABLE permit.permit_status_transition (
    from_status            text,                      -- NULL = создание
    to_status              text NOT NULL,
    actor_role             text NOT NULL,             -- основной ответственный: PA / AA / SC / SA / SYSTEM
    extra_roles            text[],                    -- дополнительный доступ
    is_automatic           boolean NOT NULL DEFAULT false,
    -- Не PRIMARY KEY: он сделал бы from_status NOT NULL, и строка создания
    -- (NULL -> Создан) не вставилась бы. NULLS NOT DISTINCT — чтобы и её нельзя
    -- было завести дважды.
    UNIQUE NULLS NOT DISTINCT (from_status, to_status)
);
-- Основная ветка:
--   NULL               -> Создан                       PA  (+AA, SC)
--   Создан             -> Согласование                 PA  (+AA, SC)
--   Согласование       -> Заверен                      AA
--   Заверен            -> Утверждён                    SC
--   Утверждён          -> Выпущен                      AA
--   Выпущен            -> В работе                     PA
--   В работе           -> Работа завершена             PA
--   В работе           -> Работа не завершена          PA
--   Работа завершена   -> Закрыт: работа завершена     AA
--   Работа не завершена-> Закрыт: работа не завершена  AA
--   Закрыт: *          -> Архив                        SYSTEM (через 365 дней; рутина — 14 дней), вручную SA
-- Возвраты и отклонения:
--   Согласование       -> Создан                       любой согласующий (+AA, SC, OA)
--   Заверен            -> Создан                       SC
-- Приостановка и продление между сменами:
--   В работе           -> Приостановлен                SYSTEM (+AA, SC, SA) — через 30 мин после конца смены
--   Утверждён/Выпущен  -> Приостановлен                AA (+SC)
--   В работе           -> Возврат                      PA
--   Возврат            -> Возвращён                    AA
--   Возвращён          -> Выпущен | Заверен            AA
--   Приостановлен      -> Возвращён                    AA
-- Просрочка:
--   Утверждён/Возврат/Возвращён/Приостановлен -> Просрочен   SYSTEM, через 30 мин после истечения срока
--   Выпущен            -> Просрочен                    SYSTEM
-- Аварийный останов:
--   кнопка «Останов» ролями SA и AA переводит ВСЕ НД «В работе» в «Приостановлен».

-- Журнал переходов. Из него восстанавливается фактическое (а не плановое)
-- время работ: valid_from/valid_to — это план, а переход в «В работе» и из
-- него — факт. Для корреляции со срабатываниями датчиков нужен именно факт.
CREATE TABLE permit.permit_status_log (
    id                     bigserial PRIMARY KEY,
    permit_id              bigint NOT NULL REFERENCES permit.permit(id) ON DELETE CASCADE,
    from_status            text,
    to_status              text NOT NULL,
    changed_at             timestamptz NOT NULL,
    changed_by             bigint REFERENCES permit.person(id),  -- NULL, если перевела система
    is_automatic           boolean NOT NULL DEFAULT false,
    comment                text
);
CREATE INDEX ON permit.permit_status_log (permit_id, changed_at);
CREATE INDEX ON permit.permit_status_log (to_status, changed_at);

-- Раздел 18 бланка КТК: ежесменный допуск / продление.
CREATE TABLE permit.permit_extension (
    id                     bigserial PRIMARY KEY,
    permit_id              bigint NOT NULL REFERENCES permit.permit(id) ON DELETE CASCADE,
    shift_date             date NOT NULL,
    started_at             timestamptz NOT NULL,      -- время начала работ в эту смену
    ended_at               timestamptz,
    approved_by            bigint REFERENCES permit.person(id),  -- РО / руководитель объекта
    accepted_by            bigint REFERENCES permit.person(id),  -- ОВР / ответственный за выполнение
    UNIQUE (permit_id, shift_date)
);
CREATE INDEX ON permit.permit_extension (started_at, ended_at);

-- Связи между нарядами (ФТС п. 3.2.1, типы связей):
--   'work_work'    «В работе/В работе»   — зависимый НД нельзя ввести в работу
--                                           раньше связанного, правило двустороннее
--   'work_notwork' «В работе/Не в работе»— один НД должен быть приостановлен
--                                           или завершён до начала другого
--   'start_end'    «Начало/Конец»        — НД с типом «начало» не вводится в работу,
--                                           пока не завершён НД с типом «конец»
CREATE TABLE permit.permit_link (
    permit_id              bigint NOT NULL REFERENCES permit.permit(id) ON DELETE CASCADE,
    linked_permit_id       bigint NOT NULL REFERENCES permit.permit(id) ON DELETE CASCADE,
    link_type              text NOT NULL CHECK (link_type IN ('work_work','work_notwork','start_end')),
    PRIMARY KEY (permit_id, linked_permit_id, link_type),
    CHECK (permit_id <> linked_permit_id)
);

-- ---------------------------------------------------------------------------
-- 5. Опасности и меры контроля
-- ---------------------------------------------------------------------------
-- Иерархия из ФТС п. 3.3.1: Группа опасностей -> Опасность -> Мера контроля,
-- связь опасностей и мер — многие-ко-многим.
-- В файле «3. База данных опасностей и мер контроля.xlsx» — 11 групп,
-- ~100 опасностей, 1196 строк «опасность + мера».

CREATE TABLE permit.hazard_group (
    code                   text PRIMARY KEY,
    name_ru                text NOT NULL,
    name_en                text
);
-- 12 групп Ямал СПГ:
--   IGN  Источники воспламенения        (Ignition Sources)        — 133 меры
--   CSE  Ограниченное пространство      (Confined Space Entry)    —  89
--   ELE  Электрическая энергия          (Electrical Energy)       —  65
--   EQP  Опасности от оборудования      (Equipment Hazards)       — 132
--   MAT  Материалы и вещества           (Materials & Substances)  — 190
--   SSI  Отказ системы безопасности     (Safety System Impairment)—  93
--   ENV  Рабочая среда                  (Working Environment)     — 399
--   EXC  Земляные работы                (Excavation)              —  16
--   HOT  Подготовка места огневых работ (Hot Work Preparations)   —  23
--   GEN  Подготовка места работ         (General Worksite Prep.)  —  17
--   CTL  Недостаточный контроль         (Inadequate Control)      —  14
--   PRE  Категория присутствия руководителя работ (Cat A/B/C)     —  24

CREATE TABLE permit.hazard (
    id                     bigserial PRIMARY KEY,
    group_code             text NOT NULL REFERENCES permit.hazard_group(code),
    name_ru                text NOT NULL,
    name_en                text,
    -- маркеры для модели пожарного риска
    is_ignition_source     boolean NOT NULL DEFAULT false,
    impairs_fire_detection boolean NOT NULL DEFAULT false,  -- напр. «Нарушение работоспособности
                                                            -- системы обнаружения газа и пламени»
    UNIQUE (group_code, name_ru)
);

CREATE TABLE permit.control_measure (
    id                     bigserial PRIMARY KEY,
    text_ru                text NOT NULL,
    text_en                text,
    -- маркер: мера явно выводит датчики из работы
    inhibits_detectors     boolean NOT NULL DEFAULT false
);
-- Меры с inhibits_detectors = true в БД Ямал СПГ:
--   «Заблокируйте датчики пожара и дыма.»  — встречается у опасностей
--       Naked Flame (Открытое пламя), Electric arc welding (Электродуговая сварка),
--       Smoke (Дым)
--   «Only one loop/zone to be taken out of action at a time. Where not possible,
--       alternative arrangements to be provided.» — у опасности Reduction of Fire &
--       gas detection facilities

CREATE TABLE permit.hazard_control (
    hazard_id              bigint NOT NULL REFERENCES permit.hazard(id) ON DELETE CASCADE,
    control_id             bigint NOT NULL REFERENCES permit.control_measure(id) ON DELETE CASCADE,
    PRIMARY KEY (hazard_id, control_id)
);

-- Оценка рисков в конкретном наряде. ФТС п. 3.3.1: уровень 1 и уровень 2;
-- меры делятся на предварительные (должны быть внедрены ДО выпуска НД)
-- и дополнительные (выполняются при проведении работ).
CREATE TABLE permit.permit_hazard (
    id                     bigserial PRIMARY KEY,
    permit_id              bigint NOT NULL REFERENCES permit.permit(id) ON DELETE CASCADE,
    hazard_id              bigint REFERENCES permit.hazard(id),
    custom_hazard_text     text,                      -- опасность, добавленная вручную под конкретный НД
    risk_level             smallint CHECK (risk_level IN (1,2)),
    probability            smallint,                  -- полуколичественная матрица, ОР2
    severity               smallint,
    residual_score         integer,
    CHECK (hazard_id IS NOT NULL OR custom_hazard_text IS NOT NULL)
);

CREATE TABLE permit.permit_control (
    id                     bigserial PRIMARY KEY,
    permit_id              bigint NOT NULL REFERENCES permit.permit(id) ON DELETE CASCADE,
    permit_hazard_id       bigint REFERENCES permit.permit_hazard(id) ON DELETE CASCADE,
    control_id             bigint REFERENCES permit.control_measure(id),
    custom_control_text    text,
    is_preliminary         boolean NOT NULL DEFAULT false,  -- предварительная мера
    implemented_at         timestamptz,               -- отметка о внедрении
    implemented_by         bigint REFERENCES permit.person(id)
);
CREATE INDEX ON permit.permit_control (permit_id, is_preliminary);

-- ---------------------------------------------------------------------------
-- 6. Газоанализ (раздел 17 бланка КТК)
-- ---------------------------------------------------------------------------
CREATE TABLE permit.permit_gas_test (
    id                     bigserial PRIMARY KEY,
    permit_id              bigint NOT NULL REFERENCES permit.permit(id) ON DELETE CASCADE,
    sampled_at             timestamptz NOT NULL,
    sample_point           text NOT NULL,             -- «Место отбора проб»
    is_primary             boolean NOT NULL DEFAULT false,  -- первичный анализ перед выпуском НД
    hc_mg_m3               numeric,                   -- C1-C10, ПДК 300 мг/м3, ПДВК 2100 мг/м3
    h2s_mg_m3              numeric,                   -- ПДК 10 мг/м3 (7 ppm)
    o2_percent             numeric,                   -- не менее 20 %
    co_mg_m3               numeric,
    lel_percent            numeric,
    analyzer_model         text,                      -- «Марка газоанализатора»
    analyzer_calibrated_at date,                      -- «дата поверки»
    performed_by           bigint REFERENCES permit.person(id)
);
CREATE INDEX ON permit.permit_gas_test (permit_id, sampled_at);

-- ---------------------------------------------------------------------------
-- 7. Изоляция оборудования и вывод систем безопасности из работы
-- ---------------------------------------------------------------------------
-- Изоляционный сертификат (ИС) — отдельный ЖЦ в ЕСНД:
--   Создан -> Заполнение точек изоляции -> Проверка точек -> Заверен ->
--   Изоляция в процессе -> Изолировано -> Деизоляция в процессе -> Деизолировано -> Архив
--   плюс ветки «Долговременная изоляция», «Деизоляция для теста» -> «Тест».
-- Правило-блокировка: НД нельзя перевести в «Согласование», пока связанные ИС
-- не в статусах «Заверен», «Изолировано», «Долговременная изоляция» или «Тест»;
-- нельзя перевести в «Выпущен», пока ИС не в «Изолировано» или «Тест».
CREATE TABLE permit.isolation_certificate (
    id                     bigserial PRIMARY KEY,
    number                 text NOT NULL UNIQUE,
    discipline             text NOT NULL,             -- mechanical | process | electrical | instrument
    status                 text NOT NULL,
    created_at             timestamptz NOT NULL DEFAULT now(),
    isolated_at            timestamptz,
    deisolated_at          timestamptz
);

CREATE TABLE permit.permit_isolation (
    permit_id              bigint NOT NULL REFERENCES permit.permit(id) ON DELETE CASCADE,
    isolation_id           bigint NOT NULL REFERENCES permit.isolation_certificate(id) ON DELETE CASCADE,
    PRIMARY KEY (permit_id, isolation_id)
);

-- ГЛАВНАЯ ТАБЛИЦА ДЛЯ ФИЛЬТРА ЛОЖНЫХ СРАБАТЫВАНИЙ.
-- Фиксирует факт вывода конкретного датчика или шлейфа СМВУ из работы под наряд.
-- В ЕСНД это «Утверждённая форма запроса на блокировку систем СБ и ПАЗ»
-- (Приложение №10) плюс мера контроля «Заблокируйте датчики пожара и дыма».
CREATE TABLE permit.permit_detector_inhibit (
    id                     bigserial PRIMARY KEY,
    permit_id              bigint NOT NULL REFERENCES permit.permit(id) ON DELETE CASCADE,
    -- Идентификатор датчика в выгрузке СМВУ = smvu.channel.tag, тег инженерной системы
    -- вида «847-11.1.131.2.» (текст, не числовой channel_id). Колонка называется
    -- sensor_id по названию поля в бланке наряда-допуска, менять её не стали.
    -- NULL, если блокируется шлейф целиком. Ключ ставит 005_xref.sql:
    -- каналы накатываются позже нарядов.
    sensor_id              text,
    loop_code              text,                      -- шлейф / зона
    location_id            bigint REFERENCES permit.location(id),
    inhibited_from         timestamptz NOT NULL,
    inhibited_to           timestamptz,
    reason                 text,
    restored_at            timestamptz,               -- факт снятия блокировки
    restored_by            bigint REFERENCES permit.person(id)
);
CREATE INDEX ON permit.permit_detector_inhibit (sensor_id, inhibited_from, inhibited_to);
CREATE INDEX ON permit.permit_detector_inhibit USING gist (tstzrange(inhibited_from, inhibited_to));

-- ---------------------------------------------------------------------------
-- 8. Вложения
-- ---------------------------------------------------------------------------
CREATE TABLE permit.permit_attachment (
    id                     bigserial PRIMARY KEY,
    permit_id              bigint NOT NULL REFERENCES permit.permit(id) ON DELETE CASCADE,
    kind                   text NOT NULL,             -- risk_cert | briefing_cert | gas_cert | site_plan |
                                                      -- hidden_utilities_scheme | sb_paz_inhibit_request |
                                                      -- detector_layout | isolation_cert | ppr | checklist
    is_mandatory           boolean NOT NULL DEFAULT false,
    file_uri               text,
    uploaded_at            timestamptz NOT NULL DEFAULT now()
);

-- ============================================================================
-- 9. Витрина признаков для ML
-- ============================================================================

-- 9.1. Фактические окна работ. valid_from/valid_to — план; реальное окно —
-- интервалы между переходом в «В работе» и выходом из него. Для смен берём
-- permit_extension. Это то окно, внутри которого срабатывание датчика
-- объясняется работами, а не пожаром.
CREATE VIEW permit.v_permit_active_window AS
SELECT
    p.id                        AS permit_id,
    p.number,
    p.work_type_code,
    p.work_subtype_code,
    wt.fire_risk,
    wt.ignition_source,
    p.location_id,
    p.adjacent_location_id,
    p.contractor_org_id,
    COALESCE(e.started_at, in_work.changed_at, p.valid_from) AS actual_from,
    COALESCE(e.ended_at,   done.changed_at,    p.valid_to)   AS actual_to
FROM permit.permit p
JOIN permit.permit_work_type wt ON wt.code = p.work_type_code
LEFT JOIN permit.permit_extension e ON e.permit_id = p.id
LEFT JOIN LATERAL (
    SELECT changed_at FROM permit.permit_status_log l
    WHERE l.permit_id = p.id AND l.to_status = 'В работе'
    ORDER BY changed_at LIMIT 1
) in_work ON true
LEFT JOIN LATERAL (
    SELECT changed_at FROM permit.permit_status_log l
    WHERE l.permit_id = p.id
      AND l.to_status IN ('Работа завершена','Работа не завершена','Возврат','Приостановлен')
    ORDER BY changed_at DESC LIMIT 1
) done ON true;

-- 9.2. Признаки под конкретное срабатывание датчика.
-- Вызов: SELECT * FROM permit.f_permit_features('<sensor_id>', '<location_id>', '<event_time>');
--
-- Окна взяты из регламентов, а не с потолка:
--   * +30 мин после конца работ — пожарный наблюдающий по стандарту Ямал СПГ
--     обязан оставаться на месте «не менее 30 минут после последних огневых
--     работ» (Fire Watchman must stay at site at least half hour after last HW);
--     столько же тлеет и дымит остывающий шов.
--   * 4 часа — грубая верхняя граница «шлейфа» от битума и окрасочных работ.
--   * Смежный участок (adjacent_location_id) учитывается, потому что дым в
--     коллекторе уходит по вентиляции в соседнюю секцию.
CREATE OR REPLACE FUNCTION permit.f_permit_features(
    p_sensor_id   text,
    p_location_id bigint,
    p_event_time  timestamptz
) RETURNS TABLE (
    -- прямое совпадение по месту и времени
    hot_work_now              boolean,   -- огневые работы идут прямо сейчас на этом участке
    hot_work_30min            boolean,   -- огневые работы закончились менее 30 минут назад
    hot_work_4h               boolean,   -- огневые работы были в пределах 4 часов
    hot_work_adjacent_now     boolean,   -- огневые работы на смежном участке
    -- прочие типы
    any_permit_now            boolean,
    gas_work_now              boolean,
    excavation_now            boolean,
    electrical_now            boolean,
    -- блокировка датчика
    detector_inhibited        boolean,   -- этот датчик выведен из работы под наряд
    -- количественные
    permits_active_cnt        integer,   -- сколько нарядов действует на участке в этот момент
    crew_size                 integer,   -- суммарная численность бригад на участке
    minutes_since_hot_work    integer,   -- минут с окончания последних огневых работ (NULL, если не было)
    contractor_permit_cnt_30d integer    -- сколько нарядов подрядчик открыл на участке за 30 дней
) AS $$
    WITH win AS (
        SELECT w.*
        FROM permit.v_permit_active_window w
        WHERE (w.location_id = p_location_id OR w.adjacent_location_id = p_location_id)
    ),
    hot AS (
        SELECT * FROM win WHERE fire_risk AND ignition_source
    )
    SELECT
        EXISTS (SELECT 1 FROM hot WHERE location_id = p_location_id
                 AND p_event_time BETWEEN actual_from AND actual_to),
        EXISTS (SELECT 1 FROM hot WHERE location_id = p_location_id
                 AND p_event_time BETWEEN actual_from AND actual_to + interval '30 minutes'),
        EXISTS (SELECT 1 FROM hot WHERE location_id = p_location_id
                 AND p_event_time BETWEEN actual_from AND actual_to + interval '4 hours'),
        EXISTS (SELECT 1 FROM hot WHERE adjacent_location_id = p_location_id
                 AND p_event_time BETWEEN actual_from AND actual_to + interval '30 minutes'),
        EXISTS (SELECT 1 FROM win WHERE p_event_time BETWEEN actual_from AND actual_to),
        EXISTS (SELECT 1 FROM win WHERE work_type_code IN ('GAS','CSE')
                 AND p_event_time BETWEEN actual_from AND actual_to),
        EXISTS (SELECT 1 FROM win WHERE work_type_code = 'EXC'
                 AND p_event_time BETWEEN actual_from AND actual_to),
        EXISTS (SELECT 1 FROM win WHERE work_type_code = 'ELE'
                 AND p_event_time BETWEEN actual_from AND actual_to),
        EXISTS (SELECT 1 FROM permit.permit_detector_inhibit di
                 WHERE (di.sensor_id = p_sensor_id OR di.location_id = p_location_id)
                   AND p_event_time >= di.inhibited_from
                   AND p_event_time <= COALESCE(di.inhibited_to, di.restored_at, 'infinity'::timestamptz)),
        (SELECT count(*)::int FROM win WHERE p_event_time BETWEEN actual_from AND actual_to),
        (SELECT COALESCE(sum(c.n),0)::int FROM win w
          JOIN LATERAL (SELECT count(*) n FROM permit.permit_crew pc WHERE pc.permit_id = w.permit_id) c ON true
         WHERE p_event_time BETWEEN w.actual_from AND w.actual_to),
        (SELECT EXTRACT(epoch FROM (p_event_time - max(actual_to)))::int / 60
           FROM hot WHERE actual_to <= p_event_time),
        (SELECT count(*)::int FROM permit.v_permit_active_window w2
          WHERE w2.location_id = p_location_id
            AND w2.contractor_org_id IS NOT NULL
            AND w2.actual_from BETWEEN p_event_time - interval '30 days' AND p_event_time);
$$ LANGUAGE sql STABLE;

-- 9.3. Нагрузка работ на участок за окно — признак для модели пожарного риска.
-- Считаем человеко-часы огневых работ: это прокси «сколько искр было брошено».
CREATE OR REPLACE FUNCTION permit.f_location_hotwork_load(
    p_location_id bigint,
    p_from timestamptz,
    p_to   timestamptz
) RETURNS TABLE (
    hot_permits_cnt     integer,
    hot_man_hours       numeric,
    welding_permits_cnt integer,
    days_since_last_hot integer
) AS $$
    SELECT
        count(*)::int,
        COALESCE(sum(
            EXTRACT(epoch FROM (least(w.actual_to, p_to) - greatest(w.actual_from, p_from))) / 3600.0
            * GREATEST((SELECT count(*) FROM permit.permit_crew pc WHERE pc.permit_id = w.permit_id), 1)
        ), 0),
        count(*) FILTER (WHERE w.work_subtype_code IN ('HOT-01','HOT-02','HOT-04'))::int,
        (SELECT EXTRACT(day FROM (p_to - max(w2.actual_to)))::int
           FROM permit.v_permit_active_window w2
          WHERE w2.location_id = p_location_id AND w2.fire_risk AND w2.actual_to <= p_to)
    FROM permit.v_permit_active_window w
    WHERE w.location_id = p_location_id
      AND w.fire_risk
      AND tstzrange(w.actual_from, w.actual_to) && tstzrange(p_from, p_to);
$$ LANGUAGE sql STABLE;

-- ============================================================================
-- 10. Что НЕ переносится из ЕСНД и почему
-- ============================================================================
-- * Модуль «Замки и бирки» (LOTO): в коллекторах физическая блокировка
--   применяется, но реестра замков у Москоллектора нет. Добавлять на будущее
--   не надо — таблица без данных всё равно мёртвая.
-- * Модуль «Извлечённые уроки» и «Аудиты»: полезны как процесс, но признаков
--   для модели не дают. Заводить, когда появится источник данных.
-- * Двуязычность (рус/англ): в ЕСНД была обязательной из-за иностранных
--   подрядчиков Total. Москоллектору не нужна.
-- * Интерактивная матрица оценки рисков ОР2: поля probability/severity в
--   permit_hazard заведены, но саму матрицу без методики заказчика не заполнить.
