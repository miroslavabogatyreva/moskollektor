"""US-22 сц. 3 «История канала» (docs/user-stories.md): техник открывает участок
из заявки и видит прошлые эпизоды потери связи канала с датами и длительностью.

Метод GET /api/objects/{section_id}/channels/{channel_id}/episodes. Базы не нужно:
подставное соединение отвечает на два запроса метода по тексту SQL.
"""

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.api import objects

Т = datetime(2026, 5, 18, 7, 40, tzinfo=timezone.utc)


class Соединение:
    def __init__(self, канал_на_участке: bool, эпизоды: list[dict]):
        self.канал_на_участке = канал_на_участке
        self.эпизоды = эпизоды
        self.запросы: list[str] = []

    async def fetchrow(self, sql, *args):
        self.запросы.append(sql)
        assert "smvu.channel" in sql, sql
        if not self.канал_на_участке:
            return None
        return {
            "channel_id": args[1],
            "name": "ДД кам. (т/с) ПК0",
            "sensor_kind": "Датчик дыма",
            "system_kind": "Пожарная охрана",
        }

    async def fetch(self, sql, *args):
        self.запросы.append(sql)
        # Отказ — то же представление и то же окно, что у faults_cnt в
        # GET /api/objects/{id}/channels: число эпизодов обязано совпасть с таблицей.
        assert "smvu.model_failure_event" in sql and "pred.weight_window()" in sql, sql
        assert "ORDER BY e.started_at DESC" in sql, sql
        return self.эпизоды


def вызвать(conn, monkeypatch):
    async def видит_всё(user, conn, section_id):
        return None

    monkeypatch.setattr(objects, "проверить_участок", видит_всё)
    return asyncio.run(
        objects.list_channel_episodes(
            section_id=401, channel_id=154850, conn=conn, user={"login": "tech1"}
        )
    )


def test_episodes_with_duration_newest_first(monkeypatch):
    conn = Соединение(
        True,
        [
            # Порядок — как отдаёт SQL: свежий эпизод сверху.
            {
                "started_at": datetime(2026, 6, 20, 1, 0, tzinfo=timezone.utc),
                "ended_at": None,
                "fault_value": "Неисправен",
            },
            {
                "started_at": Т,
                "ended_at": datetime(2026, 5, 18, 9, 10, tzinfo=timezone.utc),
                "fault_value": "Неисправен",
            },
        ],
    )
    ответ = вызвать(conn, monkeypatch)
    assert ответ["channel"]["name"] == "ДД кам. (т/с) ПК0"
    assert ответ["total"] == 2
    # Свежий эпизод сверху; открытый — без длительности, а не с нулём.
    assert [э["duration_h"] for э in ответ["items"]] == [None, 1.5]
    assert ответ["items"][1]["started_at"] == Т


def test_channel_of_other_section_is_404(monkeypatch):
    with pytest.raises(HTTPException) as e:
        вызвать(Соединение(False, []), monkeypatch)
    assert e.value.status_code == 404
