"""Contract arithmetic only; no fabricated data enters research or the database."""
import copy
import pytest
from app.worker.run_local24 import validate_score, validate_model, validate_prediction


def sample():
    return dict(schema_version='score.local24.v1',feature_schema='feat.local24.v1',
        object_level='section',horizon_h=24,timezone='Europe/Moscow',archive=True,
        model_version='test-local24',model_sha256='a'*64,feature_names=['confirmed_365d'],
        as_of='2026-06-15T00:00:00',data_watermark='2026-06-30T23:59:59',recommended_budget=10,
        sections=[dict(section_id=1,collector_id=10,rank=2,p=.01,features=[2.]),
                  dict(section_id=2,collector_id=10,rank=1,p=.02,features=[5.])])


def test_preserves_local_probabilities_and_timezone():
    data=sample();as_of,rows=validate_score(data)
    assert as_of.isoformat()=='2026-06-15T00:00:00+03:00'
    assert [r['p'] for r in rows]==[.01,.02]
    model={k:data[k] for k in ('model_version','feature_schema','object_level','horizon_h','feature_names')}
    model.update(sha256=data['model_sha256'],directions=['sensor_failure'])
    validate_model(data,model)
    reply=dict(schema_version='pred.v1',model_version=data['model_version'],model_sha256=data['model_sha256'],
               run_id=8,horizon_h=24,degraded=False,predictions=[dict(direction='sensor_failure',
               section_ids=[1,2],probability=[.01,.02],factors=[[dict(f='confirmed_365d',v=.5)],[dict(f='confirmed_365d',v=-.2)]])])
    factors,error=validate_prediction(data,rows,reply,8)
    assert error==0
    assert factors[0][0]['v']==2 and factors[0][0]['contribution']==.5
    reply['predictions'][0]['probability'][0]=.0101
    with pytest.raises(ValueError,match='probabilities differ'):
        validate_prediction(data,rows,reply,8)


@pytest.mark.parametrize('mutate',[
    lambda d:d.update(horizon_h=720),lambda d:d.update(object_level='collector'),
    lambda d:d.update(archive=False),lambda d:d['sections'][1].update(section_id=1),
    lambda d:d['sections'][1].update(rank=2),lambda d:d['sections'][1].update(p=float('nan')),
    lambda d:d['sections'][1].update(features=[]),lambda d:d.update(as_of='2026-06-15T12:00:00'),
    lambda d:d.update(feature_names=['x','x']),
    lambda d:d.update(as_of='2027-01-01T00:00:00'),
])
def test_rejects_mislabelled_or_corrupt_score(mutate):
    data=copy.deepcopy(sample());mutate(data)
    with pytest.raises(ValueError):validate_score(data)


def test_explicit_spread_disable_skips_weight_query(monkeypatch):
    import asyncio
    from app.worker import publish
    reached = []
    async def forbidden(_conn):
        raise AssertionError('Local probability must never consult spreading weights')
    class StopAtTransaction:
        def transaction(self):
            reached.append(True)
            raise RuntimeError('transaction reached without spread')
    monkeypatch.setattr(publish, 'веса_участков', forbidden)
    with pytest.raises(RuntimeError, match='transaction reached'):
        asyncio.run(publish.записать(StopAtTransaction(), 1, None, 24, 'sensor_failure',
                    [1,2], [.01,.01], [[],[]], spread_enabled=False,
                    политика_записи=dict(publish.ПОЛИТИКА_ПО_УМОЛЧАНИЮ)))
    assert reached == [True]
