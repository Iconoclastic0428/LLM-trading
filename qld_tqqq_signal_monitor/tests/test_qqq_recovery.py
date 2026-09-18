from copy import deepcopy
from datetime import datetime, timezone
from email.utils import format_datetime
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest
import requests

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import monitor
import reliable_data as data
import qqq_recovery as q
import health_checkpoint as hc
import publish_status as pub

REPORT = pd.Timestamp('2026-09-17')
NOW = pd.Timestamp('2026-09-18T02:50:00Z')

@pytest.fixture
def example():
    dates = monitor._normalize_sessions(monitor._calendar().sessions_in_range('2024-01-02',REPORT))
    values = 350*np.exp(.0003*np.arange(len(dates))+.015*np.sin(np.arange(len(dates))/19))
    series = pd.Series(values,index=dates,name='QQQ')
    rows = [{'date':str(d.strftime('%m/%d/%Y')),'open':str(v),'high':str(v*1.01),'low':str(v*.99),'close':str(v),'volume':'1000000'}
            for d,v in series.iloc[-62:].iloc[::-1].items()]
    raw = {'status':{'rCode':200},'data':{'symbol':'QQQ','tradesTable':{'headers':{
        'date':'Date','open':'Open','high':'High','low':'Low','close':'Close/Last'},'rows':rows}}}
    return series, raw


def response(raw, date='Fri, 18 Sep 2026 02:50:00 GMT', age='0'):
    r=requests.Response();r.status_code=200;r._content=json.dumps(raw).encode()
    r.headers.update({'Date':date,'Age':age});return r


def chart(series):
    ts=(series.index.tz_localize('America/New_York')+pd.Timedelta(hours=9,minutes=30)).tz_convert('UTC').asi8//10**9
    return {'chart':{'error':None,'result':[{'meta':{'symbol':'QQQ'},'timestamp':list(map(int,ts)),
        'indicators':{'quote':[{'close':series.tolist()}]}}]}}


def test_actual_17th_after_stale_16th_appended_exactly(example):
    s,raw=example
    tail=q.parse_history(raw,REPORT,monitor._calendar(),NOW)
    restored,audit=q.append_history(s.iloc[:-1],tail,REPORT,monitor._calendar(),NOW)
    pd.testing.assert_series_equal(restored,s,check_freq=False)
    assert audit['appended']=={'2026-09-17':s.iloc[-1]}
    assert audit['overlap_sessions']==20

@pytest.mark.parametrize('gap',[1,2,3,4,5])
def test_append_bounds_and_original_history_unchanged(example,gap):
    s,raw=example;anchor=s.iloc[:-gap]
    tail=q.parse_history(raw,REPORT,monitor._calendar(),NOW)
    out,a=q.append_history(anchor,tail,REPORT,monitor._calendar(),NOW)
    pd.testing.assert_series_equal(out.loc[anchor.index],anchor,check_freq=False)
    assert len(a['appended'])==gap

@pytest.mark.parametrize('fault',['wrong_symbol','bad_status','quote','duplicate','null','negative','range','stale','non_session'])
def test_reject_bad_historical_table(example,fault):
    _,raw=example;raw=deepcopy(raw);table=raw['data']['tradesTable'];rows=table['rows']
    if fault=='wrong_symbol':raw['data']['symbol']='TQQQ'
    if fault=='bad_status':raw['status']['rCode']=500
    if fault=='quote':table['headers']['close']='Last Sale'
    if fault=='duplicate':rows.append(rows[0])
    if fault=='null':rows[0]['close']=None
    if fault=='negative':rows[0]['close']='-1'
    if fault=='range':rows[0]['close']='10000'
    if fault=='stale':table['rows']=rows[1:]
    if fault=='non_session':rows[-1]['date']='09/12/2026'
    with pytest.raises((ValueError,TypeError)):
        q.parse_history(raw,REPORT,monitor._calendar(),NOW)

@pytest.mark.parametrize('fault',['short','gap','too_stale','no_gap','overlap','scale','tail_gap'])
def test_reject_invalid_anchor_or_overlap(example,fault):
    s,raw=example;h=s.iloc[:-1];tail=q.parse_history(raw,REPORT,monitor._calendar(),NOW)
    if fault=='short':h=h.iloc[-499:]
    if fault=='gap':h=h.drop(h.index[-45])
    if fault=='too_stale':h=s.iloc[:-6]
    if fault=='no_gap':h=s
    if fault=='overlap':tail=tail.drop(h.index[-15])
    if fault=='scale':tail=tail*1.01
    if fault=='tail_gap':tail=tail.drop(REPORT)
    with pytest.raises(ValueError):q.append_history(h,tail,REPORT,monitor._calendar(),NOW)


def test_future_values_do_not_modify_past(example):
    s,raw=example;future=deepcopy(raw)
    future['data']['tradesTable']['rows'].append({'date':'09/18/2026','close':None,'open':None,'high':None,'low':None})
    pd.testing.assert_series_equal(q.parse_history(raw,REPORT,monitor._calendar(),NOW),
                                  q.parse_history(future,REPORT,monitor._calendar(),NOW))


def test_preclose_fails_without_http(example):
    s,_=example
    class Forbidden:
        def get(self,*a,**k):pytest.fail('no HTTP before session completes')
    with pytest.raises(ValueError):q.fetch_tail(Forbidden(),s.iloc[:-1],REPORT,[],monitor._calendar(),'2026-09-17T19:00:00Z')

