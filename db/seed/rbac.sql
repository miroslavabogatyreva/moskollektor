-- rbac.sql — какие разрешения даёт каждая роль, четыре тестовые учётные записи,
-- их роли и область видимости. Таблицы заводят db/migrations/008_rbac.sql
-- (ref.app_user, ref.role_permission), 044_roles_scope.sql (ref.user_role,
-- ref.user_scope) и 047_auth.sql (password_hash, ref.ldap_role_map),
-- этот файл их только наполняет.
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
-- Накатывать после db/migrations/047_auth.sql:
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

-- Четыре тестовые записи, по одной на роль, и пятая — tech2 для проверки сложения
-- областей видимости. analyst1 и engineer1 из прежнего
-- сида 044 отключила (is_active = false), строки остались ради внешних ключей.
--
-- password_hash — argon2 (db/migrations/047_auth.sql, MOS-39). Пароли первых
-- четырёх — те же, что показывает подсказка GET /api/auth/info
-- (backend/app/api/auth.py, DEMO_ACCOUNTS): демо обязано работать без
-- каталога (решение Славы 24.09.2026). Это ВСЕГДА локальные учётки, отдельные
-- от одноимённых ldap_* в каталоге — у обоих путей входа своя пара
-- «логин/секрет», смешивать их нельзя.
--
-- tech2 в DEMO_ACCOUNTS нарочно не входит (подопытный для проверки сложения
-- областей видимости, не демо-учётка со страницы входа) — пароль записан
-- только здесь: tech2123123. Хеш перегенерирован 24.09.2026 (MOS-226):
-- первый, заведённый в MOS-39 (коммит b373c22), был нигде не записан и
-- необратим — потерялся раньше, чем кто-либо им воспользовался.
INSERT INTO ref.app_user (login, full_name, auth_source, password_hash) VALUES
    ('dispatcher1', 'Тестовый диспетчер',     'local', '$argon2id$v=19$m=65536,t=3,p=4$W3DAWpcjq6AWcOjKU8hXXg$bDZhR+9jFwI74Bg5IuB2yZsaMJ56mVYkCSwatPdrY0E'),
    ('ods1',        'Тестовый диспетчер ОДС', 'local', '$argon2id$v=19$m=65536,t=3,p=4$OP7Y01uuWH56hJrmN4ssLA$DSrZdqPpPn+s6XL+B00xTP+bu502QCJUHQOYCxw//S8'),
    ('tech1',       'Тестовый техник',        'local', '$argon2id$v=19$m=65536,t=3,p=4$VH5tU5alwapN3s4VKo3NfQ$8fMA7COqLtjQ5IlzFLU7Y+MIvmCnny+ENVITMG7yBH4'),
    ('admin1',      'Тестовый администратор', 'local', '$argon2id$v=19$m=65536,t=3,p=4$hi21eVs/+Z4TC+MviMr/qQ$fiWyF7z+LFJYhUBo/YKjDMBOn8c7hbnKcDFs1/4/ud4'),
    ('tech2',       'Тестовый техник двух коллекторов', 'local', '$argon2id$v=19$m=65536,t=3,p=4$qtIfrl0986Qq2OhfN4oJiw$/v/8MrUD81Y4oAS5ntGuG49UpxI5dL9NkWA4dfLF4+4')

ON CONFLICT (login) DO UPDATE SET
    full_name     = excluded.full_name,
    auth_source   = excluded.auth_source,
    password_hash = excluded.password_hash;

-- Каталог демо-стенда (deploy/ldap/bootstrap.ldif, MOS-39/8a): группа → роль
-- ИЛИ область видимости, cn согласованы между 41 и 8a 24.09.2026. Учётки
-- ldap_dispatcher1/ldap_ods1/ldap_tech1/ldap_admin1 сюда не заводим — их
-- ref.app_user создаёт сам вход при первом успешном bind (backend/app/auth/ldap.py).
INSERT INTO ref.ldap_role_map (group_cn, role_code, object_id) VALUES
    ('role-dispatcher', 'dispatcher',     NULL),
    ('role-ods',        'ods_dispatcher', NULL),
    ('role-tech',       'technician',     NULL),
    ('role-admin',      'admin',          NULL),
    ('scope-tech-6',    NULL,             6)

ON CONFLICT (group_cn) DO NOTHING;

INSERT INTO ref.user_role (login, role_code) VALUES
    ('dispatcher1', 'dispatcher'),
    ('ods1',        'ods_dispatcher'),
    ('tech1',       'technician'),
    ('admin1',      'admin'),
    ('tech2',       'technician')

ON CONFLICT DO NOTHING;

-- Область видимости. Район в выгрузке один (5773 «Район по эксплуатации»), поэтому
-- диспетчер района видит все 3 173 участка. Техник — коллектор 6 «объект Бета»:
-- 79 участков по УЧАСТКИ_КОЛЛЕКТОРА и 11 заявок из 303 на 23.09.2026.
-- tech2 — два узла, коллекторы 6 «объект Бета» и 4068 «объект Сигма»: объединение
-- обязано дать 79 + 9 = 88 участков — строго больше, чем у tech1, и строго меньше
-- 3 173. Пара «техник + диспетчер района» дала бы 3 173 и прошла бы проверку
-- даже при сломанном объединении, потому что район в выгрузке один.
INSERT INTO ref.user_scope (login, object_id) VALUES
    ('dispatcher1', 5773),
    ('tech1',       6),
    ('tech2',       6),
    ('tech2',       4068)

ON CONFLICT DO NOTHING;
