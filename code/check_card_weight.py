"""Карточка участка считает тот же отказ, что вес участка, плюс свежие (MOS-153, 038).

Вес участка (pred.section_weight.episodes_cnt) — отказы D5 участка внутри окна
pred.weight_window(). Карточка (GET /api/objects/{id}/channels) — те же отказы
от начала окна и до конца архива: верхняя граница окна нужна весу, чтобы
не подглядывать в проверочное окно модели, а диспетчеру нужны и апрель–июнь
2026. Поэтому сумма faults_cnt по каналам участка обязана равняться
episodes_cnt плюс отказы D5 после окна. Разошлись — карточка показывает не те
отказы, из которых вырос вес, и объяснить его не может.

Зовём сам обработчик list_object_channels, а не копию его SQL: копия прошла бы
и тогда, когда запрос в objects.py разошёлся с представлением.

Вторая строка — два канала, на которых 22.09.2026 нашлась обрезка карточки
по верхней границе окна: канал 334599 (участок 161) отказывал 14 раз в
апреле–июне 2026 и ни разу в окне, и карточка писала «отказов не было»;
у канала 333463 (участок 169) последний отказ 12.06.2026, а карточка
показывала 08.12.2025.

Запуск (нужна база):
    DATABASE_URL=postgresql://... PYTHONPATH=backend python code/check_card_weight.py
"""

import asyncio
import os
import sys
from datetime import date
from zoneinfo import ZoneInfo

import asyncpg

from app.api.objects import list_object_channels

СТРАНИЦА = 1000  # верхняя граница limit в обработчике
МСК = ZoneInfo("Europe/Moscow")

# Отказы D5 участка после окна веса — то, что карточка показывает сверх веса.
ПОСЛЕ_ОКНА = """
SELECT c.section_id, count(*) AS n
  FROM smvu.channel c
  JOIN smvu.model_failure_event e ON e.channel_id = c.channel_id
 CROSS JOIN pred.weight_window() w
 WHERE timezone('Europe/Moscow', e.started_at)::date > w.date_to
 GROUP BY c.section_id
"""


# Метод вызывается напрямую, мимо Depends: пользователь нужен для области видимости
# (MOS-107). Роль ods_dispatcher видит весь парк, базу про область не спрашивает.
ВИДИТ_ВСЁ = {"login": "check_card_weight", "roles": ["ods_dispatcher"]}


async def каналы(conn, section_id):
    items, offset = [], 0
    while True:
        ответ = await list_object_channels(
            section_id, limit=СТРАНИЦА, offset=offset, conn=conn, user=ВИДИТ_ВСЁ
        )
        items += ответ["items"]
        offset += СТРАНИЦА
        if offset >= ответ["total"]:
            return items


async def карточка_равна_весу(conn):
    """(ok, текст). Сверяет все участки представления, а не выборку."""
    вес = await conn.fetch(
        "SELECT section_id, episodes_cnt FROM pred.section_weight ORDER BY section_id"
    )
    if not вес:
        return False, "pred.section_weight пуста — сверять нечего, это не зелёный результат"
    после = {r["section_id"]: r["n"] for r in await conn.fetch(ПОСЛЕ_ОКНА)}
    разошлись, в_окне, свежих, на_карточках = [], 0, 0, 0
    for r in вес:
        n = sum(к["faults_cnt"] for к in await каналы(conn, r["section_id"]))
        ждём = r["episodes_cnt"] + после.get(r["section_id"], 0)
        в_окне += r["episodes_cnt"]
        свежих += после.get(r["section_id"], 0)
        на_карточках += n
        if n != ждём:
            разошлись.append((r["section_id"], ждём, n))
    # Ноль отказов в окне или после него сделал бы сверку холостой.
    if в_окне == 0 or свежих == 0:
        return False, (f"{len(вес)} участков, отказов в окне {в_окне}, после окна {свежих} — "
                       "сверка холостая")
    if разошлись:
        пример = ", ".join(f"участок {s}: ждём {w}, карточка {c}" for s, w, c in разошлись[:3])
        return False, (f"разошлись {len(разошлись)} участков из {len(вес)}; ждём {в_окне} "
                       f"в окне + {свежих} после, на карточках {на_карточках}; {пример}")
    return True, (f"{len(вес)} участков: на карточках {на_карточках} = {в_окне} в весе "
                  f"+ {свежих} после окна")


async def свежие_отказы_видны(conn):
    """(ok, текст). Два канала, на которых нашлась обрезка карточки 22.09.2026."""
    к161 = {к["channel_id"]: к for к in await каналы(conn, 161)}.get(334599)
    к169 = {к["channel_id"]: к for к in await каналы(conn, 169)}.get(333463)
    if к161 is None or к169 is None:
        return False, "на карточках участков 161 и 169 нет каналов 334599 и 333463"
    последний = к169["last_fault_at"] and к169["last_fault_at"].astimezone(МСК).date()
    ok = к161["faults_cnt"] == 14 and последний == date(2026, 6, 12)
    return ok, (f"канал 334599 (участок 161): отказов {к161['faults_cnt']}, ждём 14; "
                f"канал 333463 (участок 169): последний {последний}, ждём 2026-06-12")


ПРОВЕРКИ = (("карточка = вес + после окна", карточка_равна_весу),
            ("свежие отказы на карточке", свежие_отказы_видны))


async def проверить(conn):
    return [(имя, *await ф(conn)) for имя, ф in ПРОВЕРКИ]


async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"], command_timeout=120)
    try:
        результаты = await проверить(conn)
    finally:
        await conn.close()
    for имя, ok, текст in результаты:
        print(("OK   " if ok else "СБОЙ ") + f"{имя}: {текст}")
    return 0 if all(ok for _, ok, _ in результаты) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
