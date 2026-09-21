"""Контракт failure.v3, его строки в миграции 030 и склейка цели модели.

Запуск из корня репозитория: `python3 -m pytest code/tests`.
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pytest

КОРЕНЬ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(КОРЕНЬ / "code"))

import model_failure  # noqa: E402

МИГРАЦИЯ = КОРЕНЬ / "db" / "migrations" / "030_model_failure_value.sql"
ЭКРАН = КОРЕНЬ / "db" / "migrations" / "004_events.sql"


def test_контракт_читается_и_называет_метку_d5():
    к = model_failure.загрузить_контракт()
    assert к["model_version"] == "lgbm-v3-bag-2026.09.21"
    assert к["definition_id"] == "D5"
    assert к["failure_values"] == [
        "Неисправен", "Батарея неисправна", "Много неисправных устройств", "Не определено"]
    assert к["episode"]["min_duration_seconds"] == 3600
    assert к["incident"]["merge_minutes"] == 60


def test_миграция_засевает_ровно_значения_контракта():
    к = model_failure.загрузить_контракт()
    sql = МИГРАЦИЯ.read_text(encoding="utf-8")
    строки = re.findall(r"\('([^']+)',\s*'([^']+)',\s*'([^']+)',\s*(\d+)\)", sql)
    assert sorted(с[0] for с in строки) == sorted(к["failure_values"])
    assert {(с[1], с[2], int(с[3])) for с in строки} == {
        (к["model_version"], к["definition_id"], к["episode"]["min_duration_seconds"])}


def test_экранное_правило_миграция_не_трогает():
    # Экран держит smvu.fault_rule; 030 не должна ни писать в неё,
    # ни класть строки в smvu.fault_episode — их читают пять мест.
    sql = МИГРАЦИЯ.read_text(encoding="utf-8")
    пишет = re.findall(
        r"(?:INSERT\s+INTO|UPDATE|ALTER\s+TABLE|DELETE\s+FROM|TRUNCATE|DROP\s+TABLE)"
        r"\s+(smvu\.\w+)", sql, flags=re.I)
    assert set(пишет) == {"smvu.model_failure_value"}, пишет
    # И «Неопределен» экрана — не «Не определено» модели.
    assert "'Неопределен'" in ЭКРАН.read_text(encoding="utf-8")
    assert "Неопределен" not in model_failure.загрузить_контракт()["failure_values"]


@pytest.mark.parametrize("поле, значение", [
    ("schema_version", "failure.v2"),
    ("failure_values", []),
    ("failure_values", ["Неисправен", "Неисправен"]),
])
def test_кривой_контракт_роняет_загрузку(tmp_path, поле, значение):
    к = json.loads(model_failure.КОНТРАКТ.read_text(encoding="utf-8"))
    к[поле] = значение
    путь = tmp_path / "failure.json"
    путь.write_text(json.dumps(к, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(AssertionError):
        model_failure.загрузить_контракт(путь)


def test_склейка_по_окну_контракта():
    d = datetime.fromisoformat
    отказы = [(101, d("2026-04-10 09:00")), (102, d("2026-04-10 09:45")),
              (103, d("2026-04-10 10:40")), (104, d("2026-04-10 09:10"))]
    # 09:00 → 09:45 → 10:40: соседи ближе часа, цепочка — один инцидент, хотя
    # первый и последний разошлись на 100 минут. У 104 узла нет — свой ключ.
    коллектор = {101: 7, 102: 7, 103: 7}
    окно = model_failure.загрузить_контракт()["incident"]["merge_minutes"]
    assert model_failure.инциденты(отказы, коллектор, окно) == [
        ("ch:104", d("2026-04-10 09:10")), ("obj:7", d("2026-04-10 09:00"))]
    # Узкая склейка в 10 минут разводит те же три отказа коллектора на три инцидента.
    assert len(model_failure.инциденты(отказы, коллектор, 10)) == 4


def test_подпись_называет_модель_и_окно():
    текст = model_failure.подпись(model_failure.загрузить_контракт())
    assert "lgbm-v3-bag-2026.09.21" in текст and "склейка 60 мин" in текст
