"""MOS-182, MOS-184, Ф-48 — тот же путь заявок, но через настоящую базу.

Чистые функции проверяет test_orders_openings.py; здесь — SQL, который им подаёт
«уже выбранные» предупреждения, живые заявки на участках и нижнюю границу по
прошлому прогону, и вставка заявок с нарядами. Каждый тест работает в транзакции
и откатывает её: база остаётся той, что была.

Нужна база со всеми миграциями и сидами (`python -m app.migrate`):
    TEST_DATABASE_URL=postgresql://postgres:pg@127.0.0.1:55432/moskollektor pytest …
Без переменной тесты пропускаются, а не зеленеют впустую.
"""

import asyncio
import os
from datetime import datetime, timedelta

import pytest
from app.domain import order_rules
from app.domain.order_rules import МОСКВА
from app.worker import run_v3
from app.worker.score_v3 import открытия

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DSN, reason="нет TEST_DATABASE_URL — базы для проверки нет"
)

УЧАСТКИ = [(900001 + i, f"889:{i}") for i in range(5)]
AS_OF = datetime(2026, 6, 30, 23, 59, 59, tzinfo=МОСКВА)
ВЕСА_ДО = {
    900001: (7, 0.9),
    900002: (7, 0.8),
    900003: (7, 0.7),
    900004: (7, 0.1),
    900005: (7, 0.05),
}
ВЕСА_ПОСЛЕ = {
    900001: (7, 0.1),
    900002: (7, 0.2),
    900003: (7, 0.3),
    900004: (7, 0.9),
    900005: (7, 0.8),
}


def _в_базе(тело):
    """Запустить корутину `тело(conn)` в транзакции, которая всегда откатывается."""
    import asyncpg

    async def main():
        conn = await asyncpg.connect(DSN)
        tr = conn.transaction()
        await tr.start()
        try:
            await _участки(conn)
            return await тело(conn)
        finally:
            await tr.rollback()
            await conn.close()

    return asyncio.run(main())


async def _участки(conn):
    """Пять участков префикса 889: технические места класса A и строки object_xref."""
    корень = await conn.fetchrow(
        "SELECT * FROM asset.func_location WHERE hierarchy_level = 2 LIMIT 1"
    )
    крит_a = await conn.fetchval("SELECT id FROM ref.criticality WHERE code = 'A'")
    for sid, ключ in УЧАСТКИ:
        floc = await conn.fetchval(
            "INSERT INTO asset.func_location (code, name, floc_type_id, structure_id, "
            "hierarchy_level, district_id, parent_id, criticality_id) "
            "VALUES ($1, $2, $3, $4, 4, $5, $6, $7) RETURNING id",
            f"T.{sid}",
            f"Коллектор 889, пикет {ключ}",
            корень["floc_type_id"],
            корень["structure_id"],
            корень["district_id"],
            корень["id"],
            крит_a,
        )
        await conn.execute(
            "INSERT INTO ref.object_xref (section_id, smvu_key, func_location_id) VALUES ($1, $2, $3)",
            sid,
            ключ,
            floc,
        )


async def _прогон(conn, as_of, статус="done"):
    """Строка pred.run и прогнозы всех пяти участков — их ищет ОТБОР в завести()."""
    run_id = await conn.fetchval(
        "INSERT INTO pred.run (status, as_of, model_version) VALUES ($1, $2, 'v3-test') RETURNING run_id",
        статус,
        as_of,
    )
    for sid, _ in УЧАСТКИ:
        await conn.execute(
            "INSERT INTO pred.forecast (run_id, section_id, direction, horizon_h, probability, "
            "risk_rank, factors) VALUES ($1, $2, 'sensor_failure', 24, 0.5, 1, '[]')",
            run_id,
            sid,
        )
    return run_id


def _предупреждение(opened_at, **поля):
    return {
        "pfx": "889",
        "opened_at": opened_at,
        "expires_at": None,
        "p": 0.5,
        "закрыто": False,
        "участки": list(УЧАСТКИ),
    } | поля


async def _завести(conn, run_id, предупреждения, веса, as_of=AS_OF):
    """То, что делает run.py на пути v3: план ДО записи, потом завести()."""
    исходные = order_rules._веса

    async def подменённые(_conn):
        return веса

    order_rules._веса = подменённые
    try:
        план = await order_rules.план_заявок(
            conn, предупреждения, {s for s, _ in УЧАСТКИ}, as_of
        )
        return await order_rules.завести(
            conn, run_id, "sensor_failure", план["участки"]
        )
    finally:
        order_rules._веса = исходные


