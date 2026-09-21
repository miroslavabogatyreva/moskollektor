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
from datetime import datetime, timedelta

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


# Срез расчёта — край данных, а не текущий момент.
#
# ЗАЧЕМ. Выгрузка заказчика кончается 30.06.2026, а планировщик считал на
# `datetime.now()`. Расчёт при этом отрабатывает честно и быстро — 5,6 с вместо
# 32,7 — но читает пустое окно: у прогона 147 факторы пустые у всех 3 173
# прогнозов, карточка объекта пишет «Датчики этого участка молчат 83 суток
# подряд», а дашборд помечает «свежий» у всех участков сразу. Продукт работает
# правильно и показывает пустоту. Нашла это проверяющая сессия, пройдя экраны
# браузером; решение считать по краю данных приняла Слава (MOS-142).
#
# ПОЧЕМУ ГРАНИЦУ БЕРЁМ У СВЁРТКИ. `max(read_time)` по журналу в 313 млн строк
# идёт 60–94 с в зависимости от нагрузки стенда (замеры 21.09.2026), а
# `max(day)` по `feat.channel_daily` — 19–47 мс, потому что свёртка меньше
# в тысячу раз и по `day` есть индекс. Обогнать журнал свёртка не может
# по устройству: `022_channel_daily.sql` берёт каждый день из `read_time::date`
# настоящего показания, без календаря. Ошибка такой мерки делает срез раньше,
# а не позже, то есть расчёт прочитает меньше данных, но не прочитает пустоту.
#
# КОНСТАНТЫ ЗДЕСЬ НЕТ НАРОЧНО: записанная дата устареет в тот день, когда
# заказчик пришлёт хоть одну строку.
# Приведение к timestamptz делает БАЗА, а не Python: так момент считается
# в поясе сеанса (`TimeZone = Europe/Moscow` на стенде) и приезжает в asyncpg
# уже с поясом. Без этого приведения вернулся бы наивный timestamp, и его пояс
# зависел бы от настроек процесса worker, а не базы — при разъезде настроек
# срез уехал бы на три часа молча.
КРАЙ_ДАННЫХ = """
SELECT ((max(day) + 1)::timestamp - interval '1 second')::timestamptz
  FROM feat.channel_daily
"""


async def тик_расчёт():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"], command_timeout=900)
    try:
        край = await conn.fetchval(КРАЙ_ДАННЫХ)
        if край is None:
            # Свёртки нет вовсе — считаем по текущему моменту и говорим об этом
            # вслух. Молчаливый откат к now() вернул бы ровно ту беду, ради
            # которой срез и переехал на край данных.
            print("свёртка feat.channel_daily пуста, срез беру по текущему моменту")
            as_of = None
        else:
            as_of = край
            print(f"срез по краю данных: {as_of:%d.%m.%Y %H:%M %z}")
        строка = await run.прогон(conn, as_of)
        if строка.get("status") == "занято":
            print("пропущено: блокировка занята")
    finally:
        await conn.close()


async def тик_свёртка():
    """НФ-73: только стадия 2 (свёртка), без блокировки — с полным прогоном не конфликтует.

    **Каждый запуск оставляет строку в `feat.refresh_run`** (миграция 023). До
    21.09.2026 свёртка писала только `print` в журнал контейнера, который живёт
    до пересоздания: пропуск свёртки не видела ни одна проверка, а её задержку
    нельзя было померить запросом. Строку заводим ДО вызова функции и закрываем
    после — иначе свёртка, упавшая на середине, не оставила бы следа вовсе,
    а это ровно тот случай, ради которого журнал и заведён.
    """
    conn = await asyncpg.connect(os.environ["DATABASE_URL"], command_timeout=60)
    try:
        refresh_id = await conn.fetchval(
            "INSERT INTO feat.refresh_run (status) VALUES ('running') RETURNING refresh_id"
        )
        try:
            as_of = datetime.now().astimezone()
            свёрнуто = await conn.fetchval(
                "SELECT feat.refresh_section_daily($1::date - 1, $2::date)", as_of, as_of
            )
        except Exception as e:
            await conn.execute(
                "UPDATE feat.refresh_run SET status = 'failed', finished_at = now(), "
                "error_text = $2 WHERE refresh_id = $1",
                refresh_id, f"{type(e).__name__}: {e}"[:1000],
            )
            print(f"свёртка упала: {type(e).__name__}: {e}")
            raise
        await conn.execute(
            "UPDATE feat.refresh_run SET status = 'done', finished_at = now(), "
            "rows_updated = $2 WHERE refresh_id = $1",
            refresh_id, свёрнуто,
        )
        print(f"свёртка {refresh_id}: обновила {свёрнуто} строк")
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


