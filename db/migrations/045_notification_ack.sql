-- 045_notification_ack.sql — квитирование уведомлений (Q6.9) и доступ к трём
-- новым методам API. Задача MOS-42 (Q4.5), приёмка Ф-88, Ф-89, Ф-90.
--
-- ПОРЯДОК: этот файл идёт ПОСЛЕ 044_roles_scope.sql (MOS-107) — INSERT ниже
-- вставляет права под новые коды ролей (dispatcher, ods_dispatcher, technician,
-- admin), а 008_rbac.sql ставит на ref.role_permission.role_code CHECK,
-- который 044 сужает до этих четырёх. На старых кодах (008 без 044) этот файл
-- не накатится — это ожидаемо, не чинить.
--
-- ПРАВА — МИГРАЦИЕЙ, А НЕ СИДОМ. db/seed/rbac.sql обычно и есть место для
-- нового permission_code (комментарий в его шапке), но 044 переписывает этот
-- файл целиком (роли заказчика, область видимости) — правка тех же строк
-- второй веткой была бы гарантированным конфликтом слияния. Права трёх новых
-- методов едут отдельным файлом, который сид не трогает.

-- Квитирование: кто первым нажал «Принял», тот и остаётся — вторая попытка
-- не перезаписывает (backend/app/api/notifications.py::ack). acked_at NULL —
-- уведомление всё ещё висит в полосе (5.6) и на вкладке «Неквитированные» (6.9).
ALTER TABLE maint.notification
    ADD COLUMN acked_by bigint REFERENCES ref.app_user(user_id),
    ADD COLUMN acked_at timestamptz;

COMMENT ON COLUMN maint.notification.acked_by IS 'Квитирование (MOS-42, Q6.9): кто первым нажал «Принял»';
COMMENT ON COLUMN maint.notification.acked_at IS 'Момент квитирования; NULL — уведомление ещё не отработано';

INSERT INTO ref.role_permission (role_code, permission_code) VALUES
    ('dispatcher',     'notifications.read'),
    ('dispatcher',     'notifications.ack'),
    ('dispatcher',     'tech_events.read'),
    ('ods_dispatcher', 'notifications.read'),
    ('ods_dispatcher', 'notifications.ack'),
    ('ods_dispatcher', 'tech_events.read'),
    ('technician',     'notifications.read'),
    ('technician',     'notifications.ack'),
    ('technician',     'tech_events.read'),
    ('admin',          'notifications.read'),
    ('admin',          'notifications.ack'),
    ('admin',          'tech_events.read')
ON CONFLICT (role_code, permission_code) DO NOTHING;
