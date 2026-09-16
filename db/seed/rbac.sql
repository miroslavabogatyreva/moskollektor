-- rbac.sql — какие разрешения даёт каждая роль, и четыре тестовые учётные записи.
-- Таблицы ref.app_user и ref.role_permission заводит db/migrations/008_rbac.sql
-- (задача MOS-38, Q4.1), эта задача их только наполняет.
--
-- Коды разрешений называются по методу, который они открывают: risks.read
-- закрывает GET /api/risks (Q4.3), forecasts.read — GET /api/forecasts(/{id})
-- (Q4.3), orders.read — GET /api/orders(/{id}) (Q4.6), audit.read — GET /api/audit
-- (Q4.10). Новый метод API добавляет свой permission_code сюда же — это правка
-- сида, а не схемы.
--
-- Кто что получает — НФ-44: диспетчер видит мониторинг и дашборды (риски,
-- прогнозы, заявки), но не настройки и не журнал аудита; администратор видит
-- всё, включая audit.read. Аналитик и инженер по ТЗ разд. 12 тоже читают
-- мониторинг — им обоим нужна та же тройка, что диспетчеру.
--
-- Накатывать после db/migrations/008_rbac.sql:
--   docker compose exec -T db psql -U moskollektor -d moskollektor -f /dev/stdin < db/seed/rbac.sql

INSERT INTO ref.role_permission (role_code, permission_code) VALUES
    ('dispatcher', 'risks.read'),
    ('dispatcher', 'forecasts.read'),
    ('dispatcher', 'orders.read'),
    ('analyst',    'risks.read'),
    ('analyst',    'forecasts.read'),
    ('analyst',    'orders.read'),
    ('engineer',   'risks.read'),
    ('engineer',   'forecasts.read'),
    ('engineer',   'orders.read'),
    ('admin',      'risks.read'),
    ('admin',      'forecasts.read'),
    ('admin',      'orders.read'),
    ('admin',      'audit.read')

ON CONFLICT (role_code, permission_code) DO NOTHING;

-- Четыре тестовые записи, по одной на роль — без них require(permission_code)
-- нечем проверить до того, как Q4.2 подключит вход по LDAP или локальный.
INSERT INTO ref.app_user (login, full_name, role_code, auth_source) VALUES
    ('dispatcher1', 'Тестовый диспетчер',    'dispatcher', 'local'),
    ('analyst1',    'Тестовый аналитик',     'analyst',    'local'),
    ('engineer1',   'Тестовый инженер',      'engineer',   'local'),
    ('admin1',      'Тестовый администратор','admin',      'local')

ON CONFLICT (login) DO UPDATE SET
    full_name   = excluded.full_name,
    role_code   = excluded.role_code,
    auth_source = excluded.auth_source;
