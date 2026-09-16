-- 009_audit.sql — журнал действий пользователей. Задача MOS-47 (Q4.10),
-- приёмка НФ-77: «в журнале должно быть ровно столько записей, сколько
-- действий, у каждой — пользователь, время и объект действия».
--
-- user_id NULLABLE: промежуточный слой в backend/app/api/main.py пишет строку
-- на КАЖДЫЙ запрос кроме /health (буквальный текст тикета), а не только
-- на успешно опознанные — иначе отказ несуществующей учётной записи (401)
-- выпал бы из журнала безопасности молча.

CREATE SCHEMA IF NOT EXISTS audit;
CREATE SCHEMA IF NOT EXISTS ref;  -- уже есть после 008_rbac.sql, файл обязан быть самодостаточным

CREATE TABLE audit.user_action (
    action_id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     bigint REFERENCES ref.app_user(user_id),  -- NULL = запрос не опознан (401 и т.п.)
    occurred_at timestamptz NOT NULL DEFAULT now(),
    method      text NOT NULL,       -- 'GET', 'POST', ...
    path        text NOT NULL,       -- объект действия: '/api/orders/42'
    status_code smallint NOT NULL,
    ip_address  inet
);
COMMENT ON TABLE audit.user_action IS 'Журнал действий пользователей (НФ-77): кто, когда, что запросил и с каким кодом ответа';

CREATE INDEX user_action_user_idx ON audit.user_action (user_id, occurred_at DESC);