async def _заявки(conn):
    return await conn.fetch(
        "SELECT n.source_key, n.status, x.section_id FROM maint.notification n "
        "JOIN ref.object_xref x ON x.func_location_id = n.func_location_id "
        "WHERE n.source_system = 'forecast' AND x.section_id BETWEEN 900001 AND 900005 "
        "ORDER BY n.id"
    )


def test_миграция_ставит_три_участка_на_предупреждение():
    async def тело(conn):
        return await conn.fetchval(
            "SELECT value FROM ref.app_setting WHERE key = 'order_top_sections_per_object'"
        )

    assert float(_в_базе(тело)) == 3


def test_смена_веса_после_открытия_не_заводит_новых_заявок():
    async def тело(conn):
        п = [_предупреждение("2026-06-10T08:36:56")]
        первый = await _завести(conn, await _прогон(conn, AS_OF), п, ВЕСА_ДО)
        второй = await _завести(conn, await _прогон(conn, AS_OF), п, ВЕСА_ПОСЛЕ)
        return первый, второй, await _заявки(conn)

    первый, второй, заявки = _в_базе(тело)
    assert первый["заявок"] == 3 and второй["заявок"] == 0
    assert sorted(з["section_id"] for з in заявки) == [900001, 900002, 900003]


def test_живая_заявка_глушит_новое_открытие_а_закрытая_нет():
    async def тело(conn):
        await _завести(
            conn,
            await _прогон(conn, AS_OF),
            [_предупреждение("2026-06-10T08:36:56")],
            ВЕСА_ДО,
        )
        # Новое открытие того же префикса: на 1…3 живые заявки, новых там нет.
        глушит = await _завести(
            conn,
            await _прогон(conn, AS_OF),
            [_предупреждение("2026-06-20T10:00:00")],
            ВЕСА_ДО,
        )
        # Бригада закрыла заявку на участке 900001 — следующее открытие её заводит.
        await conn.execute(
            "UPDATE maint.notification n SET status = 'COMPLETED' FROM ref.object_xref x "
            "WHERE x.func_location_id = n.func_location_id AND x.section_id = 900001"
        )
        после = await _завести(
            conn,
            await _прогон(conn, AS_OF),
            [_предупреждение("2026-06-25T10:00:00")],
            ВЕСА_ДО,
        )
        return глушит, после, await _заявки(conn)

    глушит, после, заявки = _в_базе(тело)
    assert глушит["заявок"] == 0
    assert после["заявок"] == 1
    assert заявки[-1]["source_key"] == "warn:889:2026-06-25T10:00:00:900001"


def test_просроченная_заявка_не_глушит():
    async def тело(conn):
        await _завести(
            conn,
            await _прогон(conn, AS_OF),
            [_предупреждение("2026-06-10T08:36:56")],
            ВЕСА_ДО,
        )
        # Срок первых заявок — срез + 16 ч. Прогон через двое суток: срок наступил.
        позже = AS_OF + timedelta(days=2)
        return await _завести(
            conn,
            await _прогон(conn, позже),
            [_предупреждение("2026-07-02T10:00:00")],
            ВЕСА_ДО,
            позже,
        )

    assert _в_базе(тело)["заявок"] == 3


def test_нижняя_граница_срез_прошлого_прогона_но_не_раньше_горизонта():
    async def тело(conn):
        текущий = await _прогон(conn, AS_OF, "running")
        без_прошлых = await run_v3.нижняя_граница(conn, текущий, AS_OF, 24)
        прошлый = AS_OF - timedelta(hours=4)
        await _прогон(conn, прошлый)
        await _прогон(conn, AS_OF - timedelta(hours=2), "failed")  # упавший не в счёт
        с_прошлым = await run_v3.нижняя_граница(conn, текущий, AS_OF, 24)
        return без_прошлых, с_прошлым, прошлый

    без_прошлых, с_прошлым, прошлый = _в_базе(тело)
    assert без_прошлых == AS_OF - timedelta(hours=24)
    assert с_прошлым == прошлый


