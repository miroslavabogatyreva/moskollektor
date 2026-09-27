-- 056. Исход прогноза — отдельно от решения диспетчера. История US-10, строки
-- приёмки Ф-34, Ф-35, Ф-36, Ф-75, Ф-95.
--
-- ЗАЧЕМ ОТДЕЛЬНО ОТ pred.feedback. Решение (052) — что диспетчер сделал, когда
-- пришёл прогноз: «Выезд бригады», «Мониторинг ситуации»… Исход — чем прогноз
-- кончился на самом деле, отмечается позже: подтвердилось, ложная или не проверяли.
-- В pred.feedback.verdict места под третье значение нет (CHECK verdict IN (0, 1)),
-- а «не проверяли» — честный ответ, который нельзя записать ни в 1, ни в 0.
-- Три исхода и пять причин — наше решение, строки Ф-34 и Ф-35.
--
-- СИСТЕМА ИСХОД САМА НЕ СТАВИТ (ТЗ разд. 5, US-10 сц. 4). Строку пишет только
-- метод POST /api/forecasts/{id}/outcome под правом forecasts.decide, decided_by
-- обязателен. Прогноз без строки здесь с истёкшим горизонтом журнал показывает
-- словами «горизонт истёк», а не выдуманным исходом.

CREATE TABLE ref.forecast_outcome (
    code       text     PRIMARY KEY,
    name       text     NOT NULL,
    sort_order smallint NOT NULL UNIQUE
);

INSERT INTO ref.forecast_outcome (code, name, sort_order) VALUES
    ('confirmed',   'подтвердилось', 1),
    ('false_alarm', 'ложная',        2),
    ('not_checked', 'не проверяли',  3);

-- История, а не одна строка на прогноз: исход могут поправить (сначала «не
-- проверяли», потом бригада доехала). Действует последний по decided_at.
CREATE TABLE pred.forecast_outcome (
    outcome_id   bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    forecast_id  bigint      NOT NULL REFERENCES pred.forecast(forecast_id),
    outcome_code text        NOT NULL REFERENCES ref.forecast_outcome(code),
    -- Причина — только у «ложная» и там обязательна, из пяти (039, Ф-35).
    reason_code  text        REFERENCES ref.feedback_reason(code),
    decided_by   text        NOT NULL,
    decided_at   timestamptz NOT NULL DEFAULT now(),
    CHECK ((outcome_code = 'false_alarm') = (reason_code IS NOT NULL))
);

CREATE INDEX forecast_outcome_forecast_idx ON pred.forecast_outcome (forecast_id, decided_at DESC);

COMMENT ON TABLE pred.forecast_outcome IS
    'Исход прогноза (US-10): подтвердилось / ложная (с причиной) / не проверяли; пишет только человек';
