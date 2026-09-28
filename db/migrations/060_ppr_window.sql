-- 060. Окна планового демонтажа по графику ППР заказчика. Эпик MOS-248, задача SL.2 (MOS-251).
--
-- ЗАЧЕМ. До 060 окно ППР Каппы ДУ (Объект 14 графика, 04.06–18.06.2026) было
-- константой в backend/app/domain/sensor_risk.py. Теперь все 26 строк графика
-- «График ППР АКМ на 2026г. РЭК.xlsx» лежат здесь, сид — db/seed/ppr_2026.sql,
-- тик app.worker.sensor_scores читает окна одним запросом. Эпизод отказа газового
-- канала, начатый внутри окна с match = 'sure', в балл датчика не идёт: датчик
-- сняли на поверку, это не отказ.
--
-- ПОЧЕМУ НЕ maint.plan / maint.work_order. plan — цикл ТОиР без дат, узла и числа
-- датчиков; work_order читают экран заявок и признак days_since_last_repair модели,
-- окна ППР появились бы там ремонтами, которых не было. Разбор —
-- docs/proof/2026-09-28-sensor-level/ppr-match.md, разд. 5.
--
-- Окно = [dismantle_from 00:00; return_to 23:59:59] по Москве. object_id — узел
-- smvu.object_tree, только у строк, сопоставленных с пачкой газовых эпизодов
-- (sure — 2 строки, doubtful — 2); у остальных 22 NULL.

CREATE TABLE maint.ppr_window (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source         text    NOT NULL,
    plan_row       text    NOT NULL,
    object_id      integer REFERENCES smvu.object_tree(object_id),
    match          text    NOT NULL CHECK (match IN ('sure', 'doubtful', 'none')),
    sensor_kind    text    NOT NULL REFERENCES smvu.sensor_kind,
    qty            integer NOT NULL CHECK (qty > 0),
    dismantle_from date    NOT NULL,
    return_to      date    NOT NULL,
    accepted_on    date,
    UNIQUE (source, plan_row),
    CHECK (return_to >= dismantle_from)
);

COMMENT ON TABLE maint.ppr_window IS
    'Окна планового демонтажа датчиков по графику ППР заказчика; в балл датчика не идут эпизоды из окон match=sure. MOS-251';
COMMENT ON COLUMN maint.ppr_window.plan_row IS
    'Имя строки графика как есть: «Объект 14»';
COMMENT ON COLUMN maint.ppr_window.match IS
    'Вердикт сопоставления с пачкой газовых эпизодов: sure, doubtful, none (ppr-match.md)';
COMMENT ON COLUMN maint.ppr_window.return_to IS
    'Вывоз из ОМ; окно кончается в 23:59:59 этого дня по Москве';