def test_два_файла_подряд_второй_заводит_только_новые_открытия():
    """Критерий приёмки MOS-182: откат на два файла, второй — только открытия между срезами."""
    срез1 = datetime(2026, 4, 1, 23, 59, 59, tzinfo=МОСКВА)
    срез2 = срез1 + timedelta(days=1)
    файл1 = {
        "horizon_h": 24,
        "alert_threshold": 0.63,
        "collectors": [
            {
                "pfx": "889",
                "p": 0.7,
                "warning_open": True,
                "features": [],
                "warning_opened_at": "2026-04-01T07:44:31",
                "warning_expires_at": "2026-04-02T07:44:31",
            }
        ],
        "alerts": [
            {"pfx": "889", "t": "2026-04-01T04:54:26"},
            {"pfx": "889", "t": "2026-04-01T07:44:31"},
        ],
    }
    файл2 = файл1 | {
        "alerts": файл1["alerts"] + [{"pfx": "889", "t": "2026-04-02T09:00:00"}]
    }

    async def тело(conn):
        итоги = []
        for срез, файл in ((срез1, файл1), (срез2, файл2)):
            run_id = await _прогон(conn, срез, "running")
            граница = await run_v3.нижняя_граница(conn, run_id, срез, 24)
            о = открытия(файл, граница)
            for п in о:
                п["участки"] = list(УЧАСТКИ)
            итоги.append(await _завести(conn, run_id, о, ВЕСА_ДО, срез))
            await conn.execute(
                "UPDATE pred.run SET status = 'done' WHERE run_id = $1", run_id
            )
        return итоги, await _заявки(conn)

    (первый, второй), заявки = _в_базе(тело)
    # Первый файл: два открытия 889 ведут на одни три участка — три заявки с ключом
    # позднего открытия, раннее названо в тексте.
    assert первый["заявок"] == 3
    assert {з["source_key"].rsplit(":", 1)[0] for з in заявки[:3]} == {
        "warn:889:2026-04-01T07:44:31"
    }
    # Второй файл: у 07:44:31 заявки уже есть, 04:54:26 раньше границы. Новое 09:00
    # получает свои три: срок первых заявок — срез1 + 16 ч, к срезу2 он наступил,
    # и живыми они уже не считаются.
    assert второй["заявок"] == 3
    assert {з["source_key"].rsplit(":", 1)[0] for з in заявки[3:]} == {
        "warn:889:2026-04-02T09:00:00"
    }


