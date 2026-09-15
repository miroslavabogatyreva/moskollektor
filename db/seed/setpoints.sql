-- setpoints.sql — восемь уставок для ref.setpoint (013_setpoints.sql).
-- Задача MOS-23 (Q2.2). Форма и решения по direction/inclusive — в шапке
-- 013_setpoints.sql, здесь только откуда взято каждое число.
--
-- Пять уставок части II Регламента — дословно из docs/acceptance-test.md,
-- разд. II.1 (строки НФ-56…НФ-59; у НФ-59 два порога на одно число метана —
-- запрет допуска строго выше 1 % и отдельно указание вывести людей уже
-- на 1,0 % ровно, это разные inclusive, см. миграцию). Числа сверены
-- со вторым независимым источником — code/reglament_glossary.json, строки
-- 408-411 (тот же П. 28.2.12, извлечён отдельно при разборе глоссария):
-- вода 200 мм, температура воды 45 °C, кислород 20 %, метан 1 % совпадают.
-- Только «1,0 % и более, указание вывести людей» в глоссарии нет — это
-- НФ-59 добавляет со ссылкой на отдельный п. 24.4.10.
--
-- Три уставки из журнала СМВУ — не из регламента, а из того, что прибор
-- заказчика УЖЕ пишет в данных (docs/day-one.md, раздел «Полный словарь
-- текстовых значений за 2019 год», и code/thermal_anomaly_rules.py, где
-- эти же числа откалиброваны по 2025 году). Числа двух источников не
-- совпадают (2019 суточный срез считал иначе, чем 2025 год целиком) —
-- беру более точные годовые из thermal_anomaly_rules.py, docstring
-- r1_absolute(), это подтверждено self-check самого файла.
--
-- Накатывать после 013_setpoints.sql. Роль/база — POSTGRES_USER/POSTGRES_DB
-- из deploy/.env, на стенде moskollektor/moskollektor:
--   docker compose exec -T db psql -U moskollektor -d moskollektor -f /dev/stdin < db/seed/setpoints.sql

INSERT INTO ref.setpoint
    (code, param, low_value, high_value, unit, direction, inclusive, action, source)
VALUES
    ('water-above', 'вода', NULL, 200, 'мм', 'above_bad', false,
     'допуск на участок запрещён',
     'Регламент п. 28.2.12 (дублируется в п. 26.10.4); docs/acceptance-test.md разд. II.1, НФ-56'),

    ('water-temp-above', 'температура воды', NULL, 45, '°C', 'above_bad', false,
     'допуск на участок запрещён',
     'Регламент п. 28.2.12; docs/acceptance-test.md разд. II.1, НФ-57'),

    ('oxygen-below', 'кислород', 20, NULL, '%', 'below_bad', false,
     'допуск на участок запрещён',
     'Регламент п. 28.2.12, «менее 20 %» — строго; docs/acceptance-test.md разд. II.1, НФ-58'),

    ('methane-above-zapret', 'метан', NULL, 1, '%', 'above_bad', false,
     'допуск на участок запрещён',
     'Регламент п. 28.2.12, «выше 1 %» — строго; docs/acceptance-test.md разд. II.1, НФ-59'),

    ('methane-above-evacuate', 'метан', NULL, 1.0, '%', 'above_bad', true,
     'указание: вывести людей, остановить электрические машины, включить вентиляцию',
     'Регламент п. 24.4.10, «1,0 % и более» — нестрого; docs/acceptance-test.md разд. II.1, НФ-59'),

    ('journal-temp-below', 'температура (журнал СМВУ)', 3, NULL, '°C', 'below_bad', true,
     'тревога: температура опустилась до нижней уставки (риск разморозки трубопровода)',
     'журнал СМВУ за 2025 год: «Температура ниже 3ºC» — 1640 записей на 199 каналах, code/thermal_anomaly_rules.py, r1_absolute()'),

    ('journal-temp-above', 'температура (журнал СМВУ)', NULL, 40, '°C', 'above_bad', true,
     'тревога: температура достигла верхней уставки',
     'журнал СМВУ за 2025 год: «Температура выше 40ºC» — 367 записей на 35 каналах, code/thermal_anomaly_rules.py, r1_absolute()'),

    ('journal-temp-normal', 'температура (журнал СМВУ)', 3, 40, '°C', 'normal_range', true,
     'норма: рабочий диапазон температуры по факту журнала СМВУ, не тревога',
     'журнал СМВУ, значение «В норме от +3 до +40» — 1649 записей за 2019 год, docs/day-one.md, раздел «Полный словарь текстовых значений»')

ON CONFLICT (code) DO UPDATE SET
    param      = excluded.param,
    low_value  = excluded.low_value,
    high_value = excluded.high_value,
    unit       = excluded.unit,
    direction  = excluded.direction,
    inclusive  = excluded.inclusive,
    action     = excluded.action,
    source     = excluded.source;
