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


def test_horizon_expired_counts_from_issue_time():
    сейчас = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
    выдан = сейчас - timedelta(hours=24)
    assert routes.горизонт_истёк(выдан, 23, сейчас) is True
    assert routes.горизонт_истёк(выдан, 24, сейчас) is False
    assert routes.горизонт_истёк(выдан, 720, сейчас) is False


class Журнал:
    """Страница журнала из одной строки: прогноз посчитан час назад на срезе
    данных трёхмесячной давности — так на стенде, где край выгрузки 30.06.2026."""

    def __init__(self, сейчас):
        self.строка = {"forecast_id": 1, "section_id": 7, "direction": "channel",
                       "horizon_h": 720, "probability": 0.5, "risk_rank": 1,
                       "as_of": сейчас - timedelta(days=90),
                       "computed_at": сейчас - timedelta(hours=1), "write_reason": "change"}

    async def fetchval(self, sql, *args):
        return 1

    async def fetch(self, sql, *args):
        return [self.строка] if "FROM pred.forecast f" in sql else []


def test_horizon_counts_from_computed_at_not_data_slice():
    # Горизонт — окно после выдачи прогноза. as_of — срез данных: на стенде это край
    # архива, и от него 720 ч истекли у всех прогнозов, даже у посчитанных сегодня.
    ответ = asyncio.run(routes.list_forecasts(
        from_=None, to=None, section_id=None, limit=200, offset=0,
        conn=Журнал(datetime.now(timezone.utc)), user=ДИСПЕТЧЕР))
    assert ответ["items"][0]["horizon_expired"] is False
