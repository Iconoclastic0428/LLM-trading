from pathlib import Path
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import monitor
import reliable_data as data


def test_fred_uses_verified_plain_csv_transport(monkeypatch):
    index = monitor._normalize_sessions(monitor._calendar().sessions_in_range('2022-01-03', '2026-09-04'))
    n = pd.Series(14000 + np.arange(len(index)), index=index)
    seen = []
    class Raw:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self):
            return n.rename('NASDAQ100').to_csv(index_label='observation_date').encode()
    def opener(request, timeout):
        seen.append(request)
        assert request.full_url == monitor.FRED_NDX_URL
        assert request.get_header('Accept-encoding') is None
        return Raw()
    monkeypatch.setattr(data, 'urlopen', opener)
    with data.http_session() as session:
        out = data.get_ndx(session, n.index[-1], [])
    assert len(seen) == 1 and out.index[-1] == n.index[-1]
