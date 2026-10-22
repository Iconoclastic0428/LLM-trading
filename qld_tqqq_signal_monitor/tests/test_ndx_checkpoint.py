"""FRED transport outage must not block a verified, bounded live tail."""
from pathlib import Path
from copy import deepcopy
import json
import sys
import numpy as np
import pandas as pd
import pytest
import requests
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import monitor
import price_checkpoint as cp
import ndx_checkpoint as nc
import ndx_fallback as fb
import ndx_loader
import reliable_data as data
REPORT=pd.Timestamp('2026-10-21')
NOW=pd.Timestamp('2026-10-22T00:45:00Z')

@pytest.fixture
def case(tmp_path,monkeypatch):
    monkeypatch.setenv('PRICE_CHECKPOINT_DIR',str(tmp_path/'cache'))
    cal=monitor._calendar(); idx=fb.sessions(cal,'2023-01-03',REPORT)
    x=np.arange(len(idx)); q=pd.Series(500*np.exp(.0002*x+.006*np.sin(x/17)),index=idx,name='QQQ')
    n=(q*40).rename('NDX');n.attrs['source']=monitor.FRED_NDX_URL;q.attrs['source']='test QQQ'
    anchor=pd.Timestamp('2026-10-19');cp.save(q.loc[:anchor],n.loc[:anchor],anchor,[],NOW-pd.Timedelta(days=1))
    s=n.iloc[-65:]
    raw={'chart':{'error':None,'result':[{'meta':{'symbol':'^NDX','instrumentType':'INDEX','currency':'USD',
        'exchangeTimezoneName':'America/New_York','dataGranularity':'1d','regularMarketTime':int(pd.Timestamp('2026-10-21T21:00:00Z').timestamp())},
        'timestamp':[int((d.tz_localize('America/New_York')+pd.Timedelta(hours=9,minutes=30)).timestamp()) for d in s.index],
        'indicators':{'quote':[{'open':s.tolist(),'high':(s*1.01).tolist(),'low':(s*.99).tolist(),'close':s.tolist()}]}}]}}
    class Session:
        def get(self,url,**kwargs):
            if url==monitor.FRED_NDX_URL:raise requests.Timeout('injected FRED outage')
            assert url in fb.URLS
            r=requests.Response();r.status_code=200;r._content=json.dumps(raw).encode();return r
    return tmp_path/'cache',q,n,raw,Session

def test_prior_cache_requires_new_bars_preserves_full_history(case):
    folder,q,n,raw,S=case;a=[];out=nc.recover(S(),REPORT,a,NOW)
    pd.testing.assert_series_equal(out,n,check_exact=True,check_freq=False)
    detail=a[-1];assert detail['appended_dates']==['2026-10-20','2026-10-21']
    assert detail['fred_origin_date']=='2026-10-19' and detail['preserved_rows']==len(n)-2
    assert detail['live_tail_required'] and not detail['execution_authorized']
    assert json.loads((folder/'manifest.json').read_text())['report_date']=='2026-10-19'

def test_loader_exercises_new_fallback(case,monkeypatch):
    folder,q,n,raw,S=case
    real=nc.recover;monkeypatch.setattr(nc,'recover',lambda session,report,audit:real(session,report,audit,NOW))
    a=[];out=ndx_loader.get_ndx_reliable(S(),REPORT,a)
    pd.testing.assert_series_equal(out,n,check_exact=True,check_freq=False)
    assert any(x.get('request')=='ndx_checkpoint_live_append' and x.get('status')=='ok' for x in a)

@pytest.mark.parametrize('field',['open','high','low','close'])
def test_new_ohlc_missing_is_not_filled(case,field):
    folder,q,n,raw,S=case;raw['chart']['result'][0]['indicators']['quote'][0][field][-1]=None
    with pytest.raises(fb.TailUnavailable):nc.recover(S(),REPORT,[],NOW)

@pytest.mark.parametrize('change',['hash','future','contract','symlink','unknown_source'])
def test_cache_failure_never_authorizes_new_close(case,change):
    folder,q,n,raw,S=case;p=folder/'manifest.json';doc=json.loads(p.read_text())
    if change=='hash':(folder/'NDX.csv').write_text((folder/'NDX.csv').read_text()+'\n')
    elif change=='future':doc['verified_at']='2026-10-23T00:00:00Z'
    elif change=='contract':doc['rules_sha256']='bad'
    elif change=='unknown_source':doc['prices']['NDX']['source']='unknown index'
    elif change=='symlink':
        f=folder/'NDX.csv';body=f.read_bytes();f.unlink();o=folder.parent/'other';o.write_bytes(body);f.symlink_to(o)
    p.write_text(json.dumps(doc))
    with pytest.raises((fb.TailUnavailable,ValueError)):nc.recover(S(),REPORT,[],NOW)

@pytest.mark.parametrize('message',['NDX overlap mismatch','Observed price conflict','Wrong symbol','duplicate daily closes'])
def test_live_conflicts_cannot_be_overruled(case,message):
    folder,q,n,raw,S=case
    with pytest.raises(fb.TailUnavailable,match='conflict'):nc.recover(S(),REPORT,[{'error':message}],NOW)

def test_original_fred_age_not_renewed_by_cached_append(case):
    folder,q,n,raw,S=case;p=folder/'manifest.json';doc=json.loads(p.read_text())
    doc['prices']['NDX']['source']='FRED NASDAQ100 through 2026-10-12; verified checkpoint through 2026-10-19; append-only daily ^NDX'
    p.write_text(json.dumps(doc))
    with pytest.raises(fb.TailUnavailable,match='five sessions'):nc.recover(S(),REPORT,[],NOW)

def test_five_session_boundary_allowed(case):
    folder,q,n,raw,S=case;p=folder/'manifest.json';doc=json.loads(p.read_text())
    doc['prices']['NDX']['source']='FRED NASDAQ100 through 2026-10-14; append-only daily ^NDX'
    p.write_text(json.dumps(doc));out=nc.recover(S(),REPORT,[],NOW)
    assert 'through 2026-10-14;' in out.attrs['source']

def test_all_transports_offline_cannot_advance_session(case):
    class Offline:
        def get(self,*a,**kw):raise requests.ConnectionError('offline')
    with pytest.raises(fb.TailUnavailable):nc.recover(Offline(),REPORT,[],NOW)

def test_overlap_conflict_rejected(case):
    folder,q,n,raw,S=case;raw['chart']['result'][0]['indicators']['quote'][0]['close'][-10]*=1.01
    with pytest.raises(fb.TailUnavailable,match='mismatch'):nc.recover(S(),REPORT,[],NOW)

def test_earlier_cache_not_same_session_restore(case):
    assert cp.restore(REPORT,[],NOW) is None

def test_future_report_without_new_bar_rejected(case):
    folder,q,n,raw,S=case
    with pytest.raises(fb.TailUnavailable):nc.recover(S(),REPORT+pd.Timedelta(days=1),[],NOW)
