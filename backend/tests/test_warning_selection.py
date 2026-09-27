"""MOS-184 PostgreSQL regression: real migrations, planner, forecasts and orders.

Opt in with a LOCAL throwaway PostGIS server (never the stand)::

    MOS184_TEST_DSN=postgresql://postgres:mos184_test@127.0.0.1:55484/postgres \
      python -m pytest backend/tests/test_warning_selection.py -v

A random database is created and removed on that server. DATABASE_URL is never
used. Only ranking input is substituted; all persistence/transactions/unique
constraints and forecast -> notification -> work-order writes are real.
"""
import asyncio
import json
import os
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import pytest
from app.domain import order_rules
from app.migrate import CREATE_JOURNAL
from app.worker import publish, score_v3

ROOT = Path(__file__).resolve().parents[2]
OPENED = "2026-06-10T08:36:56"
EXPIRES = "2026-07-10T08:36:56"
SECTIONS = set(range(1, 7))
# Срез, который план_заявок сравнивает со сроком живых заявок (Ф-48, решение Славы
# 26.09.2026). Эти тесты проверяют выбор участков (MOS-184), а не Ф-48, поэтому
# срез — через сутки после среза прогонов: сроки всех заявок фикстуры (срез + 16 ч)
# к нему наступили, живых нет. Ф-48 проверяет test_orders_openings_db.py.
AS_OF = order_rules.момент_файла("2026-06-30T23:59:59") + timedelta(days=1)


def warning(pfx="889", opened=OPENED):
    return {"pfx": pfx, "opened_at": opened, "expires_at": EXPIRES,
            "p": .495, "участки": [(sid, f"{pfx}:{sid}") for sid in SECTIONS]}


@pytest.fixture(scope="module")
def database():
    raw = os.environ.get("MOS184_TEST_DSN")
    if not raw:
        pytest.skip("requires isolated local PostGIS via MOS184_TEST_DSN")
    parsed = urlsplit(raw)
    assert parsed.hostname in {"127.0.0.1", "localhost", "::1"}, "local test server only"
    assert not parsed.query, "DSN query options could override isolated database"
    name = "mos184_test_" + uuid4().hex
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
            # Historical warning is inserted below before 050, as on an upgrade.
            for path in sorted((ROOT / "db/migrations").glob("*.sql")):
                if path.name[:3] < "050":
                    async with conn.transaction():
                        await conn.execute(path.read_text())
            for sid in sorted(SECTIONS):
                await conn.execute("INSERT INTO ref.object_xref(section_id,smvu_key) VALUES($1,$2)",
                                   sid, f"889:{sid}")
            # Build the real synthetic TOiR registry from those section keys.
            registry_sql = (ROOT / "db/migrations/010_orders.sql").read_text()
            registry_sql = registry_sql.split("-- ЧАСТЬ 2.", 1)[1].split("-- ЧАСТЬ 3.", 1)[0]
            await conn.execute("-- ЧАСТЬ 2." + registry_sql)
            await conn.execute("UPDATE ref.app_setting SET value=0 WHERE key='forecast_spread_enabled'")
            run = await forecast(conn)
            historical = {sid: {**warning("history"), "ключ": f"warn:history:{OPENED}:{sid}",
                                "маршрут": [f"889:{sid}"]} for sid in (1, 2, 3, 4)}
            assert (await order_rules.завести(conn, run, план=historical))["заявок"] == 4
            one = {1: {**warning("history-one"), "ключ": f"warn:history-one:{OPENED}:1",
                       "маршрут": ["889:1"]}}
            assert (await order_rules.завести(conn, await forecast(conn), план=one))["заявок"] == 1
            for path in sorted((ROOT / "db/migrations").glob("*.sql")):
                if path.name[:3] >= "050":
                    async with conn.transaction():
                        await conn.execute(path.read_text())
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


async def forecast(conn, *, mandatory=frozenset()):
    as_of = order_rules.момент_файла("2026-06-30T23:59:59")
    run = await conn.fetchval("INSERT INTO pred.run(model_version,as_of) VALUES('mos184-fixture-720',$1) RETURNING run_id", as_of)
    await publish.записать(conn, run, as_of, 720, "sensor_failure", sorted(SECTIONS),
                          [.495] * 6, [[] for _ in SECTIONS],
                          full_log=True, обязательно=mandatory)
    return run


