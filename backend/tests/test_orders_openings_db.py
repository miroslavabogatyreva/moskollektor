"""MOS-182, Ф-48, MOS-154 и проверка М-10 — через настоящую базу.

Та же одноразовая база, что у test_warning_selection.py (фикстура `database`:
все миграции, реестр ТОиР из 010, шесть участков 889:1…889:6), тот же ключ::

    MOS184_TEST_DSN=postgresql://postgres:…@127.0.0.1:…/postgres \
      python -m pytest backend/tests/test_orders_openings_db.py -v

Без переменной тесты пропускаются, а не зеленеют впустую. Каждый тест идёт
в транзакции и откатывает её: тесты не видят заявок друг друга.
"""

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone

from test_warning_selection import SECTIONS, database, ranking  # noqa: F401 — фикстура

from app.domain import order_rules
from app.domain.order_rules import момент_файла
from app.worker import publish, run_v3
from app.worker.score_v3 import открытия

sys.path.insert(0, str(order_rules.__file__).rsplit("/backend/", 1)[0] + "/code")
import check_orders  # noqa: E402

ТРОЙКА = [1, 2, 3, 4, 5, 6]  # порядок весов: худшие 1, 2, 3


def _в_базе(database, тело):
    import asyncpg

    async def main():
        conn = await asyncpg.connect(database)
        tr = conn.transaction()
        await tr.start()
        try:
            return await тело(conn)
        finally:
            await tr.rollback()
            await conn.close()

    return asyncio.run(main())


async def _прогон(conn, срез, *, обязательно=frozenset(), доли=None, статус="done"):
    """Прогон worker по шести участкам: строка pred.run, журнал прогноза, статус."""
    run = await conn.fetchval(
        "INSERT INTO pred.run(model_version, as_of) VALUES('mos182-test', $1) RETURNING run_id",
        срез,
    )
    вероятности = (
        [доли[s] for s in sorted(SECTIONS)] if доли else [0.495] * len(SECTIONS)
    )
    await publish.записать(
        conn,
        run,
        срез,
        24,
        "sensor_failure",
        sorted(SECTIONS),
        вероятности,
        [[] for _ in SECTIONS],
        full_log=True,
        обязательно=обязательно,
    )
    await conn.execute("UPDATE pred.run SET status=$1 WHERE run_id=$2", статус, run)
    return run


def _открытие(pfx, opened, **поля):
    return {
        "pfx": pfx,
        "opened_at": opened,
        "expires_at": None,
        "p": 0.5,
        "закрыто": False,
        "участки": [(s, f"889:{s}") for s in sorted(SECTIONS)],
    } | поля


async def _завести(conn, открытия_, срез):
    """То, что делает run.py на пути v3: план ДО записи прогноза, потом завести()."""
    план = await order_rules.план_заявок(conn, открытия_, SECTIONS, срез)
    run = await _прогон(conn, срез, обязательно=frozenset(план["участки"]))
    return await order_rules.завести(conn, run, "sensor_failure", план["участки"])


async def _ключи(conn, pfx):
    return sorted(
        r["source_key"]
        for r in await conn.fetch(
            "SELECT source_key FROM maint.notification WHERE source_key LIKE $1",
            f"warn:{pfx}:%",
        )
    )


# ---------------------------------------------------------------- MOS-182


def test_два_файла_подряд_второй_заводит_только_новые_открытия(database, monkeypatch):
    """Критерий приёмки MOS-182 на двух файлах: второй — только открытия между срезами."""
    ranking(monkeypatch, ТРОЙКА)
    срез1 = момент_файла("2026-07-01T23:59:59")
    срез2 = срез1 + timedelta(days=1)
    файл1 = {
        "horizon_h": 24,
        "alert_threshold": 0.63,
        "collectors": [
            {
                "pfx": "1051",
                "p": 0.7,
                "warning_open": True,
                "features": [],
                "warning_opened_at": "2026-07-01T07:44:31",
                "warning_expires_at": "2026-07-02T07:44:31",
            }
        ],
        "alerts": [
            {"pfx": "1051", "t": "2026-07-01T04:54:26"},
            {"pfx": "1051", "t": "2026-07-01T07:44:31"},
        ],
    }
    файл2 = файл1 | {
        "alerts": файл1["alerts"] + [{"pfx": "1051", "t": "2026-07-02T09:00:00"}]
    }

    async def тело(conn):
        итоги = []
        for срез, файл in ((срез1, файл1), (срез2, файл2)):
            текущий = await conn.fetchval(
                "INSERT INTO pred.run(model_version, as_of, status) VALUES('граница', $1, 'running') "
                "RETURNING run_id",
                срез,
            )
            граница = await run_v3.нижняя_граница(conn, текущий, срез, 24)
            о = открытия(файл, граница)
            for п in о:
                п["участки"] = [(s, f"889:{s}") for s in sorted(SECTIONS)]
            итоги.append(await _завести(conn, о, срез))
        return итоги, await _ключи(conn, "1051")

    (первый, второй), ключи = _в_базе(database, тело)
    # Первый файл: два открытия ведут на одну тройку — три заявки с ключом позднего.
    assert первый["заявок"] == 3 and второй["заявок"] == 3
    assert {k.rsplit(":", 1)[0] for k in ключи} == {
        "warn:1051:2026-07-01T07:44:31",
        "warn:1051:2026-07-02T09:00:00",
    }


