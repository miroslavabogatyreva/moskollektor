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
-- Накатывает контейнер migrate после миграций, заново на каждом прогоне
-- (MOS-119): вставки идемпотентные, повтор строк не удваивает. Порядок —
-- после db/migrations/047_auth.sql. Руками — когда нужен один файл, а не весь
-- migrate:
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
--
-- Вставка идемпотентная, но ОБНОВЛЯЕТ ТОЛЬКО full_name, и это сознательно
-- (MOS-119, 26.09.2026): сид накатывается заново на каждом старте migrate,
-- и любая другая колонка здесь была бы дырой.
--   * password_hash не трогаем: администратор меняет пароль, а следующая
--     выкладка вернула бы демо-хеш — подмена пароля вместо наката сида.
--   * auth_source не трогаем: первый вход через каталог LDAP ставит
--     auth_source='ldap' и password_hash=NULL (backend/app/api/auth.py:142),
--     и DO UPDATE с excluded вернул бы ldap-учётке 'local' и демо-хеш — снял бы
--     вход по каталогу и оставил учётку с известным паролем.
--   * is_active не трогаем и не включаем в SET: блокировка из интерфейса
--     (MOS-226) обязана переживать перезаливку сида.
INSERT INTO ref.app_user (login, full_name, auth_source, password_hash) VALUES
    ('dispatcher1', 'Тестовый диспетчер',     'local', '$argon2id$v=19$m=65536,t=3,p=4$W3DAWpcjq6AWcOjKU8hXXg$bDZhR+9jFwI74Bg5IuB2yZsaMJ56mVYkCSwatPdrY0E'),
    ('ods1',        'Тестовый диспетчер ОДС', 'local', '$argon2id$v=19$m=65536,t=3,p=4$OP7Y01uuWH56hJrmN4ssLA$DSrZdqPpPn+s6XL+B00xTP+bu502QCJUHQOYCxw//S8'),
    ('tech1',       'Тестовый техник',        'local', '$argon2id$v=19$m=65536,t=3,p=4$VH5tU5alwapN3s4VKo3NfQ$8fMA7COqLtjQ5IlzFLU7Y+MIvmCnny+ENVITMG7yBH4'),
    ('admin1',      'Тестовый администратор', 'local', '$argon2id$v=19$m=65536,t=3,p=4$hi21eVs/+Z4TC+MviMr/qQ$fiWyF7z+LFJYhUBo/YKjDMBOn8c7hbnKcDFs1/4/ud4'),
    ('tech2',       'Тестовый техник двух коллекторов', 'local', '$argon2id$v=19$m=65536,t=3,p=4$qtIfrl0986Qq2OhfN4oJiw$/v/8MrUD81Y4oAS5ntGuG49UpxI5dL9NkWA4dfLF4+4'),
    -- US-16: подопытные «района А». Район в выгрузке один, поэтому «район А» —
    -- два коллектора, 5 «объект Альфа» и 7 «объект Гамма»; disptech — ещё
    -- и техник комплекса 4068 «объект Сигма» из «района Б» (сц. 4, объединение).
    -- Пароли записаны только здесь: disp2123123 и disptech123123.
    ('disp2',       'Тестовый диспетчер района А', 'local', '$argon2id$v=19$m=65536,t=3,p=4$HxqQqqxM0wRtfSbUZGG+Lw$Ga/XIR9roTT9R7A+mhyXx/WvbKGBWNEVG8Itb451iqE'),
    ('disptech',    'Тестовый диспетчер района А и техник комплекса Сигма', 'local', '$argon2id$v=19$m=65536,t=3,p=4$fabQ8nTSyxazrB8itulk9g$FbTDW9XNHrnw7BXxrTIdeq2jclpyzXliJOQvSX50kgQ')

ON CONFLICT (login) DO UPDATE SET
    full_name = excluded.full_name;

