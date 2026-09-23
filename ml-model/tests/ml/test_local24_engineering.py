"""Real riskset transformations: target blindness, permutation and time causality."""
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ml.local24_data import FEATURE_NAMES
from ml.local24_engineering import transform,MODES

ROOT=Path(os.environ.get('LOCAL24_ROOT',Path(__file__).resolve().parents[2]))
DATA=ROOT/'data/03_processed/local24_20260923/dataset.parquet'
pytestmark=pytest.mark.skipif(not DATA.exists(),reason='real local24 data absent')


@pytest.fixture(scope='module')
def rows():
    # Two complete actual eligible risksets, never just a convenient row sample.
    return pd.read_parquet(DATA,filters=[('as_of','in',[pd.Timestamp('2025-05-15'),pd.Timestamp('2025-06-15')])]).reset_index(drop=True)


@pytest.mark.parametrize('mode',MODES)
def test_real_rows_permutation_prefix_and_target_blindness(rows,mode):
    original=rows.copy(deep=True)
    actual=transform(rows,mode)
    names=[c for c in actual if c.startswith('eng_')]
    assert names and np.isfinite(actual[names].to_numpy()).all()
    pd.testing.assert_frame_equal(actual[rows.columns],original)
    pd.testing.assert_frame_equal(rows,original)
    permuted=transform(rows.sample(frac=1,random_state=42),mode).sort_index()
    pd.testing.assert_frame_equal(actual,permuted)
    first=rows[rows.as_of==rows.as_of.min()]
    prefix=transform(first.drop(columns=['y','n_events']),mode)
    pd.testing.assert_frame_equal(actual.loc[first.index,names],prefix[names])
    # Target values can change or disappear without any engineered input changing.
    target_changed=rows.assign(y=1-rows.y,n_events=rows.n_events+99)
    pd.testing.assert_frame_equal(actual[names],transform(target_changed,mode)[names])
    assert all(actual[n].dtype==np.dtype('float32') for n in names)


def test_context_uses_only_same_collector_actual_count_sums(rows):
    actual=transform(rows,'context')
    g=rows.groupby(['as_of','collector_id'])
    np.testing.assert_array_equal(actual.eng_collector_known_channels,
        g.known_channels.transform('sum').to_numpy(dtype=np.float32))
    np.testing.assert_array_equal(actual.eng_collector_sections,
        g.section_id.transform('size').to_numpy(dtype=np.float32))
    expected=(g.n_bad_7d.transform('sum')-rows.n_bad_7d)/np.maximum(
        g.known_channels.transform('sum')-rows.known_channels,1)
    np.testing.assert_array_equal(actual.eng_other_sections_bad_per_channel_7d,expected.astype('float32'))
    with pytest.raises(ValueError,match='Duplicate'):
        transform(pd.concat([rows,rows.iloc[[0]]]),'context')
    with pytest.raises(ValueError,match='Missing base'):
        transform(rows.drop(columns=[FEATURE_NAMES[0]]),'normalized')