def test_нижняя_граница_срез_прошлого_прогона_но_не_раньше_горизонта(database):
    срез = момент_файла("2026-07-03T23:59:59")

    async def тело(conn):
        текущий = await conn.fetchval(
            "INSERT INTO pred.run(model_version, as_of, status) VALUES('граница', $1, 'running') "
            "RETURNING run_id",
            срез,
        )
        без_прошлых = await run_v3.нижняя_граница(conn, текущий, срез, 24)
        await conn.execute(
            "INSERT INTO pred.run(model_version, as_of, status) VALUES('x', $1, 'done')",
            срез - timedelta(hours=4),
        )
        await conn.execute(
            "INSERT INTO pred.run(model_version, as_of, status) VALUES('x', $1, 'failed')",
            срез - timedelta(hours=2),
        )
        return без_прошлых, await run_v3.нижняя_граница(conn, текущий, срез, 24)

    без_прошлых, с_прошлым = _в_базе(database, тело)
    # Прогоны фикстуры моложе среза на трое суток, горизонт 24 ч — граница по горизонту.
    assert без_прошлых == срез - timedelta(hours=24)
    assert с_прошлым == срез - timedelta(hours=4), "упавший прогон не в счёт"


def test_собрать_отдаёт_закрытые_открытия_и_границу(database, tmp_path, monkeypatch):
    """run_v3.собрать — то место worker, где файл превращается в открытия."""
    срез = момент_файла("2026-07-04T23:59:59")
    файл = tmp_path / "score.json"
    файл.write_text(
        json.dumps(
            {
                "schema_version": "score.v3",
                "feature_schema": "feat.v3",
                "as_of": "2026-07-04T23:59:59",
                "model_version": "v3-test",
                "horizon_h": 720,
                "alert_threshold": 0.63,
                "feature_names": ["f1"],
                "collectors": [
                    {
                        "pfx": "889",
                        "p": 0.7,
                        "features": [1.0],
                        "warning_open": True,
                        "warning_opened_at": "2026-07-04T20:00:00",
                        "warning_expires_at": "2026-08-03T20:00:00",
                    }
                ],
                "alerts": [
                    {"pfx": "889", "t": "2026-07-04T10:00:00"},
                    {"pfx": "889", "t": "2026-07-04T20:00:00"},
                ],
            }
        ),
        encoding="utf-8",
    )

    async def мост(_conn):
        return {"889": 7}

    async def участки(_conn):
        return {7: sorted(SECTIONS)}

    def модель(run_id, as_of, horizon_h, коллекторы, значения, направления, **_):
        return {
            "predictions": [
                {
                    "probability": [0.7],
                    "section_ids": коллекторы,
                    "factors": [[{"f": "f1", "v": 0.1}]],
                }
            ]
        }

    monkeypatch.setattr(run_v3, "мост_ключей", мост)
    monkeypatch.setattr(run_v3, "участки_коллекторов", участки)
    monkeypatch.setattr(run_v3.client, "predict", модель)

    async def тело(conn):
        run_id = await conn.fetchval(
            "INSERT INTO pred.run(model_version, as_of, status) VALUES('x', $1, 'running') RETURNING run_id",
            срез,
        )
        return await run_v3.собрать(
            conn, str(файл), run_id, 24, "sensor_failure", 3, срез
        )

    v3 = _в_базе(database, тело)
    assert v3["граница"] == срез - timedelta(hours=24)
    assert sorted(п["opened_at"] for п in v3["предупреждения"]) == [
        "2026-07-04T10:00:00",
        "2026-07-04T20:00:00",
    ]
    assert v3["закрытых"] == 1
    # Участки префикса 889 пришли из object_xref, а не из подмены.
    assert {s for п in v3["предупреждения"] for s, _ in п["участки"]} == SECTIONS


