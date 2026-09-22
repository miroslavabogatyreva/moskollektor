"""Нормализация имён типов датчика и системы."""

from app.ingest.kind_names import canon, canon_map, norm_kind

# Имена из smvu.sensor_kind и smvu.system_kind (db/migrations/004_events.sql).
ДАТЧИКИ = canon_map(["КД АВ", "Датчик дыма", "Тепловой датчик", "КД Дверь"])
СИСТЕМЫ = canon_map(["Охранная подсистема", "Пожарная охрана"])


def test_norm_kind():
    assert norm_kind("  Охранная   подсистема ") == "охранная подсистема"
    assert norm_kind("Тепловой датчик") == norm_kind("тепловой ДАТЧИК")
    assert norm_kind("Всё") == "все"
    assert norm_kind(None) == ""


def test_latin_lookalikes_map_to_db_name():
    assert canon("КД AB", ДАТЧИКИ) == "КД АВ"          # латинские A и B
    assert canon("кд ав", ДАТЧИКИ) == "КД АВ"
    assert canon("Oхранная пoдсистема", СИСТЕМЫ) == "Охранная подсистема"  # латинские o
    assert canon("Пожарная  охрана", СИСТЕМЫ) == "Пожарная охрана"


def test_unknown_stays_unknown():
    # незнакомый тип не выдумывается: загрузчик пишет NULL и строку в load.error
    assert canon("Выдуманная подсистема", СИСТЕМЫ) is None
    assert canon("", СИСТЕМЫ) is None
