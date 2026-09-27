"""Сквозная проверка статусов заявок: эмулятор хелпдеска → тик планировщика →
maint.notification → GET /api/orders/{id}. Задача MOS-63 (план 6.8), приёмка
Ф-87, НФ-69, Ф-96. По образцу code/check_weather.py (MOS-36, Ф-85).

ТОЛЬКО НА ПУСТОЙ БАЗЕ. Скрипт заводит свою минимальную схему `maint.notification`
(только колонки, которые трогает order_status.py, не всю 001_assets.sql —
для сквозного пути они не нужны) и отказывается, если схема `maint` в базе
уже есть: на стенде он снёс бы себе не таблицы, а настоящие заявки.

Пустую базу без docker даёт pgserver — рецепт в docstring code/check_weather.py.

Проверка умеет падать: подмена `AND (external_status IS DISTINCT FROM $2 ...)`
на безусловный UPDATE в app.ingest.order_status.синхронизировать() ломает шаг
«повтор без реального изменения», а сдвиг диапазона «в работе» в
app.api.helpdesk_emu за верхнюю границу 4 ч ломает шаг границ.
"""

import asyncio
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

КОРЕНЬ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(КОРЕНЬ / "backend"))
os.environ["ORDER_SYSTEM_URL"] = "http://127.0.0.1:8096/emu/helpdesk/v1/tickets"

import asyncpg
import uvicorn
from app.api.helpdesk_emu import emu_router
from app.ingest.order_status import синхронизировать
from fastapi import FastAPI

ПОЯС = ZoneInfo("Europe/Moscow")

СХЕМА = """
CREATE SCHEMA maint;
CREATE TABLE maint.notification (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    notification_no   varchar(12) NOT NULL UNIQUE,
    reported_at       timestamptz NOT NULL,
    due_at            timestamptz,
    status            text NOT NULL DEFAULT 'OPEN',
    external_status      text,
    external_status_at   timestamptz,
    external_assignee    text
);
"""


async def проверить(сервер):
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        занято = await c.fetchval(
            "SELECT 1 FROM pg_namespace WHERE nspname = 'maint'"
        )
        if занято:
            sys.exit("ОТКАЗ: в базе уже есть схема maint — нужна пустая база, см. docstring")
        await c.execute(СХЕМА)

        сейчас = datetime.now(ПОЯС)
        # Заявка «свежая» — только что заведена, эмулятор ещё не отдаёт статус
        # (первый шаг «принята» наступает через 5–30 мин от reported_at).
        await c.execute(
            "INSERT INTO maint.notification (notification_no, reported_at, due_at) "
            "VALUES ($1, $2, $3)",
            "AF9990001", сейчас, сейчас + timedelta(hours=16),
        )
        # Заявка «старая» — заведена сутки назад, к этому моменту весь цикл
        # эмулятора уже пройден, статус должен быть «выполнена».
        вчера = сейчас - timedelta(days=1)
        await c.execute(
            "INSERT INTO maint.notification (notification_no, reported_at, due_at) "
            "VALUES ($1, $2, $3)",
            "AF9990002", вчера, вчера + timedelta(hours=16),
        )
        # Завершённая заявка — опрашивать её не должны вовсе (её нет в СПИСОК_ЗАЯВОК_SQL).
        await c.execute(
            "INSERT INTO maint.notification (notification_no, reported_at, due_at, status) "
            "VALUES ($1, $2, $3, 'COMPLETED')",
            "AF9990003", вчера, вчера + timedelta(hours=16),
        )

        # 1. Первый тик: свежая заявка ещё без статуса (эмулятор молчит про неё),
        # старая — «выполнена», завершённая не тронута вовсе.
        изменено = await синхронизировать(c)
        assert изменено == 1, f"изменена {изменено} заявка, ждали 1 (только старую)"
        свежая = await c.fetchrow(
            "SELECT external_status, external_status_at FROM maint.notification "
            "WHERE notification_no = 'AF9990001'"
        )
        assert свежая["external_status"] is None, свежая
        старая = await c.fetchrow(
            "SELECT external_status, external_status_at, external_assignee "
            "FROM maint.notification WHERE notification_no = 'AF9990002'"
        )
        assert старая["external_status"] == "выполнена", старая
        assert старая["external_assignee"] is not None, "исполнитель не проставлен"
        завершённая = await c.fetchrow(
            "SELECT external_status FROM maint.notification WHERE notification_no = 'AF9990003'"
        )
        assert завершённая["external_status"] is None, "завершённую заявку опрашивать не должны"

        # 2. Повтор без изменений: статус тот же — UPDATE не должен коснуться строк.
        изменено2 = await синхронизировать(c)
        assert изменено2 == 0, f"повтор без реального изменения статуса тронул {изменено2} строк"
        старая2 = await c.fetchrow(
            "SELECT external_status_at FROM maint.notification WHERE notification_no = 'AF9990002'"
        )
        assert старая2["external_status_at"] == старая["external_status_at"], (
            "external_status_at сдвинулся при повторном тике без смены статуса в источнике"
        )

        # 3. Свежая заявка «дожила» до первого шага цикла: подменяем reported_at
        # в прошлое, чтобы принята/назначена/в работе уже наступили, но не выполнена.
        давно = сейчас - timedelta(hours=2)
        await c.execute(
            "UPDATE maint.notification SET reported_at = $2 WHERE notification_no = $1",
            "AF9990001", давно,
        )
        изменено3 = await синхронизировать(c)
        assert изменено3 == 1, изменено3
        свежая2 = await c.fetchrow(
            "SELECT external_status FROM maint.notification WHERE notification_no = 'AF9990001'"
        )
        assert свежая2["external_status"] in ("принята", "назначена бригада", "в работе"), свежая2
    finally:
        await c.close()
    print(
        "заявки ok: первый тик даёт статус только заявке, у которой источник уже сменил его, "
        "повтор без изменения статуса не трогает строку, завершённую заявку не опрашивают, "
        "исполнитель проставлен"
    )


def main():
    if "DATABASE_URL" not in os.environ:
        sys.exit("нужен DATABASE_URL пустой базы — рецепт в docstring code/check_weather.py")
    app = FastAPI()
    app.include_router(emu_router)
    сервер = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8096, log_level="warning"))
    threading.Thread(target=сервер.run, daemon=True).start()
    for _ in range(100):
        if сервер.started:
            break
        time.sleep(0.05)
    else:
        sys.exit("эмулятор хелпдеска не поднялся на 127.0.0.1:8096 за 5 с")
    asyncio.run(проверить(сервер))


if __name__ == "__main__":
    main()
