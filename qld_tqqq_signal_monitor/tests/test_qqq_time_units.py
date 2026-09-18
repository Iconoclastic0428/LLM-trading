from pathlib import Path
import sys
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import monitor
import qqq_recovery as q

@pytest.mark.parametrize('unit',['s','us','ns'])
def test_append_preserves_anchor_datetime_storage_unit(unit):
    report = pd.Timestamp('2026-09-17')
    calendar = monitor._calendar()
    dates = monitor._normalize_sessions(calendar.sessions_in_range('2024-01-02',report))
    series = pd.Series(350+np.arange(len(dates))*.1,index=dates,name='QQQ')
    anchor = series.iloc[:-1].copy()
    anchor.index = anchor.index.as_unit(unit)
    tail = series.iloc[-62:].copy()
    tail.index = tail.index.as_unit('us')
    result,_ = q.append_history(anchor,tail,report,calendar,'2026-09-18T02:50:00Z')
    assert result.index.unit == anchor.index.unit
    pd.testing.assert_series_equal(result.loc[anchor.index],anchor,check_freq=False)
    assert result.loc[report] == series.loc[report]
