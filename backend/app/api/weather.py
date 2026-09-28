"""Погода: эмулятор метеослужбы и состояние её забора. Задача MOS-36 (Q3.6), приёмка Ф-85.

ЭМУЛЯТОР. ТЗ разд. 13 требует брать метеоданные «через открытые API», а заказчик
19.09.2026 закрыл ОВ-49: «Все внешние взаимодействия необходимо реализовать через
шаблоны и эмуляцию событий», с условием — данные «адекватные и правдоподобные,
а не просто случайные числа». Поэтому `/emu/open-meteo/v1/archive` отвечает тем же
JSON, что настоящий `archive-api.open-meteo.com/v1/archive`, из настоящей погоды:
`weather_moscow.csv.gz` — это `dataset/weather.csv`, скачанный `code/load_weather.py`
15.09.2026 (реанализ ERA5, центр Москвы, 01.01.2019 — 30.06.2026, 65 712 часов).
Данные Open-Meteo идут по лицензии CC BY 4.0 — ссылку на источник даём здесь
и в docs/HLD.md разд. 11.3.

Маршрут объявлен без префикса /api, поэтому nginx наружу его не отдаёт
(deploy/nginx/nginx.conf проксирует только /api/). Worker ходит на api:8000 напрямую.
Поставить WEATHER_URL на настоящий Open-Meteo — и тот же клиент
(app.ingest.weather) заберёт живую погоду, код не меняется.

СОСТОЯНИЕ. `GET /api/weather` отдаёт последний забранный час и время последнего
успешного забора — то, что Ф-85 велит показать.

ПОГОДА СЕЙЧАС. `GET /api/weather/now` — для полосы дашборда, решение Славы 28.09.2026:
диспетчеру нужна погода за окном, а не час среза расчёта (июнь 2026). Ходит в живой
Open-Meteo, не в эмулятор, и в расчёт не идёт. Нет выхода в интернет — 503, экран
пишет «погода недоступна» и работает дальше.
"""

import asyncio
import csv
import gzip
import json
import time
import urllib.request
from datetime import date
from functools import lru_cache
from pathlib import Path

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.schemas import WeatherNow, WeatherStatus
from app.auth.deps import require
from app.db import get_conn

emu_router = APIRouter(prefix="/emu/open-meteo/v1")
router = APIRouter(prefix="/api")

АРХИВ = Path(__file__).with_name("weather_moscow.csv.gz")
СЕЙЧАС_URL = (
    "https://api.open-meteo.com/v1/forecast?latitude=55.75&longitude=37.62"
    "&current=temperature_2m,precipitation,weather_code,wind_speed_10m"
    "&wind_speed_unit=ms&timezone=Europe%2FMoscow"
)
# Коды погоды WMO, которые отдаёт Open-Meteo, — по первому коду каждой группы.
НЕБО = [(0, "ясно"), (1, "переменная облачность"), (3, "пасмурно"), (45, "туман"),
        (51, "морось"), (61, "дождь"), (71, "снег"), (80, "ливень"), (85, "снегопад"),
        (95, "гроза")]
_сейчас: dict = {"до": 0.0, "ответ": None}


def небо(код: int) -> str:
    return [слово for порог, слово in НЕБО if код >= порог][-1]


def разобрать_сейчас(сырой: dict) -> dict:
    с = сырой["current"]
    return {"observed_at": с["time"], "temp_c": с["temperature_2m"],
            "precip_mm": с["precipitation"], "wind_ms": с["wind_speed_10m"],
            "sky": небо(с["weather_code"]), "source": "Open-Meteo"}


@lru_cache(maxsize=1)
def _архив() -> list[dict]:
    # 65 712 строк, около 10 МБ в памяти процесса api — читаем один раз.
    with gzip.open(АРХИВ, "rt", encoding="utf-8") as ф:
        return list(csv.DictReader(ф))


def ответ_архива(начало: date, конец: date, величины: list[str]) -> dict:
    """Часы с 00:00 `начало` по 23:00 `конец` в поясе Москвы, как у Open-Meteo."""
    строки = _архив()
    лишние = set(величины) - set(строки[0]) - {"time"}
    if лишние:
        raise ValueError(f"нет таких величин в архиве: {', '.join(sorted(лишние))}")
    с, по = начало.isoformat(), конец.isoformat() + "T23:00"
    часы = [с_ for с_ in строки if с <= с_["time"] <= по]
    hourly = {"time": [ч["time"] for ч in часы]}
    for в in величины:
        hourly[в] = [float(ч[в]) for ч in часы]
    return {"timezone": "Europe/Moscow", "hourly": hourly}


