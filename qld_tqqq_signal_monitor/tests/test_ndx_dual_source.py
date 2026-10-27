"""A prolonged outage requires independent evidence, not a renewable timer."""
from copy import deepcopy
from email.utils import format_datetime
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import monitor
import price_checkpoint as cp
import ndx_checkpoint as single
import ndx_dual_source as dual
import ndx_loader
from ndx_fallback import TailUnavailable, sessions

REPORT = pd.Timestamp('2026-10-26')
ANCHOR = pd.Timestamp('2026-10-22')
NOW = pd.Timestamp('2026-10-27T12:00:00Z')


@pytest.fixture
def case(tmp_path, monkeypatch):
    monkeypatch.setenv('PRICE_CHECKPOINT_DIR', str(tmp_path))
    monkeypatch.delenv('SOURCE_EVIDENCE_DIR', raising=False)
    cal = monitor._calendar()
    dates = sessions(cal, pd.Timestamp('2020-01-02'), REPORT)
    x = np.arange(len(dates), dtype=float)
    n = pd.Series(10000*np.exp(0.0003*x + 0.002*np.sin(x/11)), index=dates, name='NDX')
    q = (n/40).rename('QQQ')
    n.attrs['source'] = 'FRED NASDAQ100 through 2026-10-15; verified checkpoint through 2026-10-22'
    q.attrs['source'] = monitor.YAHOO_QQQ_URLS[0]
    cp.save(q.loc[:ANCHOR], n.loc[:ANCHOR], ANCHOR, [], pd.Timestamp('2026-10-22T22:00:00Z'))
    days = dates[-40:];values=n.loc[days].to_numpy()
    bars={'open':values.tolist(),'high':(values+10).tolist(),
          'low':(values-10).tolist(),'close':values.tolist()}
    yahoo={'chart':{'error':None,'result':[{'meta':{'symbol':'^NDX','currency':'USD',
          'instrumentType':'INDEX','exchangeTimezoneName':'America/New_York','dataGranularity':'1d',
          'regularMarketTime':int(pd.Timestamp('2026-10-26T20:00:00Z').timestamp())},
          'timestamp':[int(d.tz_localize('America/New_York').timestamp())+34200 for d in days],
          'indicators':{'quote':[bars]}}]}}
    rows=[{'date':d.strftime('%m/%d/%Y'),'open':str(v),'high':str(v+10),
           'low':str(v-10),'close':str(v)} for d,v in zip(days,values)]
    nasdaq={'status':{'rCode':200},'data':{'symbol':'NDX','tradesTable':{
            'headers':{'date':'Date','open':'Open','high':'High','low':'Low','close':'Close/Last'},
            'rows':rows}}}
    state={'yahoo':yahoo,'nasdaq':nasdaq,'headers':{'Date':'Tue, 27 Oct 2026 11:59:00 GMT'},'calls':[]}
    class Fake:
        def get(self,url,**kwargs):
            state['calls'].append(url)
            if 'fred.stlouisfed.org' in url:raise requests.Timeout('FRED unavailable')
            if state.get('offline'):raise requests.Timeout('All sources unavailable')
            if url==dual.NASDAQ_URL and state.get('nasdaq_offline'):raise requests.Timeout('Issuer unavailable')
            r=requests.Response();r.status_code=200
            r._content=json.dumps(state['nasdaq'] if url==dual.NASDAQ_URL else state['yahoo']).encode()
            r.headers.update(state['headers']);return r
    return tmp_path,q,n,state,Fake()


def test_prolonged_outage_preserves_every_historical_value(case):
    path,q,n,state,s=case
    with pytest.raises(TailUnavailable,match='five'):single.recover(s,REPORT,[],NOW)
    audit=[];got=dual.recover(s,REPORT,audit,NOW)
    pd.testing.assert_series_equal(got,n,check_exact=True,check_freq=False)
    assert 'through 2026-10-15;' in got.attrs['source']
    assert audit[-1]['appended_dates']==['2026-10-23','2026-10-26']
    assert audit[-1]['preserved_rows']==len(n.loc[:ANCHOR])
    assert audit[-1]['max_independent_error']==0
    assert cp.restore(REPORT,[],NOW) is None


@pytest.mark.parametrize('change',[
    'issuer_symbol','issuer_schema','issuer_duplicate','issuer_non_session','issuer_negative',
    'issuer_old_conflict','issuer_new_conflict','issuer_two_sessions_late','issuer_missing_middle',
    'issuer_bad_ohlc','issuer_nan','issuer_status','issuer_offline','yahoo_symbol',
    'yahoo_granularity','yahoo_preclose','yahoo_future_clock','yahoo_old_conflict',
    'yahoo_new_ohlc','yahoo_missing_today','issuer_overlap_missing','yahoo_overlap_missing',
    'http_preclose','http_age','http_negative_age','http_future','all_offline'])