# ---------------------------------------------------------------- Ф-48


def test_живая_заявка_глушит_новое_открытие_а_закрытая_нет(database, monkeypatch):
    ranking(monkeypatch, ТРОЙКА)
    срез = момент_файла("2026-07-05T23:59:59")

    async def тело(conn):
        первое = await _завести(conn, [_открытие("f48", "2026-07-05T08:00:00")], срез)
        глушит = await _завести(conn, [_открытие("f48", "2026-07-05T20:00:00")], срез)
        # Бригада закрыла заявку первого открытия на участке 1 — второе заводит её.
        await conn.execute(
            "UPDATE maint.notification SET status='COMPLETED' "
            "WHERE source_key = 'warn:f48:2026-07-05T08:00:00:1'"
        )
        после = await _завести(conn, [_открытие("f48", "2026-07-05T20:00:00")], срез)
        return первое, глушит, после, await _ключи(conn, "f48")

    первое, глушит, после, ключи = _в_базе(database, тело)
    assert (первое["заявок"], глушит["заявок"], после["заявок"]) == (3, 0, 1)
    assert "warn:f48:2026-07-05T20:00:00:1" in ключи


def test_просроченная_заявка_не_глушит(database, monkeypatch):
    ranking(monkeypatch, ТРОЙКА)
    срез = момент_файла("2026-07-06T23:59:59")

    async def тело(conn):
        await _завести(conn, [_открытие("due", "2026-07-06T08:00:00")], срез)
        # Срок первых заявок — срез + 16 ч. Через двое суток он наступил.
        return await _завести(
            conn, [_открытие("due", "2026-07-08T08:00:00")], срез + timedelta(days=2)
        )

    assert _в_базе(database, тело)["заявок"] == 3


# ---------------------------------------------------------------- М-10


def _score(*открытые):
    return {
        "model": "mos182-test",
        "horizon_h": 24,
        "alerts": [],
        "open": [(pfx, момент_файла(t), t) for pfx, t in открытые],
    }


def test_м10_не_считает_потерей_открытие_под_живыми_заявками_на_всей_тройке(
    database, monkeypatch
):
    ranking(monkeypatch, ТРОЙКА)
    срез = момент_файла("2026-07-07T23:59:59")

    async def тело(conn):
        await _завести(conn, [_открытие("m10", "2026-07-07T08:00:00")], срез)
        второе = await _завести(conn, [_открытие("m10", "2026-07-07T20:00:00")], срез)
        итог = await check_orders.check_m10(
            conn, _score(("m10", "2026-07-07T08:00:00"), ("m10", "2026-07-07T20:00:00"))
        )
        return второе, итог

    второе, (ок, текст) = _в_базе(database, тело)
    assert второе["заявок"] == 0
    assert ок, текст
    assert "без заявки 0 из 2" in текст and "из-за живой на всей тройке 1" in текст, (
        текст
    )


def test_м10_краснеет_когда_worker_теряет_открытие_при_истёкшей_заявке(
    database, monkeypatch
):
    """Пример проверяющего (там префикс 257; здесь 889 — у участков фикстуры он настоящий,
    и прежняя проверка по префиксу smvu_key их находит): префикс открылся в T, у
    прошлого открытия заявка со сроком
    T+11 ч. Worker, который новое открытие потерял, прошлой проверкой («живая на любом
    участке префикса со сроком позже открытия») засчитывался. Теперь — нет."""
    ranking(monkeypatch, ТРОЙКА)
    T = момент_файла("2026-07-09T12:00:00")
    прошлое, новое = "2026-07-09T06:00:00", "2026-07-09T12:00:00"

    async def прогнать(conn, сломан):
        # Прошлое открытие заведено срезом T − 5 ч: срок заявки T + 11 ч.
        await _завести(conn, [_открытие("889", прошлое)], T - timedelta(hours=5))
        if сломан:

            async def теряет(*_a, **_k):
                return {"участки": {}, "уже": 0, "живых": 0, "вне_прогона": []}

            monkeypatch.setattr(order_rules, "план_заявок", теряет)
        # Прогон через 13 ч после открытия: срок прошлой заявки наступил.
        await _завести(conn, [_открытие("889", новое)], T + timedelta(hours=13))
        return await check_orders.check_m10(conn, _score(("889", новое)))

    ок, текст = _в_базе(database, lambda conn: прогнать(conn, False))
    assert ок, текст
    исходный = order_rules.план_заявок
    try:
        ок, текст = _в_базе(database, lambda conn: прогнать(conn, True))
    finally:
        monkeypatch.setattr(order_rules, "план_заявок", исходный)
    assert not ок and "без заявки 1 из 1" in текст, текст


