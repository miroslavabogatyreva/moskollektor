-- 020_app_setting.sql — пороги и горизонт прогноза как данные, а не константа.
-- Задача Q4.12 (MOS-110). Заказчик сказал это дважды на встрече 17.09.2026, как
-- о само собой разумеющемся: «у администратора есть возможность их поправить»
-- [00:12:00] и «это то, что задается в системе, там, в админке». Сегодня горизонт
-- зашит константой ГОРИЗОНТ_Ч в backend/app/worker/run.py, а Precision и Recall
-- живут только в документах — поменять и то и то можно было только пересборкой
-- образа.
--
-- Четыре строки хватает для четырёх задача 4.12 — отдельный db/seed/ не завожу,
-- как не завели его 002_geo.sql, 004_events.sql и 010_orders.sql для своих
-- маленьких справочников: сид прямо в миграции, если он на несколько строк
-- и меняется редко.
CREATE TABLE ref.app_setting (
    key         text        PRIMARY KEY,
    value       numeric     NOT NULL,
    unit        text,                            -- NULL — величина без единицы (доля, вероятность)
    changed_by  bigint      REFERENCES ref.app_user(user_id),  -- NULL — значение сида, никто не менял
    changed_at  timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE ref.app_setting IS 'Пороги и горизонт прогноза, правит администратор через PUT /api/settings/{key}, не пересборкой образа';
COMMENT ON COLUMN ref.app_setting.changed_by IS 'NULL — строка сида, значение по умолчанию, никто не менял через API';

INSERT INTO ref.app_setting (key, value, unit) VALUES
    ('forecast_horizon_h', 24,   'ч'),   -- постановка требует горизонт не меньше 24 часов
    ('precision_min',      0.7,  NULL),  -- постановка: Precision строго больше этого порога
    ('recall_min',         0.5,  NULL),  -- постановка: Recall строго больше этого порога
    ('risk_threshold_high', 0.97, NULL); -- вероятность, выше которой участок красный на дашборде;
                                          -- то же число, что порог класса A в order_rules.py
                                          -- (docs/order-rules.md: медиана 0,125, p90 0,728, p99 0,969)

-- Старое значение рядом с новым для PUT /api/settings/{key} (MOS-110): у
-- audit.user_action (009_audit.sql) нет для этого места, а расширять форму
-- строки под один метод из четырёх — исключение, не правило для всех. details
-- NULL у всех методов, кроме тех, что сами его заполняют через request.state.
ALTER TABLE audit.user_action ADD COLUMN details jsonb;
COMMENT ON COLUMN audit.user_action.details IS 'Заполняет сам метод через request.state.audit_details (например old/new у PUT /api/settings/{key}), NULL у методов, которым нечего туда положить';