def test_bad_evidence_never_creates_new_prices(case,change):
    path,q,n,state,s=case
    rows=state['nasdaq']['data']['tradesTable']['rows']
    obj=state['yahoo']['chart']['result'][0];bars=obj['indicators']['quote'][0]
    if change=='issuer_symbol':state['nasdaq']['data']['symbol']='QQQ'
    elif change=='issuer_schema':state['nasdaq']['data']['tradesTable']['headers']['close']='Last'
    elif change=='issuer_duplicate':rows.append(deepcopy(rows[-1]))
    elif change=='issuer_non_session':rows[-2]['date']='10/24/2026'
    elif change=='issuer_negative':rows[-1]['close']='-1'
    elif change=='issuer_old_conflict':rows[-8]['close']=str(float(rows[-8]['close'])*1.01)
    elif change=='issuer_new_conflict':
        rows[-1]['close']=str(float(rows[-1]['close'])*1.001);rows[-1]['high']=str(float(rows[-1]['close'])+1)
    elif change=='issuer_two_sessions_late':del rows[-2:]
    elif change=='issuer_missing_middle':rows.pop(-2)
    elif change=='issuer_bad_ohlc':rows[-1]['low']=str(float(rows[-1]['high'])+1)
    elif change=='issuer_nan':rows[-1]['close']='NaN'
    elif change=='issuer_status':state['nasdaq']['status']['rCode']=400
    elif change=='issuer_offline':state['nasdaq_offline']=True
    elif change=='yahoo_symbol':obj['meta']['symbol']='QQQ'
    elif change=='yahoo_granularity':obj['meta']['dataGranularity']='5m'
    elif change=='yahoo_preclose':obj['meta']['regularMarketTime']=int(pd.Timestamp('2026-10-26T19:00Z').timestamp())
    elif change=='yahoo_future_clock':obj['meta']['regularMarketTime']=int(pd.Timestamp('2026-10-28T20:00Z').timestamp())
    elif change=='yahoo_old_conflict':bars['close'][-8]*=1.01
    elif change=='yahoo_new_ohlc':bars['low'][-1]=bars['high'][-1]+1
    elif change=='yahoo_missing_today':bars['close'][-1]=None
    elif change=='issuer_overlap_missing':del rows[-27:-21]
    elif change=='yahoo_overlap_missing':bars['close'][-27:-21]=[None]*6
    elif change=='http_preclose':state['headers']['Date']='Mon, 26 Oct 2026 19:59:00 GMT'
    elif change=='http_age':state['headers']['Age']='90000'
    elif change=='http_negative_age':state['headers']['Age']='-1'
    elif change=='http_future':state['headers']['Date']='Wed, 28 Oct 2026 12:00:00 GMT'
    elif change=='all_offline':state['offline']=True
    before=(path/'NDX.csv').read_bytes()
    with pytest.raises((ValueError,requests.RequestException)):
        dual.recover(s,REPORT,[],NOW)
    assert (path/'NDX.csv').read_bytes()==before
    assert cp.restore(REPORT,[],NOW) is None


@pytest.mark.parametrize('change',['hash','contract','unknown','old','future','grid','same_date','symlink'])
def test_invalid_anchor_cannot_become_a_new_source(case,change):
    path,q,n,state,s=case
    doc=json.loads((path/'manifest.json').read_text())
    if change=='hash':(path/'NDX.csv').write_bytes((path/'NDX.csv').read_bytes()+b'\n')
    elif change=='contract':doc['rules_sha256']='bad'
    elif change=='unknown':doc['prices']['NDX']['source']='unverified history'
    elif change=='old':doc['verified_at']='2026-10-01T22:00:00Z'
    elif change=='future':doc['verified_at']='2026-10-29T22:00:00Z'
    elif change=='grid':
        cp.save(q.loc[:ANCHOR].drop(q.index[-15]),n.loc[:ANCHOR],ANCHOR,[],pd.Timestamp('2026-10-22T22:00Z'))
        doc=json.loads((path/'manifest.json').read_text())
    elif change=='same_date':doc['report_date']=str(REPORT.date())
    elif change=='symlink':
        (path/'NDX.csv').rename(path/'real.csv');(path/'NDX.csv').symlink_to(path/'real.csv')
    (path/'manifest.json').write_text(json.dumps(doc))
    with pytest.raises(ValueError):dual.recover(s,REPORT,[],NOW)
    assert not state['calls']


def test_same_current_data_conflict_blocks_before_network(case):
    with pytest.raises(dual.SourceConflict):
        dual.recover(case[-1],REPORT,[{'error':'Observed Yahoo price mismatch'}],NOW)
    assert not case[3]['calls']


