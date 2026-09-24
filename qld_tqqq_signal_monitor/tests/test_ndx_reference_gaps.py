"""Regression for Yahoo historical nulls with a complete FRED anchor.

Reference prices are compared, not imported. New tail OHLC remains mandatory.
The September 24 raw-response replay is separately recorded in the PR audit.
"""
from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace

import exchange_calendars as xcals
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ndx_fallback as fb

REPORT = pd.Timestamp('2026-09-24')
NOW = pd.Timestamp('2026-09-24T22:57:49Z')


@pytest.fixture
def case():
    cal = xcals.get_calendar('XNYS', start='2022-01-01', end='2027-01-01')
    idx = fb.sessions(cal, '2022-01-03', REPORT)
    n = np.arange(len(idx))
    full = pd.Series(20000*np.exp(.0002*n+.005*np.sin(n/11)), index=idx, name='NDX')
    return cal, full


def raw_for(s):
    stamps = [int((d.tz_localize('America/New_York') + pd.Timedelta(hours=9, minutes=30)).timestamp()) for d in s.index]
    return {'chart': {'error': None, 'result': [{
        'meta': {'symbol':'^NDX','instrumentType':'INDEX','currency':'USD',
                 'exchangeTimezoneName':'America/New_York','dataGranularity':'1d',
                 'regularMarketTime':int(pd.Timestamp('2026-09-24T21:15:59Z').timestamp())},
        'timestamp': stamps,
        'indicators': {'quote': [{'open':s.tolist(),'close':s.tolist(),
                                 'high':(s*1.01).tolist(),'low':(s*.99).tolist()}]}
    }]}}


def null_row(raw, index):
    for field in ('open','high','low','close'):
        raw['chart']['result'][0]['indicators']['quote'][0][field][index] = None


def recover(cal, history, raw):
    class Session:
        def get(self, *a, **kw):
            return SimpleNamespace(json=lambda:deepcopy(raw), raise_for_status=lambda:None)
    audit = []
    out = fb.fetch_tail(Session(), history, REPORT, audit, cal, NOW)
    return out, audit[-1]


def test_september22_shaped_null_keeps_fred_and_accepts_september24(case):
    cal, full = case
    anchor = full.iloc[:-1].copy()
    raw = raw_for(full.iloc[-60:])
    null_row(raw, -3)  # September 22 is FRED-covered, not a missing new close.
    before = anchor.copy()
    out, detail = recover(cal, anchor, raw)
    pd.testing.assert_series_equal(anchor, before, check_exact=True)
    pd.testing.assert_series_equal(out.loc[anchor.index], anchor, check_freq=False, check_exact=True)
    assert out.loc[REPORT] == full.loc[REPORT]
    assert detail['unavailable_yahoo_overlap_dates'] == ['2026-09-22']
    assert detail['retained_fred_closes'] == {'2026-09-22':full.loc['2026-09-22']}
    assert detail['overlap_sessions'] == 24
    assert detail['appended'] == {'2026-09-24':full.loc[REPORT]}


@pytest.mark.parametrize('field', ['open','high','low'])
def test_unused_historical_ohlc_does_not_replace_verified_close(case, field):
    cal, full = case
    raw = raw_for(full.iloc[-60:])
    raw['chart']['result'][0]['indicators']['quote'][0][field][-3] = None
    out, detail = recover(cal, full.iloc[:-1], raw)
    assert out.loc['2026-09-22'] == full.loc['2026-09-22']
    assert detail['overlap_sessions'] == 25


@pytest.mark.parametrize('count', [0,1,5])
def test_bounded_reference_unavailability(case, count):
    cal, full = case
    raw = raw_for(full.iloc[-60:])
    for j in range(count): null_row(raw, -2-j)
    out, detail = recover(cal, full.iloc[:-1], raw)
    assert detail['overlap_sessions'] == 25-count
    assert len(detail['retained_fred_closes']) == count
    pd.testing.assert_series_equal(out, full, check_freq=False, check_exact=True)


def test_six_reference_nulls_fail_closed(case):
    cal, full = case
    raw = raw_for(full.iloc[-60:])
    for j in range(6): null_row(raw, -2-j)
    with pytest.raises(fb.TailUnavailable, match='at least 20'):
        recover(cal, full.iloc[:-1], raw)


@pytest.mark.parametrize('field', ['open','high','low','close'])
@pytest.mark.parametrize('offset', [-1,-2])
def test_null_in_any_new_session_still_rejected(case, field, offset):
    cal, full = case
    raw = raw_for(full.iloc[-60:])
    raw['chart']['result'][0]['indicators']['quote'][0][field][offset] = None
    with pytest.raises(fb.TailUnavailable, match='OHLC'):
        recover(cal, full.iloc[:-2], raw)


