"""Real channel-history causality and aggregation checks; no synthetic episodes."""
import os
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest

from ml.local24_sensor_features import SensorFeatures,TYPES

ROOT=Path(os.environ.get('LOCAL24_ROOT',Path(__file__).resolve().parents[2]))
BASE=ROOT/'data/03_processed/local24_20260923'
DAILY=ROOT/'data/03_processed/ml_20260915/chanday.parquet'
EPISODES=ROOT/'data/03_processed/faildef_20260915/D5/episodes_ext.parquet'
pytestmark=pytest.mark.skipif(not all(p.exists() for p in (BASE/'mapping.parquet',DAILY,EPISODES)),reason='real sources absent')


@pytest.fixture(scope='module')
def real_sources():
    c=duckdb.connect();m=pd.read_parquet(BASE/'mapping.parquet');c.register('m',m)
    top=c.execute('''SELECT section_id FROM read_parquet(?) e JOIN m USING(ch)
        WHERE section_id IS NOT NULL GROUP BY 1 ORDER BY count(*) DESC LIMIT 3''',[str(EPISODES)]).fetchdf().section_id.tolist()
    for kind in ('Газовый датчик','Датчик температуры','ИБП'):
        ids=c.execute('''SELECT section_id FROM read_parquet(?) d JOIN m USING(ch)
            WHERE section_id IS NOT NULL AND stype=? AND val_num_last IS NOT NULL
            GROUP BY 1 ORDER BY count(*) DESC LIMIT 2''',[str(DAILY),kind]).fetchdf().section_id.tolist()
        top+=ids
    m=m[m.section_id.isin(top)];c.register('chosen',m)
    daily=c.execute('SELECT d.* FROM read_parquet(?) d JOIN chosen USING(ch)',[str(DAILY)]).df()
    ep=c.execute('SELECT e.* FROM read_parquet(?) e JOIN chosen USING(ch)',[str(EPISODES)]).df()
    c.close();return m,daily,ep


@pytest.mark.parametrize('day',['2025-05-15','2026-05-15'])
def test_sensor_features_equal_strict_prefix(real_sources,day):
    m,daily,ep=real_sources;t=pd.Timestamp(day)
    full=SensorFeatures(m,daily,ep)
    prefix_ep=ep[ep.t_start<t].copy()
    mask=prefix_ep.t_end>=t
    prefix_ep.loc[mask,'t_end']=pd.NaT
    prefix_ep.loc[mask,'close_val']=None
    prefix=SensorFeatures(m,daily[pd.to_datetime(daily.d)<t],prefix_ep,cutoff=t-pd.Timedelta(seconds=1))
    try:
        a=full.build_features(t);b=prefix.build_features(t)
        pd.testing.assert_frame_equal(a,b,check_exact=True)
        assert all(x.startswith('sensor_') for x in a.columns[2:])
        assert np.isfinite(a.iloc[:,2:].to_numpy()).all()
        assert a.filter(like='_hhi_').to_numpy().min()>=0
        assert a.filter(like='_hhi_').to_numpy().max()<=1
        assert a.filter(like='numeric_change_max').to_numpy().max()<=10
        np.testing.assert_array_equal(a.sensor_bad_channels_7d,
            a[[f'sensor_{kind}_bad_channels_7d' for kind in TYPES]].sum(axis=1))
    finally:
        full.close();prefix.close()


def test_closed_durations_and_chunk_boundaries_match_known_history(real_sources):
    m,daily,ep=real_sources;t=pd.Timestamp('2026-05-15')
    builder=SensorFeatures(m,daily,ep)
    try:
        once=builder.build_features(t)
        month=builder.features('2026-05-01','2026-05-31')
        pd.testing.assert_frame_equal(once,month[month.as_of==t].reset_index(drop=True),check_exact=True)
        c=builder.con
        assert c.execute('''SELECT count(*) FROM ep JOIN episode_duplicates USING(ch,t_start)''').fetchone()[0]==0
        expected=c.execute('''SELECT m.section_id,max(date_diff('second',e.t_start,e.t_end)/3600.0)::FLOAT duration
            FROM ep e JOIN mapping m USING(ch)
            WHERE e.t_end>=? AND e.t_end<? GROUP BY 1''',[t-pd.Timedelta(days=28),t]).df()
        joined=once.merge(expected,on='section_id',how='left').fillna({'duration':0})
        np.testing.assert_array_equal(joined.sensor_closed_duration_max_h_28d,joined.duration)
        assert len(once)>0
    finally:
        builder.close()
