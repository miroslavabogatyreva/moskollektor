"""MOS-264: migrations -> exported model -> worker -> authenticated HTTP.

Uses the real disposable PostGIS fixture, not an API/model mock. Both modes
are mandatory; the synthetic response must contain the actual computed score.
"""

import asyncio
import json

import httpx
from test_sensor_risk_db import ROOT, database  # noqa: F401


def test_sensor_modes_over_real_http_and_database(database, monkeypatch):  # noqa: F811
    monkeypatch.setenv("DATABASE_URL", database)
    monkeypatch.setenv("AUTH_TRUST_HEADER", "1")
    from app import db
    from app.api.main import app
    from app.worker import sensor_scores

    monkeypatch.setattr(db, "_pool", None)
    monkeypatch.setattr(db, "_audit_pool", None)
    monkeypatch.setattr(db, "_pool_lock", asyncio.Lock())

    async def run():
        import asyncpg

        conn = await asyncpg.connect(database)
        try:
            await conn.execute((ROOT / "db/seed/rbac.sql").read_text())
            await conn.execute(
                "INSERT INTO ref.user_role(login, role_code) "
                "VALUES ('disp_kappa', 'dispatcher') ON CONFLICT DO NOTHING"
            )
            result = await sensor_scores.посчитать(conn)
            assert result["status"] == "ok"
            values = {
                row["channel_id"]: row
                for row in await conn.fetch("SELECT * FROM pred.sensor_risk")
            }
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://mos264.local"
            ) as client:
                assert (await client.get("/api/sensor-risk")).status_code == 401
                headers = {"X-User-Login": "admin1"}
                modes = {}
                for synthetic in (0, 1):
                    response = await client.get(
                        "/api/sensor-risk", headers=headers,
                        params={"synthetic": synthetic, "limit": 5000},
                    )
                    assert response.status_code == 200, response.text
                    body = response.json()
                    modes[synthetic] = body
                    assert body["synthetic"] is bool(synthetic)
                    assert body["total"] == result["rows"]
                    for item in body["items"]:
                        row = values[item["channel_id"]]
                        expected = row["score_real"] + (
                            row["score_synth"] if synthetic else 0
                        )
                        assert abs(item["score"] - expected) <= 1.1e-6
                        assert item["level"] == row[
                            "level_full" if synthetic else "level_real"
                        ]
                        expected_reasons = json.loads(row[
                            "reasons" if synthetic else "reasons_real"
                        ])
                        assert item["reasons"] == expected_reasons
                        if not synthetic:
                            assert item["equipment"] is None
                            assert all(r["kind"] != "synthetic" for r in item["reasons"])
                    summary = await client.get(
                        "/api/sensor-risk/summary", headers=headers,
                        params={"synthetic": synthetic},
                    )
                    assert summary.status_code == 200
                    counts = summary.json()
                    assert sum(counts[k] for k in ("high", "watch", "normal")) == result["rows"]
                # A working second model, not merely a different label on the same rows.
                assert any(abs(r["score_synth"]) > 1e-6 for r in values.values())
                assert any(
                    r["kind"] == "synthetic"
                    for item in modes[1]["items"] for r in item["reasons"]
                )
                scoped = await client.get(
                    "/api/sensor-risk", headers={"X-User-Login": "disp_kappa"},
                    params={"synthetic": 1, "limit": 5000},
                )
                assert scoped.status_code == 200
                assert scoped.json()["total"] < result["rows"]
                assert {r["collector_id"] for r in scoped.json()["items"]} == {15}
                assert (await client.get(
                    "/api/sensor-risk", headers=headers, params={"synthetic": 2}
                )).status_code == 422
            assert await conn.fetchval("SELECT count(*) FROM audit.user_action") >= 7
        finally:
            await conn.close()
            for name in ("_pool", "_audit_pool"):
                pool = getattr(db, name)
                if pool is not None:
                    await pool.close()
                setattr(db, name, None)

    asyncio.run(run())
