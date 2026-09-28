"""Causality and independent numerical checks for the MOS-225 ablation."""

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

spec = importlib.util.spec_from_file_location(
    "precision_experiment",
    Path(__file__).resolve().parents[2] / "scripts/mos225_precision.py",
)
exp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exp)


def test_past_sum_never_sees_current_or_future_day():
    source = np.array([[1], [2], [4], [8]], dtype=float)
    assert exp.past_sum(source, 2).ravel().tolist() == [0, 1, 3, 6]
    source[2:] = 10000
    assert exp.past_sum(source, 2)[:3].ravel().tolist() == [0, 1, 3]


def test_weighted_methane_and_future_invariance():
    days = pd.date_range("2025-01-01", periods=4, tz="Europe/Moscow")
    channels = pd.DataFrame(
        {"channel_id": [1, 2], "sensor_kind": ["Газовый датчик", "Другой"]}
    )
    rows = pd.DataFrame(
        {
            "channel_id": [1, 1, 1, 2],
            "day": ["2025-01-01", "2025-01-02", "2025-01-03", "2025-01-01"],
            "available_at": [
                "2025-01-02T00:00:00+03:00",
                "2025-01-03T00:00:00+03:00",
                "2025-01-04T00:00:00+03:00",
                "2025-01-02T00:00:00+03:00",
            ],
            "methane_valid": [2, 1, 1, 0],
            "methane_mean": [1, 4, 2, np.nan],
            "methane_ge1": [2, 1, 1, 0],
            "methane_invalid": [1, 0, 0, 0],
            "gas_alarm_text": [0, 1, 0, 0],
            "calibration_candidate": [0, 1, 0, 0],
            "epoch_marker": [0, 0, 0, 99],
        }
    )
    features, mapping, names = exp.reading_matrix(channels, days, rows)
    assert mapping == {1: 1}
    assert not features[:, 0].any()  # non-gas and epoch audit do not become errors
    assert features[2, 1, names.index("mean_7d")] == 2  # (2*1+1*4)/3
    assert features[2, 1, names.index("calibration_days_7d")] == 1
    assert not features[0].any()
    rows.loc[2, "methane_mean"] = 10000
    future, _, _ = exp.reading_matrix(channels, days, rows)
    assert np.array_equal(features[:3], future[:3])


def test_wrong_availability_is_rejected():
    days = pd.date_range("2025-01-01", periods=2, tz="Europe/Moscow")
    channels = pd.DataFrame({"channel_id": [1], "sensor_kind": ["Газовый датчик"]})
    frame = pd.DataFrame(
        {
            "channel_id": [1],
            "day": ["2025-01-01"],
            "available_at": ["2025-01-01T00:00:00+03:00"],
        }
    )
    with pytest.raises(AssertionError):
        exp.reading_matrix(channels, days, frame)


def test_paired_week_bootstrap_preserves_constant_effect():
    days = pd.date_range("2026-01-01", periods=90, tz="Europe/Moscow")
    result = exp.paired_ci(days, np.full(90, 0.2), np.zeros(90))
    assert result["difference"] == pytest.approx(0.2)
    assert result["ci95"] == pytest.approx([0.2, 0.2])
