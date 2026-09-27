"""US-20: сводка исходов за период (GET /api/forecast-outcomes).

Сводка обязана считать тот же отбор, что журнал (GET /api/forecasts): те же
период, область видимости и участок, одним и тем же WHERE. Иначе сумма пяти
чисел разойдётся с «Найдено: N» журнала — ровно это проверяет сц. 1 истории.
Сам счёт — SQL, его проверяли на PostgreSQL 16 с заглушками таблиц.
"""
import asyncio
from datetime import date

from app.api import routes

ОДС = {"login": "ods1", "roles": ["ods_dispatcher"]}


class Соединение:
    def __init__(self):
        self.запросы: list[tuple[str, tuple]] = []

    async def fetchrow(self, sql, *args):
        self.запросы.append((sql, args))
        return {"confirmed": 1, "false_alarm": 2, "not_checked": 3,
                "horizon_expired": 4, "open": 5}

    async def fetchval(self, sql, *args):
        self.запросы.append((sql, args))
        return 0

    async def fetch(self, sql, *args):
        self.запросы.append((sql, args))
        return []


def test_summary_total_is_sum_of_five():
    ответ = asyncio.run(routes.forecast_outcomes(
        from_=date(2026, 8, 1), to=date(2026, 8, 31), section_id=None,
        conn=Соединение(), user=ОДС))
    assert ответ == {"confirmed": 1, "false_alarm": 2, "not_checked": 3,
                     "horizon_expired": 4, "open": 5, "total": 15}


def test_summary_uses_journal_where_and_args():
    conn = Соединение()
    asyncio.run(routes.forecast_outcomes(
        from_=date(2026, 8, 1), to=date(2026, 8, 31), section_id=401, conn=conn, user=ОДС))
    журнал = Соединение()
    asyncio.run(routes.list_forecasts(
        from_=date(2026, 8, 1), to=date(2026, 8, 31), section_id=401, limit=200, offset=0,
        conn=журнал, user=ОДС))
    сводка_sql, сводка_args = conn.запросы[0]
    счёт_sql, счёт_args = журнал.запросы[0]
    assert routes.ОТБОР_ЖУРНАЛА in сводка_sql and routes.ОТБОР_ЖУРНАЛА in счёт_sql
    assert сводка_args == счёт_args
