import numpy as np
import pandas as pd
import pytest
from ml.local24_weather import build_weather

def source():
    d=pd.DataFrame({'time':pd.date_range('2025-01-01',periods=240,freq='h')})
    for c in ['temperature_2m','relative_humidity_2m','surface_pressure','precipitation']:
        d[c]=np.arange(len(d),dtype=float)
    return d

def test_future_weather_cannot_change_previous_origins():
    h=source(); expected=build_weather(h)
    h.loc[h.time>=pd.Timestamp('2025-01-07'),'temperature_2m']=999999
    actual=build_weather(h)
    pd.testing.assert_frame_equal(expected[expected.as_of<'2025-01-09'],actual[actual.as_of<'2025-01-09'])
    row=expected[expected.as_of=='2025-01-03'].iloc[0]
    assert row.weather_temp_mean==11.5

def test_incomplete_day_is_missing_not_zero():
    h=source().drop(index=30)
    out=build_weather(h)
    assert pd.isna(out[out.as_of=='2025-01-04'].iloc[0].weather_rain_1d)
    assert pd.isna(out[out.as_of=='2025-01-08'].iloc[0].weather_rain_7d)

def test_duplicate_hours_rejected():
    h=source()
    with pytest.raises(ValueError,match='Duplicate'):
        build_weather(pd.concat([h,h.iloc[:1]]))
