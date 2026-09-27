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
maint.notification из db/migrations/051_order_external_status.sql. UPDATE идёт
только при изменении внешнего статуса, чтобы не путать external_status_at
с моментом тика и не писать одинаковые строки впустую. Отдельной колонки
«источник смены статуса» нет: непустой external_status_at сам служит отметкой
«пришёл из внешней системы, а не проставлен человеком» (Ф-87) — его выставляет
только этот модуль, ручная смена maint.notification.status через API его
не трогает.

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
    SELECT notification_no, reported_at, due_at
      FROM maint.notification
     WHERE due_at IS NOT NULL
       AND status NOT IN ('COMPLETED', 'CANCELLED')
"""


# ponytail: все заявки одним GET, без пагинации. На стенде 351 заявка — это
# 45 675 байт строки запроса; наш nginx это пропускает, а настоящий хелпдеск
# за типовым nginx с буфером заголовков 8 КБ (large_client_header_buffers)
# такой запрос отклонит. Резать заявки на пачки, когда появится настоящий
# ORDER_SYSTEM_URL и парк заявок вырастет настолько, что упрёмся в лимит.
def адрес(url: str, заявки: list) -> str:
    параметры: list[tuple[str, str]] = []
    for з in заявки:
        параметры.append(("notification_no", з["notification_no"]))
        параметры.append(("reported_at", з["reported_at"].isoformat()))
        параметры.append(("due_at", з["due_at"].isoformat()))
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
        итог = await conn.execute(
            """
            UPDATE maint.notification
               SET external_status = $2, external_status_at = $3, external_assignee = $4
             WHERE notification_no = $1
               AND (external_status IS DISTINCT FROM $2
                    OR external_status_at IS DISTINCT FROM $3)
            """,
            т["notification_no"], т["external_status"], момент, т["external_assignee"],
        )
        if итог != "UPDATE 0":
            изменено += 1
    return изменено


if __name__ == "__main__":
    from zoneinfo import ZoneInfo

    ПОЯС = ZoneInfo("Europe/Moscow")
    заявки = [
        {"notification_no": "AF01", "reported_at": datetime(2026, 9, 1, 10, tzinfo=ПОЯС),
         "due_at": datetime(2026, 9, 2, 2, tzinfo=ПОЯС)},
        {"notification_no": "AF02", "reported_at": datetime(2026, 9, 1, 11, tzinfo=ПОЯС),
         "due_at": datetime(2026, 9, 1, 20, tzinfo=ПОЯС)},
    ]
    а = urllib.parse.parse_qs(urllib.parse.urlsplit(адрес(URL, заявки)).query)
    assert а["notification_no"] == ["AF01", "AF02"], а
    assert а["reported_at"][0] == "2026-09-01T10:00:00+03:00", а
    assert а["due_at"][1] == "2026-09-01T20:00:00+03:00", а
    print("selfcheck адреса ok: notification_no/reported_at/due_at идут одной длины и порядком")
