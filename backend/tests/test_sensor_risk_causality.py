"""Срез не знает длительность нового эпизода или будущую поверку (MOS-264)."""

import asyncio
from datetime import date, datetime, timedelta

import pytest
from app.domain import failure_sim, sensor_risk
from app.worker import sensor_scores

AT = datetime(2026, 6, 22, 21, tzinfo=sensor_risk.MSK)


@pytest.mark.parametrize("age_seconds", [0, 3599, 3600])
def test_future_qualified_episode_is_unavailable(age_seconds):
    start = AT - timedelta(seconds=age_seconds)
    # Архив уже знает, что эпизод продлится три часа; в этом срезе этого знания нет.
    assert sensor_risk.features([start], AT) == sensor_risk.features([], AT)
    assert sensor_risk.score([start], None, AT) == sensor_risk.score([], None, AT)


def test_confirmed_episode_retains_onset_age():
    start = AT - timedelta(seconds=3601)
    x = sensor_risk.features([start], AT)
    assert x["_last"] == start
    assert x["_days"] == pytest.approx(3601 / 86400)
    assert x["_n"] == (1, 1, 1)


def test_worker_does_not_count_unconfirmed_neighbor():
    class Conn:
        async def fetch(self, query, *args):
            if query == sensor_scores.ОТКАЗЫ:
                return [
                    {
                        "channel_id": 1,
                        "started_at": AT - timedelta(minutes=30),
                        "object_id": 10,
                        "sensor_kind": "Газовый датчик",
                        "collector": "test",
                        "picket": 1,
                    }
                ]
            if query == sensor_scores.КАНАЛЫ:
                return [
                    {
                        "channel_id": c,
                        "object_id": 10,
                        "sensor_kind": "Газовый датчик",
                        "collector": "test",
                        "picket": 1,
                        "eq_id": None,
                        "in_service_from": None,
                        "service_life_years": None,
                        "object_kind": None,
                    }
                    for c in (1, 2)
                ]
            return []

    rows = asyncio.run(sensor_scores.баллы(Conn(), AT))
    healthy = sensor_risk.rule_split([], None, AT)
    # Даже если источник вернул ещё неподтверждённый эпизод, он не влияет ни на
    # собственный канал, ни на соседа. Второй канал не имеет своих эпизодов.
    assert all(r[2] == healthy["score_real"] for r in rows)


def test_future_check_cannot_rewrite_observed_precursors():
    start, end = date(2026, 6, 17), AT.date()
    beta, eta, interval, mult = failure_sim.params_of(
        {"object_kind": "DEGD", "life": 10}
    )
    args = (435, date(2020, 1, 1), beta, eta, start, end)
    before = failure_sim.channel(*args, interval=interval, mult=mult)
    after = failure_sim.channel(
        *args, checks=[date(2026, 6, 23)], interval=interval, mult=mult
    )

    def observed(result):
        return [(t, true) for t, true in result[1] if t <= AT]

    assert observed(before) == observed(after)


def test_simulation_window_matches_immutable_full_timeline():
    args = (77, date(2020, 1, 1), 1.0, 30.0)
    checks = [date(2026, 2, 1), date(2026, 5, 5), date(2026, 7, 1)]
    full = failure_sim.channel(
        *args, date(2026, 1, 1), date(2026, 12, 31), checks, 60, 3
    )
    lo, hi = date(2026, 5, 1), date(2026, 5, 31)
    part = failure_sim.channel(*args, lo, hi, checks, 60, 3)
    assert part[0] == [t for t in full[0] if lo <= t.date() <= hi]
    assert part[1] == [(t, true) for t, true in full[1] if lo <= t.date() <= hi]


def test_true_warning_schedules_future_synthetic_failure():
    start, end = date(2026, 1, 1), date(2026, 6, 30)
    fails, precursors = failure_sim.channel(7, date(2020, 1, 1), 1.0, 10.0, start, end)
    assert fails and precursors
    for t, true in precursors:
        if true and (t + timedelta(days=failure_sim.PF_DAYS)).date() <= end:
            assert t + timedelta(days=failure_sim.PF_DAYS) in fails
