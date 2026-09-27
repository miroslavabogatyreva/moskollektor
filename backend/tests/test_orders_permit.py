"""US-13 сц. 2 «Заявка не создаётся»: на участке открыт наряд-допуск — превентивной
заявки по нему нет, хотя расчёт отобрал его под заявку.

Базы не нужно: подставное соединение отвечает на запросы order_rules.завести()
по тексту SQL — отбор прогона, действующие наряды, вставки заявки и заказа.
"""
import asyncio
from datetime import datetime, timezone

from app.domain import order_rules

СРЕЗ = datetime(2026, 6, 10, 8, 0, tzinfo=timezone.utc)


def кандидат(sid):
    return {"forecast_id": 100 + sid, "section_id": sid, "probability": 0.9, "horizon_h": 24,
            "explanation_ru": None, "as_of": СРЕЗ, "smvu_key": f"889:{sid}",
            "func_location_id": 1000 + sid, "crit": "A"}


class Транзакция:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class Соединение:
    def __init__(self, в_работах):
        self.в_работах = set(в_работах)
        self.заявки: list[str] = []

    def transaction(self):
        return Транзакция()

    async def fetch(self, sql, *args):
        if "FROM pred.forecast f" in sql:
            return [кандидат(1), кандидат(2)]
        if "permit.permit" in sql:
            return [{"section_id": s} for s in args[0] if s in self.в_работах]
        raise AssertionError(sql)

    async def fetchval(self, sql, *args):
        assert "INSERT INTO maint.notification" in sql, sql
        self.заявки.append(args[9])        # source_key
        return len(self.заявки)

    async def execute(self, sql, *args):
        assert "INSERT INTO maint.work_order" in sql, sql


def завести(conn, monkeypatch):
    async def справочники(_):
        return {"priority": {"2": (2, 16.0), "3": (3, 48.0), "4": (4, 72.0)},
                "order_type": {"PREV": 1}, "activity_type": {"PCM": 1, "PIN": 2, "POC": 3},
                "потолок_ч": 16.0}
    monkeypatch.setattr(order_rules, "справочники", справочники)
    предупреждение = {"pfx": "889", "opened_at": "2026-06-10T08:36:56",
                      "expires_at": "2026-06-11T08:36:56", "p": 0.9, "маршрут": ["889:1", "889:2"]}
    план = {sid: {**предупреждение, "ключ": f"warn:889:x:{sid}"} for sid in (1, 2)}
    return asyncio.run(order_rules.завести(conn, run_id=7, план=план))


def test_section_under_permit_gets_no_order(monkeypatch):
    conn = Соединение(в_работах={1})
    итог = завести(conn, monkeypatch)
    assert conn.заявки == ["warn:889:x:2"], conn.заявки
    assert итог["в_работах"] == 1


def test_without_permits_both_get_orders(monkeypatch):
    conn = Соединение(в_работах=set())
    завести(conn, monkeypatch)
    assert sorted(conn.заявки) == ["warn:889:x:1", "warn:889:x:2"]
