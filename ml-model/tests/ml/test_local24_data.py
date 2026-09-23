"""Real-data invariants: truncation parity, exact target identity and causal riskset."""
import os
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from ml.local24_data import Local24Data, FEATURE_NAMES, CUTOFF

ROOT = Path(os.environ.get('LOCAL24_ROOT', Path(__file__).resolve().parents[2]))
MAP = ROOT/'data/03_processed/local24_20260923/mapping.parquet'
DAILY = ROOT/'data/03_processed/ml_20260915/chanday.parquet'
EPISODES = ROOT/'data/03_processed/faildef_20260915/D5/episodes_ext.parquet'
pytestmark = pytest.mark.skipif(not all(p.exists() for p in (MAP,DAILY,EPISODES)),reason='real local24 sources absent')


@pytest.fixture(scope='module')
def real_inputs():
    c=duckdb.connect()
    mapping=pd.read_parquet(MAP)
    c.register('m',mapping)
    # Deliberately include a real short episode crossing midnight: it must censor
    # the at-risk row even though its future duration never reaches one hour.
    crossing=c.execute('''SELECT m.section_id,e.* FROM read_parquet(?) e JOIN m USING(ch)
        WHERE m.section_id IS NOT NULL AND e.t_start>='2023-01-01' AND e.t_end>e.t_start
        AND e.t_end<=e.t_start+INTERVAL 1 HOUR
        AND e.t_start::DATE<e.t_end::DATE ORDER BY t_start LIMIT 1''',[str(EPISODES)]).df()
    assert len(crossing)==1,'real archive needs an overnight short episode'
    ids=c.execute('''SELECT m.section_id FROM read_parquet(?) e JOIN m USING(ch)
        WHERE m.section_id IS NOT NULL GROUP BY 1 ORDER BY count(*) DESC LIMIT 5''',[str(EPISODES)]).fetchnumpy()['section_id'].tolist()
    ids.append(int(crossing.iloc[0].section_id))
    selected=mapping[mapping.section_id.isin(ids)]
    c.register('selected',selected)
    daily=c.execute('SELECT d.* FROM read_parquet(?) d JOIN selected USING(ch)',[str(DAILY)]).df()
    ep=c.execute('SELECT e.* FROM read_parquet(?) e JOIN selected USING(ch)',[str(EPISODES)]).df()
    # Keep global coverage counts from the archive in the test. Extra unmapped
    # channels affect coverage only and are never admitted to section features.
    totals=c.execute('''SELECT -1 AS ch,d,sum(n) AS n,0 AS n_bad,0 AS n_undef,
        0 AS n_alarm,0 AS n_vals,0 AS n_obes,0 AS n_batt,0 AS n_numeric,
        NULL::DOUBLE AS val_num_last FROM read_parquet(?) GROUP BY d''',[str(DAILY)]).df()
    c.close()
    return selected,pd.concat([daily,totals],ignore_index=True),ep,crossing.iloc[0]


@pytest.mark.parametrize('as_of',['2025-05-15','2026-05-15'])
def test_full_archive_equals_strict_prefix(real_inputs,as_of):
    mapping,daily,ep,_=real_inputs
    t=pd.Timestamp(as_of)
    full=Local24Data(mapping,daily,ep)
    prefix_ep=ep[ep.t_start<t].copy()
    prefix_ep.loc[prefix_ep.t_end>=t,'t_end']=pd.NaT
    # Other episode fields (duration/lastvalue) deliberately remain present: the
    # feature code is allowed to read only start and observed close timestamps.
    prefix=Local24Data(mapping,daily[pd.to_datetime(daily.d)<t],prefix_ep,cutoff=t-pd.Timedelta(seconds=1))
    try:
        a=full.build_features(t)
        b=prefix.build_features(t)
        pd.testing.assert_frame_equal(a,b)
        assert list(a.columns)==['section_id','collector_id','as_of']+FEATURE_NAMES
    finally:
        full.close();prefix.close()


def test_actual_short_episode_excluded_before_its_future_close(real_inputs):
    mapping,daily,ep,event=real_inputs
    t=pd.Timestamp(event.t_end).normalize()
    builder=Local24Data(mapping,daily,ep)
    try:
        c=builder.riskset(t)
        row=c.execute('SELECT ongoing_d5 FROM riskset WHERE section_id=? AND as_of=?',[int(event.section_id),t]).fetchone()
        assert row==(True,)
        # The episode does not become a target merely by crossing midnight.
        assert c.execute('SELECT count(*) FROM events WHERE channel_id=? AND t_start=?',[int(event.ch),event.t_start]).fetchone()==(0,)
        with pytest.raises(ValueError,match='00:00'):
            builder.build_features(t+pd.Timedelta(hours=1))
    finally:
        builder.close()


def test_labels_count_exact_section_future_onsets_and_never_future_components(real_inputs):
    mapping,daily,ep,_=real_inputs
    builder=Local24Data(mapping,daily,ep)
    try:
        c=builder.riskset(CUTOFF.replace(hour=0,minute=0,second=0))
        mismatch=c.execute('''SELECT count(*) FROM riskset r WHERE n_events<>
            (SELECT count(*) FROM events e WHERE e.section_id=r.section_id
             AND e.t_start>r.as_of AND e.t_start<=r.as_of+INTERVAL 24 HOUR)''').fetchone()[0]
        assert mismatch==0
        assert c.execute('SELECT sum(y) FROM riskset').fetchone()[0]>0
        bad_known=c.execute('''SELECT count(*) FROM features f WHERE known_channels<>
            (SELECT count(DISTINCT cd.ch) FROM cd JOIN mapping m USING(ch)
             WHERE m.section_id=f.section_id AND cd.d<f.as_of::DATE)''').fetchone()[0]
        assert bad_known==0
        assert c.execute('''SELECT count(*) FROM riskset WHERE target_covered
            AND as_of+INTERVAL 25 HOUR>?''',[CUTOFF]).fetchone()[0]==0
    finally:
        builder.close()
