-- 054. События журнала ОДС, принятые через API. История US-12 сц. 5, строка
-- приёмки Ф-61: событие от ОДС видно в журнале технологических событий
-- не позже чем через 5 секунд.
--
-- ОТКУДА ОНИ. Журнал ОДС заказчик ведёт в help desk на Django, и «напрямую
-- системы до сих пор не интегрированы и не будут интегрированы» (ответ 10,
-- сводный 6); «Никакие реальные интеграции не предоставляются» (сводный 9).
-- Поэтому события шлёт эмулятор ОДС методом POST /api/ingest/ods-events:
-- внешняя система — с токеном INGEST_TOKEN, как поток СМВУ, или администратор
-- под правом ods_events.write (так эмулятором выступает E2E-тест).
--
-- source_id — ключ события в системе ОДС: повторная отправка той же пачки
-- не заводит дубль (ON CONFLICT DO NOTHING в методе).

CREATE TABLE maint.ods_event (
    event_id    bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id   text        NOT NULL UNIQUE,
    event_time  timestamptz NOT NULL,
    section_id  integer     REFERENCES ref.object_xref(section_id),
    event_text  text        NOT NULL,
    event_type  text        NOT NULL CHECK (event_type IN ('Предупреждение', 'Норма')),
    -- Кто прислал: 'ingest-token' или логин администратора-эмулятора.
    received_by text        NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ods_event_time_idx ON maint.ods_event (event_time);

COMMENT ON TABLE maint.ods_event IS
    'События журнала ОДС из эмулятора через POST /api/ingest/ods-events (US-12 сц. 5, Ф-61)';

INSERT INTO ref.role_permission (role_code, permission_code) VALUES
    ('admin', 'ods_events.write')
ON CONFLICT (role_code, permission_code) DO NOTHING;