async def counts(conn, pfx):
    return await conn.fetchrow("""
        SELECT count(DISTINCT n.id) AS notifications, count(w.id) AS orders,
               array_agg(DISTINCT f.section_id ORDER BY f.section_id) AS sections
          FROM maint.notification n
          JOIN pred.forecast f ON f.forecast_id=n.forecast_id
          LEFT JOIN maint.work_order w ON w.notification_id=n.id
         WHERE n.source_system='forecast' AND split_part(n.source_key, ':', 2)=$1
    """, pfx)


def ranking(monkeypatch, order):
    async def weights(conn):
        return {sid: (12, float(len(order) - i)) for i, sid in enumerate(order)}
    monkeypatch.setattr(order_rules, "_веса", weights)


def test_migrations_trim_historical_excess_to_top_and_never_top_up(database, monkeypatch):
    ranking(monkeypatch, [6, 5, 4, 3, 2, 1])
    async def case():
        import asyncpg
        conn = await asyncpg.connect(database)
        try:
            assert await conn.fetchval("SELECT value FROM ref.app_setting WHERE key='order_top_sections_per_object'") == 3
            before = dict(await counts(conn, "history"))
            plan = await order_rules.план_заявок(conn, [warning("history")], SECTIONS, AS_OF)
            assert plan["участки"] == {}
            run = await forecast(conn)
            assert (await order_rules.завести(conn, run, план=plan["участки"]))["заявок"] == 0
            assert dict(await counts(conn, "history")) == before
            # 053 deletes the surplus: the three earliest orders (sections 1-3) stay.
            assert before["notifications"] == before["orders"] == 3
            assert before["sections"] == [1, 2, 3]
            one = await order_rules.план_заявок(conn, [warning("history-one")], SECTIONS, AS_OF)
            assert one["участки"] == {}
            assert (await counts(conn, "history-one"))["orders"] == 1
        finally:
            await conn.close()
    asyncio.run(case())


def test_weight_changes_repeat_and_distinct_opening(database, monkeypatch):
    ranking(monkeypatch, [1, 2, 3, 4, 5, 6])
    async def case():
        import asyncpg
        conn = await asyncpg.connect(database)
        try:
            plan = await order_rules.план_заявок(conn, [warning()], SECTIONS, AS_OF)
            assert set(plan["участки"]) == {1, 2, 3}
            run = await forecast(conn, mandatory=frozenset(plan["участки"]))
            assert (await order_rules.завести(conn, run, план=plan["участки"]))["заявок"] == 3
            ranking(monkeypatch, [6, 5, 4, 3, 2, 1])
            repeat = await order_rules.план_заявок(conn, [warning()], SECTIONS, AS_OF)
            assert repeat["участки"] == {}
            assert (await order_rules.завести(conn, await forecast(conn), план=repeat["участки"]))["заявок"] == 0
            row = await counts(conn, "889")
            assert row["notifications"] == row["orders"] == 3
            assert row["sections"] == [1, 2, 3]
            reopened = await order_rules.план_заявок(conn, [warning(opened="2026-06-10T20:00:00")], SECTIONS, AS_OF)
            assert set(reopened["участки"]) == {4, 5, 6}
            assert (await order_rules.завести(conn, await forecast(conn), план=reopened["участки"]))["заявок"] == 3
            assert (await counts(conn, "889"))["orders"] == 6
            assert await conn.fetchval("SELECT bool_and(horizon_h=720) FROM pred.forecast")
        finally:
            await conn.close()
    asyncio.run(case())


def test_persisted_choice_survives_failure_and_missing_section_without_reranking(database, monkeypatch):
    ranking(monkeypatch, [1, 2, 3, 4, 5, 6])
    async def case():
        import asyncpg
        conn = await asyncpg.connect(database)
        try:
            first = await order_rules.план_заявок(conn, [warning("retry")], SECTIONS, AS_OF)
            assert set(first["участки"]) == {1, 2, 3}
            # A failed forecast stage leaves the durable selection, no order yet.
            ranking(monkeypatch, [6, 5, 4, 3, 2, 1])
            partial = await order_rules.план_заявок(conn, [warning("retry")], SECTIONS - {1}, AS_OF)
            assert set(partial["участки"]) == {2, 3}
            resumed = await order_rules.план_заявок(conn, [warning("retry")], SECTIONS, AS_OF)
            assert resumed["участки"] == first["участки"]
            # Explicit worker rollback also rolls back the selection.
            tr = conn.transaction()
            await tr.start()
            await order_rules.план_заявок(conn, [warning("rollback")], SECTIONS, AS_OF)
            await order_rules.завести(conn, await forecast(conn), план=first["участки"])
            await tr.rollback()
            assert (await counts(conn, "retry"))["orders"] == 0
            assert await conn.fetchval("SELECT count(*) FROM pred.warning_order_selection WHERE pfx='rollback'") == 0
        finally:
            await conn.close()
    asyncio.run(case())


