"""GET /api/tech-events/sensor-kinds: список для фильтра «Тип датчика» журнала.

Проверяем, что область видимости и участок карточки доходят до запроса:
диспетчер района не должен увидеть типы датчиков чужих коллекторов.
"""

import asyncio

from app.api import tech_events


class Соединение:
    def __init__(self):
        self.запросы: list[tuple[str, tuple]] = []

    async def fetch(self, sql, *args):
        self.запросы.append((sql, args))
        if sql is tech_events.ТИПЫ_SQL:
            return [("Датчик дыма",), ("Журнал ОДС",)]
        return [{"section_id": 7}, {"section_id": 9}]  # область видимости диспетчера


def test_scope_and_section_reach_query():
    conn = Соединение()
    ответ = asyncio.run(
        tech_events.sensor_kinds(
            section_id=9, conn=conn, user={"login": "disp2", "roles": ["dispatcher"]}
        )
    )
    assert ответ == ["Датчик дыма", "Журнал ОДС"]
    sql, args = conn.запросы[-1]
    assert sql is tech_events.ТИПЫ_SQL
    assert args == ([7, 9], 9)


def test_ods_sees_all():
    conn = Соединение()
    asyncio.run(
        tech_events.sensor_kinds(
            section_id=None,
            conn=conn,
            user={"login": "ods1", "roles": ["ods_dispatcher"]},
        )
    )
    assert conn.запросы == [(tech_events.ТИПЫ_SQL, (None, None))]
