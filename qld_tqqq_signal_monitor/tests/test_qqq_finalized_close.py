from __future__ import annotations
import copy
import json
from pathlib import Path
import sys
import numpy as np
import pandas as pd
import pytest
import requests

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import monitor
import reliable_data as data
import qqq_tail as tail
import qqq_recovery as recovery

FIX=Path(__file__).parent/'fixtures/qqq_close_20261008.json'
REPORT=pd.Timestamp('2026-10-08')
NOW=pd.Timestamp('2026-10-09T00:30:00Z')
CAL=monitor._calendar()

def load(name):
 # Exact captured relevant rows; synthetic older warmup is deliberately labeled.
 # Full captured-history replay is performed separately in the incident audit.
 fixture=json.loads(FIX.read_text())
 if name in ('exchange','reference'):return fixture[name]
 fields=['open','high','low','close']
 if name=='daily':
  rows=fixture['daily_rows']
  first=pd.Timestamp(rows[0][0],unit='s',tz='UTC').tz_convert('America/New_York').normalize().tz_localize(None)
  older=monitor._normalize_sessions(CAL.sessions_in_range(first-pd.Timedelta(days=900),first))[:-1][-574:]
  padding=[[int((d.tz_localize('America/New_York')+pd.Timedelta(hours=9,minutes=30)).timestamp()),500.,501.,499.,500.] for d in older]
  rows=padding+rows;ts=[r[0] for r in rows];values=[r[1:] for r in rows]
 else:
  fields.append('volume');values=fixture['intraday_rows']
  ts=[fixture['intraday_start']+300*n for n in range(len(values))]
 return {'chart':{'error':None,'result':[{'meta':fixture[name+'_meta'],'timestamp':ts,
  'indicators':{'quote':[{f:[r[j] for r in values] for j,f in enumerate(fields)}]}}]}}

class Source:
 def __init__(self): self.calls=[]; self.info=load('exchange'); self.intraday=load('intraday'); self.daily=load('daily'); self.reference=load('reference')
 def get(self,url,**kw):
  self.calls.append((url,kw))
  if url==tail.INFO_URL:obj=self.info
  elif url==recovery.NASDAQ_URL:obj=self.reference
  elif url in monitor.YAHOO_QQQ_URLS:obj=self.intraday if kw.get('params',{}).get('interval')=='5m' else self.daily
  else:raise AssertionError(url)
  r=requests.Response();r.status_code=200;r._content=json.dumps(obj).encode();r.headers['Date']='Fri, 09 Oct 2026 00:28:30 GMT';return r

def test_captured_incident_tail_old_reader_fails_new_reader_preserves_history():
 s=Source();audit=[]
 with pytest.raises(data.DataUnavailable):data.get_qqq(s,REPORT,audit)
 h=data.chart_series(s.daily)
 with pytest.raises(recovery.RecoveryError,match='stale'):recovery.fetch_tail(s,h,REPORT,[],CAL,NOW)
 q=recovery.get_qqq_reliable(s,REPORT,audit)
 pd.testing.assert_series_equal(q.loc[h.index],h,check_freq=False)
 assert len(q)==len(h)+1 and q.loc[REPORT]==747.58
 assert 'Nasdaq explicitly finalized' in q.attrs['source']
 entry=[a for a in audit if a.get('request')=='qqq_finalized_recovery'][0]
 assert entry['selected_exchange_field']=='secondaryData' and entry['regular_bars']==78
 assert entry['overlap_sessions']==20
 assert entry['max_relative_overlap_error']<0.0001
 assert abs(q.loc[REPORT]-747.7879)>.1  # never use the live after-hours primary quote

@pytest.mark.parametrize('field,value',[('symbol','SPY'),('assetClass','STOCKS'),('exchange','NYSE')])
def test_wrong_exchange_identity(field,value):
 p=load('exchange');p['data'][field]=value
 with pytest.raises(recovery.RecoveryError):tail.exchange_close(p,REPORT,CAL,NOW)

@pytest.mark.parametrize('text',['Oct 8, 2026 4:00 PM ET','Closed at Oct 7, 2026 4:00 PM ET','Closed at Oct 8, 2026 3:59 PM ET','Closed at Oct 9, 2026 4:00 PM ET'])
def test_requires_explicit_same_date_exact_exchange_close(text):
 p=load('exchange');p['data']['secondaryData']['lastTradeTimestamp']=text
 with pytest.raises(recovery.RecoveryError):tail.exchange_close(p,REPORT,CAL,NOW)

def test_primary_secondary_order_is_not_assumed():
 p=load('exchange');d=p['data'];d['primaryData'],d['secondaryData']=d['secondaryData'],d['primaryData']
 assert tail.exchange_close(p,REPORT,CAL,NOW)[0]==747.58

def test_two_final_closes_must_not_conflict():
 p=load('exchange');p['data']['primaryData']=copy.deepcopy(p['data']['secondaryData']);p['data']['primaryData']['lastSalePrice']='$748.00'
 with pytest.raises(recovery.RecoveryError):tail.exchange_close(p,REPORT,CAL,NOW)

