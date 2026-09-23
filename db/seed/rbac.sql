-- rbac.sql — какие разрешения даёт каждая роль, четыре тестовые учётные записи,
-- их роли и область видимости. Таблицы заводят db/migrations/008_rbac.sql
-- (ref.app_user, ref.role_permission) и 044_roles_scope.sql (ref.user_role,
-- ref.user_scope), этот файл их только наполняет.
--
-- Коды разрешений называются по методу, который они открывают: risks.read
-- закрывает GET /api/risks (Q4.3), forecasts.read — GET /api/forecasts(/{id})
-- (Q4.3), orders.read — GET /api/orders(/{id}) (Q4.6), audit.read — GET /api/audit
-- (Q4.10), objects.read — GET /api/objects/{id}(/readings) (Q4.4), settings.read
-- и settings.write — GET и PUT /api/settings(/{key}) (Q4.12). Новый метод API
-- добавляет свой permission_code сюда же — это правка сида, а не схемы.
--
-- Роли — четыре, как их назвал заказчик 17.09.2026 (MOS-107, Q4.11). НФ-44:
-- диспетчер, диспетчер ОДС и техник видят мониторинг (риски, прогнозы, объекты,
-- заявки), но не настройки и не журнал аудита; администратор видит всё. Чем
-- диспетчер отличается от техника — не разрешениями, а областью видимости
-- (ref.user_scope, backend/app/auth/deps.py).
--
-- Накатывать после db/migrations/044_roles_scope.sql:
--   docker compose exec -T db psql -U moskollektor -d moskollektor -f /dev/stdin < db/seed/rbac.sql

INSERT INTO ref.role_permission (role_code, permission_code) VALUES
    ('dispatcher',     'risks.read'),
    ('dispatcher',     'forecasts.read'),
    ('dispatcher',     'orders.read'),
    ('dispatcher',     'objects.read'),
    ('ods_dispatcher', 'risks.read'),
    ('ods_dispatcher', 'forecasts.read'),
    ('ods_dispatcher', 'orders.read'),
    ('ods_dispatcher', 'objects.read'),
    ('technician',     'risks.read'),
    ('technician',     'forecasts.read'),
    ('technician',     'orders.read'),
    ('technician',     'objects.read'),
    ('admin',          'risks.read'),
    ('admin',          'forecasts.read'),
    ('admin',          'orders.read'),
    ('admin',          'objects.read'),
    ('admin',          'audit.read'),
    ('admin',          'settings.read'),
    ('admin',          'settings.write')

ON CONFLICT (role_code, permission_code) DO NOTHING;

-- Четыре тестовые записи, по одной на роль. analyst1 и engineer1 из прежнего
-- сида 044 отключила (is_active = false), строки остались ради внешних ключей.
INSERT INTO ref.app_user (login, full_name, auth_source) VALUES
    ('dispatcher1', 'Тестовый диспетчер',     'local'),
    ('ods1',        'Тестовый диспетчер ОДС', 'local'),
    ('tech1',       'Тестовый техник',        'local'),
    ('admin1',      'Тестовый администратор', 'local')

ON CONFLICT (login) DO UPDATE SET
    full_name   = excluded.full_name,
    auth_source = excluded.auth_source;

INSERT INTO ref.user_role (login, role_code) VALUES
    ('dispatcher1', 'dispatcher'),
    ('ods1',        'ods_dispatcher'),
    ('tech1',       'technician'),
    ('admin1',      'admin')

ON CONFLICT DO NOTHING;

-- Область видимости. Район в выгрузке один (5773 «Район по эксплуатации»), поэтому
-- диспетчер района видит все 3 173 участка. Техник — коллектор 6 «объект Бета»:
-- 79 участков по УЧАСТКИ_КОЛЛЕКТОРА и 11 заявок из 303 на 23.09.2026.
INSERT INTO ref.user_scope (login, object_id) VALUES
    ('dispatcher1', 5773),
    ('tech1',       6)

ON CONFLICT DO NOTHING;