@emu_router.get("/archive")
async def emu_archive(
    start_date: date,
    end_date: date,
    hourly: str = Query(...),
    latitude: float | None = None,
    longitude: float | None = None,
    timezone: str = "Europe/Moscow",
):
    # Точка в архиве одна — центр Москвы; координаты принимаем, чтобы клиент
    # слал тот же запрос, что настоящему Open-Meteo, но на ответ они не влияют.
    if timezone != "Europe/Moscow":
        raise HTTPException(400, "эмулятор отдаёт только пояс Europe/Moscow")
    try:
        return ответ_архива(start_date, end_date, hourly.split(","))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/weather", response_model=WeatherStatus)
async def weather_status(
    conn: asyncpg.Connection = Depends(get_conn),
    _user=Depends(require("risks.read")),
):
    """Последний час погоды и время последнего успешного забора (Ф-85).

    `stale = true`, когда успешного забора не было больше часа: задача
    планировщика ходит раз в час, пропуск одного раза — уже отставание.
    Таблица пуста — все поля null и `stale = true`: погоды у расчёта нет.
    """
    row = await conn.fetchrow(
        """
        SELECT w.observed_at, w.temp_c, w.humidity_pct, w.precip_mm,
               w.pressure_hpa, w.source, f.fetched_at,
               coalesce(f.fetched_at < now() - interval '1 hour', true) AS stale
          FROM (SELECT max(fetched_at) AS fetched_at FROM ext.weather_hourly) f
          LEFT JOIN LATERAL (
            SELECT * FROM ext.weather_hourly
             WHERE NOT is_forecast ORDER BY observed_at DESC LIMIT 1
          ) w ON true
        """
    )
    return dict(row)


@router.get("/weather/now", response_model=WeatherNow)
async def weather_now(_user=Depends(require("risks.read"))):
    """Погода в центре Москвы сейчас, из живого Open-Meteo. В расчёт не идёт."""
    # ponytail: кэш в памяти процесса на 10 минут — Open-Meteo обновляет current
    # раз в 15 минут, а дашборд у 20 пользователей спрашивает раз в минуту.
    if time.monotonic() < _сейчас["до"]:
        return _сейчас["ответ"]
    try:
        сырой = await asyncio.to_thread(
            lambda: json.load(urllib.request.urlopen(СЕЙЧАС_URL, timeout=5)))
        ответ = разобрать_сейчас(сырой)
    except Exception as e:  # сеть, HTTP, чужой формат — для экрана это одно «недоступна»
        raise HTTPException(503, f"погода недоступна: {e}")
    _сейчас.update(до=time.monotonic() + 600, ответ=ответ)
    return ответ


if __name__ == "__main__":
    # Самопроверка без базы: сутки 06.04.2024 — те же, что в code/load_weather.py.
    о = ответ_архива(date(2024, 4, 6), date(2024, 4, 6), ["temperature_2m", "precipitation"])
    ч = о["hourly"]
    assert len(ч["time"]) == 24, len(ч["time"])
    assert ч["time"][0] == "2024-04-06T00:00" and ч["time"][-1] == "2024-04-06T23:00"
    assert set(ч) == {"time", "temperature_2m", "precipitation"}
    assert all(isinstance(x, float) for x in ч["temperature_2m"])
    # Весь период — 65 712 часов, как записано в MOS-36.
    assert len(ответ_архива(date(2019, 1, 1), date(2026, 6, 30), ["surface_pressure"])["hourly"]["time"]) == 65712
    # За краем архива — пустой ряд, а не ошибка: так же ведёт себя клиент.
    assert ответ_архива(date(2026, 7, 1), date(2026, 7, 2), ["precipitation"])["hourly"]["time"] == []
    try:
        ответ_архива(date(2024, 4, 6), date(2024, 4, 6), ["snowfall"])
    except ValueError:
        pass
    else:
        raise AssertionError("неизвестная величина прошла молча")
    с = разобрать_сейчас({"current": {"time": "2026-09-28T10:15", "temperature_2m": 11.2,
                                       "precipitation": 0.4, "weather_code": 63,
                                       "wind_speed_10m": 4.1}})
    assert с["sky"] == "дождь" and с["temp_c"] == 11.2, с
    assert небо(0) == "ясно" and небо(2) == "переменная облачность" and небо(99) == "гроза"
    print("selfcheck эмулятора погоды ok: 24 часа за 06.04.2024, 65 712 за период, "
          "за краем пусто, неизвестная величина — отказ")
