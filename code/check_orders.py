#!/usr/bin/env python3
"""Приёмка 0.3 «Модуль автоматического формирования заявок»: строки М-09…М-13.

Скрипт спрашивает базу и печатает пять строк вида «М-NN OK …» или «М-NN СБОЙ …».
Доказательство — числа, а не слово «пройдено»: каждая строка называет и долю,
и знаменатель, от которого доля посчитана. «100 % заполнены» на нуле заявок —
это зелень на пустоте, поэтому ноль заявок здесь СБОЙ, а не молчаливый ноль.

Код возврата: 1, если хоть одна строка красная; 0 только когда все пять зелёные.

Проверка по умолчанию отказывает. Нет DATABASE_URL, не поднялась база, нет
таблицы, ноль строк — всё это СБОЙ. На 17.09.2026 блок Q6 (модуль заявок) ещё
не написан, автозаявок в базе нет, и скрипт обязан быть красным целиком.

Подключение то же, что у бэкенда (backend/app/db.py): asyncpg и переменная
DATABASE_URL. Своего способа не заводим, иначе стенд и приёмка смотрят в разные
базы и расходятся молча.

Запуск:  DATABASE_URL=postgresql://... python3 code/check_orders.py
Самопроверка без базы (М-13, сравнение сроков):  python3 code/check_orders.py --demo
"""

import asyncio
import os
import sys
from datetime import timedelta
from statistics import median

try:
    import asyncpg
except ImportError:  # пакет живёт в образе бэкенда, локально его может не быть
    asyncpg = None

ROWS = ("М-09", "М-10", "М-11", "М-12", "М-13")

# Что модулю заявок нужно в схеме. Проверяем колонки, а не таблицы: таблица
# maint.notification есть с 001_assets.sql, но без forecast_id и due_at модуль
# автозаявок на ней не собрать.
REQUIRED_COLUMNS = (
    "maint.notification.forecast_id",
    "maint.notification.due_at",
    "maint.notification.source_system",
    "maint.notification.long_text",
    "maint.work_order.notification_id",
    "maint.work_order.order_type_id",
    "maint.work_order.activity_type_id",
    "pred.forecast.horizon_h",
    "pred.forecast.run_id",
    "pred.run.as_of",
)


# ------------------------------------------------------------------ М-13: логика


def lead_hours(due_at, as_of, horizon_h):
    """Запас в часах между сроком работ и прогнозируемым отказом.

    Прогнозируемый момент отказа — это as_of прогона плюс horizon_h часов
    (pred.run.as_of + pred.forecast.horizon_h). Положительный запас = заявка
    превентивная: успеваем до отказа.

    Возвращает None, если сравнивать нечем: нет срока, нет прогноза или нет
    прогона. Это не «ноль часов запаса», а «утверждать нечего», и различать
    эти два случая обязательно — ноль ещё можно принять за границу.

    Сравнение живёт здесь, а не в SQL: одна методика в двух реализациях
    расходится молча, а самопроверке ниже нужна та же самая функция.
    """
    if due_at is None or as_of is None or horizon_h is None:
        return None
    return (as_of + timedelta(hours=horizon_h) - due_at).total_seconds() / 3600


def is_preventive(due_at, as_of, horizon_h):
    """Срок работ наступает СТРОГО раньше прогнозируемого отказа (М-13).

    Ровно в момент отказа — не превентивно: работы, начатые в час аварии,
    аварию не предотвращают.
    """
    lead = lead_hours(due_at, as_of, horizon_h)
    return lead is not None and lead > 0


# ------------------------------------------------------------------ проверки


async def check_m09(conn):
    """Модуль заявок — отдельная часть продукта: своя схема и свои строки."""
    found = {
        r[0]
        for r in await conn.fetch(
            "SELECT table_schema||'.'||table_name||'.'||column_name "
            "FROM information_schema.columns "
            "WHERE table_schema||'.'||table_name||'.'||column_name = ANY($1::text[])",
            list(REQUIRED_COLUMNS),
        )
    }
    missing = [c for c in REQUIRED_COLUMNS if c not in found]
    if missing:
        return False, (
            f"схема модуля неполна: {len(found)} из {len(REQUIRED_COLUMNS)} колонок, "
            f"нет {', '.join(missing)}"
        )
    total = await conn.fetchval("SELECT count(*) FROM maint.notification")
    if total == 0:
        return False, (
            f"{len(found)} из {len(REQUIRED_COLUMNS)} колонок на месте, "
            "но в maint.notification 0 заявок: пустая таблица модуля не образует"
        )
    return True, (
        f"{len(found)} из {len(REQUIRED_COLUMNS)} колонок модуля на месте, "
        f"в maint.notification {total} заявок"
    )


