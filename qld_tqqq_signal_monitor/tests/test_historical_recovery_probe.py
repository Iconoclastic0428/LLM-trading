"""Contract test and current-provider readiness must never share a date label."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import monitor
import historical_recovery_probe as probe
import qqq_recovery as recovery

REPORT = pd.Timestamp('2026-10-08')
NOW = pd.Timestamp('2026-10-09T01:05:00Z')


def response(raw):
    r = requests.Response()
    r.status_code = 200
    r._content = json.dumps(raw).encode()
    r.headers.update(Date='Fri, 09 Oct 2026 01:05:00 GMT', Age='0')
    return r


@pytest.fixture
def market():
    dates = monitor._normalize_sessions(monitor._calendar().sessions_in_range('2023-01-03', REPORT))
    values = 350*np.exp(.0003*np.arange(len(dates))+.015*np.sin(np.arange(len(dates))/19))
    series = pd.Series(values, index=dates, name='QQQ')
    stamps = (dates.tz_localize('America/New_York')+pd.Timedelta(hours=9,minutes=30)).tz_convert('UTC').asi8//10**9
    yahoo = {'chart': {'error': None, 'result': [{'meta': {'symbol': 'QQQ'}, 'timestamp': list(map(int, stamps)),
        'indicators': {'quote': [{'close': values.tolist(), 'open': values.tolist(),
                                 'high': (values*1.01).tolist(), 'low': (values*.99).tolist()}],
                       'adjclose': [{'adjclose': values.tolist()}]}}]}}
    rows = [{'date': d.strftime('%m/%d/%Y'), 'open': str(v), 'close': str(v),
             'high': str(v*1.01), 'low': str(v*.99)} for d, v in series.iloc[-65:].iloc[::-1].items()]
    nasdaq = {'status': {'rCode': 200}, 'data': {'symbol': 'QQQ', 'tradesTable': {'headers': {
        'date':'Date','open':'Open','high':'High','low':'Low','close':'Close/Last'}, 'rows': rows}}}
    return series, yahoo, nasdaq


class Session:
    def __init__(self, yahoo, nasdaq):
        self.yahoo, self.nasdaq = yahoo, nasdaq
        self.requests = []
    def get(self, url, **kwargs):
        self.requests.append((url, kwargs))
        if url == recovery.NASDAQ_URL:
            return response(self.nasdaq)
        if url in monitor.YAHOO_QQQ_URLS:
            return response(self.yahoo)
        pytest.fail('Diagnostic touched unrelated endpoint ' + url)
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False


@pytest.mark.parametrize('yahoo_lag,nasdaq_lag', [(0,0),(0,1),(1,0),(1,1)])
def test_current_and_historical_dates_are_separate(tmp_path, market, yahoo_lag, nasdaq_lag, capsys):
    series, yahoo, nasdaq = market
    if yahoo_lag:
        yahoo['chart']['result'][0]['indicators']['quote'][0]['close'][-1] = None
    if nasdaq_lag:
        nasdaq['data']['tradesTable']['rows'] = nasdaq['data']['tradesTable']['rows'][1:]
    session = Session(yahoo, nasdaq)
    before_y, before_n = deepcopy(yahoo), deepcopy(nasdaq)
    assert probe.probe(tmp_path, now=NOW, session_factory=lambda:session) == 0
    p = json.loads((tmp_path/'probe.json').read_text())
    date = series.index[-2] if yahoo_lag or nasdaq_lag else REPORT
    assert p['live_report_date'] == '2026-10-08'
    assert p['tested_session'] == str(date.date())
    assert p['history_lag_sessions'] == int(bool(yahoo_lag or nasdaq_lag))
    assert p['status'] == 'ok' and p['historical_fallback_verified']
    assert p['current_daily_path_status'] == ('unavailable' if nasdaq_lag else 'verified')
    assert p['execution_authorized'] is p['publish_notification'] is p['live_pipeline_verified_by_this_probe'] is False
    assert yahoo == before_y and nasdaq == before_n
    assert len(session.requests) == 2
    assert p['qqq_source_wrapper_exercised']
    closes = pd.read_csv(tmp_path/'verified_closes.csv', index_col=0, parse_dates=True)
    assert closes.index[-1] == date
    assert not (tmp_path/'signal.json').exists()
    for name, digest in p['source_sha256'].items():
        assert hashlib.sha256((tmp_path/name).read_bytes()).hexdigest() == digest
    if nasdaq_lag:
        assert '::warning::' in capsys.readouterr().out


@pytest.mark.parametrize('fault', ['two_sessions_old', 'wrong_symbol', 'bad_status', 'empty_rows',
    'duplicate', 'null_close', 'negative_close', 'range_error', 'overlap_mismatch', 'current_mismatch',
    'interior_yahoo_gap', 'short_yahoo', 'bad_date', 'null_latest_nasdaq_when_yahoo_stale'])
def test_never_step_backwards_to_hide_bad_data(tmp_path, market, fault):
    series, yahoo, nasdaq = market
    rows = nasdaq['data']['tradesTable']['rows']
    quote = yahoo['chart']['result'][0]['indicators']['quote'][0]
    if fault == 'two_sessions_old': nasdaq['data']['tradesTable']['rows'] = rows[2:]
    if fault == 'wrong_symbol': nasdaq['data']['symbol'] = 'TQQQ'
    if fault == 'bad_status': nasdaq['status']['rCode'] = 500
    if fault == 'empty_rows': nasdaq['data']['tradesTable']['rows'] = []
    if fault == 'duplicate': rows.append(deepcopy(rows[5]))
    if fault == 'null_close': rows[2]['close'] = None
    if fault == 'negative_close': rows[0]['close'] = '-1'
    if fault == 'range_error': rows[0]['high'] = '1'
    if fault == 'overlap_mismatch': rows[5]['close'] = str(float(rows[5]['close'])*1.005)
    if fault == 'current_mismatch': rows[0]['close'] = str(float(rows[0]['close'])*1.005)
    if fault == 'interior_yahoo_gap': quote['close'][-8] = None
    if fault == 'short_yahoo':
        obj = yahoo['chart']['result'][0]
        obj['timestamp'] = obj['timestamp'][-499:]
        for blocks in obj['indicators'].values():
            for block in blocks:
                for key in block: block[key] = block[key][-499:]
    if fault == 'bad_date': rows[0]['date'] = 'not a date'
    if fault == 'null_latest_nasdaq_when_yahoo_stale':
        quote['close'][-1] = None
        rows[0]['close'] = None
    s = Session(yahoo, nasdaq)
    assert probe.probe(tmp_path, now=NOW, session_factory=lambda:s) == 2
    p = json.loads((tmp_path/'probe.json').read_text())
    assert p['status'] == 'error' and not p['historical_fallback_verified']
    assert not p['execution_authorized']
    assert not (tmp_path/'verified_closes.csv').exists()


@pytest.mark.parametrize('date,age', [('Thu, 08 Oct 2026 19:00:00 GMT','0'),
    ('Fri, 09 Oct 2026 02:00:00 GMT','0'), ('Fri, 09 Oct 2026 01:05:00 GMT','-1')])
def test_bad_http_time_still_fails(tmp_path, market, date, age):
    _, yahoo, nasdaq = market
    class Bad(Session):
        def get(self, url, **kwargs):
            r = super().get(url, **kwargs)
            if url == recovery.NASDAQ_URL: r.headers.update(Date=date, Age=age)
            return r
    assert probe.probe(tmp_path, now=NOW, session_factory=lambda:Bad(yahoo,nasdaq)) == 2


def test_failure_clears_old_success(tmp_path, market):
    _, yahoo, nasdaq = market
    assert probe.probe(tmp_path, now=NOW, session_factory=lambda:Session(yahoo,nasdaq)) == 0
    class Offline(Session):
        def get(self, *args, **kwargs): raise requests.ConnectionError('offline test')
    assert probe.probe(tmp_path, now=NOW, session_factory=lambda:Offline(yahoo,nasdaq)) == 2
    assert json.loads((tmp_path/'probe.json').read_text())['status'] == 'error'
    assert not (tmp_path/'verified_closes.csv').exists()


def test_weekend_one_session_lag_is_allowed(market):
    s, _, n = market
    candidate = probe.select_session(s.iloc[:-1], n, REPORT, monitor._calendar())
    assert candidate == s.index[-2]
    # Selection requires an exchange session, never arbitrary calendar decrement.
    with pytest.raises(recovery.RecoveryError):
        probe.select_session(s, n, pd.Timestamp('2026-10-10'), monitor._calendar())


def test_original_command_uses_fixed_diagnostic(monkeypatch, tmp_path):
    monkeypatch.setattr(probe, 'probe', lambda p: 123 if p == tmp_path else 999)
    assert recovery.probe(tmp_path) == 123


def test_required_live_job_is_not_continue_on_error():
    text = (Path(__file__).resolve().parents[2]/'.github/workflows/qld-tqqq-tests.yml').read_text()
    smoke = text.split('  shadow-smoke:',1)[1]
    assert 'continue-on-error: true' not in smoke
    assert 'Generate read-only baseline audit' in smoke
    assert 'Exercise finalized QQQ close and checkpoint with real data' in smoke
    assert 'bounded common session and report current readiness' in smoke
    assert 'cat qqq_recovery_probe/probe.json' in smoke
