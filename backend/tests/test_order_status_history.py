"""US-19 сц. 1, Ф-87: смена статуса в системе учёта пишет строку истории заявки
с источником order_system — тем же запросом, что меняет external_status.

Базы не нужно: подставное соединение отвечает так, как ответил бы PostgreSQL
на запрос с CTE: строка истории появляется, только если UPDATE что-то поменял.
"""

import asyncio
from datetime import datetime, timedelta, timezone

from app.ingest import order_status

МОМЕНТ = "2026-09-28T10:20:00+03:00"


class Соединение:
    def __init__(self, текущий):
        self.текущий = текущий  # notification_no -> (status, changed_at)
        self.история: list[tuple] = []

    async def fetch(self, sql, *args):
        return [{"notification_no": н, "created_at": datetime(2026, 9, 28, tzinfo=timezone.utc),
                 "срок": timedelta(hours=16)} for н in self.текущий]

    async def execute(self, sql, no, status, момент, assignee):
        assert "INSERT INTO maint.notification_status_log" in sql
        assert "'order_system'" in sql
        if self.текущий[no] == (status, момент):
            return "INSERT 0 0"
        self.текущий[no] = (status, момент)
        self.история.append((no, status, assignee, момент))
        return "INSERT 0 1"


def test_status_change_is_logged_once(monkeypatch):
    def синхронизировать(conn, тикеты):
        monkeypatch.setattr(order_status, "_получить", lambda url: тикеты)
        return asyncio.run(order_status.синхронизировать(conn, url="http://emu"))

    момент = datetime.fromisoformat(МОМЕНТ)
    conn = Соединение({"AF01": ("принята", момент - timedelta(hours=1))})
    тикет = {"notification_no": "AF01", "external_status": "в работе",
             "external_status_at": МОМЕНТ, "external_assignee": "Бригада №12"}
    assert синхронизировать(conn, [тикет]) == 1
    assert синхронизировать(conn, [тикет]) == 0  # тот же статус — строки нет
    assert conn.история == [("AF01", "в работе", "Бригада №12", момент)]