def test_two_connections_share_first_choice_and_create_only_three_orders(database, monkeypatch):
    ranking(monkeypatch, [1, 2, 3, 4, 5, 6])
    async def case():
        import asyncpg
        a, b = await asyncpg.connect(database), await asyncpg.connect(database)
        try:
            run_a, run_b = await forecast(a), await forecast(b)
            tr = a.transaction()
            await tr.start()
            first = await order_rules.план_заявок(a, [warning("concurrent")], SECTIONS, AS_OF)
            ranking(monkeypatch, [6, 5, 4, 3, 2, 1])
            second_task = asyncio.create_task(order_rules.план_заявок(b, [warning("concurrent")], SECTIONS, AS_OF))
            await asyncio.sleep(.1)
            assert not second_task.done(), "loser must wait for first selection transaction"
            await tr.commit()
            second = await asyncio.wait_for(second_task, 5)
            assert first["участки"] == second["участки"]
            results = await asyncio.gather(
                order_rules.завести(a, run_a, план=first["участки"]),
                order_rules.завести(b, run_b, план=second["участки"]))
            assert sum(r["заявок"] for r in results) == 3
            row = await counts(a, "concurrent")
            assert row["notifications"] == row["orders"] == 3
            assert row["sections"] == [1, 2, 3]
        finally:
            await a.close()
            await b.close()
    asyncio.run(case())


def test_720_hour_score_contract_remains_accepted(tmp_path):
    data = {"schema_version": "score.v3", "feature_schema": "feat.v3",
            "model_version": "mos184-fixture-720", "horizon_h": 720,
            "as_of": "2026-06-30T23:59:59", "feature_names": ["hour"],
            "collectors": [{"pfx": "889", "p": .495, "features": [23],
                            "warning_open": True, "warning_opened_at": OPENED,
                            "warning_expires_at": EXPIRES}]}
    path = tmp_path / "score.json"
    path.write_text(json.dumps(data))
    parsed = score_v3.прочитать(path)
    assert parsed["horizon_h"] == 720
    assert score_v3.предупреждения(parsed)[0][0]["opened_at"] == OPENED


def test_changed_setting_does_not_change_snapshot_and_empty_first_try_is_retryable(database, monkeypatch):
    ranking(monkeypatch, [1, 2, 3, 4, 5, 6])
    async def case():
        import asyncpg
        conn = await asyncpg.connect(database)
        try:
            empty = await order_rules.план_заявок(conn, [warning("empty")], set(), AS_OF)
            assert empty["участки"] == {}
            assert await conn.fetchval("SELECT count(*) FROM pred.warning_order_selection WHERE pfx='empty'") == 0
            first = await order_rules.план_заявок(conn, [warning("empty")], SECTIONS, AS_OF)
            assert set(first["участки"]) == {1, 2, 3}
            await conn.execute("UPDATE ref.app_setting SET value=1 WHERE key='order_top_sections_per_object'")
            ranking(monkeypatch, [6, 5, 4, 3, 2, 1])
            repeated = await order_rules.план_заявок(conn, [warning("empty")], SECTIONS, AS_OF)
            assert repeated["участки"] == first["участки"]
        finally:
            await conn.execute("UPDATE ref.app_setting SET value=3 WHERE key='order_top_sections_per_object'")
            await conn.close()
    asyncio.run(case())


