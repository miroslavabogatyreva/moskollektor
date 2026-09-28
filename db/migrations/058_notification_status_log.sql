-- 058. История заявки: каждая смена статуса строкой, с источником. US-19 сц. 1,
-- приёмка Ф-87: «в истории заявки записано, что он пришёл из внешней системы,
-- а не проставлен человеком».
--
-- ЗАЧЕМ. До 058 статус из системы учёта лежал одним полем в maint.notification
-- (миграция 051): новый статус затирал прежний, и истории у заявки не было.
-- Строку сюда пишет app.ingest.order_status тем же запросом, что меняет
-- external_status, — одна смена в источнике, одна строка.
--
-- source — кто поставил статус: order_system — пришёл из системы учёта заказчика
-- (сейчас её эмулятор), user — проставлен человеком в сервисе. Ручной смены статуса
-- в API пока нет, значение заведено, чтобы история различала два случая, как
-- требует Ф-87, без новой миграции.

CREATE TABLE maint.notification_status_log (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    notification_id bigint      NOT NULL REFERENCES maint.notification(id) ON DELETE CASCADE,
    status          text        NOT NULL,
    assignee        text,
    changed_at      timestamptz NOT NULL,
    source          text        NOT NULL CHECK (source IN ('order_system', 'user')),
    received_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX notification_status_log_notification_idx
    ON maint.notification_status_log (notification_id, changed_at);

COMMENT ON TABLE maint.notification_status_log IS
    'История статусов заявки, строка на смену. US-19, Ф-87';
COMMENT ON COLUMN maint.notification_status_log.changed_at IS
    'Момент смены статуса в источнике (для order_system — external_status_at)';
COMMENT ON COLUMN maint.notification_status_log.source IS
    'order_system — пришёл из системы учёта заказчика; user — проставлен человеком';
COMMENT ON COLUMN maint.notification_status_log.received_at IS
    'Когда сервис записал строку: для order_system — тик опроса';

-- Статус, полученный до 058, — первая строка истории: он тоже пришёл из системы учёта.
INSERT INTO maint.notification_status_log (notification_id, status, assignee, changed_at, source)
SELECT id, external_status, external_assignee, external_status_at, 'order_system'
  FROM maint.notification
 WHERE external_status IS NOT NULL AND external_status_at IS NOT NULL;
