"""Планировщик расчёта. Задача Q3.4 (MOS-34), docs/HLD.md разд. 3.3.

Блокировка `pg_try_advisory_lock(48217)` живёт в `app.worker.run.прогон()` —
это решение задачи Q3.2 (docs/plan.md, строка 3.2): защищает сам расчёт, кто
бы его ни позвал. Здесь её не повторяем, полный расчёт просто зовёт эту
функцию по расписанию; если блокировку в это время держит другой процесс,
`прогон()` вернёт `status="занято"` сам, без ожидания.

**Второе задание — не из HLD разд. 3.3, а из приёмки.** `docs/acceptance-test.md`,
строка НФ-73: почасовой расчёт не укладывает показание в 300 секунд до расчёта,
промах в 12 раз. Дешёвый выход, названный там же, — гонять только свёртку
(`feat.refresh_section_daily`, стадия 2 прогона) каждые `SCHEDULER_REFRESH_INTERVAL_MIN`
минут, полный расчёт оставить часовым. Свёртка — это `UPSERT` по `(section_id, day)`
(db/migrations/004_events.sql), конкурентный вызов с полным прогоном безопасен.
Функция не выделена в `run.py` отдельным вызовом (файл — зона Q3.2/moskollektor-44),
поэтому здесь та же SQL-функция вызвана напрямую, а не продублирована на Python.

HLD разд. 3.3 называет ещё три задания (полный расчёт по кнопке, ночной сервис,
пересчёт метрик, проверка истёкших горизонтов) — это код других задач, которого
пока нет.

Запуск:
    python -m app.worker.scheduler                # процесс службы worker
    python -m app.worker.scheduler --selfcheck     # блокировка без модели, нужна база
"""

import argparse
import asyncio
import os
from datetime import datetime

import asyncpg
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.worker import run

INTERVAL_MIN = int(os.environ.get("SCHEDULER_INTERVAL_MIN", "60"))
REFRESH_INTERVAL_MIN = int(os.environ.get("SCHEDULER_REFRESH_INTERVAL_MIN", "5"))

# Свой ключ для самопроверки, не боевой run.БЛОКИРОВКА (48217): нашла проверяющая
# сессия 17.09.2026 — selfcheck брал боевой ключ и падал, если в этот момент шёл
# настоящий часовой прогон и держал его законно. Механизм advisory lock проверяется
# ничуть не хуже на любом числе, а от боевого расчёта теперь не зависит.
БЛОКИРОВКА_SELFCHECK = 48218


async def тик_расчёт():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"], command_timeout=900)
    try:
        строка = await run.прогон(conn)
        if строка.get("status") == "занято":
            print("пропущено: блокировка занята")
    finally:
        await conn.close()


async def тик_свёртка():
    """НФ-73: только стадия 2 (свёртка), без блокировки — с полным прогоном не конфликтует."""
    conn = await asyncpg.connect(os.environ["DATABASE_URL"], command_timeout=60)
    try:
        as_of = datetime.now().astimezone()
        свёрнуто = await conn.fetchval(
            "SELECT feat.refresh_section_daily($1::date - 1, $2::date)", as_of, as_of
        )
        print(f"свёртка: обновила {свёрнуто} строк")
    finally:
        await conn.close()


async def _selfcheck():
    """Без модели и без боевого ключа: конкуренция, снятие и снятие после сбоя."""
    dsn = os.environ["DATABASE_URL"]
    a, b = await asyncpg.connect(dsn), await asyncpg.connect(dsn)
    try:
        assert (
            await a.fetchval("SELECT pg_try_advisory_lock($1)", БЛОКИРОВКА_SELFCHECK)
            is True
        )
        assert (
            await b.fetchval("SELECT pg_try_advisory_lock($1)", БЛОКИРОВКА_SELFCHECK)
            is False
        ), "вторая сессия не должна получить занятую блокировку"
        print("пропущено: блокировка занята")
        assert (
            await a.fetchval("SELECT pg_advisory_unlock($1)", БЛОКИРОВКА_SELFCHECK)
            is True
        )
        assert (
            await b.fetchval("SELECT pg_try_advisory_lock($1)", БЛОКИРОВКА_SELFCHECK)
            is True
        ), "после unlock первой сессии вторая должна пройти"
        await b.fetchval("SELECT pg_advisory_unlock($1)", БЛОКИРОВКА_SELFCHECK)
    finally:
        await a.close()
        await b.close()
    print("selfcheck 1/2 ok: блокировка занята и снята явно в двух сессиях")

    # Сбой задания, а не штатное завершение: соединение рвётся БЕЗ pg_advisory_unlock.
    # Advisory lock снимается сам, когда умирает соединение (docs/HLD.md разд. 3.3) —
    # это и есть причина, по которой выбрана она, а не флаг в таблице.
    c, d = await asyncpg.connect(dsn), await asyncpg.connect(dsn)
    упало = False
    try:
        assert (
            await c.fetchval("SELECT pg_try_advisory_lock($1)", БЛОКИРОВКА_SELFCHECK)
            is True
        )
        c.terminate()
        упало = True
        for _ in range(50):
            if await d.fetchval(
                "SELECT pg_try_advisory_lock($1)", БЛОКИРОВКА_SELFCHECK
            ):
                break
            await asyncio.sleep(0.1)
        else:
            raise AssertionError("блокировка не снялась за 5 с после обрыва соединения")
        await d.fetchval("SELECT pg_advisory_unlock($1)", БЛОКИРОВКА_SELFCHECK)
    finally:
        if not упало:
            c.terminate()
        await d.close()
    print(
        "selfcheck 2/2 ok: блокировка снялась сама после обрыва соединения, без unlock"
    )


async def _serve():
    scheduler = AsyncIOScheduler(timezone=os.environ.get("TZ", "Europe/Moscow"))
    scheduler.add_job(тик_расчёт, IntervalTrigger(minutes=INTERVAL_MIN))
    scheduler.add_job(тик_свёртка, IntervalTrigger(minutes=REFRESH_INTERVAL_MIN))
    scheduler.start()
    print(
        f"планировщик запущен: расчёт каждые {INTERVAL_MIN} мин, "
        f"свёртка каждые {REFRESH_INTERVAL_MIN} мин"
    )
    await asyncio.Event().wait()


def main():
    р = argparse.ArgumentParser(description="Планировщик прогона расчёта")
    р.add_argument(
        "--selfcheck", action="store_true", help="проверка блокировки без модели"
    )
    а = р.parse_args()
    asyncio.run(_selfcheck() if а.selfcheck else _serve())


if __name__ == "__main__":
    main()