def test_failed_work_order_rolls_back_notification_but_retries_frozen_selection(database, monkeypatch):
    ranking(monkeypatch, [1, 2, 3, 4, 5, 6])
    async def case():
        import asyncpg
        conn = await asyncpg.connect(database)
        try:
            first = await order_rules.план_заявок(conn, [warning("failure")], SECTIONS, AS_OF)
            run = await forecast(conn)
            await conn.execute("""
                CREATE FUNCTION maint.mos184_test_fail() RETURNS trigger LANGUAGE plpgsql AS
                $$ BEGIN RAISE EXCEPTION 'MOS184 injected work-order failure'; END $$;
                CREATE TRIGGER mos184_test_fail BEFORE INSERT ON maint.work_order
                FOR EACH ROW EXECUTE FUNCTION maint.mos184_test_fail();
            """)
            try:
                with pytest.raises(asyncpg.RaiseError, match="MOS184 injected"):
                    await order_rules.завести(conn, run, план=first["участки"])
            finally:
                await conn.execute("DROP TRIGGER mos184_test_fail ON maint.work_order; DROP FUNCTION maint.mos184_test_fail()")
            row = await counts(conn, "failure")
            assert row["notifications"] == row["orders"] == 0
            ranking(monkeypatch, [6, 5, 4, 3, 2, 1])
            retry = await order_rules.план_заявок(conn, [warning("failure")], SECTIONS, AS_OF)
            assert retry["участки"] == first["участки"]
            assert (await order_rules.завести(conn, run, план=retry["участки"]))["заявок"] == 3
            row = await counts(conn, "failure")
            assert row["notifications"] == row["orders"] == 3
            assert row["sections"] == [1, 2, 3]
        finally:
            await conn.close()
    asyncio.run(case())


def test_real_sql_weights_change_without_additional_orders(database):
    """No ranking mock: real channel tree, D5 episodes and section_weight view."""
    async def case():
        import asyncpg
        conn = await asyncpg.connect(database)
        try:
            await conn.execute("""
                INSERT INTO smvu.object_tree(object_id,level,kind,name) VALUES(12,2,'collector','test');
                INSERT INTO smvu.object_tree(object_id,level,parent_id,kind,name) VALUES(120,3,12,'cabinet','test');
                INSERT INTO smvu.channel(channel_id,section_id,object_id,tag)
                  SELECT s,s,120,'889-'||s FROM generate_series(1,6) s;
                INSERT INTO feat.section_daily(section_id,day)
                  SELECT s,DATE '2026-01-01' FROM generate_series(1,6) s;
            """)
            first = await order_rules.план_заявок(conn, [warning("sql-weights")], SECTIONS, AS_OF)
            assert set(first["участки"]) == {1, 2, 3}  # identical weights, stable sid tie-break
            assert (await order_rules.завести(conn, await forecast(conn), план=first["участки"]))["заявок"] == 3
            await conn.execute("""
                INSERT INTO smvu.model_failure_episode(channel_id,section_id,started_at,ended_at,fault_value,model_version)
                SELECT s,s,TIMESTAMPTZ '2026-01-01 00:00:00+03',NULL,v.fault_value,v.model_version
                  FROM generate_series(4,6) s
                  CROSS JOIN (SELECT fault_value,model_version FROM smvu.model_failure_value LIMIT 1) v
            """)
            assert list(await conn.fetch("SELECT section_id FROM pred.section_weight ORDER BY weight DESC,section_id LIMIT 3")) == [(4,), (5,), (6,)]
            repeated = await order_rules.план_заявок(conn, [warning("sql-weights")], SECTIONS, AS_OF)
            assert repeated["участки"] == {}
            assert (await order_rules.завести(conn, await forecast(conn), план=repeated["участки"]))["заявок"] == 0
            row = await counts(conn, "sql-weights")
            assert row["notifications"] == row["orders"] == 3
            assert row["sections"] == [1, 2, 3]
        finally:
            await conn.close()
    asyncio.run(case())


def test_stale_worker_score_does_not_freeze_selection(database, monkeypatch):
    from app.worker import run
    monkeypatch.setattr(run, "ПУТЬ_SCORE", "test-stale-score.json")
    monkeypatch.setattr(run.client, "get_model", lambda: {"model_version": "test-720", "feature_schema": "feat.v3"})
    monkeypatch.setattr(run.client, "проверить_контракт", lambda *args: None)
    async def stale_score(*args):
        return {"участки": sorted(SECTIONS), "вероятности": [.495] * 6,
                "факторы": [[] for _ in SECTIONS], "срез": "2026-06-01T00:00:00",
                "предупреждения": [warning("stale")]}
    monkeypatch.setattr(run.run_v3, "собрать", stale_score)
    async def case():
        import asyncpg
        conn = await asyncpg.connect(database)
        try:
            result = await run.прогон(conn, as_of=order_rules.момент_файла("2026-06-30T23:59:59"), horizon_h=720)
            assert result["status"] == "failed"
            assert "ФайлНеГодится" in result["error_text"] and "срез файла" in result["error_text"]
            assert await conn.fetchval("SELECT count(*) FROM pred.warning_order_selection WHERE pfx='stale'") == 0
            assert (await counts(conn, "stale"))["orders"] == 0
        finally:
            await conn.close()
    asyncio.run(case())
