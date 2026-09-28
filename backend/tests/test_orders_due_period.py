"""US-18 сц. 2 «Отбор по неделе» (docs/user-stories.md): руководитель задаёт
период срока, и в списке остаются только заявки с этим сроком, а их число
совпадает с GET /api/orders с тем же периодом.

Базы не нужно: подставное соединение запоминает, какие параметры получили
запрос числа и запрос страницы.
"""

import asyncio
from datetime import date

import pytest
from fastapi import HTTPException

from app.api import orders

# Коды приоритета — сид ref.priority, db/migrations/010_orders.sql.
СПРАВОЧНИК_ПРИОРИТЕТОВ = {"1", "2", "3", "4"}


class Соединение:
    def __init__(self):
        self.вызовы: list[tuple[str, tuple]] = []

    async def fetchval(self, sql, *args):
        if "FROM ref.priority" in sql:  # проверка кода, не число и не страница
            return 1 if args[0] in СПРАВОЧНИК_ПРИОРИТЕТОВ else None
        self.вызовы.append((sql, args))
        return 0

    async def fetch(self, sql, *args):
        self.вызовы.append((sql, args))
        return []


def список(conn, monkeypatch, **период):
    async def видит_всё(user, conn):
        return None

    monkeypatch.setattr(orders, "видимые_участки", видит_всё)
    return asyncio.run(
        orders.list_orders(
            limit=200, offset=0, conn=conn, user={"login": "disp2"}, **{"q": None, "status": None, "priority": None, **период}
        )
    )


def test_period_goes_to_count_and_page_alike(monkeypatch):
    conn = Соединение()
    список(conn, monkeypatch, due_from=date(2026, 6, 1), due_to=date(2026, 6, 7))
    (sql_счёт, арг_счёт), (sql_стр, арг_стр) = conn.вызовы
    # Обе границы включительны: «по 7 июня» — весь день 7 июня, до начала 8-го.
    assert арг_счёт[1:3] == (date(2026, 6, 1), date(2026, 6, 8))
    assert арг_стр[1:3] == арг_счёт[1:3], "число и страница считают один период"
    # Даты — московские сутки: заявка со сроком 00:30 МСК 1 июня (21:30 UTC 31 мая)
    # в период «с 1 июня» входит.
    for sql in (sql_счёт, sql_стр):
        assert "n.due_at >= timezone('Europe/Moscow', $2::date::timestamp)" in sql
        assert "n.due_at < timezone('Europe/Moscow', $3::date::timestamp)" in sql


def test_without_period_nothing_is_cut(monkeypatch):
    conn = Соединение()
    список(conn, monkeypatch, due_from=None, due_to=None)
    assert all(args[1:3] == (None, None) for _, args in conn.вызовы)


def test_search_by_number_goes_to_count_and_page_alike(monkeypatch):
    """Поиск по номеру заявки: одна строка q и в числе, и в странице, пробелы
    по краям отрезаны; пустая строка — поиска нет."""
    conn = Соединение()
    список(conn, monkeypatch, due_from=None, due_to=None, q=" AF0001061588 ")
    assert [args[3] for _, args in conn.вызовы] == ["AF0001061588"] * 2
    for sql, _ in conn.вызовы:
        assert "n.id::text = $4" in sql and "wo.order_no ILIKE" in sql
    conn = Соединение()
    список(conn, monkeypatch, due_from=None, due_to=None, q="  ")
    assert [args[3] for _, args in conn.вызовы] == [None, None]


def test_status_and_priority_go_to_count_and_page_alike(monkeypatch):
    """Отбор по статусу и приоритету: одни и те же значения и в числе, и в странице.
    active — группа «в работе» плитки на главной: OPEN и IN_PROCESS."""
    conn = Соединение()
    список(conn, monkeypatch, due_from=None, due_to=None, status="active", priority="2")
    assert [args[4:6] for _, args in conn.вызовы] == [(["OPEN", "IN_PROCESS"], "2")] * 2
    for sql, _ in conn.вызовы:
        assert "n.status = ANY($5)" in sql and "p.code = $6" in sql
    conn = Соединение()
    список(conn, monkeypatch, due_from=None, due_to=None, status="COMPLETED")
    assert [args[4:6] for _, args in conn.вызовы] == [(["COMPLETED"], None)] * 2


@pytest.mark.parametrize("отбор", [{"status": "open"}, {"status": "ЗАКРЫТА"}, {"priority": "7"}])
def test_foreign_status_or_priority_is_422(monkeypatch, отбор):
    """Чужое значение — 422, а не «все заявки»: число на экране не должно врать."""
    conn = Соединение()
    with pytest.raises(HTTPException) as e:
        список(conn, monkeypatch, due_from=None, due_to=None, **отбор)
    assert e.value.status_code == 422
    assert conn.вызовы == [], "до числа и страницы запрос не дошёл"