def test_future_rows_do_not_change_historical_result(case):
    path,q,n,state,s=case
    expected=dual.recover(s,REPORT,[],NOW)
    state['nasdaq']['data']['tradesTable']['rows'].append({'date':'10/27/2026','close':'bad','open':'bad','high':'bad','low':'bad'})
    obj=state['yahoo']['chart']['result'][0]
    obj['timestamp'].append(int(pd.Timestamp('2026-10-27T13:30Z').timestamp()))
    for values in obj['indicators']['quote'][0].values():values.append(None)
    pd.testing.assert_series_equal(dual.recover(s,REPORT,[],NOW),expected,check_exact=True)


def test_actual_loader_can_recover_when_entire_fred_transport_is_down(case,monkeypatch):
    path,q,n,state,s=case
    monkeypatch.setattr(dual,'utc',lambda value=None:NOW if value is None else pd.Timestamp(value).tz_convert('UTC'))
    # Pass explicit clock into the new route only; all FRED calls still use the real loader.
    real=dual.recover
    monkeypatch.setattr(dual,'recover',lambda session,report,audit:real(session,report,audit,NOW))
    got=ndx_loader.get_ndx_reliable(s,REPORT,[])
    pd.testing.assert_series_equal(got,n,check_exact=True,check_freq=False)
    assert dual.NASDAQ_URL in state['calls']


def test_second_extension_needs_fresh_independent_evidence(case):
    path,q,n,state,s=case
    got=dual.recover(s,REPORT,[],NOW)
    cp.save(q,got,REPORT,[],NOW)
    assert json.loads((path/'manifest.json').read_text())['prices']['NDX']['source'].startswith('FRED NASDAQ100 through 2026-10-15;')
    state['nasdaq_offline']=True
    with pytest.raises(requests.Timeout):
        dual.recover(s,pd.Timestamp('2026-10-27'),[],pd.Timestamp('2026-10-28T12:00Z'))


def test_checkpoint_gap_above_five_sessions_is_still_rejected(case):
    with pytest.raises(TailUnavailable):
        dual.recover(case[-1],pd.Timestamp('2026-11-02'),[],pd.Timestamp('2026-11-02T23:00Z'))


def test_one_session_issuer_lag_is_explicit_and_never_claims_dual_confirmation(case):
    path,q,n,state,s=case
    state['nasdaq']['data']['tradesTable']['rows'].pop()
    audit=[];got=dual.recover(s,REPORT,audit,NOW)
    pd.testing.assert_series_equal(got,n,check_freq=False,check_exact=True)
    assert audit[-1]['independently_verified_through']=='2026-10-23'
    assert audit[-1]['single_source_latest_dates']==['2026-10-26']
    assert audit[-1]['current_close_independently_confirmed'] is False
    assert 'through 2026-10-15;' in got.attrs['source']


def test_yesterday_issuer_date_can_equal_checkpoint_but_cannot_lag_it(case):
    path,q,n,state,s=case
    seed=pd.Timestamp('2026-10-23')
    cp.save(q.loc[:seed],n.loc[:seed],seed,[],pd.Timestamp('2026-10-23T22:00Z'))
    state['nasdaq']['data']['tradesTable']['rows'].pop()
    audit=[];got=dual.recover(s,REPORT,audit,NOW)
    assert audit[-1]['preserved_rows']==len(n.loc[:seed])
    assert audit[-1]['single_source_latest_dates']==['2026-10-26']
    pd.testing.assert_series_equal(got,n,check_freq=False,check_exact=True)


def test_second_day_cannot_roll_forward_lagging_official_date(case):
    path,q,n,state,s=case
    state['nasdaq']['data']['tradesTable']['rows'].pop()
    got=dual.recover(s,REPORT,[],NOW)
    cp.save(q,got,REPORT,[],NOW)
    # Issuer remains Friday; a Tuesday report requires at least Monday issuer data.
    state['headers']['Date']='Wed, 28 Oct 2026 11:59:00 GMT'
    with pytest.raises(TailUnavailable,match='more than one'):
        dual.recover(s,pd.Timestamp('2026-10-27'),[],pd.Timestamp('2026-10-28T12:00Z'))


@pytest.mark.parametrize('field,value',[('close','bad'),('open','NaN'),('low','0')])
def test_present_bad_current_row_cannot_fall_back_to_prior_issuer_date(case,field,value):
    path,q,n,state,s=case
    state['nasdaq']['data']['tradesTable']['rows'][-1][field]=value
    with pytest.raises(ValueError):dual.recover(s,REPORT,[],NOW)


def test_new_issuer_correction_to_provisional_cache_close_blocks_recovery(case):
    path,q,n,state,s=case
    seed=pd.Timestamp('2026-10-23')
    cp.save(q.loc[:seed],n.loc[:seed],seed,[],pd.Timestamp('2026-10-23T22:00Z'))
    row=state['nasdaq']['data']['tradesTable']['rows'][-2]
    row['close']=str(float(row['close'])*1.001);row['high']=str(float(row['close'])+10)
    with pytest.raises(dual.SourceConflict,match='mismatch'):
        dual.recover(s,REPORT,[],NOW)
