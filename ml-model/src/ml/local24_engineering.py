"""Optional causal feature transforms for the frozen local24 riskset.

No target columns are read. Context modes REQUIRE every eligible section at each
as_of, before training sampling, feature selection or request batching. Serving
must transform the same complete past-only riskset, then select/request rows.
These transforms neither alter eligibility nor the 24h outcome/protocol.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .local24_data import FEATURE_NAMES

MODES = ('normalized', 'context', 'combined')
COUNTS = ('n_rows', 'n_alarm', 'n_bad', 'n_undef', 'n_numeric', 'n_obes', 'n_batt')


def transform(rows: pd.DataFrame, mode: str) -> pd.DataFrame:
    """Preserve input rows/order/base columns; append float32 engineered columns.

    Collector context uses (collector_id,as_of), never a whole-period aggregate.
    All sums below aggregate integer-valued counts; no row-order-sensitive means
    of probabilities, targets or continuous recencies are used.
    """
    if mode not in MODES:
        raise ValueError(f'Unknown engineering mode: {mode}')
    required=set(FEATURE_NAMES) | {'section_id','collector_id','as_of'}
    if missing:=required-set(rows.columns):
        raise ValueError(f'Missing base inputs: {sorted(missing)}')
    if rows[['section_id','collector_id','as_of']].isna().any().any():
        raise ValueError('Missing riskset identity')
    if rows.duplicated(['section_id','as_of']).any():
        raise ValueError('Duplicate section-day changes collector context')
    if any(str(c).startswith('eng_') for c in rows.columns):
        raise ValueError('Input already contains engineered features')
    # Work in float64 for division, freeze output as float32 for train/serve parity.
    values={name:rows[name].to_numpy(dtype=np.float64) for name in FEATURE_NAMES}
    engineered={}
    def put(name, value):
        if not np.isfinite(value).all():
            raise ValueError(f'Nonfinite engineered feature {name}')
        engineered['eng_'+name]=np.asarray(value,dtype=np.float32)
    known=np.maximum(values['known_channels'],1.)
    if mode in ('normalized','combined'):
        for w in (1,7,28):
            total=np.maximum(values[f'n_rows_{w}d'],1.)
            put(f'rows_per_channel_day_{w}d',values[f'n_rows_{w}d']/known/w)
            put(f'reporting_share_{w}d',values[f'n_reporting_{w}d']/known/w)
            for count in COUNTS[1:]:
                put(f'{count}_per_row_{w}d',values[f'{count}_{w}d']/total)
        for count in COUNTS:
            for w in (7,28):
                put(f'{count}_trend_1to{w}',np.log1p(values[f'{count}_1d'])-
                    np.log1p(values[f'{count}_{w}d']/w))
        for w in (7,28,90,365):
            put(f'confirmed_per_channel_{w}d',values[f'confirmed_{w}d']/known)
        for kind in ('smoke','gas','temperature'):
            put(f'{kind}_share',values[f'known_{kind}']/known)
    if mode in ('context','combined'):
        keys=[rows['as_of'],rows['collector_id']]
        def total(name):
            return rows[name].astype('float64').groupby(keys,sort=False).transform('sum').to_numpy()
        group_known=np.maximum(total('known_channels'),1.)
        group_size=rows['section_id'].groupby(keys,sort=False).transform('size').to_numpy()
        put('collector_sections',group_size)
        put('collector_known_channels',group_known)
        for w in (1,7,28):
            group_rows=total(f'n_rows_{w}d')
            put(f'collector_rows_per_channel_day_{w}d',group_rows/group_known/w)
            for count in ('n_bad','n_alarm','n_undef'):
                sums=total(f'{count}_{w}d')
                put(f'collector_{count}_per_row_{w}d',sums/np.maximum(group_rows,1.))
            # Other-section counts distinguish a common-cause burst from own noise.
            put(f'other_sections_bad_per_channel_{w}d',
                (total(f'n_bad_{w}d')-values[f'n_bad_{w}d'])/
                np.maximum(group_known-values['known_channels'],1.))
        for w in (7,28,90,365):
            put(f'collector_confirmed_per_channel_{w}d',total(f'confirmed_{w}d')/group_known)
        for count in ('n_bad','n_alarm'):
            indicator=pd.Series((values[f'{count}_1d']>0).astype('int64'),index=rows.index)
            put(f'collector_sections_with_{count}_share',
                indicator.groupby(keys,sort=False).transform('sum').to_numpy()/group_size)
    extra=pd.DataFrame(engineered,index=rows.index)
    return pd.concat([rows,extra],axis=1,copy=False)
