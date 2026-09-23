"""Independent real-model replay: no fake probabilities or generated feature inputs."""
import importlib.util
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from ml.local24_data import sha256
from ml.serving.local24_bag import Bag
from ml.serving.model_store import load_model

ROOT=Path(os.environ.get('LOCAL24_ROOT',Path(__file__).resolve().parents[2]))
DATA=ROOT/'data/03_processed/local24_20260923'
MODEL=Path(os.environ.get('LOCAL24_MODEL_DIR',ROOT/'models/local24_20260923'))
CODE=Path(__file__).resolve().parents[2]
pytestmark=pytest.mark.skipif(not (MODEL/'model_meta.json').exists() or not (DATA/'dataset.parquet').exists(),reason='real local24 model/data not yet available')
spec=importlib.util.spec_from_file_location('local24_score_under_test',CODE/'scripts/ml_local24_score.py')
scorer=importlib.util.module_from_spec(spec)
spec.loader.exec_module(scorer)


@pytest.fixture(scope='module')
def bundle():
    return load_model(MODEL)


@pytest.fixture(scope='module',params=['2026-05-15','2026-06-15'])
def replay(request,bundle):
    result=scorer.score(MODEL,DATA,request.param)
    offline=pd.read_parquet(DATA/'dataset.parquet',filters=[('as_of','==',pd.Timestamp(request.param))]).sort_values('section_id').reset_index(drop=True)
    if bundle.meta.get('engineering'):
        from ml.local24_engineering import transform
        from ml.local24_data import FEATURE_NAMES
        for name in FEATURE_NAMES:
            offline[name]=offline[name].astype('float32')
        offline=transform(offline,bundle.meta['engineering'])
    actual=pd.DataFrame(result['sections']).sort_values('section_id').reset_index(drop=True)
    return request.param,result,offline,actual


def test_strict_archive_prefix_equals_offline_vectors_and_probabilities(replay,bundle):
    day,result,offline,actual=replay
    # On these covered dates eligibility is identical, not merely an intersection.
    np.testing.assert_array_equal(actual.section_id.to_numpy(),offline.section_id.to_numpy())
    np.testing.assert_array_equal(actual.collector_id.to_numpy(),offline.collector_id.to_numpy())
    expected=offline[bundle.feature_names].to_numpy(dtype=np.float32)
    actual_x=np.array(actual.features.tolist(),dtype=np.float32)
    np.testing.assert_array_equal(actual_x,expected)
    expected_p=bundle.predict_proba(expected)
    np.testing.assert_array_equal(actual.p.to_numpy(),expected_p)
    assert result['horizon_h']==24 and result['object_level']=='section'
    assert result['feature_names']==bundle.feature_names
    assert result['explains_probability'] is False
    assert result['model_sha256']==bundle.sha256
    order=np.lexsort((actual.section_id.to_numpy(),-actual.p.to_numpy()))
    np.testing.assert_array_equal(actual['rank'].to_numpy()[order],np.arange(1,len(actual)+1))
    print(f'parity {day}: {len(actual)} exact keys, vectors/probabilities zero mismatches; {result["seconds"]:.3f}s')


def test_real_http_bundle_probability_and_horizon_contract(replay,bundle,monkeypatch):
    day,result,_,actual=replay
    monkeypatch.setenv('ML_MODEL_DIR',str(MODEL))
    from ml.serving.app import app
    sample=actual.head(12)
    body=dict(schema_version=bundle.meta['feature_schema'],run_id=1,computed_at=day+'T00:00:00+03:00',
              horizon_h=24,directions=['sensor_failure'],feature_names=bundle.feature_names,
              section_ids=sample.section_id.astype(int).tolist(),values=sample.features.tolist())
    with TestClient(app) as client:
        response=client.post('/predict',json=body)
        assert response.status_code==200,response.text
        response=response.json()
        np.testing.assert_array_equal(response['predictions'][0]['probability'],sample.p.to_numpy())
        assert response['contrib_check']['explains_probability'] is False
        assert response['contrib_check']['rows_over_tolerance']==0
        rejected=client.post('/predict',json={**body,'horizon_h':720})
        assert rejected.status_code==422
        assert rejected.json()['error']=='horizon_mismatch'


def test_sources_manifest_and_training_boundary_are_honest(bundle):
    meta=json.loads((DATA/'metadata.json').read_text())
    assert sha256(DATA/'mapping.parquet')==meta['mapping_sha256']
    for key in ('daily','episodes'):
        assert sha256(meta['sources'][key])==meta['source_sha256'][key]
    assert sha256(DATA/'dataset.parquet')==bundle.meta['data_sha256']
    assert meta['horizon_h']==24 and meta['origin'].startswith('2022-04-01')
    assert bundle.meta['holdout_precision'] is None and bundle.meta['holdout_recall'] is None
    assert bundle.meta['evaluation_kind']=='known_data_development_replay'
    fit=bundle.meta['fit']
    assert pd.Timestamp(fit['calibration_end'])+pd.Timedelta(hours=25)<pd.Timestamp(bundle.meta['valid_from'])
    with pytest.raises(ValueError,match='training boundary'):
        scorer.score(MODEL,DATA,'2026-01-01')
    with pytest.raises(ValueError,match='archive cutoff'):
        scorer.score(MODEL,DATA,'2026-07-01')
    with pytest.raises(ValueError,match='00:00'):
        scorer.score(MODEL,DATA,'2026-05-15T12:00:00')
    with pytest.raises(ValueError,match='next-24h'):
        Bag(MODEL,{**bundle.meta,'horizon_h':720})
