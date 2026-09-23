"""Arithmetic/boundary unit fixtures only; these are not model evaluation data."""
import json

import pandas as pd
import pytest

from ml.local24_eval import evaluate_predictions


def fixture():
    rows = pd.DataFrame([
        [1, 10, "2025-04-01", 1, 2],
        [2, 10, "2025-04-01", 0, 0],
        [3, 20, "2025-04-01", 1, 1],
        [1, 10, "2025-04-02", 0, 0],
        [2, 10, "2025-04-02", 1, 1],
    ], columns=["section_id", "collector_id", "as_of", "y", "n_events"])
    scores = rows[["section_id", "as_of"]].copy()
    scores["score"] = [.5, .5, .2, 0, 1]
    events = pd.DataFrame([
        ["history", 1, 10, "2025-03-30 10:00"],
        ["a", 1, 10, "2025-04-01 00:01"],
        ["b", 1, 10, "2025-04-02 00:00"],
        ["c", 3, 20, "2025-04-01 01:00"],
        ["d", 2, 10, "2025-04-02 01:00"],
        ["excluded", 7, 30, "2025-04-01 05:00"],
    ], columns=["event_id", "section_id", "collector_id", "t_start"])
    return rows, scores, events


def test_exact_budgets_ties_boundaries_and_distinct_denominators():
    rows, scores, events = fixture()
    got = evaluate_predictions(rows, scores.sample(frac=1, random_state=3), events=events,
                               budgets=(1, 10), forecast_days=pd.date_range("2025-04-01", periods=3))
    one = got["budgets"]["1"]
    assert got["n_days"] == 3
    assert one["alerts"] == one["hit_section_days"] == 2
    assert one["precision"] == 1
    assert one["section_day_recall"] == pytest.approx(2 / 3)
    assert one["matched_events"] == 3
    assert one["event_recall_eligible"] == .75
    assert one["event_recall"] == one["event_coverage"] == .6
    assert one["event_recall_one_to_one"] == .4
    assert one["matched_events_one_to_one"] == 2
    assert one["total_events"] == 5 and one["excluded_risk_events"] == 1
    assert one["alerts_per_forecast_day"] == pytest.approx(2 / 3)
    assert one["daily"][2]["alerts"] == 0
    assert one["by_event_kind"] == [
        {"kind": "first_observed", "events": 3, "eligible_events": 2, "matched_events": 1, "event_recall": 1 / 3, "event_coverage": 1 / 3, "matched_events_one_to_one": 1, "event_recall_one_to_one": 1 / 3},
        {"kind": "repeat", "events": 2, "eligible_events": 2, "matched_events": 2, "event_recall": 1.0, "event_coverage": 1.0, "matched_events_one_to_one": 1, "event_recall_one_to_one": .5},
    ]
    full = got["budgets"]["10"]
    assert full["alerts"] == 5  # min(K, eligible), not K times calendar days
    assert full["precision"] == .6
    assert full["event_recall"] == .8
    assert full["by_collector"][-1]["total_events"] == 1
    json.dumps(got, allow_nan=False)


def test_global_precision_not_unweighted_mean_daily_precision():
    rows, scores, _ = fixture()
    got = evaluate_predictions(rows, scores, budgets=(10,))["budgets"]["10"]
    assert got["precision"] == 3 / 5
    assert got["precision"] != (2 / 3 + 1 / 2) / 2
    assert got["event_recall"] is None


@pytest.mark.parametrize("problem", ["missing", "extra", "duplicate", "nan", "labels", "events", "event_duplicate", "collector"])
def test_contract_failures_are_not_silently_dropped(problem):
    rows, scores, events = fixture()
    if problem == "missing":
        scores = scores.iloc[:-1]
    elif problem == "extra":
        scores.loc[len(scores)] = [999, "2025-04-01", .2]
    elif problem == "duplicate":
        scores = pd.concat([scores, scores.iloc[:1]])
    elif problem == "nan":
        scores.loc[0, "score"] = float("nan")
    elif problem == "labels":
        rows.loc[0, "y"] = 0
    elif problem == "events":
        events = events[events.event_id != "a"]
    elif problem == "event_duplicate":
        events = pd.concat([events, events.iloc[:1]])
    else:
        events.loc[events.event_id == "a", "collector_id"] = 99
    with pytest.raises(ValueError):
        evaluate_predictions(rows, scores, events=events)


def test_empty_riskset_day_is_kept_and_does_not_mean_perfect_precision():
    rows, scores, events = fixture()
    got = evaluate_predictions(rows.iloc[:0], scores.iloc[:0], events=events,
                               forecast_days=["2025-04-01"], budgets=(5,))["budgets"]["5"]
    assert got["alerts"] == 0 and got["precision"] is None
    assert got["event_recall"] == 0
    assert got["event_recall_eligible"] is None


def test_day_bootstrap_is_deterministic_and_finite():
    rows, scores, events = fixture()
    kwargs = dict(events=events, budgets=(1,), bootstrap_samples=50, seed=4)
    a = evaluate_predictions(rows, scores, **kwargs)
    b = evaluate_predictions(rows, scores, **kwargs)
    assert a == b
    assert a["budgets"]["1"]["day_bootstrap_95"]["precision"]["low"] == 1
    json.dumps(a, allow_nan=False)
