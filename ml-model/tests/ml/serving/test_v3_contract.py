"""Regression checks on released v3 weights and actual archived feature rows."""
import json
import sys
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
from ml import v3_score as S
from ml.serving import app as A
from ml.serving.model_store import ModelLoadError, load_model

MODEL = ROOT / "models/v3_20260921"
REF = ROOT / "data/03_processed/v3_final_20260920"
pytestmark = pytest.mark.skipif(not MODEL.exists() or not REF.exists(), reason="real v3 weights/data absent")


@pytest.fixture(scope="module")
def real_rows(isolated_v3_code):
    p, t = S.load_code()
    rows = S.feature_rows(p, t, REF, 720)
    return rows.groupby("kind").tail(1)


@pytest.fixture(scope="module")
def model():
    return load_model(MODEL)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ML_MODEL_DIR", str(MODEL))
    with TestClient(A.app) as client:
        yield client


def request(model, rows):
    return dict(schema_version="feat.v3", run_id=1, horizon_h=720,
                directions=["sensor_failure"], feature_names=model.feature_names,
                section_ids=rows.pfx.astype(int).tolist(),
                values=rows[model.feature_names].replace({np.nan: None}).values.tolist())


def test_v3_enforces_real_horizon(client, model, real_rows):
    body = request(model, real_rows)
    body["horizon_h"] = 24
    response = client.post("/predict", json=body)
    assert response.status_code == 422
    assert response.json()["error"] == "horizon_mismatch"
    assert "720" in response.json()["detail"]
    body["horizon_h"] = 720
    response = client.post("/predict", json=body)
    assert response.status_code == 200
    assert response.json()["horizon_h"] == 720
    want = model.predict_proba(real_rows[model.feature_names].to_numpy(dtype=float))
    np.testing.assert_allclose(response.json()["predictions"][0]["probability"], want, atol=5e-7)
    assert client.get("/model").json()["horizon_h"] == 720


def test_explanation_explicitly_excludes_full_ensemble(client, model, real_rows):
    response = client.post("/predict", json=request(model, real_rows)).json()
    check = response["contrib_check"]
    assert check["explanation_scope"] == "mean_tree_logit"
    assert check["explains_probability"] is False
    assert "rearm" in check["limitation"] and "Platt" in check["limitation"]
    assert check["rows_over_tolerance"] == 0
    meta = client.get("/model").json()
    assert meta["explanation_scope"] == check["explanation_scope"]
    assert meta["explains_probability"] is False
    x = real_rows[model.feature_names].to_numpy(dtype=float)
    raw, contrib = model.contributions(x)
    np.testing.assert_allclose(raw, contrib.sum(axis=1), atol=1e-8)
    assert not np.allclose(1 / (1 + np.exp(-raw)), model.predict_proba(x))


def test_v3_missing_horizon_is_not_loadable(tmp_path, model):
    meta = dict(model.meta)
    del meta["horizon_h"]
    (tmp_path / "model_meta.json").write_text(json.dumps(meta))
    with pytest.raises(ModelLoadError, match="horizon_h"):
        load_model(tmp_path)
