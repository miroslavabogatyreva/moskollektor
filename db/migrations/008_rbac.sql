-- 008_rbac.sql — учётные записи, роли и разрешения. Задача MOS-38 (Q4.1),
-- идёт первой во всём блоке Q4: `require(permission_code)` вешается на каждый
-- метод API, и переставлять её после уже готовых методов дороже (docs/plan.md,
-- Q4). Приёмка: Ф-66, НФ-43, НФ-44.
--
-- Роли — четыре из постановки и ТЗ разд. 12 (docs/HLD.md разд. 3.5): диспетчер,
-- аналитик, инженер, администратор. Трёх таблиц связи не делаем: роль лежит
-- колонкой role_code прямо в ref.app_user (у пользователя ровно одна роль,
-- городить user_role ради этого незачем), а разрешения — не отдельный
-- справочник + пара связей, а одна таблица ref.role_permission, которую
-- наполняет db/seed/rbac.sql. Добавить новое разрешение — это строка сида,
-- а не миграция.
--
-- Аутентификация (LDAP simple bind, локальный пароль argon2, сессия в куке —
-- docs/HLD.md разд. 3.5) — задача Q4.2, идёт после этой. auth_source уже
-- заведён здесь, чтобы 4.2 не пришлось трогать эту таблицу: колонку читает
-- её код, а не эта миграция.
--
-- ref.audit_log из HLD разд. 3.5 не заводим: docs/plan.md и тикет Q4.10
-- называют audit.user_action — план новее раздела HLD и наполняется по ходу
-- работы, при расхождении верен он.

CREATE SCHEMA IF NOT EXISTS ref;    -- уже есть после 001_assets.sql, но файл обязан быть самодостаточным
CREATE SCHEMA IF NOT EXISTS pred;   -- уже есть после 004_events.sql, для ALTER TABLE ниже
CREATE SCHEMA IF NOT EXISTS asset;  -- уже есть после 001_assets.sql, для ALTER TABLE ниже
CREATE SCHEMA IF NOT EXISTS maint;  -- уже есть после 001_assets.sql, для ALTER TABLE ниже
CREATE SCHEMA IF NOT EXISTS load;   -- уже есть после 001_assets.sql, для ALTER TABLE ниже
CREATE SCHEMA IF NOT EXISTS geo;    -- уже есть после 002_geo.sql, для ALTER TABLE ниже

CREATE TABLE ref.app_user (
    user_id     bigint  GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    login       text    NOT NULL UNIQUE,   -- личная учётная запись, Ф-66
    full_name   text    NOT NULL,          -- имя человека для журнала и аудита (Ф-53, Ф-69)
    role_code   text    NOT NULL
                CHECK (role_code IN ('dispatcher', 'analyst', 'engineer', 'admin')),
    auth_source text    NOT NULL DEFAULT 'local'
                CHECK (auth_source IN ('ldap', 'local')),  -- заполняет Q4.2
    is_active   boolean NOT NULL DEFAULT true,   -- false = заблокирован администратором (Ф-66)
    created_at  timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE  ref.app_user IS 'Личная учётная запись (Ф-66): вход по LDAP или локально, роль — колонкой role_code, без отдельной таблицы связи';
COMMENT ON COLUMN ref.app_user.is_active IS 'Блокировка администратором. Строк не удаляем: на них уже могут ссылаться decided_by/created_by/approved_by/resolved_by';

CREATE TABLE ref.role_permission (
    role_code       text NOT NULL
                    CHECK (role_code IN ('dispatcher', 'analyst', 'engineer', 'admin')),
    permission_code text NOT NULL,
    PRIMARY KEY (role_code, permission_code)
);
COMMENT ON TABLE ref.role_permission IS 'Какие коды разрешений даёт каждая роль (НФ-43, НФ-44). Данные — db/seed/rbac.sql: новое разрешение это строка сида, не миграция';

-- ---------------------------------------------------------------------------
-- Пять ключей, которые 005_xref.sql оставил комментарием (строка 140:
-- «Таблицы пользователей в схеме пока нет. Появится — ключи ставить сюда же»).
-- Не правим 005_xref.sql: он уже накатан, его sha256 зафиксирован
-- в public.schema_migration, правка уронила бы накат на любом стенде,
-- где файл уже применён.
-- ---------------------------------------------------------------------------

-- decided_by — логин диспетчера, поставившего вердикт по прогнозу.
ALTER TABLE pred.feedback
    ADD CONSTRAINT feedback_decided_by_fk FOREIGN KEY (decided_by) REFERENCES ref.app_user(login);

-- created_by — кто переставил оборудование (история монтажа/демонтажа).
ALTER TABLE asset.equipment_install_history
    ADD CONSTRAINT eq_install_hist_created_by_fk FOREIGN KEY (created_by) REFERENCES ref.app_user(login);

-- created_by — кто завёл заявку руками (maint.notification — это «заявка»
-- из Q6, не путать с maint.work_order — заказом ТОиР, у него такой колонки нет).
-- Для автозаявок (source_system='forecast') колонка остаётся NULL — их заводит
-- расчёт, а не человек.
ALTER TABLE maint.notification
    ADD CONSTRAINT notification_created_by_fk FOREIGN KEY (created_by) REFERENCES ref.app_user(login);

-- approved_by — кто подписал протокол загрузки мастер-данных.
ALTER TABLE load.batch
    ADD CONSTRAINT batch_approved_by_fk FOREIGN KEY (approved_by) REFERENCES ref.app_user(login);

-- resolved_by — кто вручную разобрал адрес без координаты.
ALTER TABLE geo.geo_unresolved
    ADD CONSTRAINT geo_unresolved_resolved_by_fk FOREIGN KEY (resolved_by) REFERENCES ref.app_user(login);

-- asset.measurement.recorded_by ключ НЕ ставим: документ измерения может прийти
-- от СМВУ или другого источника (рядом уже есть source_system для этого
-- различения), а не только от вошедшего в систему человека — FK на ref.app_user
-- отверг бы автоматические записи.

-- smvu.fault_episode.closed_by ключ НЕ ставим: это не логин, а исход эпизода
-- («Норма», «Обесточен», «Затоплен», «Неопределен») — значение из журнала СМВУ,
-- не человек.
