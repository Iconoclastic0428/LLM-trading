from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import monitor
import reliable_data as data
import automation as auto
import publish_status as pub


@pytest.fixture
def prices():
    idx = monitor._normalize_sessions(monitor._calendar().sessions_in_range('2022-01-03', '2026-09-04'))
    t = np.arange(len(idx))
    q = pd.Series(350 * np.exp(.0003*t+.014*np.sin(t/17)), index=idx, name='QQQ')
    return q, q * 40


def payload(series, symbol='QQQ'):
    stamps = ((series.index.tz_localize('America/New_York') + pd.Timedelta(hours=9, minutes=30))
              .tz_convert('UTC').asi8 // 10**9).tolist()
    return {'chart': {'error': None, 'result': [{'meta': {'symbol': symbol},
        'timestamp': stamps, 'indicators': {'quote': [{'close': series.tolist()}]}}]}}


class Response:
    def __init__(self, body): self.body = body
    def raise_for_status(self): pass
    def json(self): return self.body
    @property
    def text(self): return self.body


class Session:
    def __init__(self, bodies): self.bodies = iter(bodies); self.urls = []
    def get(self, url, **kwargs):
        self.urls.append((url, kwargs))
        body = next(self.bodies)
        if isinstance(body, Exception): raise body
        return Response(body)
    def __enter__(self): return self
    def __exit__(self, *args): pass


def test_http_200_stale_qqq_tries_second_endpoint(prices):
    q, _ = prices
    s = Session([payload(q.iloc[:-1]), payload(q.iloc[-30:-1]), payload(q)])
    out = data.get_qqq(s, q.index[-1], [])
    assert out.index[-1] == q.index[-1]
    assert s.urls[-1][0] == monitor.YAHOO_QQQ_URLS[1]


def test_recent_closes_repair_full_history_without_changing_old_prices(prices):
    q, _ = prices
    s = Session([payload(q.iloc[:-1]), payload(q.iloc[-30:])])
    out = data.get_qqq(s, q.index[-1], [])
    np.testing.assert_array_equal(out.to_numpy(), q.to_numpy())
    assert len(s.urls) == 2


def test_rejects_incompatible_tail(prices):
    q, _ = prices
    with pytest.raises(data.DataUnavailable, match='mismatch'):
        data.merge_tail(q.iloc[:-1], q.iloc[-30:] * 2, 'QQQ')


def test_rejects_insufficient_overlap(prices):
    q, _ = prices
    with pytest.raises(data.DataUnavailable, match='overlapping'):
        data.merge_tail(q.iloc[:-1], q.iloc[-4:], 'QQQ')


def test_rejects_wrong_ticker(prices):
    with pytest.raises(data.DataUnavailable, match='symbol'):
        data.chart_series(payload(prices[0], 'QLD'))


def test_rejects_missing_interior_session(prices):
    q, _ = prices
    with pytest.raises(data.DataUnavailable, match='missing trading'):
        data.validate_asof(q.drop(q.index[-60]), q.index[-1], 'QQQ')


def test_rejects_nonfinite_and_duplicates(prices):
    q, _ = prices
    bad = q.copy(); bad.iloc[-1] = float('inf')
    for series in (bad, pd.concat([q, q.iloc[-1:]])):
        with pytest.raises(data.DataUnavailable): data.clean(series, 'QQQ')


def test_does_not_take_incomplete_future_bar(prices):
    q, _ = prices
    report = q.index[-2]
    out = data.get_qqq(Session([payload(q)]), report, [])
    assert out.index[-1] == report


def test_fred_stale_refresh_with_same_series(prices):
    _, n = prices
    def csv(s): return s.rename('NASDAQ100').to_csv(index_label='observation_date')
    session = Session([csv(n.iloc[:-1]), csv(n.iloc[-30:])])
    out = data.get_ndx(session, n.index[-1], [])
    np.testing.assert_allclose(out, n)


def test_fred_stale_failure_not_forward_filled(prices):
    _, n = prices
    csv = n.iloc[:-1].rename('NASDAQ100').to_csv(index_label='observation_date')
    with pytest.raises(data.DataUnavailable):
        data.get_ndx(Session([csv, csv]), n.index[-1], [])


def test_semantic_retry_succeeds_after_stale_attempt(monkeypatch, prices):
    q, n = prices; count = [0]; sleeps = []
    def qqq(*args):
        count[0] += 1
        if count[0] == 1: raise data.DataUnavailable('HTTP 200 stale')
        return q
    monkeypatch.setattr(data, 'get_qqq', qqq)
    monkeypatch.setattr(data, 'get_ndx', lambda *args: n)
    result = data.load_prices(q.index[-1], [], sleeper=sleeps.append,
                              session_factory=lambda: Session([]))
    assert count == [2] and sleeps == [20] and result[0].equals(q)


def test_retry_exhaustion_raises(monkeypatch, prices):
    def fail(*args): raise data.DataUnavailable('stale')
    monkeypatch.setattr(data, 'get_qqq', fail)
    sleeps = []
    with pytest.raises(data.DataUnavailable, match='3 attempts'):
        data.load_prices(prices[0].index[-1], [], sleeper=sleeps.append,
                         session_factory=lambda: Session([]))
    assert sleeps == [20, 60]


@pytest.mark.parametrize('now,expected', [
    ('2026-09-01T00:30:00-07:00','2026-08-31'),
    ('2026-09-07T22:00:00-07:00','2026-09-04'),
    ('2026-09-08T06:00:00-07:00','2026-09-04'),
    ('2026-09-04T13:10:00-07:00','2026-09-03'),
    ('2026-09-04T13:30:00-07:00','2026-09-04'),
    ('2026-11-27T10:31:00-08:00','2026-11-27'),
    ('2026-11-27T10:10:00-08:00','2026-11-25'),
])
def test_last_completed_session_calendar_and_early_close(now, expected):
    assert str(auto.latest_session(now).date()) == expected


def test_weekend_month_end_catchup_and_dst():
    # July 31 2026 is Friday; target remains publishable before Monday open.
    t = auto.monthly_timing('2026-07-31', '2026-08-03T06:10:00-07:00')
    assert t['publish_notification'] and t['execution_date'] == '2026-08-03'
    t = auto.monthly_timing('2026-10-30', '2026-11-02T06:10:00-08:00')
    assert t['publish_notification']
    assert t['open_at'].startswith('2026-11-02T14:30')


def test_expiry_and_replay_cannot_publish():
    assert not auto.monthly_timing('2026-08-31','2026-09-01T06:20:00-07:00')['publish_notification']
    assert not auto.monthly_timing('2026-08-31','2026-08-31T23:00:00-07:00',True)['publish_notification']


def test_generate_preserves_frozen_signal_math_and_has_audit(tmp_path, prices):
    q, n = prices; q=q.loc[:'2026-08-31']; n=n.loc[:'2026-08-31']
    code=auto.generate(tmp_path, now='2026-09-01T05:00:00-07:00', loader=lambda *args:(q,n))
    assert code==0
    p=json.loads((tmp_path/'signal.json').read_text())
    s=monitor.snapshot_on(q,n,'2026-08-31')
    assert p['proposed_snapshot']['realized_volatility']==s.realized_volatility
    assert p['proposed_snapshot']['trend_score']==s.trend_score
    assert p['publish_notification'] is True
    assert p['sources']['QQQ']['latest_date']=='2026-08-31'
    assert (tmp_path/'verified_closes.csv').exists()


def test_generate_future_and_holiday_fail_before_network(tmp_path):
    def forbidden(*args): raise AssertionError('network must not run')
    for day in ('2026-09-07', '2026-09-06'):
        assert auto.generate(tmp_path, day, '2026-09-06T10:00:00-07:00', forbidden)==2
        assert json.loads((tmp_path/'status.json').read_text())['publish_notification'] is False


def test_historical_replay_has_no_fresh_order_text(tmp_path, prices):
    q,n=prices; q=q.loc[:'2026-08-31']; n=n.loc[:'2026-08-31']
    assert auto.generate(tmp_path,'2026-08-31','2026-09-04T23:00:00-07:00',lambda *args:(q,n))==0
    text=(tmp_path/'report.md').read_text()
    assert '禁止按历史价格补单' in text and '月末再平衡；在' not in text
    p=json.loads((tmp_path/'signal.json').read_text())
    assert not p['publish_notification']
    assert all(x['instruction']=='historical_only' for x in p['decisions'])


def sample_status():
    return {'status':'ok','mode':'live','publish_notification':True,'report_date':'2026-08-31',
            'completed_at':'2026-09-01T12:00:00+00:00',
            'month_end':{'signal_date':'2026-08-31','execution_date':'2026-09-01',
                         'publish_deadline':'2026-09-01T13:20:00+00:00'}}


def test_publisher_dedup_ignores_nonbot_and_blocks_late():
    s=sample_status(); marker=pub.signal_marker('2026-08-31')
    now=pub.parse_time('2026-09-01T12:00:00+00:00')
    fake=[{'user':{'login':'attacker'},'body':marker}]
    assert pub.publication_plan(s,fake,now)=='publish'
    real=[{'user':{'login':'github-actions[bot]'},'body':marker}]
    assert pub.publication_plan(s,real,now)=='already_published'
    late=now+__import__('datetime').timedelta(hours=2)
    assert pub.publication_plan(s,[],late)=='missed_deadline'
    assert pub.publication_plan(s,real,late)=='already_published'
    s['mode']='replay'
    assert pub.publication_plan(s,[],late)=='replay'


def test_replay_publisher_makes_no_github_calls(tmp_path):
    (tmp_path/'status.json').write_text(json.dumps(dict(sample_status(),mode='replay')))
    class NoAPI:
        def comments(self): raise AssertionError('should not call GitHub')
    assert pub.publish(tmp_path,NoAPI(),{'GITHUB_RUN_ID':'1'})==0


def test_recovery_marks_old_bot_error_resolved():
    class Client:
        repo='owner/repo'
        def __init__(self):self.calls=[]
        def api(self,*args):self.calls.append(args)
    c=Client(); s=dict(sample_status(),report_date='2026-09-04')
    error={'id':1,'user':{'login':'github-actions[bot]'},
           'body':'<!-- qld-tqqq-signal:2026-09-03:error -->\nQQQ stale'}
    pub.repair_errors(c,s,[error],'run')
    assert len(c.calls)==1
    body=c.calls[0][2]['body']
    assert '<!-- resolved -->' in body and 'QQQ stale' in body
    assert '原始诊断' in body


def test_missing_generator_output_not_reported_healthy(tmp_path):
    class Client:
        repo='owner/repo'
        def __init__(self):self.writes=[]
        def comments(self):return []
        def upsert(self,*args):self.writes.append(args)
    c=Client()
    assert pub.publish(tmp_path,c,{'GITHUB_RUN_ID':'1'})==2
    heartbeat=next(x[1] for x in c.writes if x[0]==pub.HEARTBEAT)
    assert '需要检查' in heartbeat


def test_output_failure_resets_status_to_fail_closed(tmp_path, prices, monkeypatch):
    q,n=prices; q=q.loc[:'2026-08-31']; n=n.loc[:'2026-08-31']
    original=auto.atomic_json
    def fail_signal(path,obj):
        if path.name=='month_end.json': raise OSError('disk failure')
        return original(path,obj)
    monkeypatch.setattr(auto,'atomic_json',fail_signal)
    assert auto.generate(tmp_path,now='2026-09-01T05:00:00-07:00',loader=lambda *args:(q,n))==2
    s=json.loads((tmp_path/'status.json').read_text())
    assert s['status']=='error' and s['publish_notification'] is False


def test_month_end_publishes_once_and_rechecks_cutoff(tmp_path, monkeypatch):
    class Client:
        repo='owner/repo'
        def __init__(self):self.items=[];self.markers=[]
        def comments(self):return self.items
        def upsert(self,marker,body,comments):
            self.markers.append(marker)
            old=next((c for c in self.items if c['body'].startswith(marker)),None)
            if old:old['body']=body
            else:self.items.append({'id':len(self.items)+1,'user':{'login':'github-actions[bot]'},'body':body})
    s=sample_status(); now=pub.parse_time(s['completed_at'])
    (tmp_path/'status.json').write_text(json.dumps(s))
    (tmp_path/'month_end.md').write_text(pub.signal_marker('2026-08-31')+'\nTest signal')
    monkeypatch.setattr(pub,'now_utc',lambda:now)
    c=Client();env={'GITHUB_RUN_ID':'1'}
    assert pub.publish(tmp_path,c,env,now)==0
    assert pub.publish(tmp_path,c,env,now)==0
    assert c.markers.count(pub.signal_marker('2026-08-31'))==1
    c=Client()
    monkeypatch.setattr(pub,'now_utc',lambda:pub.parse_time('2026-09-01T13:20:00Z'))
    assert pub.publish(tmp_path,c,env,now)==2
    assert pub.signal_marker('2026-08-31') not in c.markers
