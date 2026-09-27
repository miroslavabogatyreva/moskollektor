"""Один пул соединений на процесс api, сколько бы запросов ни пришло разом (задача 1.4).

Запуск из корня репозитория: `python3 -m pytest code/tests`.

Нагрузочный прогон 27.09.2026 (deploy/load/20-users.mjs) нашёл гонку в
backend/app/db.py: пул заводится лениво, при первом запросе, и пока первый
вызов get_pool() ждёт create_pool, остальные видят _pool is None и заводят свой.
30 одновременных первых запросов к свежему api выбрали все 100 соединений базы —
«sorry, too many clients already», а nginx отвечал 504 по минуте. На стенде так
будет после каждой выкладки: api перезапускается, и все вкладки разом
переподключают поток уведомлений.
"""

import asyncio
import sys
from pathlib import Path

КОРЕНЬ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(КОРЕНЬ / "backend"))

from app import db


def test_тридцать_первых_запросов_заводят_один_пул(monkeypatch):
    заведено = []

    async def create_pool(*args, **kwargs):
        заведено.append(1)
        await asyncio.sleep(
            0.05
        )  # настоящий create_pool открывает соединения, это не мгновенно
        return object()

    monkeypatch.setattr(db.asyncpg, "create_pool", create_pool)
    monkeypatch.setattr(db, "_pool", None)
    monkeypatch.setenv("DATABASE_URL", "postgresql://x@y/z")

    async def залп():
        return await asyncio.gather(*(db.get_pool() for _ in range(30)))

    пулы = asyncio.run(залп())
    assert len(заведено) == 1, f"заведено пулов: {len(заведено)}"
    assert len({id(p) for p in пулы}) == 1