async def check_m10(conn):
    """Заявку заводит расчёт, а не диспетчер."""
    row = await conn.fetchrow(
        "SELECT count(*) AS total, "
        "count(*) FILTER (WHERE source_system = 'forecast') AS forecast_src, "
        "count(*) FILTER (WHERE source_system = 'forecast' "
        "                 AND forecast_id IS NOT NULL) AS auto "
        "FROM maint.notification"
    )
    if row["total"] == 0:
        return False, "в maint.notification 0 заявок: расчёт не завёл ни одной"
    if row["auto"] == 0:
        return False, (
            f"{row['total']} заявок, из них с source_system='forecast' "
            f"{row['forecast_src']}, с заполненным forecast_id 0 — "
            "ни одну не родил расчёт"
        )
    # Расхождение forecast_src и auto означало бы дыру в CHECK из 001_assets.sql
    # (source_system <> 'forecast' OR forecast_id IS NOT NULL), поэтому печатаем
    # оба числа: сошлись — заодно доказали, что ограничение работает.
    return True, (
        f"{row['total']} заявок, из них {row['forecast_src']} с "
        f"source_system='forecast', и у всех {row['auto']} заполнен forecast_id"
    )


async def check_m11(conn):
    """У автозаявки заполнены объект, вид работ, срок и обоснование."""
    row = await conn.fetchrow(
        "SELECT count(*) AS auto, "
        "count(*) FILTER (WHERE n.func_location_id IS NOT NULL "
        "                 OR n.equipment_id IS NOT NULL) AS with_object, "
        "count(*) FILTER (WHERE n.due_at IS NOT NULL) AS with_due, "
        "count(*) FILTER (WHERE coalesce(btrim(n.long_text), '') <> '') AS with_reason, "
        "count(*) FILTER (WHERE w.id IS NOT NULL) AS with_work_type "
        "FROM maint.notification n "
        # Вид работ берём через заказ ТОиР: order_type_id и activity_type_id там
        # NOT NULL и ссылаются на ref.order_type / ref.activity_type, то есть
        # сам факт заказа доказывает «из справочника, а не текстом».
        "LEFT JOIN LATERAL (SELECT wo.id FROM maint.work_order wo "
        "                    JOIN ref.order_type ot ON ot.id = wo.order_type_id "
        "                    JOIN ref.activity_type at ON at.id = wo.activity_type_id "
        "                   WHERE wo.notification_id = n.id LIMIT 1) w ON true "
        "WHERE n.source_system = 'forecast'"
    )
    auto = row["auto"]
    if auto == 0:
        return False, "0 автозаявок: проверять заполненность не на чем"
    parts = (
        f"объект у {row['with_object']}, срок у {row['with_due']}, "
        f"обоснование у {row['with_reason']}, "
        f"вид работ из справочника у {row['with_work_type']}"
    )
    bad = [
        k
        for k in ("with_object", "with_due", "with_reason", "with_work_type")
        if row[k] != auto
    ]
    if bad:
        return False, f"из {auto} автозаявок: {parts} — заполнены не все четыре поля"
    return True, f"{auto} автозаявок, {parts}"


async def check_m12(conn):
    """Из заявки открывается породивший прогноз, из прогноза — его заявки."""
    row = await conn.fetchrow(
        "SELECT count(*) AS auto, "
        "count(f.forecast_id) AS linked, "
        "count(DISTINCT f.forecast_id) AS forecasts "
        "FROM maint.notification n "
        # LEFT JOIN, а не JOIN: обычный JOIN выбросил бы заявки с битой ссылкой
        # и оставил зелёный счётчик на уменьшившемся знаменателе.
        "LEFT JOIN pred.forecast f ON f.forecast_id = n.forecast_id "
        "WHERE n.source_system = 'forecast'"
    )
    auto = row["auto"]
    if auto == 0:
        return False, "0 автозаявок: ссылаться на прогноз некому"
    if row["linked"] != auto:
        return False, (
            f"{auto} автозаявок, из них ссылка ведёт на существующий прогноз "
            f"только у {row['linked']}: {auto - row['linked']} ссылок битые"
        )
    return True, (
        f"{auto} автозаявок, у всех {row['linked']} forecast_id ведёт на строку "
        f"pred.forecast; обратно эти заявки собираются на {row['forecasts']} прогнозах"
    )


