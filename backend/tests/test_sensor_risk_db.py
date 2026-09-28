"""Прогноз по датчикам на настоящей базе: сид паспорта (SL.1, MOS-250), тик
pred.sensor_risk (SL.3, MOS-252) и методы GET /api/sensor-risk (SL.4, MOS-253).

Одноразовая база, как у test_warning_selection.py, тот же ключ::

    MOS184_TEST_DSN=postgresql://postgres@127.0.0.1:5432/postgres \\
      python -m pytest backend/tests/test_sensor_risk_db.py -v

Без переменной тесты пропускаются, а не зеленеют впустую. Каналы выдуманные:
11 500 штук — столько активных каналов на стенде, — все 19 видов датчиков,
два коллектора по узлу, плюс заглушка и выключенный канал, которым паспорт
не положен. Время сида и тика тест печатает: это время локальной машины,
а не стенда.
"""

import asyncio
import json
import os
import sys
import time
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import pytest
from app.api import objects
from app.domain import sensor_risk
from app.migrate import CREATE_JOURNAL
from app.worker import sensor_scores

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "code"))
import synth_sensor_level

PPR_SQL = ROOT / "db/seed/ppr_2026.sql"
КАНАЛОВ = 11_500
AS_OF = "2026-06-30 23:59:59+03"
ADMIN = {"login": "admin", "roles": ["admin"]}
DISP = {"login": "disp_kappa", "roles": ["dispatcher"]}  # видит только коллектор 15


def _в_базе(dsn, тело):
    import asyncpg

    async def main():
        conn = await asyncpg.connect(dsn)
        try:
            return await тело(conn)
        finally:
            await conn.close()

    return asyncio.run(main())


