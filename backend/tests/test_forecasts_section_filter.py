"""US-11 сц. 1: отбор журнала прогнозов по участку делает сервер.

Раньше GET /api/forecasts принимал только период, а участок журнал отбирал
в браузере в пределах страницы: total в ответе считал весь журнал, и число
строк на экране с ним не сходилось. Проверяем, что section_id доходит до
обоих запросов — счёта и страницы — одним и тем же условием.
"""
import asyncio
from datetime import date

from app.api import routes

ОДС = {"login": "ods1", "roles": ["ods_dispatcher"]}


class Соединение:
    def __init__(self):
        self.запросы: list[tuple[str, tuple]] = []

    async def fetchval(self, sql, *args):
        self.запросы.append((sql, args))
        return 0

    async def fetch(self, sql, *args):
        self.запросы.append((sql, args))
        return []


def test_section_id_reaches_count_and_page():
    conn = Соединение()
    ответ = asyncio.run(routes.list_forecasts(
        from_=date(2026, 7, 1), to=date(2026, 9, 27), section_id=401,
        limit=200, offset=0, conn=conn, user=ОДС))
    assert ответ == {"total": 0, "items": []}
    счёт, страница = conn.запросы[0], conn.запросы[1]
    for sql, args in (счёт, страница):
        assert "f.section_id = $4" in sql
        assert args[3] == 401
    # limit и offset — после условия отбора.
    assert страница[1][4:] == (200, 0)


def test_without_section_id_filter_is_off():
    conn = Соединение()
    asyncio.run(routes.list_forecasts(
        from_=None, to=None, section_id=None, limit=200, offset=0, conn=conn, user=ОДС))
    assert conn.запросы[0][1][3] is None
