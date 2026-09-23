-- 046_synthetic_geometry.sql — вид объекта «коллектор» с MULTILINESTRING.
-- Задача MOS-45 (Q4.8), приёмка Ф-81.
--
-- Координат в выгрузке нет, и заказчик 17.09.2026 разрешил нарисовать геометрию
-- самим: «Достаточно осевых MultiLineString». Ось коллектора — MULTILINESTRING,
-- по одной части на префикс тега внутри коллектора (та же линия, что рисует
-- frontend/src/screens/map/AxisLine.tsx), участок — LINESTRING, отрезок своей части.
--
-- Здесь только схема. Саму геометрию рисует backend/app/ingest/synthetic_geometry.py
-- при повторном проходе --channels: на чистой установке миграции идут до заливки
-- выгрузки, ref.object_xref ещё пуст, и миграция нарисовала бы ноль линий
-- (так уже было с 029, deploy/README.md).
--
-- Номер 046: 044 и 045 заняты MOS-107 и MOS-42, 041–043 — черновыми ветками.

ALTER TABLE geo.object_kind DROP CONSTRAINT object_kind_geom_type_check;
ALTER TABLE geo.object_kind ADD CONSTRAINT object_kind_geom_type_check
    CHECK (geom_type IN ('POINT', 'LINESTRING', 'POLYGON', 'MULTILINESTRING'));

INSERT INTO geo.object_kind (code, name_full, name_short, geom_type, is_network_node, is_network_edge)
VALUES ('collector', 'Коллектор (ось, синтетическая)', 'Коллектор', 'MULTILINESTRING', false, false);