@pytest.fixture(scope="module")
def database():
    raw = os.environ.get("MOS184_TEST_DSN")
    if not raw:
        pytest.skip("requires isolated local PostGIS via MOS184_TEST_DSN")
    parsed = urlsplit(raw)
    assert parsed.hostname in {"127.0.0.1", "localhost", "::1"}, (
        "local test server only"
    )
    name = "sensor_test_" + uuid4().hex
    dsn = urlunsplit(parsed._replace(path="/" + name))
    import asyncpg

    async def create():
        admin = await asyncpg.connect(raw)
        try:
            await admin.execute(f'CREATE DATABASE "{name}"')
        finally:
            await admin.close()
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(CREATE_JOURNAL)
            for path in sorted((ROOT / "db/migrations").glob("*.sql")):
                async with conn.transaction():
                    await conn.execute(path.read_text())
            # Чистая установка до заливки: сид молчит и ничего не пишет.
            await conn.execute(synth_sensor_level.SEED_SQL.read_text())
            assert await conn.fetchval("SELECT count(*) FROM asset.equipment") == 0
            # график ППР пишется и до заливки дерева, но без узлов
            await conn.execute(PPR_SQL.read_text())
            assert await conn.fetchval(
                "SELECT count(*) FILTER (WHERE object_id IS NULL) FROM maint.ppr_window"
            ) == 26
            await conn.execute("""
                INSERT INTO smvu.object_tree (object_id, level, parent_id, kind, name) VALUES
                  (1, 1, NULL, 'district', 'Район'),
                  (15, 2, 1, 'guardObject', 'объект Каппа'),
                  (16, 2, 1, 'guardObject', 'объект Лямбда'),
                  (5657, 3, 15, 'controlHouse', 'объект Каппа ДУ'),
                  (5675, 3, 16, 'controlHouse', 'объект Мю ДУ'),
                  (5700, 3, 16, 'controlHouse', 'объект Лямбда ДУ');
                INSERT INTO ref.object_xref (section_id, smvu_key)
                SELECT g, 'test:' || g FROM generate_series(1, 40) g;
                INSERT INTO ref.app_user (login, full_name) VALUES ('disp_kappa', 'Диспетчер Каппы');
                INSERT INTO ref.user_scope (login, object_id) VALUES ('disp_kappa', 15);
            """)
            await conn.execute(PPR_SQL.read_text())  # теперь узлы есть — допишет
            # Каналы: первая половина — узел 5657 (участки 1…20), вторая — 5700 (21…40).
            await conn.execute(
                """
                INSERT INTO smvu.channel (channel_id, system_kind, sensor_kind, tag, name,
                                          picket, section_id, object_id)
                SELECT 266000 + i, NULL, k.sensor_kind, 'test-' || i, 'ДТ ' || i || ' ПК' || (i % 900),
                       i % 900, CASE WHEN i < $1 / 2 THEN 1 + i % 20 ELSE 21 + i % 20 END,
                       CASE WHEN i < $1 / 2 THEN 5657 ELSE 5700 END
                  FROM generate_series(0, $1 - 1) i
                  JOIN (SELECT sensor_kind, row_number() OVER (ORDER BY sensor_kind) - 1 AS n
                          FROM smvu.sensor_kind) k ON k.n = i % 19;
                -- неизвестный вид, заглушка, выключенный канал
                UPDATE smvu.channel SET sensor_kind = NULL WHERE channel_id = 266001;
                INSERT INTO smvu.channel (channel_id, tag, is_stub) VALUES (999001, 'stub', true);
                UPDATE smvu.channel SET is_active = false WHERE channel_id = 266002;
            """.replace("$1", str(КАНАЛОВ))
            )
            # Отказы: газ узла 5657 — в окне ППР 04.06.2026 (не отказ), каждый 7-й
            # канал — свежий отказ 30.06 за 6 ч до среза, каждый 11-й — отказ позже среза (не видим).
            await conn.execute("""
                INSERT INTO smvu.model_failure_episode
                       (channel_id, section_id, started_at, ended_at, fault_value, model_version)
                SELECT c.channel_id, c.section_id, s.t, s.t + interval '3 hours',
                       'Неисправен', 'lgbm-v3-bag-2026.09.21'
                  FROM smvu.channel c
                  JOIN (VALUES (timestamptz '2026-06-04 09:30+03', 'ppr'),
                               (timestamptz '2026-06-30 18:00+03', 'fresh'),
                               (timestamptz '2026-07-10 10:00+03', 'future')) AS s(t, what)
                    ON (s.what = 'ppr' AND c.object_id = 5657 AND c.sensor_kind = 'Газовый датчик')
                    OR (s.what = 'fresh' AND c.channel_id % 7 = 0)
                    OR (s.what = 'future' AND c.channel_id % 11 = 0)
                 WHERE c.is_active AND NOT c.is_stub;
            """)
            t0 = time.monotonic()
            async with conn.transaction():
                await conn.execute(synth_sensor_level.SEED_SQL.read_text())
            print(
                f"\nсид на {КАНАЛОВ} каналах: {time.monotonic() - t0:.1f} с (локально)"
            )
            run = await conn.fetchval(
                "INSERT INTO pred.run (model_version, as_of, status) VALUES ('t', $1::text::timestamptz, 'done')"
                " RETURNING run_id",
                AS_OF,
            )
            await conn.execute(
                """
                INSERT INTO pred.forecast_current (section_id, run_id, as_of, horizon_h,
                                                   probability, risk_rank, factors)
                SELECT section_id, $1, $2::text::timestamptz, 24, 0.1, section_id, '[]'
                  FROM ref.object_xref""",
                run,
                AS_OF,
            )
        finally:
            await conn.close()

    async def remove():
        admin = await asyncpg.connect(raw)
        try:
            await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        finally:
            await admin.close()

    try:
        asyncio.run(create())
        yield dsn
    finally:
        asyncio.run(remove())


