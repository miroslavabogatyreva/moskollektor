"""Разбор по настоящему справочнику каналов. Файла нет — тест пропускается.

Путь к справочникам задают переменные SMVU_CHANNELS и SMVU_OBJECTS; по умолчанию
берётся dataset/ в корне репозитория, как в инструкции загрузчика.
"""

import csv
import os
from collections import Counter
from pathlib import Path

import pytest

from app.ingest.kind_names import canon, canon_map
from app.ingest.tag_to_section import collector_of, location_kind, section_key

КОРЕНЬ = Path(__file__).resolve().parents[2]
КАНАЛЫ = Path(os.environ.get("SMVU_CHANNELS",
                             КОРЕНЬ / "dataset" / "справочник_каналов_датчиков.csv"))
ОБЪЕКТЫ = Path(os.environ.get("SMVU_OBJECTS",
                              КОРЕНЬ / "dataset" / "справочник_объектов_диспетчер.csv"))

pytestmark = pytest.mark.skipif(not (КАНАЛЫ.is_file() and ОБЪЕКТЫ.is_file()),
                                reason="настоящих справочников нет")


def _read(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_real_dictionary_classification():
    каналы = _read(КАНАЛЫ)
    виды = {r["ид_объект"]: r["вид_объекта"] for r in _read(ОБЪЕКТЫ)}
    места = Counter(location_kind(r["тег_инженерной_системы"], r["название_датчика"],
                                  виды.get(r["ид_объект"])) for r in каналы)
    с_тегом = [r for r in каналы if collector_of(r["тег_инженерной_системы"])]
    было = sum(1 for r in каналы
               if section_key(r["тег_инженерной_системы"], r["название_датчика"]))
    print(f"\nканалов {len(каналы)}, коллектор из тега {len(с_тегом)}, "
          f"с участком {было}, места {dict(места)}")
    # коллектор есть у каждого канала, чей тег его даёт, а не только у каналов с пикетом
    assert len(с_тегом) == len(каналы)
    assert sum(места.values()) == len(каналы)
    assert места["section"] == было
    # здание и зона не бывают у канала с участком
    for r in каналы:
        if section_key(r["тег_инженерной_системы"], r["название_датчика"]):
            assert location_kind(r["тег_инженерной_системы"], r["название_датчика"],
                                 виды.get(r["ид_объект"])) == "section"


def test_real_types_match_exactly_as_before():
    # Справочники базы: db/migrations/004_events.sql. Сверяем, что нормализация
    # не меняет ни одного типа на настоящей выгрузке — те же каналы типизированы.
    import re
    sql = (КОРЕНЬ / "db" / "migrations" / "004_events.sql").read_text(encoding="utf-8")
    блок = sql.split("INSERT INTO smvu.sensor_kind", 1)[1].split(";", 1)[0]
    датчики_бд = re.findall(r"\('([^']+)', \d+, (?:true|false)\)", блок)
    блок = sql.split("INSERT INTO smvu.system_kind", 1)[1].split(";", 1)[0]
    системы_бд = re.findall(r"\('([^']+)',", блок)
    assert len(датчики_бд) == 19 and len(системы_бд) == 6
    датчики, системы = canon_map(датчики_бд), canon_map(системы_бд)
    assert len(датчики) == 19 and len(системы) == 6   # нормализация не склеила имена
    каналы = _read(КАНАЛЫ)
    точно = sum(1 for r in каналы
                if r["тип_датчика"] in датчики_бд and r["тип_инж_системы"] in системы_бд)
    нормально = sum(1 for r in каналы
                    if canon(r["тип_датчика"], датчики) == r["тип_датчика"]
                    and canon(r["тип_инж_системы"], системы) == r["тип_инж_системы"])
    print(f"\nтипизировано точно {точно}, после нормализации {нормально}")
    assert точно == нормально
