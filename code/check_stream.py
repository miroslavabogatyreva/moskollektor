"""Сквозная проверка потока СМВУ: архив → эмулятор → POST /api/ingest/readings → smvu.reading.

Задача MOS-37 (Q3.7), приёмка Ф-82. Поднимает приёмный метод (app.api.ingest) на
127.0.0.1:8097, накатывает настоящую 049_reading_stream_partitions.sql поверх
минимальных копий load.batch, smvu.channel и smvu.reading и гоняет настоящий шаг
эмулятора (app.ingest.smvu_emulator.шаг).

ТОЛЬКО НА ПУСТОЙ БАЗЕ: заводит свои схемы load и smvu. Если они уже есть —
отказывается и ничего не пишет. Пустую базу даёт pgserver, рецепт — в
code/check_weather.py, только имя скрипта другое.

Проверка умеет падать: 27.09.2026 сдвиг 364 → 365 дней в smvu_emulator.py
роняет шаг «копия ровно через 364 дня».
"""

import asyncio
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

КОРЕНЬ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(КОРЕНЬ / "backend"))
URL = "http://127.0.0.1:8097/api/ingest/readings"
os.environ["INGEST_URL"] = URL
os.environ["INGEST_TOKEN"] = ТОКЕН = "t" * 40

import asyncpg
import uvicorn
from app.api.ingest import router
from app.ingest import smvu_emulator
from fastapi import FastAPI

СХЕМА = """
CREATE SCHEMA load; CREATE SCHEMA smvu;
CREATE TABLE load.batch (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, object_name text NOT NULL,
    source_file text, tool text, planned_rows integer NOT NULL,
    loaded_rows integer NOT NULL DEFAULT 0, failed_rows integer NOT NULL DEFAULT 0,
    stage text NOT NULL CHECK (stage IN ('test','production')),
    started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
    approved_by text, approved_at timestamptz);
CREATE TABLE smvu.channel (channel_id integer PRIMARY KEY, section_id integer);
CREATE TABLE smvu.reading (
    journal_id bigint NOT NULL, read_time timestamptz NOT NULL,
    channel_id integer NOT NULL REFERENCES smvu.channel, section_id integer,
    is_alarm boolean NOT NULL, value_text text, value_num real,
    source_batch bigint REFERENCES load.batch(id),
    PRIMARY KEY (journal_id, read_time)) PARTITION BY RANGE (read_time);
CREATE TABLE smvu.reading_default PARTITION OF smvu.reading DEFAULT;
"""


def послать(тело: dict, токен: str | None = ТОКЕН) -> tuple[int, dict]:
    з = urllib.request.Request(URL, data=json.dumps(тело).encode(), method="POST",
                               headers={"Content-Type": "application/json"})
    if токен:
        з.add_header("Authorization", f"Bearer {токен}")
    try:
        with urllib.request.urlopen(з) as о:
            return о.status, json.load(о)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def строка(j=1, ch=1, t="2026-09-27T12:00:00+03:00", v="28"):
    return {"journal_id": j, "channel_id": ch, "read_time": t, "is_alarm": False, "value": v}