# ---------------------------------------------------------------- MOS-154


ДОЛИ = dict(zip(sorted(SECTIONS), [0.6, 0.3, 0.05, 0.03, 0.01, 0.0]))


async def _разнос_включён(conn, monkeypatch):
    await conn.execute(
        "UPDATE ref.app_setting SET value=1 WHERE key='forecast_spread_enabled'"
    )
    await conn.execute(
        "INSERT INTO smvu.object_tree(object_id, level, kind, name) "
        "VALUES (7, 2, 'object', 'объект Мю') ON CONFLICT DO NOTHING"
    )

    async def веса(_conn):
        return {sid: (7, д) for sid, д in ДОЛИ.items()}

    monkeypatch.setattr(order_rules, "_веса", веса)
    monkeypatch.setattr(publish, "веса_участков", веса)


def test_прежний_путь_с_разносом_заводит_одну_заявку_на_объект_с_object_id(
    database, monkeypatch
):
    """Q6.10: доли 0,6 / 0,3 / … — объект 0,99 > 0,97, заявка одна, на участке 1."""
    срез = момент_файла("2026-07-10T23:59:59")

    async def тело(conn):
        await _разнос_включён(conn, monkeypatch)
        run = await conn.fetchval(
            "INSERT INTO pred.run(model_version, as_of) VALUES('spread', $1) RETURNING run_id",
            срез,
        )
        for sid, д in ДОЛИ.items():
            await conn.execute(
                "INSERT INTO pred.forecast(run_id, section_id, direction, horizon_h, probability, "
                "risk_rank, factors) VALUES ($1, $2, 'sensor_failure', 24, $3, 1, '[]')",
                run,
                sid,
                д,
            )
        счёт = await order_rules.завести(conn, run, "sensor_failure", None)
        return счёт, await conn.fetch(
            "SELECT n.object_id, n.long_text, x.section_id FROM maint.notification n "
            "JOIN ref.object_xref x ON x.func_location_id = n.func_location_id "
            "WHERE n.source_key LIKE 'day:%' AND n.reported_at = $1",
            срез,
        )

    счёт, заявки = _в_базе(database, тело)
    assert счёт["заявок"] == 1 and len(заявки) == 1
    з = заявки[0]
    assert (з["section_id"], з["object_id"]) == (1, 7)
    assert (
        "по объекту — 0.990" in з["long_text"]
        and "доля участка — 0.600" in з["long_text"]
    )


def test_класс_риска_с_разносом_по_вероятности_объекта(database, monkeypatch):
    """MOS-154, п. 5: объект 0,9 разнесён долями — high по объекту, в журнале доля."""
    срез = момент_файла("2026-07-11T23:59:59")

    async def тело(conn):
        await _разнос_включён(conn, monkeypatch)
        run = await conn.fetchval(
            "INSERT INTO pred.run(model_version, as_of) VALUES('spread', $1) RETURNING run_id",
            срез,
        )
        await publish.записать(
            conn,
            run,
            срез,
            24,
            "sensor_failure",
            sorted(SECTIONS),
            [0.9] * len(SECTIONS),
            [[] for _ in SECTIONS],
            full_log=True,
            # Через час: удержание класса (10 мин) от строк фикстуры не мешает.
            сейчас=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        return await conn.fetch(
            "SELECT section_id, probability, risk_class FROM pred.forecast_current "
            "ORDER BY section_id"
        )

    строки = _в_базе(database, тело)
    # Доли в сумме 0,99 — разнос нормирует их: 0,9 × 0,6 / 0,99.
    assert abs(строки[0]["probability"] - 0.9 * 0.6 / 0.99) < 1e-6, (
        "в журнал идёт доля участка"
    )
    assert max(с["probability"] for с in строки) < 0.80, (
        "по доле high не получил бы никто"
    )
    assert {с["risk_class"] for с in строки} == {"high"}, [dict(с) for с in строки]
