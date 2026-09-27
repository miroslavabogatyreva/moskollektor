"""Сквозная проверка погоды: эмулятор → задача планировщика → ext.weather_hourly → статус.

Задача MOS-36 (Q3.6), приёмка Ф-85. Проверяет весь путь по HTTP, а не функции
по отдельности: поднимает эмулятор Open-Meteo (app.api.weather) на 127.0.0.1:8098,
зовёт настоящий `тик_погода` из планировщика и читает то же, что отдаёт
`GET /api/weather`.

ТОЛЬКО НА ПУСТОЙ БАЗЕ. Скрипт накатывает 048_weather.sql и заводит свои pred.run
и pred.forecast_current. Если в базе уже есть схема pred или ext — отказывается
и ничего не пишет: на стенде он снёс бы себе не таблицы, а доверие к прогнозу.

Пустую базу без docker даёт pgserver (на Python 3.14 колёс нет, отсюда 3.12):

    uv venv -p 3.12 /tmp/pgv && uv pip install -p /tmp/pgv/bin/python pgserver
    /tmp/pgv/bin/python -c "import pgserver,subprocess,sys,os,tempfile; \
      s=pgserver.get_server(tempfile.mkdtemp()); \
      sys.exit(subprocess.run(['.venv/bin/python','code/check_weather.py'], \
      env={**os.environ,'DATABASE_URL':s.get_uri()}).returncode)"

Проверка умеет падать: 27.09.2026 подмена `fetched_at = now()` в upsert
(app/ingest/weather.py) на старое значение роняет шаг «повтор» AssertionError.
"""

import asyncio
import gzip
import os
import sys
import threading
import time
from pathlib import Path

КОРЕНЬ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(КОРЕНЬ / "backend"))
os.environ["WEATHER_URL"] = "http://127.0.0.1:8098/emu/open-meteo/v1/archive"

import asyncpg
import uvicorn
from app.api.weather import АРХИВ, emu_router, weather_status
from app.worker import scheduler
from fastapi import FastAPI

МИГРАЦИЯ = КОРЕНЬ / "db/migrations/048_weather.sql"


async def проверить(сервер):
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        занято = await c.fetchval(
            "SELECT string_agg(nspname, ', ') FROM pg_namespace WHERE nspname IN ('pred', 'ext')")
        if занято:
            sys.exit(f"ОТКАЗ: в базе уже есть схемы {занято} — нужна пустая база, см. docstring")
        await c.execute(МИГРАЦИЯ.read_text())
        await c.execute("""
            CREATE SCHEMA pred;
            CREATE TABLE pred.run (run_id int PRIMARY KEY, as_of timestamptz, started_at timestamptz);
            CREATE TABLE pred.forecast_current (run_id int);
            INSERT INTO pred.run VALUES (1, '2026-06-02 00:30+03', now());
            INSERT INTO pred.forecast_current VALUES (1);""")

        # 1. Забор: сутки 01.06 00:00 … 02.06 00:00 по Москве — 25 часов.
        await scheduler.тик_погода()
        n = await c.fetchval("SELECT count(*) FROM ext.weather_hourly")
        assert n == 25, f"часов {n}, ждали 25"

        # 2. Статус совпадает с архивом в час 02.06 00:00.
        ст = await weather_status(c, None)
        with gzip.open(АРХИВ, "rt", encoding="utf-8") as ф:
            архив = next(с for с in ф if с.startswith("2026-06-02T00:00")).strip().split(",")
        assert ст["stale"] is False and ст["source"] == "эмулятор Open-Meteo", ст
        assert [round(ст[к], 1) for к in ("temp_c", "humidity_pct", "precip_mm", "pressure_hpa")] \
            == [float(x) for x in архив[1:]], (ст, архив)

        # 3. Повтор: строк столько же, fetched_at сдвинулся — stale снова false.
        await c.execute("UPDATE ext.weather_hourly SET fetched_at = now() - interval '2 hours'")
        assert (await weather_status(c, None))["stale"] is True
        await scheduler.тик_погода()
        assert await c.fetchval("SELECT count(*) FROM ext.weather_hourly") == 25
        assert (await weather_status(c, None))["stale"] is False, "повтор не обновил fetched_at"

        # 4. Источник лёг: планировщик не падает, данные устаревают.
        await c.execute("UPDATE ext.weather_hourly SET fetched_at = now() - interval '2 hours'")
        сервер.should_exit = True
        await asyncio.sleep(0.5)
        await scheduler.тик_погода()
        assert (await weather_status(c, None))["stale"] is True

        # 5. Пустая таблица — stale, а не ошибка.
        await c.execute("TRUNCATE ext.weather_hourly")
        ст = await weather_status(c, None)
        assert ст["stale"] is True and ст["fetched_at"] is None, ст
    finally:
        await c.close()
    print(f"погода ok: 25 ч до среза 02.06 00:30, час 00:00 = архив {','.join(архив[1:])}; "
          "повтор без дублей; источник лёг — stale; пусто — stale")


def main():
    if "DATABASE_URL" not in os.environ:
        sys.exit("нужен DATABASE_URL пустой базы — рецепт в docstring")
    app = FastAPI()
    app.include_router(emu_router)
    сервер = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8098, log_level="warning"))
    threading.Thread(target=сервер.run, daemon=True).start()
    for _ in range(100):
        if сервер.started:
            break
        time.sleep(0.05)
    else:
        sys.exit("эмулятор не поднялся на 127.0.0.1:8098 за 5 с")
    asyncio.run(проверить(сервер))


if __name__ == "__main__":
    main()
