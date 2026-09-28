-- 061. Балл датчика считают правила давности и предвестника, а не ручная формула.
-- Эпик MOS-248, задача SL.10 (MOS-263).
--
-- ЗАЧЕМ. До 061 app.domain.sensor_risk складывал ручные веса, и синтетический паспорт
-- мог балл только поднять: score_synth — добавка, отсюда CHECK score_synth >= 0 в 059.
-- Теперь балл — частота отказа канала в ближайшие 24 ч, посчитанная на 2022–2025 годах
-- (пороги и таблицы — backend/app/domain/sensor_rules.json, функция rule_split):
--   score_real  — правило давности: частота отказа при такой давности последнего
--                 подтверждённого отказа канала (реальный журнал СМВУ);
--   score_synth — сколько добавляет правило синтетического предвестника с P-F
--                 (app.domain.failure_sim): балл ?synthetic=1 =
--                 1 − (1 − давность)(1 − предвестник) = score_real + score_synth.
-- При такой сборке score_synth не бывает отрицательным, но CHECK снимаем: это
-- разница двух правил, и замена правила не должна требовать миграции.
-- reasons_real — причины без синтетики: у двух режимов разные наборы причин, и причины
-- полного режима с выброшенными synthetic не обязаны складываться в score_real.
-- GET /api/sensor-risk при ?synthetic=0 отдаёт reasons_real, ответ метода не меняется.
--
-- Старые строки формулы остаются до следующего тика: ограничения ниже они выполняют.

ALTER TABLE pred.sensor_risk DROP CONSTRAINT sensor_risk_score_synth_check;
ALTER TABLE pred.sensor_risk
    ADD CONSTRAINT sensor_risk_score_real_range CHECK (score_real BETWEEN 0 AND 1),
    ADD COLUMN reasons_real jsonb NOT NULL DEFAULT '[]';

COMMENT ON COLUMN pred.sensor_risk.score_real IS
    'Частота отказа канала в ближайшие 24 ч по правилу давности; ?synthetic=0. MOS-263';
COMMENT ON COLUMN pred.sensor_risk.score_synth IS
    'Добавка правила синтетического предвестника; балл ?synthetic=1 = score_real + score_synth. MOS-263';
COMMENT ON COLUMN pred.sensor_risk.reasons IS
    'Причины режима с синтетикой [{text, weight, kind: real|synthetic|plan}] по убыванию веса';
COMMENT ON COLUMN pred.sensor_risk.reasons_real IS
    'Причины режима без синтетики [{text, weight, kind: real|plan}]; ?synthetic=0. MOS-263';