@pytest.mark.parametrize('date,age',[('Thu, 17 Sep 2026 19:00:00 GMT','0'),('Fri, 18 Sep 2026 02:50:00 GMT','40000'),('Fri, 18 Sep 2026 03:50:00 GMT','0'),('Fri, 18 Sep 2026 02:50:00 GMT','-1')])
def test_cached_intraday_http_rejected(example,date,age):
    s,raw=example
    class S:
        def get(self,*a,**k):return response(raw,date,age)
    with pytest.raises(ValueError):q.fetch_tail(S(),s.iloc[:-1],REPORT,[],monitor._calendar(),NOW)


def test_primary_success_no_recovery_request(monkeypatch,example):
    s,_=example;monkeypatch.setattr(data,'get_qqq',lambda *a:s)
    class Forbidden:
        def get(self,*a,**k):pytest.fail('unnecessary recovery')
    assert q.get_qqq_reliable(Forbidden(),REPORT,[]) is s


def test_stale_yahoo_end_to_end_source_wrapper(monkeypatch,example):
    s,raw=example
    class S:
        def get(self,url,**kwargs):
            return response(raw if url==q.NASDAQ_URL else chart(s.iloc[:-1]))
    audit=[]
    result=q.get_qqq_reliable(S(),REPORT,audit)
    assert result.index[-1]==REPORT
    assert 'Nasdaq' in result.attrs['source']
    # pandas 3 may decode epoch seconds as microseconds while the synthetic
    # calendar uses nanoseconds. Compare identical dates at a common resolution,
    # and separately require exact preservation of the decoded anchor itself.
    expected = s.copy()
    expected.index = expected.index.as_unit(result.index.unit)
    pd.testing.assert_series_equal(result,expected,check_freq=False)
    anchor = data.chart_series(chart(s.iloc[:-1]))
    pd.testing.assert_series_equal(result.loc[anchor.index],anchor,check_freq=False)
    assert any(x.get('request')=='verified_qqq_tail' for x in audit)


def test_both_providers_stale_block_signal(example):
    s,raw=example;raw=deepcopy(raw);raw['data']['tradesTable']['rows']=raw['data']['tradesTable']['rows'][1:]
    class S:
        def get(self,url,**kwargs):return response(raw if url==q.NASDAQ_URL else chart(s.iloc[:-1]))
    with pytest.raises(data.DataUnavailable):q.get_qqq_reliable(S(),REPORT,[])


def status(report='2026-09-17',at='2026-09-17T22:03:28+00:00'):
    return {'status':'ok','mode':'live','report_date':report,'expected_session':report,'completed_at':at,
            'data_sha256':'a'*64}


def bot(checkpoint):
    return {'user':{'login':'github-actions[bot]'},'body':pub.HEARTBEAT+'\n'+hc.encode(checkpoint)}


def test_later_failure_does_not_erase_verified_session():
    now=NOW.to_pydatetime();last=hc.choose(status(),[],{'GITHUB_RUN_ID':'10'},now)
    failure=dict(status(),status='error',completed_at=NOW.isoformat())
    assert hc.choose(failure,[bot(last)],{'GITHUB_RUN_ID':'11'},now)==last

@pytest.mark.parametrize('mode,state',[('replay','ok'),('live','error'),('live','running')])
def test_replay_or_failure_cannot_create_verified_checkpoint(mode,state):
    s=dict(status(),mode=mode,status=state)
    assert hc.choose(s,[],{'GITHUB_RUN_ID':'10'},NOW.to_pydatetime()) is None


def test_untrusted_future_or_corrupt_checkpoint_ignored():
    now=NOW.to_pydatetime();valid=hc.choose(status(),[],{'GITHUB_RUN_ID':'10'},now)
    user=bot(valid);user['user']['login']='attacker'
    assert hc.choose({},[user],{},now) is None
    for key,val in [('report_date','invalid'),('verified_at','2027-01-01T00:00:00Z'),('run_id','12\nrun=true'),('data_sha256','x')]:
        bad=dict(valid);bad[key]=val
        assert hc.choose({},[bot(bad)],{},now) is None


def test_same_or_older_report_cannot_roll_back_checkpoint():
    now=NOW.to_pydatetime();last=hc.choose(status(),[],{'GITHUB_RUN_ID':'10'},now)
    assert hc.choose(status('2026-09-16'),[bot(last)],{'GITHUB_RUN_ID':'11'},now)==last


def test_publisher_remains_red_and_preserves_prior_success(tmp_path):
    now=NOW.to_pydatetime();last=hc.choose(status(),[],{'GITHUB_RUN_ID':'10'},now)
    class Client:
        repo='owner/repo'
        def __init__(self):self.items=[bot(last)];self.writes=[]
        def comments(self):return self.items
        def upsert(self,*args):self.writes.append(args)
    c=Client();s=dict(status(),status='error',error='QQQ stale')
    (tmp_path/'status.json').write_text(json.dumps(s))
    assert pub.publish(tmp_path,c,{'GITHUB_RUN_ID':'11'},now)==2
    published=json.loads((tmp_path/'publication.json').read_text())
    assert published['latest_attempt_status']=='error'
    assert published['last_verified']==last
    body=next(x[1] for x in c.writes if x[0]==pub.HEARTBEAT)
    assert '本次失败' in body and '最近成功核验交易日' in body
