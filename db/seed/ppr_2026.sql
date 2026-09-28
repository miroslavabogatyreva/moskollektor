-- График ППР аппаратуры контроля метана на 2026 год — 26 строк в maint.ppr_window.
-- Эпик MOS-248, задача SL.2 (MOS-251).
--
-- Источник: docs/График ППР АКМ на 2026г. РЭК.xlsx, лист «РЭК», строки «Объект 1 …
-- Объект 26» как есть; объединённые ячейки дат развёрнуты по строкам. Сопоставление
-- строк с узлами smvu.object_tree — docs/proof/2026-09-28-sensor-level/ppr-match.md,
-- разд. 3: уверенно Объект 14 = 5657 «объект Каппа ДУ» и Объект 9 = 5675 «объект Мю ДУ»,
-- сомнительно Объект 3 = 4610 «ДУ объект Дельта» и Объект 6 = 4369 «ДУ ПС объект Ро»,
-- у остальных 22 узла нет (10 без пачки в журнале, 12 после его конца 30.06.2026).
--
-- Когда срабатывает. Накатывает контейнер migrate на каждом прогоне
-- (backend/app/migrate.py), в своей транзакции. Узел пишется, только если он есть
-- в smvu.object_tree: на чистой установке до заливки дерева все object_id = NULL,
-- а после заливки следующий прогон их допишет. Строки source = 'customer-ppr-2026'
-- совпали с телом файла до значения — ничего не делает. Иначе сносит свои строки
-- и пишет заново; строк других источников не трогает.
DO $ppr$
BEGIN
CREATE TEMP TABLE ppr_src ON COMMIT DROP AS
SELECT 'customer-ppr-2026'::text AS source, v.plan_row, t.object_id, v.match,
       'Газовый датчик'::text AS sensor_kind, v.qty,
       v.dismantle_from::date AS dismantle_from, v.return_to::date AS return_to,
       v.accepted_on::date AS accepted_on
  FROM (VALUES
    ('Объект 1',   NULL::int, 'none',      56, '2026-01-12', '2026-01-22', '2026-01-27'),
    ('Объект 2',   NULL,      'none',      13, '2026-01-12', '2026-01-22', '2026-01-28'),
    ('Объект 3',   4610,      'doubtful',  62, '2026-01-29', '2026-02-09', '2026-02-12'),
    ('Объект 4',   NULL,      'none',      47, '2026-02-13', '2026-02-24', '2026-02-27'),
    ('Объект 5',   NULL,      'none',     102, '2026-03-02', '2026-03-13', '2026-03-19'),
    ('Объект 6',   4369,      'doubtful',  34, '2026-03-23', '2026-04-03', '2026-04-08'),
    ('Объект 7',   NULL,      'none',      12, '2026-03-23', '2026-04-03', '2026-04-08'),
    ('Объект 8',   NULL,      'none',     101, '2026-04-09', '2026-04-17', '2026-04-22'),
    ('Объект 9',   5675,      'sure',      40, '2026-04-23', '2026-05-07', '2026-05-14'),
    ('Объект 10',  NULL,      'none',       6, '2026-05-15', '2026-05-26', '2026-05-29'),
    ('Объект 11',  NULL,      'none',       8, '2026-05-15', '2026-05-26', '2026-05-29'),
    ('Объект 12',  NULL,      'none',      21, '2026-05-15', '2026-05-26', '2026-05-29'),
    ('Объект 13',  NULL,      'none',      21, '2026-05-15', '2026-05-26', '2026-05-29'),
    ('Объект 14',  5657,      'sure',      33, '2026-06-04', '2026-06-18', '2026-06-29'),
    ('Объект 15',  NULL,      'none',      41, '2026-07-02', '2026-07-16', '2026-07-23'),
    ('Объект 16',  NULL,      'none',      95, '2026-07-28', '2026-08-14', '2026-08-19'),
    ('Объект 17',  NULL,      'none',      22, '2026-07-28', '2026-08-14', '2026-08-21'),
    ('Объект 18',  NULL,      'none',      21, '2026-08-24', '2026-09-04', '2026-09-10'),
    ('Объект 19',  NULL,      'none',       5, '2026-08-24', '2026-09-04', '2026-09-10'),
    ('Объект 20',  NULL,      'none',      79, '2026-09-15', '2026-09-30', '2026-10-07'),
    ('Объект 21',  NULL,      'none',      17, '2026-09-15', '2026-09-30', '2026-10-08'),
    ('Объект 22',  NULL,      'none',      22, '2026-10-13', '2026-10-23', '2026-10-29'),
    ('Объект 23',  NULL,      'none',      36, '2026-10-13', '2026-10-23', '2026-10-29'),
    ('Объект 24',  NULL,      'none',       4, '2026-10-13', '2026-10-23', '2026-10-29'),
    ('Объект 25',  NULL,      'none',      55, '2026-11-05', '2026-11-19', '2026-11-25'),
    ('Объект 26',  NULL,      'none',      55, '2026-12-02', '2026-12-17', '2026-12-22')
  ) AS v(plan_row, node, match, qty, dismantle_from, return_to, accepted_on)
  LEFT JOIN smvu.object_tree t ON t.object_id = v.node;

IF NOT EXISTS (
     (SELECT source, plan_row, object_id, match, sensor_kind, qty, dismantle_from,
             return_to, accepted_on FROM ppr_src
      EXCEPT
      SELECT source, plan_row, object_id, match, sensor_kind, qty, dismantle_from,
             return_to, accepted_on FROM maint.ppr_window WHERE source = 'customer-ppr-2026')
     UNION ALL
     (SELECT source, plan_row, object_id, match, sensor_kind, qty, dismantle_from,
             return_to, accepted_on FROM maint.ppr_window WHERE source = 'customer-ppr-2026'
      EXCEPT
      SELECT * FROM ppr_src)) THEN
  RETURN;
END IF;

DELETE FROM maint.ppr_window WHERE source = 'customer-ppr-2026';
INSERT INTO maint.ppr_window (source, plan_row, object_id, match, sensor_kind, qty,
                              dismantle_from, return_to, accepted_on)
SELECT * FROM ppr_src;
END
$ppr$;