def test_проверка_м10_не_считает_потерей_предупреждение_под_живой_заявкой():
    """code/check_orders.py, М-10, на тех же данных, что заводит worker (Ф-48)."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "code"))
    import check_orders

    ранее, позже = "2026-06-10T08:36:56", "2026-06-20T10:00:00"

    async def тело(conn):
        await _завести(conn, await _прогон(conn, AS_OF), [_предупреждение(ранее)], ВЕСА_ДО)
        второй = await _завести(conn, await _прогон(conn, AS_OF), [_предупреждение(позже)], ВЕСА_ДО)
        score = {"model": "v3-test", "horizon_h": 720, "alerts": [],
                 "open": [("889", check_orders._момент(т), т) for т in (ранее, позже)]}
        return второй, await check_orders.check_m10(conn, score)

    второй, (ок, текст) = _в_базе(тело)
    assert второй["заявок"] == 0
    assert ок, текст
    assert "без заявки 0 из 2" in текст and "из-за живой на участке 1" in текст, текст
    assert "вне 1…3: 0" in текст, текст


def test_собрать_отдаёт_закрытые_открытия_и_границу(tmp_path, monkeypatch):
    """run_v3.собрать — то место worker, где файл превращается в открытия (MOS-182)."""
    import json as _json

    файл = tmp_path / "score.json"
    файл.write_text(_json.dumps({
        "schema_version": "score.v3", "feature_schema": "feat.v3", "as_of": "2026-06-30T23:59:59",
        "model_version": "v3-test", "horizon_h": 720, "alert_threshold": 0.63,
        "feature_names": ["f1"],
        "collectors": [{"pfx": "889", "p": 0.7, "features": [1.0], "warning_open": True,
                        "warning_opened_at": "2026-06-30T20:00:00",
                        "warning_expires_at": "2026-07-30T20:00:00"}],
        "alerts": [{"pfx": "889", "t": "2026-06-30T10:00:00"},
                   {"pfx": "889", "t": "2026-06-30T20:00:00"}],
    }), encoding="utf-8")

    async def мост(_conn):
        return {"889": 7}

    async def участки(_conn):
        return {7: [s for s, _ in УЧАСТКИ]}

    def модель(run_id, as_of, horizon_h, коллекторы, значения, направления, **_):
        return {"predictions": [{"probability": [0.7], "section_ids": коллекторы,
                                 "factors": [[{"f": "f1", "v": 0.1}]]}]}

    monkeypatch.setattr(run_v3, "мост_ключей", мост)
    monkeypatch.setattr(run_v3, "участки_коллекторов", участки)
    monkeypatch.setattr(run_v3.client, "predict", модель)

    async def тело(conn):
        run_id = await _прогон(conn, AS_OF, "running")
        return await run_v3.собрать(conn, str(файл), run_id, 24, "sensor_failure", 3, AS_OF)

    v3 = _в_базе(тело)
    assert v3["граница"] == AS_OF - timedelta(hours=24)
    assert sorted(п["opened_at"] for п in v3["предупреждения"]) == [
        "2026-06-30T10:00:00", "2026-06-30T20:00:00"]
    assert v3["закрытых"] == 1 and v3["предупреждений"] == 2
    # Участки префикса 889 пришли из object_xref, а не из подмены.
    assert {s for п in v3["предупреждения"] for s, _ in п["участки"]} == {s for s, _ in УЧАСТКИ}


def test_прежний_путь_с_разносом_заводит_одну_заявку_на_объект():
    """Q6.10, MOS-154: доли 0,6 / 0,3 / 0,05 / 0,03 / 0,01 — объект 0,99 > 0,97."""
    доли = dict(zip([s for s, _ in УЧАСТКИ], [0.6, 0.3, 0.05, 0.03, 0.01]))

    async def тело(conn, разнос):
        await conn.execute(
            "UPDATE ref.app_setting SET value = $1 WHERE key = 'forecast_spread_enabled'", разнос)
        run_id = await _прогон(conn, AS_OF)
        for sid, д in доли.items():
            await conn.execute(
                "UPDATE pred.forecast SET probability = $1 WHERE run_id = $2 AND section_id = $3",
                д, run_id, sid)
        исходные = order_rules._веса

        async def веса(_conn):
            return {sid: (7, д) for sid, д in доли.items()}

        order_rules._веса = веса
        try:
            счёт = await order_rules.завести(conn, run_id, "sensor_failure", None)
        finally:
            order_rules._веса = исходные
        текст = await conn.fetchval(
            "SELECT long_text FROM maint.notification WHERE source_key LIKE 'day:%' "
            "ORDER BY id DESC LIMIT 1")
        return счёт, await _заявки(conn), текст

    счёт, заявки, текст = _в_базе(lambda conn: тело(conn, 1))
    assert счёт["заявок"] == 1 and [з["section_id"] for з in заявки] == [900001]
    assert "по объекту — 0.990" in текст and "доля участка — 0.600" in текст, текст
    # Разнос выключен — участки судятся по своим числам, как было: 0,6 < 0,97.
    счёт, заявки, _ = _в_базе(lambda conn: тело(conn, 0))
    assert счёт["заявок"] == 0 and заявки == []


def test_класс_риска_с_разносом_по_вероятности_объекта():
    """MOS-154, п. 5 приёмки: high присваивается по объекту, а не по доле участка.

    Объект 0,9 разнесён по пяти участкам: у участка 900001 доля 0,54, у 900005 — 0,009.
    Порог high 0,80: по доле high не получил бы никто, и с карты пропал бы весь класс.
    """
    from app.worker import publish

    доли = dict(zip([s for s, _ in УЧАСТКИ], [0.6, 0.3, 0.05, 0.04, 0.01]))

    async def тело(conn):
        await conn.execute("UPDATE ref.app_setting SET value = 1 WHERE key = 'forecast_spread_enabled'")
        исходные = publish.веса_участков

        async def веса(_conn):
            return {sid: (7, д) for sid, д in доли.items()}

        publish.веса_участков = веса
        try:
            run_id = await conn.fetchval(
                "INSERT INTO pred.run (status, as_of, model_version) VALUES ('running', $1, 'v3-test') "
                "RETURNING run_id", AS_OF)
            участки = [s for s, _ in УЧАСТКИ]
            await publish.записать(conn, run_id, AS_OF, 24, "sensor_failure", участки,
                                   [0.9] * len(участки), [[] for _ in участки], full_log=True)
        finally:
            publish.веса_участков = исходные
        return await conn.fetch(
            "SELECT section_id, probability, risk_class FROM pred.forecast_current "
            "WHERE section_id BETWEEN 900001 AND 900005 ORDER BY section_id")

    строки = _в_базе(тело)
    assert abs(строки[0]["probability"] - 0.54) < 1e-6, "в журнал идёт доля участка, как было"
    assert {с["risk_class"] for с in строки} == {"high"}, [dict(с) for с in строки]
