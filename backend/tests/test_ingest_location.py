"""Коллектор, пикет и место канала — на настоящих названиях из справочника каналов."""

import pytest

from app.ingest.tag_to_section import (collector_of, linear_metres, location_kind,
                                       picket_of, section_key)


def test_section_key_keeps_picket_pairs():
    assert section_key("847-11.1.131.2.", "КД АВ ПК106 ур.3") == ("847", 106)
    assert section_key("847-1.1.1.1.", "ПК176+6,5") == ("847", 176)


def test_collector_without_picket():
    # тег даёт коллектор всегда, даже когда участка нет
    assert section_key("16-1.1.4096.6.", "Охранная зона Ленинский 83") is None
    assert collector_of("16-1.1.4096.6.") == "16"
    assert collector_of("884-g1.") == "884"
    assert collector_of("") is None
    assert collector_of(None) is None


def test_picket_without_pk_is_narrow():
    assert picket_of("ОД АВ 185+5") == 185
    assert section_key("645-1.1.117.5.", "ОД АВ 185+5") == ("645", 185)
    # номер кабинета — не пикет
    assert picket_of("ДД каб.201+201А") is None
    # линейную координату узкое правило не трогает
    assert linear_metres("ОД АВ 185+5") is None


@pytest.mark.parametrize("tag, name, obj, kind", [
    ("847-11.1.131.2.", "КД АВ ПК106 ур.3", "controlHouse", "section"),
    ("847-1.1.1.1.", "ПК176+6,5", None, "section"),
    ("645-1.1.117.5.", "ОД АВ 185+5", "guardObject", "section"),
    ("16-1.1.4096.6.", "Охранная зона Ленинский 83", "guardObject", "zone"),
    ("15-11.1.4096.4095.", "[Охранная зона]0: 0: Гагаринский тоннель", "guardObject", "zone"),
    ("884-g1.", "ОС ЦДТ", "guardObject", "zone"),
    # зона сильнее здания: шкаф ОПС на controlHouse, но в охранной зоне
    ("217-1.1.4096.6.", "Охранная зона Шкаф ОПС", "controlHouse", "zone"),
    ("1-1.1.1.1.", "КД подвал вход ДП", "controlHouse", "building"),
    ("1-1.1.1.1.", "Темп. диспетчерская ДП", "controlHouse", "building"),
    ("1-1.1.1.1.", "Темп. ДП", "controlHouse", "building"),
    ("1-1.1.1.1.", "КД вход в ДП", "controlHouse", "building"),
    ("477-1.1.13.4.", "ИПР ДП Зап. выход", "guardObject", "unknown"),
    ("1-1.1.1.1.", "КД вход в ДП", None, "unknown"),
])
def test_location_kind(tag, name, obj, kind):
    assert location_kind(tag, name, obj) == kind
