-- 061. Балл датчика считает обученная модель, а не ручная формула. Эпик MOS-248,
-- задача SL.10 (MOS-263).
--
-- ЗАЧЕМ. До 061 app.domain.sensor_risk складывал ручные веса, и синтетический паспорт
-- мог балл только поднять: score_synth — добавка, отсюда CHECK score_synth >= 0 в 059.
-- Теперь моделей две (коэффициенты — backend/app/domain/sensor_model.json):
--   score_real  — вероятность отказа канала модели без синтетики (реальные признаки
--                 журнала, реальные отказы);
--   score_synth — разница «модель с синтетикой минус модель без неё». Модель
--                 с синтетикой учена на симулированном мире (реальные отказы плюс
--                 отказы, досимулированные из паспорта, code/failure_sim.py), и её
--                 вероятность у канала со свежим паспортом бывает НИЖЕ реальной.
--                 Поэтому score_synth может быть отрицательным, а балл при
--                 ?synthetic=1 — по-прежнему score_real + score_synth.
-- reasons_real — причины модели без синтетики: у двух моделей разные веса, и причины
-- полной модели с выброшенными synthetic больше не складываются в score_real.
-- GET /api/sensor-risk при ?synthetic=0 отдаёт reasons_real, ответ метода не меняется.
--
-- Старые строки формулы остаются до следующего тика: ограничения ниже они выполняют.

ALTER TABLE pred.sensor_risk DROP CONSTRAINT sensor_risk_score_synth_check;
ALTER TABLE pred.sensor_risk
    ADD CONSTRAINT sensor_risk_score_real_range CHECK (score_real BETWEEN 0 AND 1),
    ADD COLUMN reasons_real jsonb NOT NULL DEFAULT '[]';

COMMENT ON COLUMN pred.sensor_risk.score_real IS
    'Вероятность отказа канала на горизонте модели, модель без синтетики; ?synthetic=0. MOS-263';
COMMENT ON COLUMN pred.sensor_risk.score_synth IS
    'Модель с синтетикой минус модель без неё, может быть < 0; балл ?synthetic=1 = score_real + score_synth. MOS-263';
COMMENT ON COLUMN pred.sensor_risk.reasons IS
    'Причины модели с синтетикой [{text, weight, kind: real|synthetic|plan}] по убыванию веса';
COMMENT ON COLUMN pred.sensor_risk.reasons_real IS
    'Причины модели без синтетики [{text, weight, kind: real|plan}]; ?synthetic=0. MOS-263';
