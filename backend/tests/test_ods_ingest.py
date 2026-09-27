"""US-12 сц. 5: приём событий ОДС (POST /api/ingest/ods-events, миграция 054).

Базы не нужно: подставное соединение отвечает так, как ответил бы PostgreSQL
на INSERT … ON CONFLICT DO NOTHING — «INSERT 0 1» или «INSERT 0 0» на дубль.
"""
import asyncio

import pytest
from fastapi import HTTPException

from app.api import ingest

ТОКЕН = "t" * 40


class Соединение:
    def __init__(self, участки=(1,)):
        self.ключи: set[str] = set()
        self.участки = set(участки)

    async def fetch(self, sql, ids):
        return [{"section_id": i} for i in ids if i in self.участки]

    async def execute(self, sql, source_id, *rest):
        if source_id in self.ключи:
            return "INSERT 0 0"
        self.ключи.add(source_id)
        return "INSERT 0 1"

    async def fetchval(self, sql):
        return len(self.ключи) or None


def событие(key, section_id=None):
    return ingest.OdsEvent(source_id=key, event_time="2026-09-27T10:00:00+03:00",
                           section_id=section_id, event_text="Звонок из коллектора",
                           event_type="Предупреждение")


def вызвать(conn, events, authorization=f"Bearer {ТОКЕН}"):
    return asyncio.run(ingest.ingest_ods_events(
        body=ingest.OdsEventsIn(events=events), request=None,
        authorization=authorization, x_user_login=None, conn=conn))


def test_token_accepts_and_counts_duplicates(monkeypatch):
    monkeypatch.setenv("INGEST_TOKEN", ТОКЕН)
    conn = Соединение()
    assert вызвать(conn, [событие("a", 1), событие("b")]) == {
        "received": 2, "accepted": 2, "duplicates": 0, "last_id": 2}
    # Повтор той же пачки дублей не заводит.
    assert вызвать(conn, [событие("a", 1)])["duplicates"] == 1


def test_wrong_token_is_401(monkeypatch):
    monkeypatch.setenv("INGEST_TOKEN", ТОКЕН)
    with pytest.raises(HTTPException) as e:
        вызвать(Соединение(), [событие("a")], authorization="Bearer не-тот")
    assert e.value.status_code == 401


def test_unknown_section_is_422_not_500(monkeypatch):
    monkeypatch.setenv("INGEST_TOKEN", ТОКЕН)
    with pytest.raises(HTTPException) as e:
        вызвать(Соединение(участки=(1,)), [событие("a", 999)])
    assert e.value.status_code == 422


def test_event_type_only_two_values():
    with pytest.raises(ValueError):
        ingest.OdsEvent(source_id="a", event_time="2026-09-27T10:00:00+03:00",
                        event_text="x", event_type="Авария")
