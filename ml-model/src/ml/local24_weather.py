"""Lagged weather research covariates; archive reanalysis, not live forecast data."""
from pathlib import Path
import pandas as pd

FEATURES = ['weather_temp_mean', 'weather_temp_range', 'weather_humidity',
            'weather_pressure', 'weather_pressure_change', 'weather_rain_1d',
            'weather_rain_7d', 'weather_temp_7d', 'weather_freeze_thaw']

def build_weather(source: Path | pd.DataFrame) -> pd.DataFrame:
    h = pd.read_csv(source) if not isinstance(source, pd.DataFrame) else source.copy()
    h['time'] = pd.to_datetime(h['time'])
    if h.time.duplicated().any():
        raise ValueError('Duplicate weather timestamps')
    h = h.sort_values('time').set_index('time')
    cols = ['temperature_2m','relative_humidity_2m','surface_pressure','precipitation']
    daily = h[cols].resample('D').agg({cols[0]:['mean','min','max','count'],
          cols[1]:['mean','count'],cols[2]:['mean','count'],cols[3]:['sum','count']})
    complete = (daily.xs('count',level=1,axis=1)==24).all(axis=1)
    out = pd.DataFrame(index=daily.index)
    out['weather_temp_mean'] = daily[cols[0],'mean']
    out['weather_temp_range'] = daily[cols[0],'max']-daily[cols[0],'min']
    out['weather_humidity'] = daily[cols[1],'mean']
    out['weather_pressure'] = daily[cols[2],'mean']
    out['weather_rain_1d'] = daily[cols[3],'sum']
    out['weather_freeze_thaw'] = ((daily[cols[0],'min']<0)&(daily[cols[0],'max']>=0)).astype(float)
    out.loc[~complete,:] = float('nan')
    out['weather_pressure_change'] = out.weather_pressure.diff()
    out['weather_rain_7d'] = out.weather_rain_1d.rolling(7,min_periods=7).sum()
    out['weather_temp_7d'] = out.weather_temp_mean.rolling(7,min_periods=7).mean()
    # 24h conservative availability lag beyond the completed previous day:
    # origin t only uses weather through t-2. Archived reanalysis has no release vintage.
    out.index += pd.Timedelta(days=2)
    return out[FEATURES].rename_axis('as_of').reset_index()
