from pathlib import Path
import subprocess
import sys
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import monitor
import reliable_data as data
import fred_transport


def test_fred_uses_tested_system_transport_and_retains_validation(monkeypatch):
    index = monitor._normalize_sessions(monitor._calendar().sessions_in_range('2022-01-03', '2026-09-04'))
    n = pd.Series(14000 + np.arange(len(index)), index=index)
    seen = []
    def runner(args, **kwargs):
        seen.append(args)
        assert args[0] == '/usr/bin/python3'
        assert args[2] == monitor.FRED_NDX_URL
        assert 'shell' not in kwargs
        return subprocess.CompletedProcess(args, 0,
            stdout=n.rename('NASDAQ100').to_csv(index_label='observation_date').encode(), stderr=b'')
    monkeypatch.setattr(data.subprocess, 'run', runner)
    with data.http_session() as session:
        out = data.get_ndx(session, n.index[-1], [])
    assert len(seen) == 1 and out.index[-1] == n.index[-1]


def test_fred_transport_failure_does_not_return_empty_success(monkeypatch):
    monkeypatch.setattr(data.subprocess, 'run', lambda args, **kw:
        subprocess.CompletedProcess(args, 2, stdout=b'', stderr=b'timed out'))
    with data.http_session() as session:
        with pytest.raises(data.requests.RequestException, match='timed out'):
            session.get(monitor.FRED_NDX_URL, timeout=(8, 25))


def test_fred_helper_is_constrained_to_original_provider():
    for url in ('https://example.com', 'http://fred.stlouisfed.org/graph/fredgraph.csv?id=NASDAQ100',
                'https://fred.stlouisfed.org/graph/fredgraph.csv?id=SP500'):
        with pytest.raises(ValueError, match='original'):
            fred_transport.download(url, 1)
