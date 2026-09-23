"""Canonicalization v3 restores valid history without modifying frozen v2."""
import pandas as pd
import numpy as np
import pytest
from test_local24_sensor_features import real_sources,pytestmark
from ml.local24_sensor_features_v3 import SensorFeaturesV3


@pytest.mark.parametrize('day',['2025-05-15','2026-05-15'])
def test_v3_exact_prefix_after_censoring(real_sources,day):
    mapping,daily,ep=real_sources;t=pd.Timestamp(day)
    full=SensorFeaturesV3(mapping,daily,ep)
    clipped=ep[ep.t_start<t].copy();mask=clipped.t_end>=t
    clipped.loc[mask,'t_end']=pd.NaT;clipped.loc[mask,'close_val']=None
    prefix=SensorFeaturesV3(mapping,daily[pd.to_datetime(daily.d)<t],clipped,cutoff=t-pd.Timedelta(seconds=1))
    try:
        a=full.build_features(t);b=prefix.build_features(t)
        pd.testing.assert_frame_equal(a,b,check_exact=True)
        assert full.con.execute('SELECT count(*) FROM ep WHERE t_end<=t_start').fetchone()[0]==0
        assert (a.sensor_valid_short_count_28d>=a.sensor_valid_short_count_7d).all()
    finally:full.close();prefix.close()


def test_positive_short_counts_directly_match_canonical_known_closures(real_sources):
    mapping,daily,ep=real_sources;t=pd.Timestamp('2026-05-15')
    b=SensorFeaturesV3(mapping,daily,ep)
    try:
        actual=b.build_features(t)
        canonical=ep[ep.t_start<t].copy()
        future=canonical.t_end>=t
        canonical.loc[future,'t_end']=pd.NaT;canonical.loc[future,'close_val']=None
        canonical=canonical[canonical.t_end.isna() | (canonical.t_end>canonical.t_start)]
        canonical=canonical[['ch','t_start','t_end','close_val']].drop_duplicates()
        for w in (7,28):
            short=canonical[(canonical.t_end>=t-pd.Timedelta(days=w)) & (canonical.t_end<t)
                & (canonical.t_end<=canonical.t_start+pd.Timedelta(hours=1))]
            expected=short.merge(mapping[['ch','section_id']],on='ch').groupby('section_id').size()
            np.testing.assert_array_equal(actual[f'sensor_valid_short_count_{w}d'],actual.section_id.map(expected).fillna(0))
        assert b.con.execute('''SELECT count(*) FROM ep e JOIN episode_duplicates q USING(ch,t_start)
            WHERE e.t_end>e.t_start+INTERVAL 1 HOUR''').fetchone()[0]>0
    finally:b.close()


def test_real_unknown_ongoing_episode_is_retained(real_sources):
    mapping,daily,ep=real_sources
    candidates=ep[(ep.t_start>='2023-01-01') & (ep.t_end>ep.t_start+pd.Timedelta(days=2))]
    assert len(candidates)>0
    event=candidates.sort_values('t_start').iloc[0]
    t=event.t_start.normalize()+pd.Timedelta(days=1)
    clipped=ep[ep.t_start<t].copy();mask=clipped.t_end>=t
    clipped.loc[mask,'t_end']=pd.NaT;clipped.loc[mask,'close_val']=None
    prefix=SensorFeaturesV3(mapping,daily[pd.to_datetime(daily.d)<t],clipped,cutoff=t-pd.Timedelta(seconds=1))
    try:
        row=prefix.con.execute('SELECT t_end FROM ep WHERE ch=? AND t_start=?',[int(event.ch),event.t_start]).fetchall()
        assert row==[(None,)]
    finally:prefix.close()


def test_bounded_real_raw_journal_prefix_rebuild(real_sources):
    """Independent source-order check for actual channels with conflicting starts."""
    from pathlib import Path
    import tempfile
    import duckdb
    from test_local24_sensor_features import ROOT
    from ml import failure_defs as F
    mapping,daily,ep=real_sources
    duplicate=ep[ep.duplicated(['ch','t_start'],keep=False)]
    channels=sorted(set(duplicate.ch))[:2]
    assert len(channels)==2
    rawroot=ROOT/'data/02_interim/mk'
    if not (rawroot/'parquet/j2026.parquet').exists():pytest.skip('raw journal absent')
    def canonical(frame,t):
        out=frame.loc[frame.t_start<t,['ch','t_start','t_end','close_val']].copy()
        future=out.t_end>=t
        out.loc[future,'t_end']=pd.NaT;out.loc[future,'close_val']=None
        out=out[out.t_end.isna() | (out.t_end>out.t_start)].drop_duplicates()
        assert not out.duplicated(['ch','t_start']).any()
        return out.sort_values(['ch','t_start']).reset_index(drop=True)
    old=F.C.MK
    con=duckdb.connect();con.execute("SET threads=4;SET memory_limit='8GB'")
    try:
        F.C.MK=rawroot
        where='ch IN ('+','.join(str(int(x)) for x in channels)+')'
        with tempfile.TemporaryDirectory(prefix='sensor-raw-prefix-') as tmp:
            con.execute("SET temp_directory='"+tmp+"'")
            F.build_episodes(con,[F.EXT],chunks=1,ch_where=where,ts_to='2026-06-30 23:59:59',log=lambda _:None)
            full=con.execute('SELECT * FROM ep_0').df()
            assert len(full)>0
            for day in ('2025-05-15','2026-05-15'):
                t=pd.Timestamp(day)
                F.build_episodes(con,[F.EXT],chunks=1,ch_where=where,
                    ts_to=str(t-pd.Timedelta(seconds=1)),log=lambda _:None)
                prefix=con.execute('SELECT * FROM ep_0').df()
                pd.testing.assert_frame_equal(canonical(full,t),canonical(prefix,t),check_exact=True)
    finally:
        F.C.MK=old;con.close()
