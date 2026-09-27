-- MOS-184: один выбор участков на всё время жизни предупреждения.
-- Установка перед кодом worker; 035/037 и исторические заявки не меняются.
CREATE TABLE pred.warning_order_selection (
    pfx text NOT NULL,
    opened_at timestamptz NOT NULL,
    plan jsonb NOT NULL CHECK (jsonb_typeof(plan) = 'object'),
    PRIMARY KEY (pfx, opened_at)
);
COMMENT ON TABLE pred.warning_order_selection IS
    'MOS-184: неизменный первый план заявок по предупреждению; пустой объект '
    'запечатывает уже выданное старым worker предупреждение без добавления заявок';

-- И меньше трёх, и прежние превышения оставляем как есть: решение об очистке
-- принадлежит владельцу стенда. Изменение весов не должно дополнять историю.
INSERT INTO pred.warning_order_selection(pfx, opened_at, plan)
SELECT DISTINCT split_part(source_key, ':', 2), warning_opened_at, '{}'::jsonb
FROM maint.notification
WHERE source_system = 'forecast'
  AND split_part(source_key, ':', 1) = 'warn'
  AND warning_opened_at IS NOT NULL;

UPDATE ref.app_setting SET value = 3 WHERE key = 'order_top_sections_per_object';
DO $$
BEGIN
    ASSERT (SELECT value = 3 FROM ref.app_setting
            WHERE key = 'order_top_sections_per_object'), 'MOS-184: лимит должен быть 3';
END $$;
