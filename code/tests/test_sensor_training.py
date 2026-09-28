"""Regression checks for MOS-264 replay contract, without running training."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest


@pytest.fixture
def training():
    path = Path(__file__).resolve().parents[2] / 'docs/proof/2026-09-28-sensor-model/train_sensor_model.py'
    spec = importlib.util.spec_from_file_location('sensor_training_review', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_import_does_not_read_data_or_train(training):
    assert not hasattr(training, 'C')
    assert not hasattr(training, 'run')


def test_failure_confirmation_is_strict_and_shared_with_runtime(training, monkeypatch):
    m = training
    start = 1_700_000_000
    for name, value in {'C': 1, 'F': {0: np.array([start])}, 'COL': ['a'], 'PK': [('a', 1)],
                        'GC': {'a': np.array([start])}, 'GP': {('a', 1): np.array([start])}}.items():
        monkeypatch.setattr(m, name, value, raising=False)
    times = np.array([start + 3599, start + 3600, start + 3601, start + 7200])
    x = m.real_features(np.zeros(len(times), dtype='int32'), times)
    assert x[:2, 0].tolist() == [0, 0]
    assert x[2:, 0].tolist() == [1, 0]
    assert np.all(x[:2, 3:6] == 0)
    assert np.all(x[2:, 3:6] > 0)
    for i, t in enumerate(times):
        at = m.datetime.fromtimestamp(int(t), m.MSK)
        event = m.datetime.fromtimestamp(start, m.MSK)
        runtime = m.sensor_risk.features([event], at)
        np.testing.assert_allclose(x[i], [runtime[n] for n in m.sensor_risk.REAL], atol=1e-7)


def test_label_and_event_matching_both_exclude_failure_at_decision(training, monkeypatch):
    m = training
    monkeypatch.setattr(m, 'C', 1, raising=False)
    channel, time = np.array([0]), np.array([10000])
    at_decision = {0: np.array([10000])}
    assert not m.labels(channel, time, at_decision, 24)[0]
    r = m.exact(channel, time, at_decision, 24)
    assert (r['tp'], r['fp'], r['fn']) == (0, 1, 1)
    next_second = {0: np.array([10001])}
    assert m.labels(channel, time, next_second, 24)[0]
    r = m.exact(channel, time, next_second, 24)
    assert (r['tp'], r['fp'], r['fn']) == (1, 0, 0)
    assert r['lead_under_24h_share'] == 1
    assert r['median_lead_hours'] == pytest.approx(1 / 3600)


def test_threshold_sits_between_tie_groups_not_on_mass_probability(training):
    p = np.array([0.01, 0.02, 0.3, 0.3])
    threshold = training.separated_threshold(p, 1)
    assert 0.02 < threshold < 0.3
    assert np.count_nonzero(p >= threshold) == 2
    assert np.array_equal(p >= threshold, (p - 1e-15) >= threshold)
    assert training.separated_threshold(np.full(5, 0.3), 1) == 0


def test_export_preserves_fresh_mass_probabilities_and_decisions(training, monkeypatch):
    m = training
    monkeypatch.setattr(m, 'kinds', ['gas'], raising=False)
    monkeypatch.setattr(m, 'KIND', np.array([0]), raising=False)
    model = m.Scaled()
    model.coef_ = np.array([1.876543210987654] + [0.0] * 8)
    model.intercept_ = -2.123456789012345
    features = np.zeros((3, len(m.sensor_risk.REAL)), dtype='float32')
    features[1:, 0] = 1
    D = m.Design(np.zeros(3, dtype='int32'), np.arange(3), np.zeros(3, dtype='int8'), features, 'real')
    mask = np.ones(3, bool)
    p = model.proba(D.rows(mask))
    high = m.separated_threshold(np.sort(p), 1)
    artifact = m.export(model, m.sensor_risk.REAL, 'real', high, 0)
    assert artifact['coef']['fresh'] == model.coef_[0]
    assert m.verify_export(model, artifact, D, mask)['passed']
    assert (m.exported_proba(artifact, D, mask) >= high).tolist() == [False, True, True]


def test_synthetic_features_do_not_accidentally_get_real_gate(training, monkeypatch):
    m = training
    monkeypatch.setattr(m, 'kinds', ['gas'], raising=False)
    monkeypatch.setattr(m, 'KIND', np.array([0]), raising=False)
    x = np.full((1, 8), 1.5, dtype='float32')  # even future feature-count collision is safe
    D = m.Design(np.array([0]), np.array([100]), np.array([0]), x, 'sim')
    np.testing.assert_array_equal(D.rows(np.array([True]))[0, :8], x[0])


def test_global_topk_has_equal_budget_and_stable_channel_ties(training, monkeypatch):
    m = training
    monkeypatch.setattr(m, 'C', 3, raising=False)
    monkeypatch.setattr(m, 'cid', np.array([30, 10, 20]), raising=False)
    mc = np.repeat(np.arange(3), 2)
    mt = np.tile(np.array([10000, 100000]), 3)
    D = SimpleNamespace(mc=mc, mt=mt)
    world = {1: np.array([10001, 100001])}
    result = m.ranked(D, np.ones(6, bool), np.ones(6), world, world, 24, budgets=(1, 2))
    assert result['1']['selected_rows'] == 2
    assert result['2']['selected_rows'] == 4
    assert result['1']['positive_selected_rows'] == 2
    assert result['1']['precision_at_k'] == 1
    assert result['2']['precision_at_k'] == 0.5
    assert result['1']['event_metrics']['tp'] == 2
