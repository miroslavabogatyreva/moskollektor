"""Пул API держит потолок на запрос: зависший запрос не занимает соединение навсегда.

27.09.2026 пул без потолка занялся целиком, и все методы /api/* висели дольше
25 минут. Проверяем, с чем пул создаётся, — базы тесту не нужно.
"""
import asyncio

import asyncpg

from app import db


def test_pool_has_statement_and_command_timeout(monkeypatch):
    seen = {}

    async def fake_create_pool(dsn, **kw):
        seen.update(kw)
        return object()

    monkeypatch.setenv("DATABASE_URL", "postgresql://x@127.0.0.1/x")
    monkeypatch.setattr(asyncpg, "create_pool", fake_create_pool)
    monkeypatch.setattr(db, "_pool", None)
    asyncio.run(db.get_pool())

    assert seen["command_timeout"] == 60
    assert seen["server_settings"] == {"statement_timeout": "60s"}
