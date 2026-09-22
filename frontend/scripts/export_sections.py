#!/usr/bin/env python3
"""Выгружает ref.object_xref в frontend/public/data/sections.json — один раз,
пока GET /api/objects не готов (задача Q4.4). Дальше экран читает fetch('/data/sections.json'),
в базу с фронта не ходит.

Подключение через SSH-туннель на localhost:55432 (docs/server.md), пароль берём
переменной окружения PGPASSWORD, чтобы не класть его в аргументы командной строки.

Задача 5.12 (MOS-122, приёмка Ф-93) добавила поле `kinds` — вид_объекта
(smvu.object_tree.kind) участка, для фильтра по типу объекта на экране карты.
У участка бывает несколько видов сразу (435 из 3173 несут оба — датчик
диспетчерского дома и охранной зоны на одном пикете), поэтому это массив,
а не одно значение. «Район» в фильтр не попал: у дерева объектов заказчика
один корень на весь парк, «Район по эксплуатации» (day-one.md:187) — различать
там нечего, это не наш недосмотр, а свойство выгрузки.
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
            """
            SELECT x.section_id, x.smvu_key,
                   COALESCE(k.kinds, ARRAY[]::text[]) AS kinds
            FROM ref.object_xref x
            LEFT JOIN (
                SELECT c.section_id, array_agg(DISTINCT ot.kind ORDER BY ot.kind) AS kinds
                FROM smvu.channel c
                JOIN smvu.object_tree ot ON ot.object_id = c.object_id
                WHERE c.section_id IS NOT NULL
                GROUP BY c.section_id
            ) k ON k.section_id = x.section_id
            WHERE x.smvu_key IS NOT NULL
            """
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
            "kinds": list(r["kinds"]),
        })
    sections.sort(key=lambda s: (s["collector"], s["picket"]))

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(sections, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{len(sections)} участков -> {OUT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
