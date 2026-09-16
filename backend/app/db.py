"""Пул соединений с базой, общий для всех методов API. Заводится здесь, а не
внутри каждого модуля, потому что deps.py (Q4.1) и списочные методы (Q4.3, 4.6,
4.10) читают одну и ту же базу через один и тот же пул: два пула на процесс
только удвоили бы число соединений без всякой пользы.
"""
import json
import os

import asyncpg


async def _init_conn(conn: asyncpg.Connection) -> None:
    # asyncpg отдаёт jsonb как сырую строку, если не поставить кодек: pred.forecast.factors
    # (Q4.3) иначе придёт клиенту как JSON-строка внутри JSON, а не вложенным объектом.
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog", format="text",
    )


_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], init=_init_conn)
    return _pool


async def get_conn():
    pool = await get_pool()
    async with pool.acquire() as conn:
        yield conn
