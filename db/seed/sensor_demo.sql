-- СИНТЕТИКА (synthetic-demo): паспорта оборудования, поверки и моточасы выдуманы,
-- реальны только каналы smvu.channel. Эпик MOS-248, задача SL.1 (MOS-250).
--
-- Что делает. Каждому активному каналу smvu.channel (is_active AND NOT is_stub, на
-- стенде около 11,5 тыс.) без собственного оборудования заводит строку
-- asset.equipment с source_system = 'synthetic-demo' и привязывает к ней канал
-- (smvu.channel.equipment_id). Газоанализатору и датчику температуры — точку
-- «Поверка» с историей поверок, насосу и вентилятору — счётчик моточасов с историей
-- ТО. Всё считается в базе от hashtext(channel_id || ':' || соль): одна и та же
-- база даёт один и тот же паспорт на каждом прогоне, а файл остаётся маленьким.
--
-- Чего НЕ читает. Отказы, показания и прогнозы (smvu.model_failure_*, smvu.reading,
-- smvu.fault_*, pred.*): подгони мы год выпуска или просрочку поверки под реальные
-- отказы — балл «угадывал» бы ответ, который сам же подсмотрел. Проверяет
-- code/synth_sensor_level.py: текстом (--selfcheck) и на базе (--db).
--
-- Когда срабатывает. Накатывает контейнер migrate на каждом прогоне
-- (backend/app/migrate.py), в своей транзакции. Активных каналов в базе нет (чистая
-- установка до заливки) — молчит. Метка версии в ref.equipment_type 'S' совпала
-- и у каждого активного канала есть оборудование — ничего не делает. Иначе сносит
-- свою прошлую синтетику и пишет заново. Метка — хеш тела этого файла; правишь
-- файл — пересчитай её: python3 code/synth_sensor_level.py --fix-mark.
DO $synth$
DECLARE
  mark constant text := 'Оборудование СМВУ, synthetic-demo 4454376257ab';
  -- Дата, от которой отсчитана синтетика (конец архива СМВУ). Константа, а не срез
  -- прогноза: паспорт не должен меняться от того, на какой момент считаем балл.
  ref_date constant date := '2026-06-30';
BEGIN
IF NOT EXISTS (SELECT 1 FROM smvu.channel WHERE is_active AND NOT is_stub) THEN
  RAISE NOTICE 'sensor_demo: активных каналов нет, пропускаю';
  RETURN;
END IF;
IF EXISTS (SELECT 1 FROM ref.equipment_type WHERE code = 'S' AND name = mark)
   AND NOT EXISTS (SELECT 1 FROM smvu.channel
                    WHERE is_active AND NOT is_stub AND equipment_id IS NULL) THEN
  RETURN;
END IF;

UPDATE smvu.channel SET equipment_id = NULL
 WHERE equipment_id IN (SELECT id FROM asset.equipment WHERE source_system = 'synthetic-demo');
DELETE FROM asset.measurement WHERE source_system = 'synthetic-demo';
DELETE FROM asset.measuring_point
 WHERE equipment_id IN (SELECT id FROM asset.equipment WHERE source_system = 'synthetic-demo');
DELETE FROM asset.equipment_install_history
 WHERE equipment_id IN (SELECT id FROM asset.equipment WHERE source_system = 'synthetic-demo');
DELETE FROM asset.equipment WHERE source_system = 'synthetic-demo';

