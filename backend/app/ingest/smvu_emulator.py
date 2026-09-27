"""Эмулятор СМВУ: проигрывает архив со сдвигом +364 дня потоком в наш приём. MOS-37 (Q3.7).

Зачем. Настоящей СМВУ у нас нет и не будет: заказчик 19.09.2026 — «Все внешние
взаимодействия необходимо реализовать через шаблоны и эмуляцию событий», с условием
«данные, которые выглядят адекватно и правдоподобно, а не просто случайные числа».
Поэтому эмулятор ничего не выдумывает: раз в минуту берёт из smvu.reading показания
ровно на 364 дня раньше текущей минуты и шлёт их в `POST /api/ingest/readings`
с временем, сдвинутым на 364 дня вперёд. 364, а не 365, — чтобы совпал день недели:
в будни и выходные в журнале разный фон. Пример: в 27.09.2026 14:05 уходят
показания из 28.09.2025 14:05. Архив кончается 30.06.2026, значит эмулятору хватит
до 29.06.2027.

journal_id берём как в архиве: ключ smvu.reading — пара (journal_id, read_time),
а read_time у копии другой, так что с исходной строкой она не столкнётся.

Отказ приёма. Минуту, которую не приняли, не теряем: окно двигается только после
успешной отправки, и следующая попытка шлёт всё с последней принятой минуты.
Эмулятор перезапустили — начинает с текущей минуты, пропущенное не догоняет:
так ведёт себя и настоящий источник, который простоял.

Запуск (служба emulator-smvu в deploy/docker-compose.yml):
    python -u -m app.ingest.smvu_emulator
"""

import asyncio
import json
import os
import urllib.request
from datetime import datetime, timedelta

import asyncpg

from app.ingest.readings import МАКС_ПАЧКА

СДВИГ = timedelta(days=364)
ШАГ_С = int(os.environ.get("EMULATOR_SMVU_STEP_S", "60"))
URL = os.environ.get("INGEST_URL", "http://api:8000/api/ingest/readings")

ВЫБОРКА = """
SELECT journal_id, read_time + $3::interval AS read_time, channel_id, is_alarm, value_text
  FROM smvu.reading
 WHERE read_time > $1::timestamptz - $3::interval AND read_time <= $2::timestamptz - $3::interval
 ORDER BY read_time, journal_id
"""


def пачки(строки: list, размер: int = МАКС_ПАЧКА):
    for i in range(0, len(строки), размер):
        yield строки[i:i + размер]


def тело(пачка) -> bytes:
    return json.dumps({"readings": [
        {"journal_id": r["journal_id"], "channel_id": r["channel_id"],
         "read_time": r["read_time"].isoformat(), "is_alarm": r["is_alarm"],
         "value": r["value_text"]} for r in пачка]}, ensure_ascii=False).encode()


def отправить(пачка) -> dict:
    запрос = urllib.request.Request(URL, data=тело(пачка), method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {os.environ['INGEST_TOKEN']}"})
    with urllib.request.urlopen(запрос, timeout=60) as ответ:
        return json.load(ответ)


async def шаг(conn, с: datetime, по: datetime) -> dict:
    """Отправить показания окна (с, по] после сдвига. Итог — сумма ответов приёма."""
    строки = await conn.fetch(ВЫБОРКА, с, по, СДВИГ)
    итог = {"sent": len(строки), "accepted": 0, "duplicates": 0, "unknown_channel": 0}
    for п in пачки(строки):
        ответ = await asyncio.to_thread(отправить, п)
        for к in ("accepted", "duplicates", "unknown_channel"):
            итог[к] += ответ[к]
    return итог


async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"], command_timeout=120)
    с = datetime.now().astimezone()
    print(f"эмулятор СМВУ: сдвиг {СДВИГ.days} дн, шаг {ШАГ_С} с, приём {URL}")
    while True:
        await asyncio.sleep(ШАГ_С)
        по = datetime.now().astimezone()
        try:
            итог = await шаг(conn, с, по)
        except Exception as e:  # noqa: BLE001 — приём лёг: окно не двигаем, повторим
            print(f"эмулятор СМВУ: отправка не прошла, повторю с {с:%H:%M:%S}: {e!r}")
            continue
        print(f"эмулятор СМВУ: {с:%H:%M:%S}–{по:%H:%M:%S} {итог}")
        с = по


if __name__ == "__main__":
    asyncio.run(main())