async def проверить():
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        занято = await c.fetchval(
            "SELECT string_agg(nspname, ', ') FROM pg_namespace WHERE nspname IN ('load', 'smvu')")
        if занято:
            sys.exit(f"ОТКАЗ: в базе уже есть схемы {занято} — нужна пустая база")
        await c.execute(СХЕМА)
        сейчас = datetime.now().astimezone()
        архив_месяц = (сейчас - smvu_emulator.СДВИГ).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        await c.execute(
            f"CREATE TABLE smvu.reading_arch PARTITION OF smvu.reading "
            f"FOR VALUES FROM ('{архив_месяц:%Y-%m-%d}') TO ('{архив_месяц + timedelta(days=62):%Y-%m-01}')")

        # 1. 049 заводит двенадцать партиций, июль 2026 … июнь 2027.
        await c.execute((КОРЕНЬ / "db/migrations/049_reading_stream_partitions.sql").read_text())
        партиции = await c.fetch(
            "SELECT relname FROM pg_class WHERE relkind = 'r' AND relname ~ '^reading_20(26|27)_' ORDER BY 1")
        имена = [р["relname"] for р in партиции]
        assert len(имена) == 12 and имена[0] == "reading_2026_07" and имена[-1] == "reading_2027_06", имена

        # 2. Токен: не задан — 503, чужой — 401.
        os.environ["INGEST_TOKEN"] = ""
        assert послать({"readings": [строка()]})[0] == 503
        os.environ["INGEST_TOKEN"] = ТОКЕН
        assert послать({"readings": [строка()]}, "x" * 40)[0] == 401
        assert послать({"readings": [строка()]}, None)[0] == 401

        # 3. Время без пояса и пачка больше 5000 — 422, в базу ничего.
        assert послать({"readings": [строка(t="2026-09-27T12:00:00")]})[0] == 422
        assert послать({"readings": [строка(j=i) for i in range(5001)]})[0] == 422
        assert await c.fetchval("SELECT count(*) FROM load.batch") == 0

        # 4. Архив за минуту ровно 364 дня назад: число, состояние и тревога.
        await c.execute("INSERT INTO smvu.channel VALUES (1, 10), (2, 20)")
        т = сейчас - smvu_emulator.СДВИГ - timedelta(seconds=30)
        await c.executemany(
            "INSERT INTO smvu.reading (journal_id, read_time, channel_id, section_id, is_alarm, value_text, value_num) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7)",
            [(100, т, 1, 10, False, "28", 28.0), (101, т, 2, 20, True, "Обнаружен дым", None),
             (102, т - timedelta(minutes=5), 1, 10, False, "27", 27.0)])  # вне окна — не уходит

        итог = await smvu_emulator.шаг(c, сейчас - timedelta(seconds=60), сейчас)
        assert итог == {"sent": 2, "accepted": 2, "duplicates": 0, "unknown_channel": 0}, итог
        копии = await c.fetch(
            "SELECT r.*, p.relname FROM smvu.reading r JOIN pg_class p ON p.oid = r.tableoid "
            "WHERE source_batch IS NOT NULL ORDER BY journal_id")
        assert [к["journal_id"] for к in копии] == [100, 101]
        # Копия ровно через 364 дня, участок из справочника, число разобрано.
        assert all(к["read_time"] - т == timedelta(days=364) for к in копии), [к["read_time"] for к in копии]
        assert (копии[0]["section_id"], копии[0]["value_num"]) == (10, 28.0)
        assert (копии[1]["is_alarm"], копии[1]["value_text"], копии[1]["value_num"]) == (True, "Обнаружен дым", None)
        assert копии[0]["relname"] == f"reading_{сейчас:%Y_%m}", копии[0]["relname"]
        assert await c.fetchval("SELECT count(*) FROM smvu.reading_default") == 0

        # 5. Повтор той же минуты — дубли, новых строк нет.
        итог = await smvu_emulator.шаг(c, сейчас - timedelta(seconds=60), сейчас)
        assert (итог["accepted"], итог["duplicates"]) == (0, 2), итог

        # 6. Канала нет в справочнике — считаем, но не берём и заглушку не заводим.
        код, о = послать({"readings": [строка(j=500, ch=999), строка(j=501, ch=1, v="3,5")]})
        assert код == 200 and (о["accepted"], о["unknown_channel"]) == (1, 1), о
        assert await c.fetchval("SELECT value_num FROM smvu.reading WHERE journal_id = 501") == 3.5
        assert await c.fetchval("SELECT count(*) FROM smvu.channel") == 2

        # 7. Каждая пачка — строка load.batch с tool='api' и временем окончания.
        пачки = await c.fetch("SELECT * FROM load.batch ORDER BY id")
        assert [(п["tool"], п["loaded_rows"], п["failed_rows"]) for п in пачки] == \
            [("api", 2, 0), ("api", 0, 0), ("api", 1, 1)], [dict(п) for п in пачки]
        assert all(п["finished_at"] is not None for п in пачки)
    finally:
        await c.close()
    print("поток СМВУ ok: 12 партиций 07.2026–06.2027; токен 503/401; без пояса и 5001 — 422; "
          "эмулятор: 2 из 2 через 364 дня в партиции месяца, default пуст; повтор — 2 дубля; "
          "чужой канал не взят; пачки в load.batch")


def main():
    if "DATABASE_URL" not in os.environ:
        sys.exit("нужен DATABASE_URL пустой базы — рецепт в code/check_weather.py")
    app = FastAPI()
    app.include_router(router)
    сервер = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8097, log_level="warning"))
    threading.Thread(target=сервер.run, daemon=True).start()
    for _ in range(100):
        if сервер.started:
            break
        time.sleep(0.05)
    else:
        sys.exit("приём не поднялся на 127.0.0.1:8097 за 5 с")
    asyncio.run(проверить())


if __name__ == "__main__":
    main()