def следующий_слот(минут, сейчас=None):
    """Ближайший будущий момент, кратный интервалу от полуночи.

    Зачем. `IntervalTrigger(minutes=60)` без `start_date` отсчитывает первый запуск
    от старта планировщика, а не от круглого часа: контейнер, поднятый в 09:37,
    считает в 10:37, 11:37 и так далее. Расписание оказывается отпечатком момента
    выкладки, и после каждого пересоздания контейнера минута уезжает. 21.09.2026
    так и вышло: `moskollektor-worker-1` пересоздали, слот «в 37 минут» пропал,
    а в протоколе у нас записано «планировщик считает раз в час, в 37 минут» —
    фраза, которая перестаёт быть верной при первой же выкладке.

    Что даёт выравнивание. Расчёт идёт в 00 минут каждого часа, свёртка при
    интервале 5 минут — в 00, 05, 10 и так далее, независимо от того, когда
    подняли службу. Диспетчер знает, к какой минуте обновятся данные, а проверка
    разрывов в `code/check_runtime.py` может сравнивать промежуток с нормой,
    не гадая, от какого старта его отсчитывать.

    Оговорка про сутки. Если интервал не делит 1440 минут нацело (например, 7),
    последний слот суток окажется короче остальных: после 23:59 отсчёт начинается
    заново от полуночи. Для наших 60 и 5 это не наступает, а для кривого интервала
    лучше короткий слот раз в сутки, чем расписание, зависящее от момента старта.
    """
    сейчас = сейчас or datetime.now().astimezone()
    полночь = сейчас.replace(hour=0, minute=0, second=0, microsecond=0)
    прошло_мин = (сейчас - полночь).total_seconds() / 60
    слот = (int(прошло_мин // минут) + 1) * минут
    return полночь + timedelta(minutes=слот)


async def _serve():
    scheduler = AsyncIOScheduler(timezone=os.environ.get("TZ", "Europe/Moscow"))
    расчёт_с = следующий_слот(INTERVAL_MIN)
    свёртка_с = следующий_слот(REFRESH_INTERVAL_MIN)
    scheduler.add_job(
        тик_расчёт, IntervalTrigger(minutes=INTERVAL_MIN, start_date=расчёт_с)
    )
    scheduler.add_job(
        тик_свёртка, IntervalTrigger(minutes=REFRESH_INTERVAL_MIN, start_date=свёртка_с)
    )
    scheduler.start()
    print(
        f"планировщик запущен: расчёт каждые {INTERVAL_MIN} мин "
        f"(первый в {расчёт_с:%H:%M}), свёртка каждые {REFRESH_INTERVAL_MIN} мин "
        f"(первая в {свёртка_с:%H:%M}); слоты выровнены по полуночи, "
        f"а не по моменту запуска службы"
    )
    await asyncio.Event().wait()


def _selfcheck_слоты():
    """Выравнивание слотов. Базы не требует, поэтому идёт в каждом прогоне проверок."""
    д = datetime.fromisoformat
    # Час: контейнер подняли в 09:37 — первый расчёт всё равно в 10:00, а не в 10:37.
    assert следующий_слот(60, д("2026-09-21 09:37:35")) == д("2026-09-21 10:00:00")
    # Ровно на границе слот следующий, а не текущий: иначе задание встало бы
    # в прошлое и APScheduler запустил бы его немедленно.
    assert следующий_слот(60, д("2026-09-21 10:00:00")) == д("2026-09-21 11:00:00")
    # Пять минут: 09:37 -> 09:40, а не 09:42.
    assert следующий_слот(5, д("2026-09-21 09:37:35")) == д("2026-09-21 09:40:00")
    # Последний слот суток: в 23:59 следующий — полночь, отсчёт начинается заново.
    assert следующий_слот(60, д("2026-09-21 23:59:00")) == д("2026-09-22 00:00:00")
    # Интервал, не делящий сутки: слоты всё равно от полуночи, а не от старта.
    # 09:37 — это 577-я минута суток, 577 // 7 = 82, значит следующий слот 83·7 = 581,
    # то есть 09:41. Число тут неочевидное нарочно: с круглым интервалом такая
    # проверка прошла бы и при неверной формуле.
    assert следующий_слот(7, д("2026-09-21 09:37:00")) == д("2026-09-21 09:41:00")
    print("selfcheck слотов ok: 09:37 + час -> 10:00, граница, пять минут, полночь")


def main():
    р = argparse.ArgumentParser(description="Планировщик прогона расчёта")
    р.add_argument(
        "--selfcheck", action="store_true", help="проверка блокировки без модели"
    )
    а = р.parse_args()
    _selfcheck_слоты()
    asyncio.run(_selfcheck() if а.selfcheck else _serve())


if __name__ == "__main__":
    main()
