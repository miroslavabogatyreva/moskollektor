-- 047_auth.sql — вход по логину и паролю, сопоставление групп каталога с ролями.
-- Задача MOS-39 (Q4.2), приёмка НФ-76, НФ-43, Ф-66. Идёт после 046 (последняя
-- в origin/master на момент ветки, проверено `git ls-tree origin/master db/migrations/`).
--
-- password_hash — только для auth_source='local' (008_rbac.sql уже завела саму
-- колонку auth_source). У LDAP-пользователя пароль не хранится вообще: строка
-- заводится при первом входе с password_hash IS NULL, а роль и область видимости
-- при каждом входе пересчитываются заново по ref.ldap_role_map — так блокировка
-- «убрать из групп каталога» тоже действует немедленно, без отдельного признака.
--
-- ref.ldap_role_map — правило заказчика 17.09.2026: «группы созданы для
-- комплексов коллекторов, для области видимости» (docs/meetings/2026-09-17-эксперты.md
-- разд. 1). Одна строка даёт ЛИБО роль, ЛИБО узел области видимости — не оба
-- сразу: у заказчика это разные группы каталога, и CHECK держит это в базе,
-- а не только в коде синхронизации.

ALTER TABLE ref.app_user ADD COLUMN password_hash text NULL;
COMMENT ON COLUMN ref.app_user.password_hash IS 'argon2, только auth_source=local. У auth_source=ldap всегда NULL — пароль не хранится (докс HLD 3.5)';

CREATE TABLE ref.ldap_role_map (
    group_cn  text NOT NULL PRIMARY KEY,
    role_code text NULL
              CHECK (role_code IN ('dispatcher', 'ods_dispatcher', 'technician', 'admin')),
    object_id integer NULL REFERENCES smvu.object_tree(object_id),
    CHECK ((role_code IS NULL) <> (object_id IS NULL))
);
COMMENT ON TABLE ref.ldap_role_map IS 'Группа каталога → роль ИЛИ узел области видимости (не оба сразу). Наполняет db/seed/rbac.sql, синхронизацию при входе делает backend/app/auth/ldap.py';
