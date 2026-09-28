-- 061. Балл датчика считает обученная модель, а не ручная формула. Эпик MOS-248,
-- задача SL.10 (MOS-263).
--
-- ЗАЧЕМ. До 061 app.domain.sensor_risk складывал ручные веса, и синтетический паспорт
-- мог балл только поднять: score_synth — добавка, отсюда CHECK score_synth >= 0 в 059.
-- Теперь балл — вероятность отказа канала на горизонте модели (коэффициенты —
-- backend/app/domain/sensor_model.json):
--   score_real  — вероятность модели журнала (реальные признаки, реальные отказы);
--   score_synth — сколько добавляет модель симулированных отказов по паспорту
--                 и синтетическому предвестнику (app.domain.failure_sim): балл
--                 ?synthetic=1 = 1 − (1 − p_real)(1 − p_sim) = score_real + score_synth.
-- При такой сборке score_synth не бывает отрицательным, но CHECK снимаем: это
-- разница двух моделей, и замена модели не должна требовать миграции.
-- reasons_real — причины модели журнала: веса причин у двух режимов разные, и причины
-- полного режима с выброшенными synthetic больше не складываются в score_real.
-- GET /api/sensor-risk при ?synthetic=0 отдаёт reasons_real, ответ метода не меняется.
--
-- Старые строки формулы остаются до следующего тика: ограничения ниже они выполняют.

ALTER TABLE pred.sensor_risk DROP CONSTRAINT sensor_risk_score_synth_check;
ALTER TABLE pred.sensor_risk
    ADD CONSTRAINT sensor_risk_score_real_range CHECK (score_real BETWEEN 0 AND 1),
    ADD COLUMN reasons_real jsonb NOT NULL DEFAULT '[]';

COMMENT ON COLUMN pred.sensor_risk.score_real IS
    'Вероятность отказа канала на горизонте модели журнала; ?synthetic=0. MOS-263';
COMMENT ON COLUMN pred.sensor_risk.score_synth IS
    'Добавка модели паспорта и предвестника; балл ?synthetic=1 = score_real + score_synth. MOS-263';
COMMENT ON COLUMN pred.sensor_risk.reasons IS
    'Причины модели с синтетикой [{text, weight, kind: real|synthetic|plan}] по убыванию веса';
COMMENT ON COLUMN pred.sensor_risk.reasons_real IS
    'Причины модели без синтетики [{text, weight, kind: real|plan}]; ?synthetic=0. MOS-263';