-- Каталог демо-стенда (deploy/ldap/bootstrap.ldif, MOS-39/8a): группа → роль
-- ИЛИ область видимости, cn согласованы между 41 и 8a 24.09.2026. Учётки
-- ldap_dispatcher1/ldap_ods1/ldap_tech1/ldap_admin1 сюда не заводим — их
-- ref.app_user создаёт сам вход при первом успешном bind (backend/app/auth/ldap.py).
--
-- Строки с object_id кладутся только тогда, когда объект уже есть в
-- smvu.object_tree, а дерево приходит выгрузкой заказчика, а не миграцией.
-- На чистой базе его ещё нет, и вставка, которая упёрлась бы во внешний ключ,
-- уронила бы migrate ДО загрузки выгрузки: api и worker ждут migrate через
-- condition: service_completed_successfully и не поднялись бы вовсе, а выгрузку
-- льют через контейнер api. Залить её стало бы нечем. Поэтому строки с object_id
-- пропускаются, а не валят накат: сид накатывается на каждом старте migrate,
-- и следующий прогон докладывает недостающее само, как только дерево появилось.
-- Сколько должно быть строк: ref.ldap_role_map — 5, ref.user_scope — 9.
INSERT INTO ref.ldap_role_map (group_cn, role_code, object_id)
SELECT v.group_cn, v.role_code, v.object_id
FROM (VALUES
    ('role-dispatcher', 'dispatcher'::text,     NULL::integer),
    ('role-ods',        'ods_dispatcher'::text, NULL::integer),
    ('role-tech',       'technician'::text,     NULL::integer),
    ('role-admin',      'admin'::text,          NULL::integer),
    ('scope-tech-6',    NULL::text,             6::integer)
) AS v(group_cn, role_code, object_id)
WHERE v.object_id IS NULL
   OR EXISTS (SELECT 1 FROM smvu.object_tree t WHERE t.object_id = v.object_id)

ON CONFLICT (group_cn) DO NOTHING;

INSERT INTO ref.user_role (login, role_code) VALUES
    ('dispatcher1', 'dispatcher'),
    ('ods1',        'ods_dispatcher'),
    ('tech1',       'technician'),
    ('admin1',      'admin'),
    ('tech2',       'technician'),
    ('disp2',       'dispatcher'),
    ('disptech',    'dispatcher'),
    ('disptech',    'technician')

ON CONFLICT DO NOTHING;

-- Область видимости. Район в выгрузке один (5773 «Район по эксплуатации»), поэтому
-- диспетчер района видит все 3 173 участка. Техник — коллектор 6 «объект Бета»:
-- 79 участков по УЧАСТКИ_КОЛЛЕКТОРА и 11 заявок из 303 на 23.09.2026.
-- tech2 — два узла, коллекторы 6 «объект Бета» и 4068 «объект Сигма»: объединение
-- обязано дать 79 + 9 = 88 участков — строго больше, чем у tech1, и строго меньше
-- 3 173. Пара «техник + диспетчер района» дала бы 3 173 и прошла бы проверку
-- даже при сломанном объединении, потому что район в выгрузке один.
-- Как и в ref.ldap_role_map выше, строки кладутся только по уже известным
-- объектам: smvu.object_tree приходит выгрузкой, на чистой базе его нет, и
-- упавший по внешнему ключу сид остановил бы migrate до загрузки выгрузки.
-- Пропуск — не потеря: сид накатывается на каждом старте migrate. Сколько
-- должно быть строк: ref.user_scope — 9.
INSERT INTO ref.user_scope (login, object_id)
SELECT v.login, v.object_id
FROM (VALUES
    ('dispatcher1', 5773),
    ('tech1',       6),
    ('tech2',       6),
    ('tech2',       4068),
    ('disp2',       5),
    ('disp2',       7),
    ('disptech',    5),
    ('disptech',    7),
    ('disptech',    4068)
) AS v(login, object_id)
WHERE EXISTS (SELECT 1 FROM smvu.object_tree t WHERE t.object_id = v.object_id)

ON CONFLICT DO NOTHING;