def test_seed_passport_everywhere_deterministic_and_blind(database):
    """SL.1: паспорт у каждого активного канала, всех видов, повтор не трогает,
    отказы на паспорт не влияют (check_on_db откатывает всё за собой)."""

    async def тело(conn):
        активных = await conn.fetchval(
            "SELECT count(*) FROM smvu.channel WHERE is_active AND NOT is_stub"
        )
        assert активных == КАНАЛОВ - 1
        assert await synth_sensor_level.check_on_db(conn) == активных
        без = await conn.fetchval("""
            SELECT count(*) FROM smvu.channel c
              LEFT JOIN asset.equipment e ON e.id = c.equipment_id
             WHERE c.is_active AND NOT c.is_stub AND e.source_system IS DISTINCT FROM 'synthetic-demo'""")
        assert без == 0
        # заглушка и выключенный канал паспорта не получили
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM smvu.channel WHERE channel_id IN (999001, 266002) "
                "AND equipment_id IS NOT NULL"
            )
            == 0
        )
        виды = await conn.fetchval("""
            SELECT count(DISTINCT k.code) FROM asset.equipment e
              JOIN ref.object_kind k ON k.id = e.object_kind_id
             WHERE e.source_system = 'synthetic-demo'""")
        assert виды == 20, виды  # 19 видов выгрузки и «прочий» у канала без вида
        проверок = await conn.fetchrow("""
            SELECT count(DISTINCT p.id) AS points, count(*) AS readings
              FROM asset.measuring_point p JOIN asset.measurement m ON m.point_id = p.id""")
        assert проверок["points"] > 0 and проверок["readings"] > проверок["points"]
        # газ узла 5657 поверен в день вывоза из ОМ по графику ППР
        assert await conn.fetchval("""
            SELECT bool_and(d = date '2026-06-18') FROM (
              SELECT max(timezone('Europe/Moscow', m.measured_at)::date) AS d
                FROM smvu.channel c JOIN asset.measuring_point p ON p.equipment_id = c.equipment_id
                JOIN asset.measurement m ON m.point_id = p.id
               WHERE c.object_id = 5657 AND c.sensor_kind = 'Газовый датчик'
               GROUP BY c.channel_id) x""")

    _в_базе(database, тело)


def test_ppr_seed_rows_nodes_and_rerun(database):
    """SL.2: 26 строк графика, узел — только у пар, которые есть в дереве;
    повторный накат ничего не трогает; тик берёт окна match = 'sure'."""

    async def тело(conn):
        строки = await conn.fetch(
            "SELECT id, plan_row, object_id, match FROM maint.ppr_window ORDER BY id"
        )
        assert len(строки) == 26
        по = {r["plan_row"]: r for r in строки}
        assert [по[f"Объект {n}"]["match"] for n in (14, 9, 3, 6)] == [
            "sure", "sure", "doubtful", "doubtful",
        ]
        assert sum(r["match"] == "none" for r in строки) == 22
        # 4610 и 4369 в тестовом дереве нет — узла нет и у строки
        assert {r["plan_row"]: r["object_id"] for r in строки if r["object_id"]} == {
            "Объект 14": 5657, "Объект 9": 5675,
        }
        await conn.execute(PPR_SQL.read_text())
        assert await conn.fetch(
            "SELECT id, plan_row, object_id, match FROM maint.ppr_window ORDER BY id"
        ) == строки
        окна = sensor_risk.plan_windows(await conn.fetch(sensor_risk.ОКНА))
        assert set(окна) == {(5657, "Газовый датчик"), (5675, "Газовый датчик")}
        assert окна[(5675, "Газовый датчик")][0]["text"].startswith("23.04.2026")

    _в_базе(database, тело)


