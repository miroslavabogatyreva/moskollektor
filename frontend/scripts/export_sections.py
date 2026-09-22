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

MOS-181 (Q5.35, М-05, 22.09.2026): раньше поле `collector` было префиксом тега
(`smvu_key.split(":")`) — 30 разных значений, а прогноз и заявки считают риск
по 16 коллекторам дерева заказчика (`smvu.channel_collector`, миграция 029).
«Коллектор» на схеме и «коллектор» в прогнозе были не одной и той же сущностью.
Правило «участок → collector_id, где у него больше активных каналов» уже жило
в `backend/app/worker/run_v3.py` (запрос УЧАСТКИ_КОЛЛЕКТОРА) — оттуда и берём
его отсюда импортом, а не второй копией: копия молча разошлась бы с прогнозом
при первой правке run_v3.py, а участок 1490 («798:0») делится между двумя
коллекторами (13 каналов на Зите, 3 на Бете) и без общего правила разъедет
схему и карточку `GET /api/objects/{id}` по разным цифрам риска.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

import asyncpg

# backend/ — сосед этого файла по репозиторию (frontend/scripts/../../backend),
# не отдельный пакет. Импорт стоит только запроса УЧАСТКИ_КОЛЛЕКТОРА и того, что
# он тянет за собой (app.mlclient.client, app.worker.score_v3) — оба на голом
# stdlib, fastapi/asyncpg из backend/requirements.txt для этого не нужны.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "backend"))
from app.worker.run_v3 import УЧАСТКИ_КОЛЛЕКТОРА  # noqa: E402

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
        # Тот же запрос, что относит участок к коллектору при расчёте прогноза
        # (backend/app/worker/run_v3.py:53-61) — не пересчитываем правило своей
        # копией, берём его результат.
        collector_by_section = {
            r["section_id"]: r["collector_id"] for r in await conn.fetch(УЧАСТКИ_КОЛЛЕКТОРА)
        }
        collector_names = {
            r["collector_id"]: r["collector_name"]
            for r in await conn.fetch(
                "SELECT DISTINCT collector_id, collector_name FROM smvu.channel_collector"
            )
        }
    finally:
        await conn.close()

    sections = []
    без_коллектора = []
    for r in rows:
        collector_id = collector_by_section.get(r["section_id"])
        if collector_id is None:
            без_коллектора.append(r["section_id"])
            continue
        _, picket = r["smvu_key"].split(":")
        sections.append({
            "section_id": r["section_id"],
            "smvu_key": r["smvu_key"],
            "collector": collector_id,
            "collector_name": collector_names[collector_id],
            "picket": int(picket),
            "kinds": list(r["kinds"]),
        })
    if без_коллектора:
        sys.exit(
            f"{len(без_коллектора)} участков без активного канала на дереве "
            f"коллекторов, коллектор для схемы не определить: {без_коллектора[:20]}"
        )
    sections.sort(key=lambda s: (s["collector"], s["picket"]))

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(sections, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    коллекторов = len({s["collector"] for s in sections})
    print(f"{len(sections)} участков, {коллекторов} коллекторов -> {OUT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
