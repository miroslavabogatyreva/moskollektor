"""Забор статусов заявок из системы учёта заказчика. Задача MOS-63 (план 6.8),
приёмка Ф-87, НФ-69, Ф-96.

Зовёт задача планировщика раз в ORDER_STATUS_INTERVAL_MIN минут
(app.worker.scheduler). Ходит по ORDER_SYSTEM_URL запросом к /emu/helpdesk/v1/tickets
— по умолчанию наш эмулятор на api (app.api.helpdesk_emu), тот же приём, что у
погоды (app.ingest.weather): подставь адрес настоящего хелпдеска — и код
не меняется.

ТОЛЬКО ЧТЕНИЕ (НФ-69). Модуль ходит наружу одним GET-запросом за тик и дальше
только читает свой ответ; во внешнюю систему ничего не пишет. Заказчик прямо
запретил обратную запись — «Реальная отправка заявок во внешние системы
не требуется» (docs/meetings/2026-09-19-ответы-заказчика.md, сводный 20).

ЧТО ОБНОВЛЯЕМ И ПОЧЕМУ ТАК. external_status, external_status_at (момент смены
статуса В ИСТОЧНИКЕ, не момент нашего тика) и external_assignee — колонки
maint.notification из db/migrations/051_order_external_status.sql. Каждая смена
ещё и строка истории заявки maint.notification_status_log с source='order_system'
(db/migrations/058_notification_status_log.sql, US-19, Ф-87). UPDATE идёт
только при изменении внешнего статуса, чтобы не путать external_status_at
с моментом тика и не писать одинаковые строки впустую. «Пришёл из внешней
системы, а не проставлен человеком» (Ф-87) говорит колонка source строки
истории: order_system ставит только этот модуль.

КАКИЕ ЗАЯВКИ ОПРАШИВАЕМ. Только те, у кого есть due_at (автозаявки от прогноза
— заявки без due_at заведены руками и в хелпдеск заказчика не попадали) и кто
ещё не закрыт/не отменён: статус завершённой заявки в источнике эмулятор
всё равно не меняет, опрашивать её дальше незачем.

ОТКАЗ ИСТОЧНИКА. Любая ошибка — сеть, HTTP, пустой ответ — поднимается наверх;
никаких строк это не портит, потому что UPDATE ещё не начался.
"""

import asyncio
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime

URL = os.environ.get("ORDER_SYSTEM_URL", "http://api:8000/emu/helpdesk/v1/tickets")

СПИСОК_ЗАЯВОК_SQL = """
    SELECT notification_no, created_at, (due_at - reported_at) AS срок
      FROM maint.notification
     WHERE due_at IS NOT NULL
       AND status NOT IN ('COMPLETED', 'CANCELLED')
"""


# ponytail: все заявки одним GET, без пагинации. На стенде 351 заявка — это
# 45 675 байт строки запроса; наш nginx это пропускает, а настоящий хелпдеск
# за типовым nginx с буфером заголовков 8 КБ (large_client_header_buffers)
# такой запрос отклонит. Резать заявки на пачки, когда появится настоящий
# ORDER_SYSTEM_URL и парк заявок вырастет настолько, что упрёмся в лимит.
#
# Эмулятору передаём created_at, а не reported_at. У автозаявок с проигрыванием
# архива reported_at — архивное время (май-июнь 2026): для эмулятора такая
# заявка выглядит заведённой несколько месяцев назад и сразу «выполнена» — Ф-87
# («сменить статус → новый статус виден») не показать ни разу. Хелпдеск
# заказчика узнаёт о заявке в момент, когда мы её завели у СЕБЯ, — это
# created_at (нашла проверяющая на стенде 27.09.2026: у всех 351 автозаявки
# external_status оказался «выполнена», включая заведённые сегодня). Срок
# заявки (due_at − reported_at) сохраняем, но откладываем его от created_at.
def адрес(url: str, заявки: list) -> str:
    параметры: list[tuple[str, str]] = []
    for з in заявки:
        параметры.append(("notification_no", з["notification_no"]))
        параметры.append(("reported_at", з["created_at"].isoformat()))
        параметры.append(("due_at", (з["created_at"] + з["срок"]).isoformat()))
    return url + "?" + urllib.parse.urlencode(параметры)


def _получить(url: str) -> list[dict]:
    with urllib.request.urlopen(url, timeout=30) as ответ:
        return json.load(ответ)["tickets"]


async def синхронизировать(conn, url: str = URL) -> int:
    """Опрашивает источник и обновляет изменившиеся заявки. Возвращает их число."""
    заявки = await conn.fetch(СПИСОК_ЗАЯВОК_SQL)
    if not заявки:
        return 0
    тикеты = await asyncio.to_thread(_получить, адрес(url, заявки))
    изменено = 0
    for т in тикеты:
        момент = datetime.fromisoformat(т["external_status_at"])
        # Смена статуса и строка истории заявки (миграция 058, US-19, Ф-87) — одним
        # запросом: строка появляется, только если UPDATE правда что-то поменял.
        итог = await conn.execute(
            """
            WITH смена AS (
                UPDATE maint.notification
                   SET external_status = $2, external_status_at = $3, external_assignee = $4
                 WHERE notification_no = $1
                   AND (external_status IS DISTINCT FROM $2
                        OR external_status_at IS DISTINCT FROM $3)
                RETURNING id
            )
            INSERT INTO maint.notification_status_log
                   (notification_id, status, assignee, changed_at, source)
            SELECT id, $2, $4, $3, 'order_system' FROM смена
            """,
            т["notification_no"], т["external_status"], момент, т["external_assignee"],
        )
        if итог != "INSERT 0 0":
            изменено += 1
    return изменено


if __name__ == "__main__":
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    ПОЯС = ZoneInfo("Europe/Moscow")
    заявки = [
        {"notification_no": "AF01", "created_at": datetime(2026, 9, 27, 10, tzinfo=ПОЯС),
         "срок": timedelta(hours=16)},
        {"notification_no": "AF02", "created_at": datetime(2026, 9, 27, 11, tzinfo=ПОЯС),
         "срок": timedelta(hours=9)},
    ]
    а = urllib.parse.parse_qs(urllib.parse.urlsplit(адрес(URL, заявки)).query)
    assert а["notification_no"] == ["AF01", "AF02"], а
    # Эмулятору уходит created_at под именем reported_at, а не архивный reported_at
    # заявки: если код вернуть на з["reported_at"], в заявках этого ключа больше
    # нет вовсе, и адрес() упадёт KeyError — самопроверка красная.
    assert а["reported_at"][0] == "2026-09-27T10:00:00+03:00", а
    assert а["due_at"][1] == "2026-09-27T20:00:00+03:00", а  # 11:00 + 9 ч
    print(
        "selfcheck адреса ok: notification_no/reported_at/due_at идут одной длины и "
        "порядком, цикл отсчитан от created_at, а не от архивного reported_at"
    )