def test_entirely_null_new_session_not_filled_from_previous_price(case):
    cal, full = case
    raw = raw_for(full.iloc[-60:]); null_row(raw,-1)
    with pytest.raises(fb.TailUnavailable, match='2026-09-24'):
        recover(cal, full.iloc[:-1], raw)


@pytest.mark.parametrize('bad', [-1,0,float('inf'),99999])
def test_bad_nonnull_reference_close_not_discarded(case, bad):
    cal, full = case
    raw = raw_for(full.iloc[-60:]); null_row(raw,-3)
    raw['chart']['result'][0]['indicators']['quote'][0]['close'][-4] = bad
    with pytest.raises(fb.TailUnavailable): recover(cal,full.iloc[:-1],raw)


def test_all_available_reference_closes_checked_not_just_last20(case):
    cal, full = case
    raw = raw_for(full.iloc[-60:]); null_row(raw,-3)
    raw['chart']['result'][0]['indicators']['quote'][0]['close'][-26] *= 1.01
    with pytest.raises(fb.TailUnavailable, match='overlap mismatch'):
        recover(cal,full.iloc[:-1],raw)


def test_missing_reference_timestamp_is_reported(case):
    cal, full = case
    raw=raw_for(full.iloc[-60:].drop(pd.Timestamp('2026-09-22')))
    out,detail=recover(cal,full.iloc[:-1],raw)
    assert detail['unavailable_yahoo_overlap_dates']==['2026-09-22']
    assert out.loc['2026-09-22']==full.loc['2026-09-22']


def test_missing_new_timestamp_fails(case):
    cal, full = case
    with pytest.raises(fb.TailUnavailable):
        recover(cal,full.iloc[:-2],raw_for(full.iloc[-60:].drop(full.index[-2])))


@pytest.mark.parametrize('duplicate', [-1,-3])
def test_duplicate_dates_remain_errors(case,duplicate):
    cal, full = case
    s=full.iloc[-60:]
    with pytest.raises(fb.TailUnavailable,match='duplicate'):
        recover(cal,full.iloc[:-1],raw_for(pd.concat([s,s.iloc[duplicate:duplicate+1] if duplicate!=-1 else s.iloc[-1:]])))


@pytest.mark.parametrize('unit', ['s','us','ns'])
def test_ndx_preserves_anchor_datetime_unit(case, unit):
    cal,full=case
    h=full.iloc[:-1].copy();h.index=h.index.as_unit(unit)
    raw=raw_for(full.iloc[-60:]);null_row(raw,-3)
    out,_=recover(cal,h,raw)
    assert out.index.unit==unit
    pd.testing.assert_series_equal(out.loc[h.index],h,check_exact=True,check_freq=False)


def test_future_values_and_unused_remote_history_do_not_change_recovery(case):
    cal,full=case
    raw=raw_for(full.iloc[-60:]);null_row(raw,-3)
    expected,_=recover(cal,full.iloc[:-1],raw)
    raw['chart']['result'][0]['indicators']['quote'][0]['close'][0]='unused history'
    actual,_=recover(cal,full.iloc[:-1],raw)
    pd.testing.assert_series_equal(actual,expected,check_exact=True)
    extended=pd.concat([full.iloc[-60:],pd.Series([float('inf')],index=[REPORT+pd.Timedelta(days=1)])])
    future=raw_for(extended);null_row(future,-4)
    actual,_=recover(cal,full.iloc[:-1],future)
    pd.testing.assert_series_equal(actual,expected,check_exact=True)


def test_actual_loader_path_with_reference_gap(case,monkeypatch):
    import reliable_data as data
    cal,full=case
    oldutc=fb.utc
    monkeypatch.setattr(fb,'utc',lambda v=None:oldutc(NOW if v is None else v))
    monkeypatch.setattr(data,'get_qqq',lambda *a:full/40)
    csv=full.iloc[:-1].rename('NASDAQ100').to_csv(index_label='observation_date')
    raw=raw_for(full.iloc[-60:]);null_row(raw,-3)
    class Session:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def get(self,url,**kw):return SimpleNamespace(text=csv,raise_for_status=lambda:None,json=lambda:raw)
    audit=[]
    q,n=data.load_prices(REPORT,audit,attempts=1,session_factory=Session)
    np.testing.assert_allclose(n,full,rtol=1e-15)
    detail=next(d for d in audit if d.get('validation_policy')=='scoped_fred_anchor_v2')
    assert detail['unavailable_yahoo_overlap_dates']==['2026-09-22']
    assert n.index[-1]==q.index[-1]==REPORT
