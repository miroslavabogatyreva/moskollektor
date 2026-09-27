-- 051. Статус заявки из системы учёта заказчика (эмулятор хелпдеска). Задача
-- MOS-63 (план 6.8), приёмка Ф-87, НФ-69, Ф-96.
--
-- Номер 051, не 050: 050 зарезервирован под PR Николая (MOS-184), 041…043 тоже его.
--
-- ТРЕТЬЕЙ КОЛОНКИ «источник смены статуса» НЕТ. Непустой external_status_at сам
-- служит отметкой «пришёл из внешней системы, а не проставлен человеком» (Ф-87) —
-- его выставляет только app.ingest.order_status, ручная смена
-- maint.notification.status через API его не трогает и трогать не может.

ALTER TABLE maint.notification
    ADD COLUMN external_status text,
    ADD COLUMN external_status_at timestamptz,
    ADD COLUMN external_assignee text;

COMMENT ON COLUMN maint.notification.external_status IS
    'Статус заявки в системе учёта заказчика (эмулятор хелпдеска, MOS-63). '
    'null — заявка туда ещё не дошла (эмулятор не отдал статус) или у неё нет due_at';
COMMENT ON COLUMN maint.notification.external_status_at IS
    'Момент смены статуса В ИСТОЧНИКЕ, а не время нашего опроса — сама эта колонка '
    'и есть отметка «пришёл из внешней системы» (Ф-87), непустая только после ingest';
COMMENT ON COLUMN maint.notification.external_assignee IS
    'Бригада/исполнитель, назначенные в системе учёта заказчика';
