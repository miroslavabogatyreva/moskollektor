-- 052. Решение диспетчера по прогнозу — закрытый справочник из четырёх кодов.
-- Задача MOS-55 (план 5.8), приёмка Ф-92, история US-09.
--
-- Номер 052: 050 зарезервирован под PR Николая (MOS-184), 051 — MOS-63.
--
-- ЧТО ПИШЕТ ДИСПЕТЧЕР. ТЗ разд. 12, шаг 5: «диспетчер фиксирует решение в системе
-- (например, „выезд бригады“ или „ложное срабатывание“) с выбором причины из
-- справочника»; для подтопления — «направление бригады на проверку» или
-- «мониторинг ситуации». Решений у заказчика нет («Метки с решением сотрудника
-- нет. Поэтому только симуляция»), справочник составили мы — ровно из этих
-- четырёх фраз ТЗ.
--
-- VERDICT НЕ ТРОГАЕМ, А ВЫВОДИМ. pred.feedback.verdict (004_events.sql) читают
-- code/check_no_auto_verdict.py и счёт ложных; его выставляет метод
-- POST /api/forecasts/{id}/feedback: false_alarm → 0, остальные три → 1.
-- Для 0 CHECK из 004_events.sql требует reason_code из ref.feedback_reason —
-- поэтому при «Ложном срабатывании» диалог просит ещё и причину.
--
-- decision_code допускает NULL: строк pred.feedback до этой миграции на стенде
-- нет (0 на 26.09.2026, Ф-75), но NOT NULL на ALTER упал бы на любой старой
-- записи, а обязательность решения держит метод API (422 без decision_code).

-- sort_order — порядок в списке диалога: от самого сильного действия к отбою.
-- Названия дословно из ТЗ разд. 12 (с заглавной буквы), менять только с заказчиком.
CREATE TABLE ref.dispatcher_decision (
    code       text     PRIMARY KEY,
    name       text     NOT NULL,
    sort_order smallint NOT NULL UNIQUE
);

INSERT INTO ref.dispatcher_decision (code, name, sort_order) VALUES
    ('crew_dispatch', 'Выезд бригады',                   1),
    ('send_check',    'Направление бригады на проверку', 2),
    ('monitor',       'Мониторинг ситуации',             3),
    ('false_alarm',   'Ложное срабатывание',             4);

-- Ключ отдельным ADD CONSTRAINT, а не REFERENCES в ADD COLUMN: code/check_schema.py
-- видит внешние ключи из ALTER только в этой форме.
ALTER TABLE pred.feedback ADD COLUMN decision_code text;
ALTER TABLE pred.feedback
    ADD CONSTRAINT feedback_decision_code_fk
    FOREIGN KEY (decision_code) REFERENCES ref.dispatcher_decision(code);

-- Отметка «Проверено по внешним источникам» — шаг 4 сценария ТЗ разд. 12
-- («при необходимости использует внешние источники, например камеры»). Интеграции
-- с камерами нет и не будет, HLD разд. 11.6 закрывает шаг чекбоксом (Ф-91).
ALTER TABLE pred.feedback ADD COLUMN verified_externally boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN pred.feedback.decision_code IS
    'Решение диспетчера (MOS-55, Ф-92); verdict из него выводится: false_alarm → 0, прочие → 1';

-- Писать решение могут только диспетчер и диспетчер ОДС — ту же пару ролей
-- требует половина Б code/check_no_auto_verdict.py (Ф-75). Техник получает 403.
-- Права — миграцией, а не сидом, по той же причине, что в 045_notification_ack.sql.
INSERT INTO ref.role_permission (role_code, permission_code) VALUES
    ('dispatcher',     'forecasts.decide'),
    ('ods_dispatcher', 'forecasts.decide')
ON CONFLICT (role_code, permission_code) DO NOTHING;
