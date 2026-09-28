"""MOS-225: real CSV parser → PostgreSQL → existing daily feature function."""

import asyncio
import os
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import pytest
from app.ingest.smvu_csv import CHUNK_COLUMNS, rows_from_file


def test_epoch_is_received_not_fault_or_missing(tmp_path):
    dsn = os.getenv("MOS225_TEST_DSN")
    if not dsn:
        pytest.skip("requires isolated loopback PostgreSQL via MOS225_TEST_DSN")
    parsed = urlsplit(dsn)
    assert parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    assert not parsed.query
    path = tmp_path / "journal.csv"
    path.write_text(
        "ид_события,ид_канала_данных,дата,время,тревожное,значение_датчика\n"
        "1,1,2026-01-01,12:00:00,f,01.01.1970 03:00:00\n"
        "2,1,2026-01-01,12:00:01,f,01.01.1970 03:00:01\n"
    )
    records = []
    for _, row, error in rows_from_file(str(path)):
        assert error is None
        assert row[-1] is None
        records.append(row)

    async def run():
        import asyncpg

        name = "mos225_" + uuid4().hex
        admin = await asyncpg.connect(dsn)
        conn = None
        try:
            await admin.execute(f'CREATE DATABASE "{name}"')
            conn = await asyncpg.connect(urlunsplit(parsed._replace(path="/" + name)))
            await conn.execute("""
                SET TIME ZONE 'Europe/Moscow';
                CREATE SCHEMA smvu; CREATE SCHEMA feat;
                CREATE TABLE smvu.channel(channel_id INTEGER PRIMARY KEY);
                INSERT INTO smvu.channel VALUES(1);
                CREATE TABLE smvu.reading(journal_id BIGINT, read_time TIMESTAMPTZ,
                    channel_id INTEGER, is_alarm BOOLEAN, value_text TEXT, value_num DOUBLE PRECISION);
            """)
            migration = (
                Path(__file__).resolve().parents[2]
                / "db/migrations/022_channel_daily.sql"
            )
            await conn.execute(migration.read_text())
            await conn.copy_records_to_table(
                "reading", schema_name="smvu", columns=CHUNK_COLUMNS, records=records
            )
            await conn.execute(
                "SELECT feat.refresh_channel_daily('2026-01-01', '2026-01-01')"
            )
            result = await conn.fetchrow("SELECT * FROM feat.channel_daily")
            assert result["day"] == date(2026, 1, 1)
            assert result["readings_total"] == 2
            assert result["fault_total"] == result["undefined_total"] == 0
            assert result["last_read"] == records[-1][1]
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM smvu.reading WHERE value_text LIKE '01.01.1970%'"
                )
                == 2
            )
        finally:
            if conn:
                await conn.close()
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
            await admin.close()

    asyncio.run(run())
