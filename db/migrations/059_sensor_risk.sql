-- 059. Балл риска по каждому датчику. Эпик MOS-248, задача SL.3 (MOS-252).
--
-- ЗАЧЕМ. До 059 балл датчика считал сам метод GET /api/sensor-risk на лету и только
-- по одному узлу (5657 «объект Каппа ДУ», 188 каналов). На весь парк — около 11,5 тыс.
-- активных каналов — так считать на каждый запрос дорого, а плиткам дашборда нужен
-- счёт по всему парку сразу. Теперь балл считает worker после прогноза
-- (app.worker.sensor_scores), а метод читает готовую строку.
--
-- Формула одна — app.domain.sensor_risk.score(). Балл хранится двумя слагаемыми:
--   score_real  — только реальные отказы smvu.model_failure_event (давность и число
--                 за 90 сут), это балл при ?synthetic=0;
--   score_synth — добавка синтетического паспорта (выработка срока, давность поверки
--                 или ТО, source_system = 'synthetic-demo'); при ?synthetic=1 балл —
--                 сумма слагаемых.
-- Уровни посчитаны тем же sensor_risk.level() от каждого из двух баллов:
-- level_real — от score_real, level_full — от суммы. reasons — причины полного
-- балла, у каждой kind: real, synthetic или plan; при ?synthetic=0 метод
-- выбрасывает synthetic.
--
-- as_of — тот же срез, что у GET /api/risks: max(as_of) в pred.forecast_current.
-- Тик держит строки последних суток до среза и удаляет всё новее: при проигрывании
-- архива срез ходит по часу, и таблица не растёт больше чем на 25 срезов.

CREATE TABLE pred.sensor_risk (
    channel_id   integer     NOT NULL REFERENCES smvu.channel(channel_id) ON DELETE CASCADE,
    as_of        timestamptz NOT NULL,
    score_real   real        NOT NULL CHECK (score_real >= 0),
    score_synth  real        NOT NULL CHECK (score_synth >= 0),
    level_real   text        NOT NULL CHECK (level_real IN ('high', 'watch', 'normal')),
    level_full   text        NOT NULL CHECK (level_full IN ('high', 'watch', 'normal')),
    reasons      jsonb       NOT NULL,
    computed_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (channel_id, as_of)
);

CREATE INDEX sensor_risk_as_of_idx ON pred.sensor_risk (as_of);

COMMENT ON TABLE pred.sensor_risk IS
    'Балл риска по датчику на срез прогноза: реальная часть и синтетическая добавка. MOS-252';
COMMENT ON COLUMN pred.sensor_risk.score_real IS
    'Балл только по реальным отказам smvu.model_failure_event; ?synthetic=0';
COMMENT ON COLUMN pred.sensor_risk.score_synth IS
    'Добавка синтетического паспорта (synthetic-demo): полный балл = score_real + score_synth';
COMMENT ON COLUMN pred.sensor_risk.reasons IS
    'Причины полного балла [{text, weight, kind: real|synthetic|plan}] по убыванию веса';
