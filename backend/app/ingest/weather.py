"""Забор почасовой погоды в ext.weather_hourly. Задача MOS-36 (Q3.6), приёмка Ф-85.

Зовёт задача планировщика раз в час (app.worker.scheduler). Ходит по WEATHER_URL
запросом формата Open-Meteo archive: по умолчанию это наш эмулятор на api
(app.api.weather), но подставь `https://archive-api.open-meteo.com/v1/archive` —
и заберёт живую погоду тем же кодом.

КАКИЕ ЧАСЫ. Продукт живёт на срезе, а не на сегодняшнем дне: выгрузка кончается
30.06.2026, и «сейчас» для расчёта — край данных или момент проигрывания. Поэтому
забираем сутки до среза того же прогона, а не до now(): погода на экране должна
стоять рядом с тем же часом, что прогноз. Часы позже среза отбрасываем — это
будущее относительно расчёта.

ОТКАЗ ИСТОЧНИКА. Любая ошибка — сеть, HTTP, пустой ответ — поднимается наверх,
fetched_at при этом не двигается, и `GET /api/weather` через час покажет
stale = true. Расчёт от погоды не зависит (признаки погоды в модель не идут,
docs/acceptance-test.md, разбор направления прогноза), поэтому он идёт дальше.
"""

import asyncio
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

МОСКВА = ZoneInfo("Europe/Moscow")
URL = os.environ.get("WEATHER_URL", "http://api:8000/emu/open-meteo/v1/archive")
ТОЧКА = (55.75, 37.62)   # центр Москвы, та же, что в code/load_weather.py
# Величина Open-Meteo → колонка ext.weather_hourly.
КОЛОНКИ = {
    "temperature_2m": "temp_c",
    "relative_humidity_2m": "humidity_pct",
    "precipitation": "precip_mm",
    "surface_pressure": "pressure_hpa",
}


def адрес(url: str, срез: datetime) -> str:
    конец = срез.astimezone(МОСКВА).date()
    return url + "?" + urllib.parse.urlencode({
        "latitude": ТОЧКА[0], "longitude": ТОЧКА[1],
        "start_date": конец - timedelta(days=1), "end_date": конец,
        "hourly": ",".join(КОЛОНКИ), "timezone": "Europe/Moscow",
    })


def имя_источника(url: str) -> str:
    return "эмулятор Open-Meteo" if "/emu/" in url else urllib.parse.urlsplit(url).hostname


def разобрать(hourly: dict, срез: datetime) -> list[tuple]:
    """Строки (observed_at, temp, humidity, precip, pressure) не позже среза.

    null остаётся None: у настоящего Open-Meteo последние часы архива бывают
    ещё не посчитаны, и ноль вместо них соврал бы про осадки.
    """
    строки = []
    for i, t in enumerate(hourly["time"]):
        час = datetime.fromisoformat(t).replace(tzinfo=МОСКВА)
        if час <= срез:
            строки.append((час, *(hourly[в][i] for в in КОЛОНКИ)))
    return строки


def _получить(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as ответ:
        return json.load(ответ)["hourly"]


async def забрать_погоду(conn, срез: datetime, url: str = URL) -> int:
    hourly = await asyncio.to_thread(_получить, адрес(url, срез))
    строки = разобрать(hourly, срез)
    if not строки:
        raise RuntimeError(f"метеоисточник не отдал ни одного часа до среза {срез:%d.%m.%Y %H:%M}")
    источник = имя_источника(url)
    await conn.executemany(
        """
        INSERT INTO ext.weather_hourly
               (observed_at, is_forecast, temp_c, humidity_pct, precip_mm, pressure_hpa, source)
        VALUES ($1, false, $2, $3, $4, $5, $6)
        ON CONFLICT (observed_at, is_forecast, source) DO UPDATE
           SET temp_c = EXCLUDED.temp_c, humidity_pct = EXCLUDED.humidity_pct,
               precip_mm = EXCLUDED.precip_mm, pressure_hpa = EXCLUDED.pressure_hpa,
               fetched_at = now()
        """,
        [(*с, источник) for с in строки],
    )
    return len(строки)


if __name__ == "__main__":
    срез = datetime(2026, 6, 2, 0, 30, tzinfo=МОСКВА)
    а = urllib.parse.parse_qs(urllib.parse.urlsplit(адрес(URL, срез)).query)
    assert а["start_date"] == ["2026-06-01"] and а["end_date"] == ["2026-06-02"], а
    # Срез в UTC приходит из базы — дата берётся московская: 01.06 21:30 UTC = 02.06 00:30 МСК.
    а = urllib.parse.parse_qs(urllib.parse.urlsplit(адрес(URL, срез.astimezone(ZoneInfo("UTC")))).query)
    assert а["end_date"] == ["2026-06-02"], а
    часы = {"time": ["2026-06-01T23:00", "2026-06-02T00:00", "2026-06-02T01:00"],
            "temperature_2m": [15.0, 14.5, 14.0], "relative_humidity_2m": [80, 82, None],
            "precipitation": [0.0, 0.2, 0.0], "surface_pressure": [1000.1, 1000.0, 999.9]}
    с = разобрать(часы, срез)
    assert [x[0].hour for x in с] == [23, 0], "час позже среза не отброшен"
    assert с[1] == (datetime(2026, 6, 2, 0, tzinfo=МОСКВА), 14.5, 82, 0.2, 1000.0)
    assert разобрать(dict(часы, relative_humidity_2m=[None] * 3), срез)[0][2] is None
    assert имя_источника(URL) == "эмулятор Open-Meteo"
    assert имя_источника("https://archive-api.open-meteo.com/v1/archive") == "archive-api.open-meteo.com"
    print("selfcheck забора погоды ok: окно 01–02.06 по Москве, час после среза отброшен, "
          "null не стал нулём, имя источника")
