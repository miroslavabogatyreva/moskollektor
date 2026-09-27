"""US-10: исход прогноза (POST /api/forecasts/{id}/outcome, миграция 056).

Базы не нужно: подставное соединение отвечает на запросы метода так, как ответил
бы PostgreSQL, и запоминает, что метод пытался записать.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.api import routes
from app.api.schemas import OutcomeIn

ДИСПЕТЧЕР = {"login": "dispatcher1", "roles": ["ods_dispatcher"]}
ИСХОДЫ = {"confirmed", "false_alarm", "not_checked"}
ПРИЧИНЫ = {"sensor_fault", "planned_works", "weather", "neighbour_work", "unknown"}


class Запрос:
    class state:
        pass


class Соединение:
    def __init__(self):
        self.записано: list[tuple] = []

    async def fetchval(self, sql, *args):
        if "FROM pred.forecast" in sql:
            return 7 if args[0] == 1 else None
        if "ref.forecast_outcome" in sql:
            return args[0] in ИСХОДЫ or None
        if "ref.feedback_reason" in sql:
            return args[0] in ПРИЧИНЫ or None
        if "INSERT INTO pred.forecast_outcome" in sql:
            self.записано.append(args)
            return len(self.записано)
        raise AssertionError(sql)

    async def fetchrow(self, sql, outcome_id):
        forecast_id, code, reason, by = self.записано[outcome_id - 1]
        return {
            "outcome_id": outcome_id,
            "outcome_code": code,
            "outcome_name": code,
            "reason_code": reason,
            "reason_name": reason,
            "decided_by": by,
            "decided_at": datetime(2026, 9, 27, tzinfo=timezone.utc),
        }


def отметить(conn, outcome_code, reason_code=None, forecast_id=1):
    return asyncio.run(
        routes.post_outcome(
            forecast_id=forecast_id,
            body=OutcomeIn(outcome_code=outcome_code, reason_code=reason_code),
            request=Запрос(),
            conn=conn,
            user=ДИСПЕТЧЕР,
        )
    )


def test_confirmed_is_written_by_session_login():
    conn = Соединение()
    ответ = отметить(conn, "confirmed")
    assert conn.записано == [(1, "confirmed", None, "dispatcher1")]
    assert ответ["decided_by"] == "dispatcher1"


def test_false_alarm_without_reason_is_422_and_not_written():
    conn = Соединение()
    with pytest.raises(HTTPException) as e:
        отметить(conn, "false_alarm")
    assert e.value.status_code == 422
    assert conn.записано == []


def test_false_alarm_with_reason_is_written():
    conn = Соединение()
    отметить(conn, "false_alarm", "weather")
    assert conn.записано == [(1, "false_alarm", "weather", "dispatcher1")]


def test_reason_only_for_false_alarm():
    with pytest.raises(HTTPException) as e:
        отметить(Соединение(), "confirmed", "weather")
    assert e.value.status_code == 422


def test_unknown_outcome_and_reason_are_422():
    for код, причина in (("fixed", None), ("false_alarm", "model_error")):
        with pytest.raises(HTTPException) as e:
            отметить(Соединение(), код, причина)
        assert e.value.status_code == 422


def test_missing_forecast_is_404():
    with pytest.raises(HTTPException) as e:
        отметить(Соединение(), "confirmed", forecast_id=2)
    assert e.value.status_code == 404


def test_horizon_expired_counts_from_as_of():
    сейчас = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
    as_of = сейчас - timedelta(hours=24)
    assert routes.горизонт_истёк(as_of, 23, сейчас) is True
    assert routes.горизонт_истёк(as_of, 24, сейчас) is False
    assert routes.горизонт_истёк(as_of, 720, сейчас) is False
