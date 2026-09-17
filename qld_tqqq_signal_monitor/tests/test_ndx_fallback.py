from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace

import exchange_calendars as xcals
import numpy as np
import pandas as pd
import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ndx_fallback as fb

NOW = pd.Timestamp('2026-09-17T02:00:00Z')
REPORT = pd.Timestamp('2026-09-16')


@pytest.fixture
def inputs():
    cal = xcals.get_calendar('XNYS', start='2022-01-01', end='2027-01-01')
    idx = fb.sessions(cal, '2022-01-03', REPORT)
    n = np.arange(len(idx))
    full = pd.Series(20000*np.exp(.0002*n+.005*np.sin(n/11)), index=idx, name='NDX')
    return cal, full


def payload(s):
    return {'chart': {'error': None, 'result': [{
        'meta': {'symbol':'^NDX','instrumentType':'INDEX','currency':'USD',
                 'exchangeTimezoneName':'America/New_York','dataGranularity':'1d',
                 'regularMarketTime':int(pd.Timestamp('2026-09-16T20:15:00Z').timestamp())},
        'timestamp': ((s.index.tz_localize('America/New_York')+pd.Timedelta(hours=9, minutes=30)).asi8//10**9).tolist(),
        'indicators': {'quote': [{'open':s.tolist(),'close':s.tolist(),
                                 'high':(s*1.01).tolist(),'low':(s*.99).tolist()}]}
    }]}}


@pytest.mark.parametrize('missing', [1,2,5])
def test_append_only_preserves_all_fred_history(inputs, missing):
    cal, full = inputs
    anchor = full.iloc[:-missing].copy()
    tail = full.iloc[-55:].copy() * (1+0.000001)
    before = anchor.copy()
    out, audit = fb.append_tail(anchor, tail, REPORT, cal, NOW)
    pd.testing.assert_series_equal(anchor, before)
    pd.testing.assert_series_equal(out.loc[anchor.index], anchor, check_freq=False)
    assert out.loc[REPORT] == tail.loc[REPORT]
    assert audit['overlap_sessions']==20
    assert len(audit['appended'])==missing


@pytest.mark.parametrize('missing', [0,6,20])
def test_no_wholesale_provider_replacement(inputs, missing):
    cal, full = inputs
    anchor = full if missing==0 else full.iloc[:-missing]
    with pytest.raises(fb.TailUnavailable, match='one to five'):
        fb.append_tail(anchor, full.iloc[-80:], REPORT, cal, NOW)


def test_interior_anchor_gap_rejected(inputs):
    cal, full=inputs
    h=full.iloc[:-1].drop(full.index[-40])
    with pytest.raises(fb.TailUnavailable, match='interior'):
        fb.append_tail(h, full.iloc[-90:], REPORT, cal, NOW)


def test_insufficient_history_not_replaced(inputs):
    cal, full=inputs
    with pytest.raises(fb.TailUnavailable, match='500'):
        fb.append_tail(full.iloc[-300:-1], full.iloc[-60:], REPORT, cal, NOW)


@pytest.mark.parametrize('case', ['short_overlap','deleted_overlap','deleted_tail','duplicate','infinite','negative','mismatch'])
def test_bad_tail_rejected(inputs, case):
    cal, full=inputs
    tail=full.iloc[-55:].copy()
    if case=='short_overlap': tail=tail.iloc[-10:]
    if case=='deleted_overlap': tail=tail.drop(tail.index[-7])
    if case=='deleted_tail': tail=tail.iloc[:-1]
    if case=='duplicate': tail=pd.concat([tail,tail.iloc[-1:]])
    if case=='infinite': tail.iloc[-1]=np.inf
    if case=='negative': tail.iloc[-1]=-1
    if case=='mismatch': tail.iloc[-3]*=1.01
    with pytest.raises(fb.TailUnavailable):
        fb.append_tail(full.iloc[:-1],tail,REPORT,cal,NOW)


@pytest.mark.parametrize('key,value', [('symbol','QQQ'),('symbol','^IXIC'),('instrumentType','ETF'),
    ('currency','EUR'),('exchangeTimezoneName','UTC'),('dataGranularity','1m')])
def test_wrong_instrument_and_bar_type_rejected(inputs, key, value):
    cal, full=inputs
    raw=payload(full.iloc[-55:]); raw['chart']['result'][0]['meta'][key]=value
    with pytest.raises(fb.TailUnavailable, match='metadata'):
        fb.daily_ndx(raw,REPORT,cal,NOW)


@pytest.mark.parametrize('time', ['2026-09-16T13:44:00Z','2026-09-16T19:59:59Z','2026-09-18T20:00:00Z'])
def test_same_day_cached_intraday_or_future_quote_rejected(inputs, time):
    cal,full=inputs
    raw=payload(full.iloc[-55:])
    raw['chart']['result'][0]['meta']['regularMarketTime']=int(pd.Timestamp(time).timestamp())
    with pytest.raises(fb.TailUnavailable, match='Provider timestamp'):
        fb.daily_ndx(raw,REPORT,cal,NOW)


@pytest.mark.parametrize('field', ['close','low','open','high'])
def test_incomplete_ohlc_rejected(inputs,field):
    cal,full=inputs
    raw=payload(full.iloc[-55:])
    raw['chart']['result'][0]['indicators']['quote'][0][field][-1]=None
    with pytest.raises(fb.TailUnavailable, match='OHLC'):
        fb.daily_ndx(raw,REPORT,cal,NOW)


def test_metadata_quote_never_used_as_close(inputs):
    cal,full=inputs
    raw=payload(full.iloc[-55:])
    raw['chart']['result'][0]['meta']['regularMarketPrice']=1e9
    actual=fb.daily_ndx(raw,REPORT,cal,NOW)
    assert actual.iloc[-1]==full.iloc[-1]


def test_future_values_do_not_change_historical_prefix(inputs):
    cal,full=inputs
    h=full.iloc[:-1]
    tail=full.iloc[-55:]
    expected, _=fb.append_tail(h,tail,REPORT,cal,NOW)
    tail=pd.concat([tail,pd.Series([np.inf],index=[REPORT+pd.Timedelta(days=1)])])
    actual,_=fb.append_tail(h,tail,REPORT,cal,NOW)
    pd.testing.assert_series_equal(actual,expected)


@pytest.mark.parametrize('now', ['2026-09-16T19:00:00Z','2026-09-16T20:29:59Z'])
def test_not_completed_session_cannot_request_network(inputs,now):
    cal,full=inputs
    class NoNetwork:
        def get(self,*args,**kwargs):pytest.fail('network called before close')
    with pytest.raises(fb.TailUnavailable,match='not complete'):
        fb.fetch_tail(NoNetwork(),full.iloc[:-1],REPORT,[],cal,now)


def test_http_fallback_and_audit(inputs):
    cal,full=inputs
    raw=payload(full.iloc[-55:])
    class Session:
        def __init__(self):self.urls=[]
        def get(self,url,**kwargs):
            self.urls.append(url)
            if len(self.urls)==1:raise requests.ConnectionError('temporary error')
            return SimpleNamespace(raise_for_status=lambda:None,json=lambda:raw)
    session=Session(); audit=[]
    out=fb.fetch_tail(session,full.iloc[:-1],REPORT,audit,cal,NOW)
    assert session.urls==list(fb.URLS)
    assert out.iloc[-1]==full.iloc[-1]
    assert 'error' in audit[0] and audit[1]['status']=='ok'
    assert audit[1]['appended']=={'2026-09-16':full.iloc[-1]}
    assert len(audit[1]['response_sha256'])==64
    assert 'append-only daily ^NDX' in out.attrs['source']


def test_both_sources_stale_fail_closed(inputs):
    cal,full=inputs
    raw=payload(full.iloc[-55:-1])
    class Session:
        def get(self,*args,**kwargs):
            return SimpleNamespace(raise_for_status=lambda:None,json=lambda:raw)
    audit=[]
    with pytest.raises(fb.TailUnavailable,match='fallback failed'):
        fb.fetch_tail(Session(),full.iloc[:-1],REPORT,audit,cal,NOW)
    assert len(audit)==2 and all('error' in x for x in audit)


def test_invalid_report_rejected(inputs):
    cal,full=inputs
    for report in ['2026-09-16T01:00:00','2026-09-16T00:00:00Z','2026-09-13']:
        with pytest.raises(fb.TailUnavailable):
            fb.validate_anchor(full.iloc[:-1],report,cal,NOW)


def integration_modules():
    import importlib.util
    if importlib.util.find_spec('reliable_data') is None:
        pytest.skip('full repository integration is exercised in GitHub CI')
    import reliable_data
    return reliable_data


def test_production_loader_recovers_delayed_fred(inputs,monkeypatch):
    data=integration_modules(); cal,full=inputs
    original_utc=fb.utc
    monkeypatch.setattr(fb,'utc',lambda v=None:original_utc(NOW if v is None else v))
    monkeypatch.setattr(data,'get_qqq',lambda *a:full/40)
    csv=full.iloc[:-1].rename('NASDAQ100').to_csv(index_label='observation_date')
    raw=payload(full.iloc[-55:])
    class Session:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def get(self,url,**kwargs):
            return SimpleNamespace(text=csv,raise_for_status=lambda:None,json=lambda:raw)
    audit=[]
    q,n=data.load_prices(REPORT,audit,attempts=1,session_factory=Session)
    np.testing.assert_allclose(n,full,rtol=1e-15)
    detail=next(x for x in audit if x.get('method')=='FRED_anchor_append_only')
    assert detail['appended']=={'2026-09-16':full.iloc[-1]}
    assert n.index[-1]==q.index[-1]==REPORT


def test_production_loader_all_delayed_still_fails(inputs,monkeypatch):
    data=integration_modules(); cal,full=inputs
    original_utc=fb.utc
    monkeypatch.setattr(fb,'utc',lambda v=None:original_utc(NOW if v is None else v))
    monkeypatch.setattr(data,'get_qqq',lambda *a:full/40)
    csv=full.iloc[:-1].rename('NASDAQ100').to_csv(index_label='observation_date')
    raw=payload(full.iloc[-55:-1])
    class Session:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def get(self,url,**kwargs):
            return SimpleNamespace(text=csv,raise_for_status=lambda:None,json=lambda:raw)
    with pytest.raises(data.DataUnavailable,match='Fresh inputs unavailable'):
        data.load_prices(REPORT,[],attempts=1,session_factory=Session)


def test_fresh_fred_does_not_contact_fallback(inputs):
    data=integration_modules(); cal,full=inputs
    csv=full.rename('NASDAQ100').to_csv(index_label='observation_date')
    class Session:
        def __init__(self):self.calls=[]
        def get(self,url,**kwargs):
            self.calls.append(url)
            assert url==data.monitor.FRED_NDX_URL
            return SimpleNamespace(text=csv,raise_for_status=lambda:None)
    session=Session()
    from ndx_loader import get_ndx_reliable
    out=get_ndx_reliable(session,REPORT,[])
    assert len(session.calls)==1 and out.index[-1]==REPORT
