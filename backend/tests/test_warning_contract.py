from datetime import timedelta
import json
import pytest
from app.worker import score_v3
from app.domain.order_rules import срок


def test_horizon_is_model_metadata_not_operator_default():
    score = {'horizon_h':720,'model_version':'collector-model'}
    assert score_v3.горизонт(score,score) == 720
    for model, requested in [({**score,'horizon_h':24},None),(score,24),({**score,'model_version':'other'},None)]:
        with pytest.raises(score_v3.ФайлНеГодится):
            score_v3.горизонт(score,model,requested)


def test_operational_due_uses_current_norm_from_original_onset():
    onset = score_v3.момент('2026-06-01T12:00:00')
    assert срок(onset,16) == onset + timedelta(hours=16)
    assert срок(onset,48) == onset + timedelta(hours=48)


def test_product_horizon_rejects_candidate_720_and_accepts_trained_24():
    candidate = {'horizon_h':720,'model_version':'candidate'}
    with pytest.raises(score_v3.ФайлНеГодится, match='MOS-219'):
        score_v3.горизонт_продукта(candidate,candidate)
    approved_shape = {'horizon_h':24,'model_version':'24h-test-fixture'}
    assert score_v3.горизонт_продукта(approved_shape,approved_shape) == 24


def test_score_rejects_mutated_expiry_and_duplicate_object_keys(tmp_path):
    data = {'schema_version':'score.v3','feature_schema':'feat.v3','object_level':'collector',
            'model_version':'m','as_of':'2026-06-02T00:00:00','horizon_h':720,'feature_names':['x'],
            'collectors':[{'collector_id':12,'p':0.2,'features':[1]}],
            'warnings':[{'collector_id':12,'opened_at':'2026-06-01T00:00:00',
                         'expires_at':'2026-07-01T00:00:00','probability':0.8,'features':[2]}]}
    p = tmp_path/'score.json'
    p.write_text(json.dumps(data))
    assert score_v3.прочитать(p)['warnings'][0]['probability'] == .8
    data['warnings'][0]['expires_at']='2026-06-02T00:00:00'
    p.write_text(json.dumps(data))
    with pytest.raises(score_v3.ФайлНеГодится):
        score_v3.прочитать(p)


def test_immutable_numeric_comparison_accepts_reduction_noise_only():
    from app.domain.order_rules import численно_равны
    assert численно_равны(0.81,0.8100000000000002)
    assert численно_равны(None,None)
    assert not численно_равны(None,0)
    assert not численно_равны(0.81,0.8101)


def test_warning_identity_normalizes_equivalent_timezone_representations():
    naive = score_v3.момент("2026-04-01T12:00:00")
    utc = score_v3.момент("2026-04-01T09:00:00+00:00")
    assert naive.isoformat() == utc.isoformat()
