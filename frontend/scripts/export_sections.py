#!/usr/bin/env python3
"""Выгружает ref.object_xref в frontend/public/data/sections.json — один раз,
пока GET /api/objects не готов (задача Q4.4). Дальше экран читает fetch('/data/sections.json'),
в базу с фронта не ходит.

Подключение через SSH-туннель на localhost:55432 (docs/server.md), пароль берём
переменной окружения PGPASSWORD, чтобы не класть его в аргументы командной строки.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

import asyncpg

OUT_PATH = Path(__file__).resolve().parent.parent / "public" / "data" / "sections.json"


async def main() -> None:
    password = os.environ.get("PGPASSWORD")
    if not password:
        sys.exit("Задайте PGPASSWORD (см. docs/server.md, POSTGRES_PASSWORD)")

    conn = await asyncpg.connect(
        host="127.0.0.1", port=55432, database="moskollektor",
        user="moskollektor", password=password,
    )
    try:
        rows = await conn.fetch(
            "SELECT section_id, smvu_key FROM ref.object_xref "
            "WHERE smvu_key IS NOT NULL ORDER BY smvu_key"
        )
    finally:
        await conn.close()

    sections = []
    for r in rows:
        collector, picket = r["smvu_key"].split(":")
        sections.append({
            "section_id": r["section_id"],
            "smvu_key": r["smvu_key"],
            "collector": int(collector),
            "picket": int(picket),
        })
    sections.sort(key=lambda s: (s["collector"], s["picket"]))

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(sections, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{len(sections)} участков -> {OUT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