def test_tick_writes_every_channel_on_forecast_as_of(database):
    """SL.3: строка на каждый активный канал, срез — как у /api/risks, модель — одна."""

    async def тело(conn):
        t0 = time.monotonic()
        итог = await sensor_scores.посчитать(conn)
        print(f"\nтик: {итог}, {time.monotonic() - t0:.1f} с (локально)")
        assert итог["status"] == "ok" and итог["rows"] == КАНАЛОВ - 1, итог
        срез = await conn.fetchval("SELECT max(as_of) FROM pred.forecast_current")
        assert await conn.fetchval(
            "SELECT array_agg(DISTINCT as_of) FROM pred.sensor_risk"
        ) == [срез]
        # повторный тик на том же срезе переписывает срез, а не удваивает
        assert (await sensor_scores.посчитать(conn))["rows"] == КАНАЛОВ - 1
        assert (
            await conn.fetchval("SELECT count(*) FROM pred.sensor_risk") == КАНАЛОВ - 1
        )
        # занято — выходит сразу
        other = await conn.fetchval("SELECT pg_backend_pid()")
        import asyncpg

        держатель = await asyncpg.connect(database)
        try:
            await держатель.fetchval(
                "SELECT pg_advisory_lock($1)", sensor_scores.БЛОКИРОВКА
            )
            assert (await sensor_scores.посчитать(conn)) == {"status": "занято"}
        finally:
            await держатель.close()
        assert other
        # модель одна: строка совпадает с sensor_risk.split() на тех же входах
        строки = {r[0]: r for r in await sensor_scores.баллы(conn, срез)}
        for r in await conn.fetch("SELECT * FROM pred.sensor_risk"):
            s = строки[r["channel_id"]]
            assert abs(r["score_real"] - s[2]) < 1e-6 and abs(r["score_synth"] - s[3]) < 1e-6
            assert (r["level_real"], r["level_full"]) == (s[4], s[5])
            assert 0 <= r["score_real"] + r["score_synth"] <= 1
        # миграция 061 сняла CHECK score_synth >= 0: модель с синтетикой вправе дать
        # вероятность ниже, чем модель без неё
        import asyncpg

        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute(
                "UPDATE pred.sensor_risk SET score_synth = -score_real WHERE channel_id = $1",
                min(строки),
            )
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(
                    "UPDATE pred.sensor_risk SET score_real = 1.5 WHERE channel_id = $1",
                    min(строки),
                )
        finally:
            await tr.rollback()

        виды = dict(await conn.fetch("SELECT channel_id, sensor_kind FROM smvu.channel"))

        def здоровый(channel_id):
            """Вероятность модели без синтетики у канала того же вида без отказов."""
            return sensor_risk.score([], None, срез, kind=виды[channel_id])["score"]

        # ППР: газ 5657 без свежего отказа — причина plan, балл как у здорового
        ппр = await conn.fetchrow("""
            SELECT r.* FROM pred.sensor_risk r JOIN smvu.channel c USING (channel_id)
             WHERE c.object_id = 5657 AND c.sensor_kind = 'Газовый датчик'
               AND c.channel_id % 7 <> 0 AND c.channel_id % 11 <> 0 LIMIT 1""")
        assert abs(ппр["score_real"] - здоровый(ппр["channel_id"])) < 1e-6
        assert "plan" in {x["kind"] for x in json.loads(ппр["reasons"])}
        assert "plan" in {x["kind"] for x in json.loads(ппр["reasons_real"])}
        assert not any(x["kind"] == "synthetic" for x in json.loads(ппр["reasons_real"]))
        # отказ позже среза не виден, свежий — виден
        for r in await conn.fetch(
            "SELECT channel_id, score_real, reasons_real FROM pred.sensor_risk WHERE channel_id % 7 = 0"
        ):
            assert r["score_real"] > здоровый(r["channel_id"]), r
            assert json.loads(r["reasons_real"])[0]["kind"] == "real"
        for r in await conn.fetch(
            "SELECT channel_id, score_real FROM pred.sensor_risk "
            "WHERE channel_id % 11 = 0 AND channel_id % 7 <> 0 AND channel_id NOT IN "
            "(SELECT channel_id FROM smvu.channel WHERE object_id = 5657 AND sensor_kind = 'Газовый датчик')"
        ):
            assert abs(r["score_real"] - здоровый(r["channel_id"])) < 1e-6, r

    _в_базе(database, тело)


