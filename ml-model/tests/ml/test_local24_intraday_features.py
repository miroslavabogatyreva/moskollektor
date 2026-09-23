"""Raw-journal prefix and extrema checks, plus explicit customer service-code rule."""
import os
from pathlib import Path
import tempfile

import duckdb
import numpy as np
import pandas as pd
import pytest

from ml.local24_intraday_features import (connection,install_mapping,numeric_daily,
    install_features,features_for_keys,FEATURE_NAMES)

ROOT=Path(os.environ.get('LOCAL24_ROOT',Path(__file__).resolve().parents[2]))
BASE=ROOT/'data/03_processed/local24_20260923'


@pytest.fixture(scope='module')
def real_numeric():
    if not (BASE/'mapping.parquet').exists():pytest.skip('real Local24 archive absent')
    mapping=pd.read_parquet(BASE/'mapping.parquet')
    daily=pd.read_parquet(ROOT/'data/03_processed/ml_20260915/chanday.parquet')
    counts=daily.groupby('ch').n_numeric.sum().rename('numeric_n')
    candidates=mapping.merge(counts,on='ch')
    # Exercise actual high-volume numeric channels for all three separate types.
    selected=pd.concat([candidates[(candidates.stype==kind)&candidates.section_id.notna()]
        .sort_values('numeric_n',ascending=False).head(2) for kind in ('Газовый датчик','Датчик температуры','ИБП')])
    return mapping[mapping.ch.isin(selected.ch)].copy(),[ROOT/f'data/02_interim/mk/parquet/j{y}.parquet' for y in (2025,2026)]


@pytest.mark.parametrize('day',['2025-05-15','2026-05-15'])
def test_real_raw_prefix_and_intraday_extrema(real_numeric,day):
    mapping,sources=real_numeric;t=pd.Timestamp(day)
    keys=mapping[['section_id']].drop_duplicates().assign(as_of=t)
    frames=[]
    for before in ('2026-07-01',day):
        con=connection()
        try:
            with tempfile.TemporaryDirectory(prefix='intraday-prefix-') as tmp:
                con.execute(f"SET temp_directory='{tmp}'")
                install_mapping(con,mapping)
                numeric_daily(con,sources,before,after='2025-04-01')
                daily=con.execute('SELECT * FROM numeric_daily ORDER BY ch,d').df()
                assert (pd.to_datetime(daily.d)<pd.Timestamp(before)).all()
                install_features(con,daily)
                frames.append(features_for_keys(con,keys))
                # Independently recompute min/max/std/IQR for an actual variable day.
                selected=daily[(daily.v_max>daily.v_min)&(pd.to_datetime(daily.d)<t)]
                assert len(selected)>0
                row=selected.sort_values('d').iloc[-1]
                con.register('source_day',pd.DataFrame({'ch':[row.ch],'d':[row.d]}))
                paths='['+','.join("'"+str(p)+"'" for p in sources)+']'
                raw=con.execute(f'''SELECT v FROM (SELECT DISTINCT j.ch,ts,ev,val,alarm,try_cast(val AS DOUBLE) AS v
                    FROM read_parquet({paths}) j JOIN source_day s ON j.ch=s.ch AND j.ts::DATE=s.d
                    WHERE isfinite(try_cast(val AS DOUBLE)))
                    WHERE NOT(v IN (-3276,-127,-100,255) AND NOT coalesce(alarm,false))''').df().v.to_numpy()
                np.testing.assert_allclose([row.v_min,row.v_max,row.v_var,row.v_iqr],
                    [raw.min(),raw.max(),raw.var(ddof=1),np.quantile(raw,.75)-np.quantile(raw,.25)],rtol=1e-10,atol=1e-10)
        finally:con.close()
    pd.testing.assert_frame_equal(*frames,check_exact=True)
    assert len(FEATURE_NAMES)==18
    assert np.isfinite(frames[0][FEATURE_NAMES].to_numpy()).all()


def test_service_codes_alarm_identity_and_constant_baseline(tmp_path):
    # Actual QA code semantics are independent of the training data distribution.
    mapping=pd.DataFrame({'ch':[1],'section_id':[7],'collector_id':[2],'stype':['Газовый датчик']})
    raw=pd.DataFrame({'ch':[1]*7,'ts':pd.to_datetime(['2025-05-01 01:00','2025-05-01 02:00',
        '2025-05-02 01:00','2025-05-02 02:00','2025-05-02 02:00','2025-05-02 02:00','2025-05-02 03:00']),
        'ev':[1,2,3,4,4,4,5],'val':['5','5','6','-127','-127','-127','255'],
        'alarm':[False,False,False,False,True,True,None]})
    path=tmp_path/'raw.parquet';raw.to_parquet(path,index=False)
    con=connection()
    try:
        install_mapping(con,mapping);stats=numeric_daily(con,[path],'2025-05-03')
        assert stats['removed_non_alarm_code_rows']==2
        assert stats['retained_alarm_code_rows']==2
        assert stats['retained_distinct_numeric_rows']==4
        daily=con.execute('SELECT * FROM numeric_daily ORDER BY d').df()
        assert daily.iloc[-1].v_min==-127 and daily.iloc[-1].alarm_code_n==1
        install_features(con,daily)
        values=features_for_keys(con,pd.DataFrame({'section_id':[7],'as_of':[pd.Timestamp('2025-05-03')]})).iloc[0]
        assert values.intra_gas_changed_constant_share==1
        assert values.intra_gas_range_z_max==0
        assert np.isclose(values.intra_gas_numeric_n_log1p,np.log(3))
    finally:con.close()
