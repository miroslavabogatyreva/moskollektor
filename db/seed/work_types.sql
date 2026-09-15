-- work_types.sql — виды нарядов-допуска для permit.permit_work_type
-- и их огневые подтипы для permit.permit_work_subtype (обе таблицы уже
-- созданы в db/migrations/003_permits.sql, эта задача их не трогает —
-- только наполняет). Задача MOS-23 (Q2.2), тот же code/work_types.json,
-- на который уже ссылается комментарий у CREATE TABLE permit.permit_work_type
-- («Наполняется из work_types.json») и Q6 плана («Справочник видов работ
-- здесь не заводится — его кладёт Q2.2»).
--
-- Источник — code/work_types.json, ключ "work_types" (9 видов) и
-- "subtypes" внутри HOT (15 огневых подвидов, нужны чтобы отличить сварку
-- от пайки: разная интенсивность дыма для фильтра ложных срабатываний
-- дымовых датчиков). Остальные разделы файла (hot_work_categories,
-- ignition_sources, hazard_factors_by_energy, required_attachments,
-- checklists, typical_list_grkb) в схему НЕ легли — под них нет колонок
-- в permit.permit_work_type/permit_work_subtype, а заводить колонки под
-- данные, которые сейчас никто не читает, — не наша задача (см. ponytail
-- в CLAUDE.md проекта). Если понадобятся, это отдельная миграция.
--
-- validity_hours/reapproval_hours — часы, а не дни: так задано в самой
-- колонке (003_permits.sql, комментарий «HOT=72, прочие ПО=672 (28 сут)»).
-- Считаю как validity_days_yamal * 24: HOT 3 сут -> 72 ч, ГАЗ/высота/
-- электро/земля/прочие ПО 28 сут -> 672 ч с переутверждением 7 сут -> 168 ч,
-- ROUTINE 1 сут -> 24 ч. У CSE и LFT в источнике нет числа суток вообще
-- (CSE — часть газоопасных работ по классификатору Ямал СПГ, LFT — только
-- текстовый срок КТК «не более 14 дней», числа Ямал нет) — validity_hours
-- NULL, а не придуманное число.
--
-- ЛОВУШКА: gas_test_required у permit_work_type — boolean, а источник для
-- EXC и OTH пишет не да/нет, а «если применимо» (условие, а не факт).
-- Записываю false, а не true — с текстом как повод не решать за регламент,
-- какое именно условие. Отдельно фиксирую здесь для отчёта: если EXC
-- (земляные работы) или OTH ведутся вблизи газовых сетей, газоанализ
-- фактически нужен, но эта таблица такого условия не хранит — при
-- потребности нужна отдельная колонка или связь с ref.setpoint (метан),
-- не выдумываю её сейчас, раз задача о ней не просила.
--
-- Накатывать после того, как есть permit.permit_work_type/permit_work_subtype
-- (003_permits.sql — уже накатан на стенде), порядок с 013_setpoints.sql
-- не важен, таблицы разных схем:
--   docker compose exec -T db psql -U moskollektor -d moskollektor -f /dev/stdin < db/seed/work_types.sql

INSERT INTO permit.permit_work_type
    (code, name, color, fire_risk, ignition_source, detector_inhibit_expected, gas_test_required, validity_hours, reapproval_hours)
VALUES
    ('HOT', 'Огневые работы', 'красный', true, true, true, true, 72, NULL),
    ('GAS', 'Газоопасные работы', 'голубой', true, false, false, true, 672, 168),
    ('CSE', 'Работы в замкнутом (ограниченном) пространстве', 'голубой (в составе газоопасных)', false, false, false, true, NULL, NULL),
    ('HGT', 'Работы на высоте', 'без цветовой кодировки (отдельный бланк)', false, false, false, false, 672, 168),
    ('ELE', 'Работы в действующих электроустановках', 'без цветовой кодировки (форма по ПОТЭЭ)', true, true, false, false, 672, 168),
    ('EXC', 'Земляные работы', 'коричневый', false, false, false, false, 672, 168),
    ('LFT', 'Грузоподъёмные работы', 'зелёный (в составе прочих ПО)', false, false, false, false, NULL, NULL),
    ('OTH', 'Другие работы повышенной опасности (общий наряд-допуск)', 'зелёный', false, false, false, false, 672, 168),
    ('ROUTINE', 'Рутинный наряд-допуск (по шаблону)', NULL, false, false, false, false, 24, NULL)

ON CONFLICT (code) DO UPDATE SET
    name                       = excluded.name,
    color                      = excluded.color,
    fire_risk                  = excluded.fire_risk,
    ignition_source            = excluded.ignition_source,
    detector_inhibit_expected  = excluded.detector_inhibit_expected,
    gas_test_required          = excluded.gas_test_required,
    validity_hours             = excluded.validity_hours,
    reapproval_hours           = excluded.reapproval_hours;

INSERT INTO permit.permit_work_subtype
    (code, work_type_code, name, fire_risk, smoke_detector_trigger)
VALUES
    ('HOT-01', 'HOT', 'Электросварка металла', true, false),
    ('HOT-02', 'HOT', 'Газосварочные и газорезательные работы', true, false),
    ('HOT-03', 'HOT', 'Бензорезка и работы с паяльными лампами', true, false),
    ('HOT-04', 'HOT', 'Термитная сварка', true, false),
    ('HOT-05', 'HOT', 'Паяльные работы', true, false),
    ('HOT-06', 'HOT', 'Зачистка металла, бетона углошлифовальными машинками', true, false),
    ('HOT-07', 'HOT', 'Механическая обработка металла с выделением искр', true, false),
    ('HOT-08', 'HOT', 'Работы с применением взрывных технологий', true, false),
    ('HOT-09', 'HOT', 'Разогрев битумов и смол', true, false),
    ('HOT-10', 'HOT', 'Высоковольтные испытания оборудования во взрывопожароопасных зонах', true, false),
    ('HOT-11', 'HOT', 'Проверка на герметичность методом задымления', true, true),
    ('HOT-12', 'HOT', 'Изоляционные работы с применением открытого огня', true, false),
    ('HOT-13', 'HOT', 'Работы со строительно-монтажным пистолетом во взрывопожароопасных зонах', true, false),
    ('HOT-14', 'HOT', 'Работы ручным слесарным искронебезопасным инструментом в газоопасных местах', true, false),
    ('HOT-15', 'HOT', 'Работа в газоопасных зонах с электроинструментом не во взрывозащищённом исполнении', true, false)

ON CONFLICT (code) DO UPDATE SET
    work_type_code          = excluded.work_type_code,
    name                     = excluded.name,
    fire_risk                = excluded.fire_risk,
    smoke_detector_trigger   = excluded.smoke_detector_trigger;