-- Вид оборудования по smvu.sensor_kind — все 19 видов выгрузки и «прочий» (NULL)
-- для неизвестного. meas: calib — поверка раз в 365 сут, motohours — ТО по
-- моточасам раз в 182 сут (интервалы — app.domain.sensor_risk.INTERVAL), NULL —
-- без точки измерения. hours — плановая годовая наработка счётчика.
CREATE TEMP TABLE syn_kind ON COMMIT DROP AS
SELECT * FROM (VALUES
  ('Газовый датчик',        'DEGD', 'Газоанализатор стационарный',         'A', 10, 'ООО «Газсенсор-Демо»',    'АО «Аналит-Демо»',            'calib',     NULL),
  ('Датчик температуры',    'DETM', 'Датчик температуры',                  'B', 10, 'АО «Аналит-Демо»',        'ООО «Термоприбор-Демо»',      'calib',     NULL),
  ('Состояние насоса',      'PUCE', 'Насос дренажный центробежный',        'B', 10, 'ООО «Дренаж-Демо»',       'АО «Насосмаш-Демо»',          'motohours', 1500),
  ('Состояние вентилятора', 'ATFA', 'Вентилятор приточно-вытяжной',        'B', 15, 'АО «Вентмаш-Демо»',       'ООО «Аэро-Демо»',             'motohours', 4000),
  ('Датчик затопления',     'DEFL', 'Датчик затопления',                   'A', 10, 'ООО «Дренаж-Демо»',       'ООО «Газсенсор-Демо»',        NULL,        NULL),
  ('Датчик дыма',           'DESM', 'Извещатель пожарный дымовой',         'B', 10, 'ООО «Пожсигнал-Демо»',    'АО «Извещатель-Демо»',        NULL,        NULL),
  ('Тепловой датчик',       'DEHT', 'Извещатель пожарный тепловой',        'B', 10, 'ООО «Пожсигнал-Демо»',    'АО «Извещатель-Демо»',        NULL,        NULL),
  ('Ручной извещатель',     'DEMC', 'Извещатель пожарный ручной',          'B', 10, 'ООО «Пожсигнал-Демо»',    'АО «Извещатель-Демо»',        NULL,        NULL),
  ('ИБП',                   'ELUP', 'Источник бесперебойного питания',     'B',  8, 'ООО «Энергорезерв-Демо»', 'АО «Электроавтоматика-Демо»', NULL,        NULL),
  ('Состояние фазы',        'SWPH', 'Реле контроля фаз',                   'C', 12, 'ООО «Релейка-Демо»',      'АО «Электроавтоматика-Демо»', NULL,        NULL),
  ('КД АВ',                 'SWAV', 'Контакт автоматического выключателя', 'C', 15, 'ООО «Релейка-Демо»',      'АО «Электроавтоматика-Демо»', NULL,        NULL),
  ('Переключатель',         'SWMS', 'Переключатель режима',                'C', 15, 'ООО «Релейка-Демо»',      'АО «Электроавтоматика-Демо»', NULL,        NULL),
  ('Состояние УИР-Р',       'SWUR', 'Устройство индикации и регистрации',  'C', 12, 'ООО «Релейка-Демо»',      'ООО «Охрана-Демо»',           NULL,        NULL),
  ('Состояние охраны',      'SWSC', 'Прибор приёмно-контрольный охранный', 'C', 10, 'ООО «Охрана-Демо»',       'АО «Извещатель-Демо»',        NULL,        NULL),
  ('Датчик движения',       'DEMO', 'Извещатель охранный объёмный',        'C', 10, 'ООО «Охрана-Демо»',       'АО «Извещатель-Демо»',        NULL,        NULL),
  ('Стекло',                'DEGL', 'Извещатель охранный разбития стекла', 'C', 10, 'ООО «Охрана-Демо»',       'АО «Извещатель-Демо»',        NULL,        NULL),
  ('КД Дверь',              'DEDR', 'Датчик положения двери',              'C', 15, 'ООО «Охрана-Демо»',       'ООО «Релейка-Демо»',          NULL,        NULL),
  ('КД Люк',                'DEHA', 'Датчик положения люка',               'C', 15, 'ООО «Охрана-Демо»',       'ООО «Релейка-Демо»',          NULL,        NULL),
  ('9-секционный люк',      'DEH9', 'Датчик положения 9-секционного люка', 'C', 15, 'ООО «Охрана-Демо»',       'ООО «Релейка-Демо»',          NULL,        NULL),
  (NULL,                    'SWXX', 'Датчик прочий',                       'C', 12, 'ООО «Релейка-Демо»',      'ООО «Охрана-Демо»',           NULL,        NULL)
) AS k(sensor_kind, code, kind_name, crit, life, maker1, maker2, meas, hours);