def test_worker_sql_waits_for_confirmation_and_check_observation(database):
    """Архивное окончание эпизода и поверка позже среза не доступны раньше."""

    async def тело(conn):
        tr = conn.transaction()
        await tr.start()
        try:
            at = datetime(2026, 6, 30, 21, tzinfo=sensor_risk.MSK)
            await conn.execute("""
                INSERT INTO smvu.channel (channel_id, tag, object_id, section_id,
                                          sensor_kind, collector, picket)
                VALUES (999011, 'causal-confirmed', 5657, 1, 'Газовый датчик', 'test', 1),
                       (999012, 'causal-unconfirmed', 5657, 1, 'Газовый датчик', 'test', 1);
                INSERT INTO smvu.model_failure_episode
                       (channel_id, section_id, started_at, ended_at, fault_value, model_version)
                VALUES (999011, 1, '2026-06-30 19:59:59+03', '2026-06-30 23:00:00+03',
                        'Неисправен', 'lgbm-v3-bag-2026.09.21'),
                       (999012, 1, '2026-06-30 20:00:00+03', '2026-06-30 23:00:00+03',
                        'Неисправен', 'lgbm-v3-bag-2026.09.21');
            """)
            rows = await conn.fetch(sensor_scores.ОТКАЗЫ, at, sensor_risk.CONFIRM_SECONDS)
            assert {r["channel_id"] for r in rows if r["channel_id"] in (999011, 999012)} == {999011}

            point = await conn.fetchrow("""
                SELECT p.id, p.equipment_id FROM asset.measuring_point p
                JOIN asset.equipment e ON e.id = p.equipment_id
                WHERE e.source_system = 'synthetic-demo' ORDER BY p.id LIMIT 1
            """)
            await conn.execute("DELETE FROM asset.measurement WHERE point_id = $1", point["id"])
            await conn.execute("""
                INSERT INTO asset.measurement (point_id, measured_at, value_num)
                VALUES ($1, '2026-06-29 10:00+03', 1), ($1, '2026-06-30 22:00+03', 1)
            """, point["id"])
            checks = await conn.fetch(sensor_scores.ПРОВЕРКИ, sensor_risk.SRC, at)
            assert next(r["last"] for r in checks if r["equipment_id"] == point["equipment_id"]) == date(2026, 6, 29)
        finally:
            await tr.rollback()

    _в_базе(database, тело)


async def _список(conn, user, **kw):
    параметры = {
        "synthetic": 1, "node": None, "collector": None, "channel": None,
        "level": None, "limit": 500, "offset": 0,
    }
    параметры.update(kw)
    return await objects.get_sensor_risk(**параметры, conn=conn, user=user)