@pytest.mark.parametrize('value',['$nan','$-1.00','$0.00','747.58','$747.5810'])
def test_bad_money(value):
 p=load('exchange');p['data']['secondaryData']['lastSalePrice']=value
 with pytest.raises(recovery.RecoveryError):tail.exchange_close(p,REPORT,CAL,NOW)

@pytest.mark.parametrize('at',['2026-10-08T19:59:00Z','2026-10-08T20:29:59Z'])
def test_no_intraday_or_early_verification(at):
 with pytest.raises(recovery.RecoveryError):tail.exchange_close(load('exchange'),REPORT,CAL,at)

@pytest.mark.parametrize('offset',[0,40,78])
def test_every_regular_bar_and_closing_marker_required(offset):
 p=load('intraday');o=p['chart']['result'][0];o['timestamp'].pop(offset)
 for v in o['indicators']['quote'][0].values():v.pop(offset)
 with pytest.raises(recovery.RecoveryError):tail.yahoo_session(p,REPORT,CAL,NOW)

@pytest.mark.parametrize('field',['open','high','low','close','volume'])
def test_null_bar_rejected(field):
 p=load('intraday');p['chart']['result'][0]['indicators']['quote'][0][field][30]=None
 with pytest.raises(recovery.RecoveryError):tail.yahoo_session(p,REPORT,CAL,NOW)

def test_afterhours_bars_never_change_close():
 p=load('intraday');o=p['chart']['result'][0];o['timestamp'].append(1791489900)
 for values in o['indicators']['quote'][0].values():values.append(9999)
 assert tail.yahoo_session(p,REPORT,CAL,NOW)[0]==pytest.approx(747.58,abs=.0001)

@pytest.mark.parametrize('change',['tick','marker','regular_price','metadata','length','duplicate'])
def test_bad_intraday_contract(change):
 p=load('intraday');o=p['chart']['result'][0]
 if change=='tick':o['meta']['regularMarketTime']-=300
 if change=='marker':o['indicators']['quote'][0]['volume'][-1]=100
 if change=='regular_price':o['meta']['regularMarketPrice']+=1
 if change=='metadata':o['meta']['currency']='EUR'
 if change=='length':o['indicators']['quote'][0]['close'].pop()
 if change=='duplicate':o['timestamp'][-1]=o['timestamp'][-2]
 with pytest.raises(recovery.RecoveryError):tail.yahoo_session(p,REPORT,CAL,NOW)

@pytest.mark.parametrize('field,value',[('open',float('nan')),('high',700),('low',800),('close',748.50)])
def test_daily_evidence_or_observed_conflict_cannot_be_hidden(field,value):
 p=load('daily');p['chart']['result'][0]['indicators']['quote'][0][field][-1]=value
 with pytest.raises(recovery.RecoveryError):tail.daily_evidence(p,REPORT,747.58)

def test_hist_reference_conflict_still_fails():
 s=Source();s.reference['data']['tradesTable']['rows'][3].update(close='999.00', high='1000.00')
 with pytest.raises(recovery.RecoveryError,match='mismatch'):
  tail.fetch_closing(s,data.chart_series(s.daily),REPORT,[],CAL,s.daily,NOW)

def test_no_more_than_one_missing_tail_date():
 s=Source();h=data.chart_series(s.daily).iloc[:-1]
 with pytest.raises(recovery.RecoveryError,match='exactly one'):
  tail.fetch_closing(s,h,REPORT,[],CAL,s.daily,NOW)

def test_http_stale_response_rejected():
 class Stale(Source):
  def get(self,*a,**k):
   r=super().get(*a,**k);r.headers['Date']='Thu, 08 Oct 2026 19:00:00 GMT';return r
 s=Stale()
 with pytest.raises(recovery.RecoveryError,match='response time'):
  tail.fetch_closing(s,data.chart_series(s.daily),REPORT,[],CAL,s.daily,NOW)

def test_nasdaq_yahoo_disagreement_rejected():
 s=Source();s.info['data']['secondaryData']['lastSalePrice']='$748.00'
 with pytest.raises(recovery.RecoveryError,match='mismatch'):
  tail.fetch_closing(s,data.chart_series(s.daily),REPORT,[],CAL,s.daily,NOW)


def test_exchange_calendar_half_day_is_not_hardcoded_to_1600():
 report=pd.Timestamp('2025-11-28')
 close=CAL.session_close(report)
 p=load('exchange')
 p['data']['secondaryData']['lastTradeTimestamp']='Closed at Nov 28, 2025 1:00 PM ET'
 assert tail.exchange_close(p,report,CAL,close+pd.Timedelta(hours=1))[0]==747.58
 p['data']['secondaryData']['lastTradeTimestamp']='Closed at Nov 28, 2025 4:00 PM ET'
 with pytest.raises(recovery.RecoveryError):tail.exchange_close(p,report,CAL,close+pd.Timedelta(hours=4))


def test_reference_needs_twenty_consecutive_valid_observations():
 s=Source();s.reference['data']['tradesTable']['rows']=s.reference['data']['tradesTable']['rows'][:19]
 h=data.chart_series(s.daily)
 with pytest.raises(recovery.RecoveryError):tail.fetch_closing(s,h,REPORT,[],CAL,s.daily,NOW)