INSERT INTO ref.equipment_type (code, name, number_range_from, number_range_to)
VALUES ('S', 'Оборудование СМВУ, synthetic-demo', 900000000, 999999999)
ON CONFLICT (code) DO NOTHING;
INSERT INTO ref.object_kind (code, family_code, name_ru, name_en)
SELECT code, left(code, 2), kind_name, 'synthetic-demo' FROM syn_kind
ON CONFLICT (code) DO NOTHING;
INSERT INTO ref.manufacturer (name)
SELECT maker1 FROM syn_kind UNION SELECT maker2 FROM syn_kind
ON CONFLICT (name) DO NOTHING;
INSERT INTO ref.characteristic (code, name, data_type, uom, decimals) VALUES
  ('SYN_CALIB_ERR', 'Основная погрешность при поверке, synthetic-demo', 'num', '%', 2),
  ('SYN_MOTOHOURS', 'Наработка, моточасы, synthetic-demo', 'num', 'ч', 0)
ON CONFLICT (code) DO NOTHING;

-- Одна строка на канал. Каждая случайная величина — hashtext от channel_id и соли,
-- приведённый к [0, 1). На входе только колонки справочника каналов.
CREATE TEMP TABLE syn ON COMMIT DROP AS
WITH u AS (
  SELECT c.channel_id, c.name, c.section_id, c.object_id, c.sensor_kind,
         coalesce(k.code, 'SWXX') AS code,
         (hashtext(c.channel_id || ':build') & 2147483647) / 2147483648.0 AS u_build,
         (hashtext(c.channel_id || ':month') & 2147483647) / 2147483648.0 AS u_month,
         (hashtext(c.channel_id || ':lag')   & 2147483647) / 2147483648.0 AS u_lag,
         (hashtext(c.channel_id || ':maker') & 2147483647) / 2147483648.0 AS u_maker,
         (hashtext(c.channel_id || ':model') & 2147483647) / 2147483648.0 AS u_model,
         (hashtext(c.channel_id || ':price') & 2147483647) / 2147483648.0 AS u_price,
         (hashtext(c.channel_id || ':last')  & 2147483647) / 2147483648.0 AS u_last,
         (hashtext(c.channel_id || ':rate')  & 2147483647) / 2147483648.0 AS u_rate,
         (hashtext(c.channel_id || ':serial') & 2147483647) % 10000000 AS serial
    FROM smvu.channel c
    LEFT JOIN syn_kind k ON k.sensor_kind = c.sensor_kind
   WHERE c.is_active AND NOT c.is_stub AND c.equipment_id IS NULL
), d AS (
  SELECT u.*, k.kind_name, k.crit, k.life, k.meas, k.hours,
         CASE WHEN u.u_maker < 0.5 THEN k.maker1 ELSE k.maker2 END AS maker,
         make_date(2008 + floor(u.u_build * 16)::int, 1 + floor(u.u_month * 12)::int, 1) AS build
    FROM u JOIN syn_kind k ON k.code = u.code
)
SELECT d.*,
       least(d.build + 30 + floor(d.u_lag * 336)::int, ref_date) AS in_service,
       CASE d.meas WHEN 'calib' THEN 365 WHEN 'motohours' THEN 182 END AS iv
  FROM d;

INSERT INTO asset.equipment (
  equipment_no, name, equipment_type_id, object_kind_id, func_location_id, district_id,
  criticality_id, valid_from, in_service_from, purchase_date, purchase_value,
  manufacturer_id, manufacturer_country, model_no, serial_no, build_year, build_month,
  service_life_years, system_status, source_system, source_key)
SELECT (900000000 + s.channel_id)::text,
       s.kind_name || ' ' || coalesce(s.name, s.channel_id::text),
       (SELECT id FROM ref.equipment_type WHERE code = 'S'),
       (SELECT id FROM ref.object_kind WHERE code = s.code),
       x.func_location_id,
       (SELECT id FROM ref.district WHERE code = '01'),
       (SELECT id FROM ref.criticality WHERE code = s.crit),
       s.in_service, s.in_service, s.build,
       round((8 + s.u_price * CASE WHEN s.hours IS NULL THEN 17 ELSE 52 END) * 10) * 100,
       (SELECT id FROM ref.manufacturer WHERE name = s.maker), 'RU',
       s.code || '-' || (100 * (1 + floor(s.u_model * 3)::int)),
       'SD-' || lpad(s.serial::text, 7, '0'),
       extract(year FROM s.build), extract(month FROM s.build),
       s.life, 'INSTALLED', 'synthetic-demo', s.channel_id::text
  FROM syn s
  LEFT JOIN ref.object_xref x ON x.section_id = s.section_id;