async def check_m13(conn):
    """Заявка превентивная: срок работ раньше прогнозируемого отказа."""
    rows = await conn.fetch(
        "SELECT n.id, n.due_at, r.as_of, f.horizon_h "
        "FROM maint.notification n "
        "LEFT JOIN pred.forecast f ON f.forecast_id = n.forecast_id "
        "LEFT JOIN pred.run r ON r.run_id = f.run_id "
        "WHERE n.source_system = 'forecast'"
    )
    if not rows:
        return False, "0 автозаявок: сравнивать срок не с чем"
    leads = [lead_hours(r["due_at"], r["as_of"], r["horizon_h"]) for r in rows]
    good = [x for x in leads if x is not None and x > 0]
    if len(good) != len(rows):
        unknown = sum(1 for x in leads if x is None)
        return False, (
            f"из {len(rows)} автозаявок превентивны только {len(good)}: "
            f"{len(rows) - len(good) - unknown} со сроком не раньше отказа, "
            f"{unknown} сравнить нечем (нет срока, прогноза или прогона)"
        )
    return True, (
        f"{len(good)} из {len(rows)} автозаявок со сроком раньше прогнозируемого "
        f"отказа, запас от {min(good):.1f} ч, медиана {median(good):.1f} ч"
    )


CHECKS = (
    ("М-09", check_m09),
    ("М-10", check_m10),
    ("М-11", check_m11),
    ("М-12", check_m12),
    ("М-13", check_m13),
)


async def run_checks(dsn):
    conn = await asyncpg.connect(dsn)
    try:
        out = []
        for name, fn in CHECKS:
            try:
                ok, text = await fn(conn)
            except asyncpg.PostgresError as e:
                # Нет таблицы или схемы — это СБОЙ конкретной строки, а не
                # падение всей проверки: остальные четыре строки всё равно надо
                # предъявить на приёмке.
                ok, text = False, f"база отказала: {str(e).splitlines()[0]}"
            out.append((name, ok, text))
        return out
    finally:
        await conn.close()


def report(results):
    for name, ok, text in results:
        print(f"{name} {'OK' if ok else 'СБОЙ'} {text}")
    return 0 if all(ok for _, ok, _ in results) else 1


def fail_all(reason):
    return report([(name, False, reason) for name in ROWS])


# ------------------------------------------------------------------ самопроверка


def demo(verbose=True):
    """Проверка логики М-13 без живой базы: сравнение срока с моментом отказа."""
    from datetime import datetime, timezone

    as_of = datetime(2026, 9, 17, 0, 0, tzinfo=timezone.utc)  # момент среза прогона
    h = 24  # горизонт прогноза -> отказ ожидаем 18.09.2026 00:00

    def due(day, hour):
        return datetime(2026, 9, day, hour, tzinfo=timezone.utc)

    # Работы за 6 часов до отказа — заявка превентивная.
    assert lead_hours(due(17, 18), as_of, h) == 6.0
    assert is_preventive(due(17, 18), as_of, h) is True

    # Ровно в момент отказа: запас 0 — не превентивная, «строго раньше».
    assert lead_hours(due(18, 0), as_of, h) == 0.0
    assert is_preventive(due(18, 0), as_of, h) is False

    # После отказа: запас отрицательный, ремонт уже аварийный, а не плановый.
    assert lead_hours(due(18, 5), as_of, h) == -5.0
    assert is_preventive(due(18, 5), as_of, h) is False

    # Сравнивать нечем: заявка без срока, прогноз без прогона, прогноз без горизонта.
    assert lead_hours(None, as_of, h) is None
    assert lead_hours(due(17, 18), None, h) is None
    assert lead_hours(due(17, 18), as_of, None) is None
    assert is_preventive(None, as_of, h) is False

    if not verbose:
        return  # при обычном прогоне печатаем только пять строк приёмки
    print(
        "демо М-13: срок 17.09 18:00 при отказе 18.09 00:00 — запас 6,0 ч, превентивна"
    )
    print(
        "демо М-13: срок 18.09 00:00 — запас 0,0 ч, НЕ превентивна (нужно строго раньше)"
    )
    print("демо М-13: срок без прогноза — запас не определён, НЕ превентивна")
    print("OK")


def main(argv):
    # логика сравнения сроков обязана быть цела до любого похода в базу
    demo(verbose="--demo" in argv)
    if "--demo" in argv:
        return 0
    if asyncpg is None:
        return fail_all("не установлен asyncpg — тот же пакет, что у backend/app/db.py")
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        return fail_all("не задана переменная DATABASE_URL, подключаться не к чему")
    try:
        results = asyncio.run(run_checks(dsn))
    except (OSError, ValueError, asyncpg.PostgresError, asyncio.TimeoutError) as e:
        return fail_all(f"нет связи с базой: {str(e).splitlines()[0]}")
    return report(results)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
