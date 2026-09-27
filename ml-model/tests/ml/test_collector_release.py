"""Customer-tree identity and immutable warning facts on real release artifacts."""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from ml import collector_identity as I, v3_score as S
from ml.serving.model_store import load_model

DATA = ROOT / 'data/03_processed/v3_collector_20260922'
MODEL = ROOT / 'models/v3_collector_20260922'
pytestmark = pytest.mark.skipif(not (MODEL / 'model_meta.json').exists(), reason='real collector release not built')


@pytest.fixture(scope='module')
def evidence(isolated_v3_code):
    model = load_model(MODEL)
    prepare, train = S.load_code()
    rows = S.feature_rows(prepare, train, DATA, 720)
    rows = rows[rows.t >= pd.Timestamp('2026-04-01')].reset_index(drop=True)
    rows['p'] = model.predict_proba(rows[model.feature_names].to_numpy(dtype=float))
    inc = pd.read_parquet(DATA / 'incidents.parquet')
    return model, prepare, rows, inc


def test_identity_is_exact_customer_parent_mapping():
    channels, objects = pd.read_csv(I.CHANNELS), pd.read_csv(I.OBJECTS)
    expected = channels.set_index('ид_канала_данных')['ид_объект'].map(objects.set_index('ид_объект')['родитель']).astype(int)
    got = pd.read_parquet(MODEL / 'channels.parquet').set_index('ch').pfx.astype(int)
    assert len(got) == 11485
    pd.testing.assert_series_equal(got.sort_index(), expected.sort_index(), check_names=False)
    assert got.nunique() == 16


def test_registry_binding_restores_original_path():
    from ml import config as C
    before = C.CHAN
    with I.registry_context(MODEL / 'channels.parquet'):
        assert C.CHAN == str((MODEL / 'channels.parquet').resolve())
    assert C.CHAN == before


def test_warning_history_preserves_opening_probability_and_features(evidence):
    model, prepare, rows, inc = evidence
    alerts = prepare.simulate(rows, inc, model.meta['alert_threshold'], 720)
    end = pd.Timestamp('2026-06-30 23:59:59')
    warnings = S.warning_history(alerts, rows, inc, end, model.meta)
    assert len(warnings) == 176
    assert len({w['warning_id'] for w in warnings}) == len(warnings)
    assert {'open', 'closed', 'expired'} <= {w['status'] for w in warnings}
    for w in warnings:
        opened = pd.Timestamp(w['opened_at'])
        source = rows[(rows.pfx == str(w['collector_id'])) & (rows.t == opened)].iloc[0]
        assert w['probability'] == source.p
        np.testing.assert_allclose(np.array(w['features'], dtype=float), source[model.feature_names].to_numpy(dtype=float), equal_nan=True)
        assert pd.Timestamp(w['expires_at']) - opened == pd.Timedelta(hours=720)
        if w['closed_at'] is not None:
            assert opened < pd.Timestamp(w['closed_at']) <= end
    early = pd.Timestamp('2026-05-15 12:00:00')
    early_rows = rows[rows.t <= early]
    early_alerts = prepare.simulate(early_rows, inc[inc.t_start + pd.Timedelta(hours=1) <= early], model.meta['alert_threshold'], 720)
    old = S.warning_history(early_alerts, early_rows, inc, early, model.meta)
    by_id = {w['warning_id']: w for w in warnings}
    for w in old:
        later = by_id[w['warning_id']]
        for key in ('collector_id', 'opened_at', 'expires_at', 'probability', 'features'):
            assert later[key] == w[key]


def test_training_and_threshold_do_not_consume_Q2():
    meta = json.loads((MODEL / 'model_meta.json').read_text())
    assert meta['object_level'] == 'collector' and meta['horizon_h'] == 720
    assert len(meta['boosters']) == 25
    assert pd.Timestamp(meta['train_until']) < pd.Timestamp('2026-03-02')
    donors = meta['threshold_selection']['donors']
    assert len(donors) == 4
    assert all(pd.Timestamp(d['score_max']) < pd.Timestamp('2026-04-01') for d in donors)
    assert meta['holdout_precision'] is None and meta['holdout_recall'] is None
    assert 'previously seen' in meta['evaluation_status']


@pytest.mark.parametrize('date', ['2026-07-01', '2021-12-31', 'NaT', '2026-06-30T00:00:00Z'])
def test_rejects_dates_outside_actual_input_coverage(date):
    with pytest.raises(ValueError, match='(latest journal|supported feature|archive-local)'):
        S.score(date, model_dir=MODEL, log=lambda m: None)