UPDATE smvu.channel c SET equipment_id = e.id
  FROM asset.equipment e
 WHERE e.source_system = 'synthetic-demo' AND e.source_key = c.channel_id::text
   AND c.equipment_id IS NULL;

-- created_by ссылается на app_user (001_assets.sql), метка синтетики — в reason.
INSERT INTO asset.equipment_install_history (equipment_id, func_location_id, installed_at, reason)
SELECT e.id, e.func_location_id, e.in_service_from, 'Первичный монтаж, synthetic-demo'
  FROM asset.equipment e
 WHERE e.source_system = 'synthetic-demo';

INSERT INTO asset.measuring_point (point_no, name, equipment_id, characteristic_id,
                                   is_counter, annual_estimate, upper_limit)
SELECT 9000000000 + s.channel_id,
       CASE s.meas WHEN 'calib' THEN 'Поверка' ELSE 'Наработка' END,
       (SELECT id FROM asset.equipment WHERE equipment_no = (900000000 + s.channel_id)::text),
       (SELECT id FROM ref.characteristic
         WHERE code = CASE s.meas WHEN 'calib' THEN 'SYN_CALIB_ERR' ELSE 'SYN_MOTOHOURS' END),
       s.meas = 'motohours', s.hours,
       CASE s.meas WHEN 'calib' THEN 10.0 END
  FROM syn s
 WHERE s.meas IS NOT NULL;

-- История проверок: последняя — от 10 сут до двух интервалов до ref_date (около
-- трети просрочена), дальше назад шагом интервал ± 20 сут, пока не упрёмся во ввод.
-- Датчики узлов из окон match = 'sure' графика ППР заказчика (maint.ppr_window,
-- сид ppr_2026.sql накатывается раньше этого) поверены в день вывоза из ОМ: газ
-- 5657 «объект Каппа ДУ» — 18.06.2026, 5675 «объект Мю ДУ» — 07.05.2026. Иначе экран
-- писал бы рядом «плановый демонтаж на поверку» и «поверка просрочена». Погрешность поверки 0,5…11 %, выше 10 % — вне допуска. Моточасы
-- растут от ввода с годовой наработкой вида × 0,6…1,3.
INSERT INTO asset.measurement (point_id, measured_at, value_num, delta_num,
                               is_out_of_limit, source_system)
SELECT m.point_id, (m.d + time '10:00') AT TIME ZONE 'Europe/Moscow', m.v,
       CASE WHEN m.meas = 'motohours'
            THEN m.v - coalesce(lag(m.v) OVER (PARTITION BY m.point_id ORDER BY m.d), 0) END,
       m.meas = 'calib' AND m.v > 10, 'synthetic-demo'
  FROM (
    SELECT p.point_id, p.meas, g.d,
           CASE p.meas
             WHEN 'calib' THEN round(0.5 + ((hashtext(p.channel_id || ':v' || g.k)
                                             & 2147483647) / 2147483648.0) * 10.5, 2)
             ELSE round((g.d - p.in_service) / 365.0 * p.hours * (0.6 + 0.7 * p.u_rate))
           END AS v
      FROM (
        SELECT s.*, mp.id AS point_id,
               coalesce((SELECT max(w.return_to) FROM maint.ppr_window w
                          WHERE w.match = 'sure' AND w.object_id = s.object_id
                            AND w.sensor_kind = s.sensor_kind AND w.return_to <= ref_date),
                        ref_date - (10 + floor(s.u_last * (2 * s.iv - 10))::int)) AS last
          FROM syn s
          JOIN asset.measuring_point mp ON mp.point_no = 9000000000 + s.channel_id
      ) p
     CROSS JOIN LATERAL (
        SELECT k, p.last - k * p.iv
                  + CASE WHEN k = 0 THEN 0
                         ELSE floor(((hashtext(p.channel_id || ':j' || k) & 2147483647)
                                     / 2147483648.0) * 41)::int - 20 END AS d
          FROM generate_series(0, greatest(p.last - p.in_service, 0) / (p.iv - 20)) k
     ) g
     WHERE g.d >= p.in_service
  ) m;

UPDATE ref.equipment_type SET name = mark WHERE code = 'S';
END
$synth$;
