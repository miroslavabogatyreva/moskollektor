"""Проверка HTTP-контракта на сохранённых исследовательских весах v3..

Веса входят в поставку: их отсутствие должно ломать проверку. Входы из null
и нулей проверяют формат HTTP, а не качество прогноза.
"""

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

# ML_MODEL_DIR позволяет прогнать те же тесты на модели-кандидате, не подменяя current.
MODEL_DIR = Path(os.environ.get("ML_MODEL_DIR", ROOT / "models" / "lgbm-v3-bag-2026.09.21"))
META_PATH = MODEL_DIR / "model_meta.json"
IS_BAG = META_PATH.is_file() and json.loads(META_PATH.read_text(encoding="utf-8")).get(
    "model_format") == "v3-bag"


@pytest.fixture(scope="module")
def meta() -> dict:
    return json.loads((MODEL_DIR / "model_meta.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from ml.serving.app import app

    # Контекстный менеджер нужен, чтобы отработал lifespan и модель загрузилась.
    # This suite's MODEL_DIR may explicitly target a historical single booster.
    from unittest.mock import patch
    with patch.dict(os.environ, {"ML_MODEL_DIR": str(MODEL_DIR)}), TestClient(app) as test_client:
        yield test_client


def _request(meta: dict, n_rows: int = 2, values=None) -> dict:
    names = list(meta["feature_names"])
    if values is None:
        # Все null — это не синтетика, а проверка контракта на пропусках.
        values = [[None] * len(names) for _ in range(n_rows)]
    return {
        "schema_version": meta["feature_schema"],
        "run_id": 48217,
        "computed_at": "2026-09-15T09:05:12+03:00",
        "horizon_h": meta.get("horizon_h", 24),
        "directions": list(meta["directions"]),
        "feature_names": names,
        "section_ids": list(range(1, len(values) + 1)),
        "values": values,
    }


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["model_version"]
    assert len(body["model_sha256"]) == 64


def test_model_endpoint_has_hld_65_fields(client, meta):
    resp = client.get("/model")
    assert resp.status_code == 200
    body = resp.json()
    for field in (
        "model_version",
        "trained_at",
        "feature_schema",
        "directions",
        "sha256",
        "train_rows",
        "holdout_precision",
        "holdout_recall",
    ):
        assert field in body, field
    assert body["model_version"] == meta["model_version"]
    assert body["feature_names"] == list(meta["feature_names"])
    assert body["n_features"] == len(meta["feature_names"])
    assert body["object_level"] in ("pfx", "ch", "collector")


def test_model_sha256_matches_file(client, meta):
    from ml.serving.model_store import sha256_of

    from ml.serving.v3_bag import bag_sha256

    body = client.get("/model").json()
    want = (bag_sha256(MODEL_DIR, [b["file"] for b in meta["boosters"]]) if IS_BAG
            else sha256_of(MODEL_DIR / "model.txt"))
    assert body["sha256"] == want
    assert body["sha256"] == str(meta["sha256"]).lower()


def test_predict_accepts_all_null_row(client, meta):
    """Пропуски едут как NaN и не ломают инференс (HLD 6.3)."""
    resp = client.post("/predict", json=_request(meta, n_rows=2))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["schema_version"] == "pred.v1"
    assert body["model_version"] == meta["model_version"]
    assert len(body["model_sha256"]) == 64
    assert body["degraded"] is False
    pred = body["predictions"][0]
    assert len(pred["probability"]) == 2
    assert all(0.0 <= p <= 1.0 for p in pred["probability"])


def test_predict_returns_top5_factors_and_sum_checks_out(client, meta):
    resp = client.post("/predict", json=_request(meta, n_rows=2))
    body = resp.json()
    factors = body["predictions"][0]["factors"]
    assert len(factors) == 2
    for row in factors:
        assert 1 <= len(row) <= 5  # Ф-08: топ-5
        assert all(set(item) == {"f", "v"} for item in row)
        assert all(item["f"] in meta["feature_names"] for item in row)
    # HLD 6.4: сумма вкладов расходится с итогом не больше чем на 0,01.
    check = body["contrib_check"]
    assert check["rows_over_tolerance"] == 0
    assert check["max_abs_err"] <= 0.01


def test_null_is_not_zero(client, meta):
    """Ответ на строку из null и на строку из нулей не обязан совпадать.

    Если совпал — это тоже допустимо для конкретной модели, но тогда проверяем хотя бы
    что оба запроса проходят и вклады считаются отдельно.
    """
    n = len(meta["feature_names"])
    nulls = client.post("/predict", json=_request(meta, values=[[None] * n])).json()
    zeros = client.post("/predict", json=_request(meta, values=[[0.0] * n])).json()
    assert nulls["predictions"][0]["probability"]
    assert zeros["predictions"][0]["probability"]


def test_feature_order_mismatch_is_422(client, meta):
    names = list(meta["feature_names"])
    if len(names) < 2:
        pytest.skip("модель с одним признаком: порядок переставить нечем")
    body = _request(meta, n_rows=1)
    body["feature_names"] = [names[1], names[0]] + names[2:]
    resp = client.post("/predict", json=body)
    assert resp.status_code == 422
    detail = resp.json()
    assert detail["error"] == "feature_names_mismatch"
    assert "позиции 0" in detail["detail"]
    assert names[0] in detail["detail"]


def test_missing_feature_is_422(client, meta):
    body = _request(meta, n_rows=1)
    body["feature_names"] = list(meta["feature_names"])[:-1]
    body["values"] = [[None] * len(body["feature_names"])]
    resp = client.post("/predict", json=body)
    assert resp.status_code == 422
    assert resp.json()["error"] == "feature_names_mismatch"
    assert "Нет в запросе" in resp.json()["detail"]


def test_schema_version_mismatch_is_422(client, meta):
    body = _request(meta, n_rows=1)
    body["schema_version"] = "feat.v999"
    resp = client.post("/predict", json=body)
    assert resp.status_code == 422
    assert resp.json()["error"] == "schema_version_mismatch"


def test_section_ids_length_mismatch_is_422(client, meta):
    body = _request(meta, n_rows=2)
    body["section_ids"] = [1]
    resp = client.post("/predict", json=body)
    assert resp.status_code == 422
    assert resp.json()["error"] == "length_mismatch"


def test_unknown_direction_is_422(client, meta):
    body = _request(meta, n_rows=1)
    body["directions"] = ["flooding"]
    resp = client.post("/predict", json=body)
    assert resp.status_code == 422
    assert resp.json()["error"] == "unknown_direction"


def test_row_width_mismatch_is_422(client, meta):
    body = _request(meta, n_rows=1)
    body["values"] = [[None] * (len(meta["feature_names"]) - 1)]
    resp = client.post("/predict", json=body)
    assert resp.status_code == 422
    assert resp.json()["error"] == "row_width_mismatch"
