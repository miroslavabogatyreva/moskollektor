-- 044_roles_scope.sql — роли заказчика, сложение ролей и область видимости.
-- Задача MOS-107 (Q4.11), приёмка Ф-66, НФ-43, НФ-44.
--
-- Заказчик 17.09.2026 назвал четыре роли поимённо: «диспетчер» подразделения
-- эксплуатации видит дерево своего района, «диспетчер ОДС» видит всё, «техник»
-- видит один комплекс, «администратор» ИС; «роли складываются». Наши коды из 008
-- (dispatcher, analyst, engineer, admin) с этим не совпали, поэтому:
--
-- 1. Коды ролей: dispatcher, ods_dispatcher, technician, admin. analyst и engineer
--    уходят — среди названных заказчиком их нет.
-- 2. Роль переезжает из колонки ref.app_user.role_code в ref.user_role: у человека
--    их может быть несколько, разрешения объединяются (backend/app/auth/deps.py).
-- 3. ref.user_scope — узлы smvu.object_tree, которые человек видит. Узел уровня 2 —
--    сам коллектор, уровня 1 — все коллекторы под ним. Участок относится
--    к коллектору запросом УЧАСТКИ_КОЛЛЕКТОРА (backend/app/worker/run_v3.py), тем же,
--    что в прогнозе. ods_dispatcher и admin видят всё без строк здесь; у dispatcher
--    и technician без строк здесь выдача пустая, а не полная.
--
-- 008 не правим: она накатана, её sha256 записан в public.schema_migration.
-- Номер 044, а не 019 из плана: 019–021 ушли на другие задачи, 041–043 заняты
-- черновыми ветками.

CREATE TABLE ref.user_role (
    login     text NOT NULL REFERENCES ref.app_user(login),
    role_code text NOT NULL
              CHECK (role_code IN ('dispatcher', 'ods_dispatcher', 'technician', 'admin')),
    PRIMARY KEY (login, role_code)
);
COMMENT ON TABLE ref.user_role IS 'Роли человека (Ф-66): роли складываются, разрешения объединяются. Коды — четыре роли заказчика, 17.09.2026';

CREATE TABLE ref.user_scope (
    login     text    NOT NULL REFERENCES ref.app_user(login),
    object_id integer NOT NULL REFERENCES smvu.object_tree(object_id),
    PRIMARY KEY (login, object_id)
);
COMMENT ON TABLE ref.user_scope IS 'Область видимости: узлы smvu.object_tree (уровень 1 — район, 2 — коллектор). ods_dispatcher и admin видят всё без строк; dispatcher и technician без строк не видят ничего';

-- Разрешения старых ролей — наши тестовые данные из db/seed/rbac.sql, не заказчика.
ALTER TABLE ref.role_permission DROP CONSTRAINT role_permission_role_code_check;
DELETE FROM ref.role_permission WHERE role_code IN ('analyst', 'engineer');
ALTER TABLE ref.role_permission ADD CONSTRAINT role_permission_role_code_check
    CHECK (role_code IN ('dispatcher', 'ods_dispatcher', 'technician', 'admin'));

-- Учётки с ролью, которой больше нет, отключаем, а не удаляем: на ref.app_user
-- ссылаются семь внешних ключей (журнал аудита, вердикты, заявки).
UPDATE ref.app_user SET is_active = false WHERE role_code IN ('analyst', 'engineer');

INSERT INTO ref.user_role (login, role_code)
SELECT login, role_code FROM ref.app_user
 WHERE role_code IN ('dispatcher', 'admin');

-- Колонку снимаем только после того, как каждая активная учётка получила роль.
DO $$
DECLARE
    без_роли integer;
BEGIN
    SELECT count(*) INTO без_роли
      FROM ref.app_user u
     WHERE u.is_active
       AND NOT EXISTS (SELECT 1 FROM ref.user_role r WHERE r.login = u.login);
    IF без_роли > 0 THEN
        RAISE EXCEPTION '044: % активных учёток без роли, role_code не снимаю', без_роли;
    END IF;
END $$;

ALTER TABLE ref.app_user DROP COLUMN role_code;
COMMENT ON TABLE ref.app_user IS 'Личная учётная запись (Ф-66): вход по LDAP или локально. Роли — ref.user_role, область видимости — ref.user_scope (044)';