def test_api_reads_table_with_filters_and_scope(database):
    """SL.4: synthetic=0|1, node, collector, level, limit/offset, роль, summary."""

    async def тело(conn):
        if not await conn.fetchval("SELECT count(*) FROM pred.sensor_risk"):
            await sensor_scores.посчитать(conn)
        полный = await _список(conn, ADMIN, limit=5000)
        assert полный["synthetic"] is True and полный["total"] == КАНАЛОВ - 1
        баллы = [i["score"] for i in полный["items"]]
        assert баллы == sorted(баллы, reverse=True)
        assert all(i["equipment"] for i in полный["items"])
        assert any(
            r["kind"] == "synthetic" for i in полный["items"] for r in i["reasons"]
        )

        реальный = await _список(conn, ADMIN, synthetic=0, limit=5000)
        assert реальный["synthetic"] is False
        assert all(i["equipment"] is None for i in реальный["items"])
        assert not any(
            r["kind"] == "synthetic" for i in реальный["items"] for r in i["reasons"]
        )
        по_таблице = dict(
            await conn.fetch(
                "SELECT channel_id, round(score_real::numeric, 6) FROM pred.sensor_risk"
            )
        )
        assert all(
            i["score"] == float(по_таблице[i["channel_id"]]) for i in реальный["items"]
        )
        полные = dict(
            await conn.fetch(
                "SELECT channel_id, round((score_real + score_synth)::numeric, 6) FROM pred.sensor_risk"
            )
        )
        assert all(i["score"] == float(полные[i["channel_id"]]) for i in полный["items"])
        # причины при synthetic=0 — reasons_real, а не отфильтрованные reasons
        причины = dict(await conn.fetch("SELECT channel_id, reasons_real FROM pred.sensor_risk"))
        i = next(i for i in реальный["items"] if i["reasons"])
        assert i["reasons"] == json.loads(причины[i["channel_id"]])

        узел = await _список(conn, ADMIN, node=5657)
        assert узел["node_name"] == "объект Каппа ДУ"
        assert узел["total"] == КАНАЛОВ // 2 - 1 and len(узел["items"]) == 500
        assert {i["node_id"] for i in узел["items"]} == {5657}
        assert {i["collector_id"] for i in узел["items"]} == {15}
        # фронт SensorDemo зовёт node=5657 и читает эти поля
        i = узел["items"][0]
        assert {
            "channel_id",
            "name",
            "sensor_kind",
            "picket",
            "section_id",
            "score",
            "level",
            "reasons",
            "equipment",
        } <= i.keys()

        лямбда = await _список(conn, ADMIN, collector=16, level="high")
        assert лямбда["collector_name"] == "объект Лямбда"
        assert лямбда["total"] > 0
        assert all(
            i["level"] == "high" and i["collector_id"] == 16 for i in лямбда["items"]
        )

        стр1 = await _список(conn, ADMIN, limit=10, offset=0)
        стр2 = await _список(conn, ADMIN, limit=10, offset=10)
        assert [i["channel_id"] for i in стр1["items"] + стр2["items"]] == [
            i["channel_id"] for i in полный["items"][:20]
        ]
        assert (await _список(conn, ADMIN, offset=10**6))["total"] == КАНАЛОВ - 1

        свой = await _список(conn, DISP, limit=5000)
        assert свой["total"] == КАНАЛОВ // 2 - 1
        assert {i["collector_id"] for i in свой["items"]} == {15}

        # channel (MOS-255): один элемент с узлом, коллектором и пикетом; вместе с
        # synthetic=0; чужой канал роли и несуществующий — одинаково пустой items
        for syn in (0, 1):
            один = await _список(conn, ADMIN, channel=266003, synthetic=syn)
            assert один["total"] == 1 and [i["channel_id"] for i in один["items"]] == [266003]
            i = один["items"][0]
            assert (i["node_id"], i["collector_id"]) == (5657, 15) and i["picket"] == 3
            assert (i["equipment"] is None) is (syn == 0)
        чужой = await _список(conn, DISP, channel=266000 + КАНАЛОВ - 3)
        assert чужой["total"] == 0 and чужой["items"] == []
        assert (await _список(conn, DISP, channel=266003))["total"] == 1
        assert (await _список(conn, ADMIN, channel=424242))["items"] == []

        # несуществующий узел — пустой items, как у channel, а не 404 (MOS-251)
        нет = await _список(conn, ADMIN, node=424242)
        assert (нет["total"], нет["items"], нет["node_name"]) == (0, [], None)
        with pytest.raises(objects.HTTPException):
            await _список(conn, ADMIN, collector=5657)  # узел, а не коллектор

        stored = await conn.fetch("""
            SELECT c.object_id, r.level_real, r.level_full
            FROM pred.sensor_risk r JOIN smvu.channel c USING (channel_id)
        """)
        expected_collectors = {}
        for syn in (0, 1):
            сводка = await objects.get_sensor_risk_summary(
                synthetic=syn, conn=conn, user=ADMIN
            )
            assert сводка["synthetic"] is bool(syn)
            assert сводка["high"] + сводка["watch"] + сводка["normal"] == КАНАЛОВ - 1
            field = "level_full" if syn else "level_real"
            expected_levels = Counter(r[field] for r in stored)
            assert {k: сводка[k] for k in ("high", "watch", "normal")} == {
                k: expected_levels[k] for k in ("high", "watch", "normal")
            }
            # Высоких рисков может не быть: количество определяет артефакт,
            # а API обязан точно агрегировать сохранённые уровни всего парка.
            expected_collectors[syn] = Counter(
                15 if r["object_id"] == 5657 else 16 for r in stored if r[field] == "high"
            )
            assert сводка["collectors_with_high"] == len(expected_collectors[syn])
            assert {c["collector_id"]: c["high"] for c in сводка["top_collectors"]} == expected_collectors[syn]
        сводка = await objects.get_sensor_risk_summary(
            synthetic=1, conn=conn, user=DISP
        )
        assert {c["collector_id"]: c["high"] for c in сводка["top_collectors"]} == {
            k: v for k, v in expected_collectors[1].items() if k == 15
        }
        assert sensor_risk.SRC == "synthetic-demo"

    _в_базе(database, тело)
