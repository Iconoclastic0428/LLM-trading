from pathlib import Path
import json
import sys
import numpy as np
import pandas as pd
import pytest

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import price_checkpoint as checkpoint
import monitor
import reliable_data

REPORT=pd.Timestamp('2026-10-08')
NOW=pd.Timestamp('2026-10-09T00:30:00Z')

def fixture(tmp_path,monkeypatch):
 monkeypatch.setenv('PRICE_CHECKPOINT_DIR',str(tmp_path/'cache'))
 real_utc=checkpoint.utc
 monkeypatch.setattr(checkpoint,'utc',lambda value=None: real_utc(NOW+pd.Timedelta(minutes=1) if value is None else value))
 grid=monitor._normalize_sessions(monitor._calendar().sessions_in_range('2023-01-03',REPORT))
 t=np.arange(len(grid));q=pd.Series(400*np.exp(.0003*t+.01*np.sin(t/20)),index=grid,name='QQQ')
 n=(q*40).rename('NDX');q.attrs['source']='validated QQQ test';n.attrs['source']='validated NDX test'
 audit=[];checkpoint.save(q,n,REPORT,audit,NOW)
 assert audit[-1]['status']=='staged'
 return q,n,tmp_path/'cache'

def test_same_report_exact_round_trip_and_full_seed_history(tmp_path,monkeypatch):
 q,n,folder=fixture(tmp_path,monkeypatch);audit=[]
 got=checkpoint.restore(REPORT,audit,NOW+pd.Timedelta(minutes=30))
 assert got is not None
 pd.testing.assert_series_equal(got[0],q,check_freq=False)
 pd.testing.assert_series_equal(got[1],n,check_freq=False)
 assert len(got[0])>500
 assert monitor.build_decision(*got,REPORT)==monitor.build_decision(q,n,REPORT)
 assert audit[-1]['new_market_observation'] is False
 assert audit[-1]['original_verified_at']==NOW.isoformat()

@pytest.mark.parametrize('report',['2026-10-07','2026-10-09'])
def test_never_promotes_old_data_to_new_day(tmp_path,monkeypatch,report):
 fixture(tmp_path,monkeypatch)
 assert checkpoint.restore(pd.Timestamp(report),[],NOW) is None

@pytest.mark.parametrize('field,value',[('report_date','2026-10-07'),('version','old'),('rules_sha256','wrong'),('verified_at','2026-10-08T19:00:00Z'),('verified_at','2026-10-10T00:00:00Z')])
def test_bad_manifest_rejected(tmp_path,monkeypatch,field,value):
 _,_,f=fixture(tmp_path,monkeypatch);p=f/'manifest.json';d=json.loads(p.read_text());d[field]=value;p.write_text(json.dumps(d))
 assert checkpoint.restore(REPORT,[],NOW) is None

def test_changed_price_bytes_rejected(tmp_path,monkeypatch):
 _,_,f=fixture(tmp_path,monkeypatch);p=f/'QQQ.csv';p.write_text(p.read_text()+'\n')
 assert checkpoint.restore(REPORT,[],NOW) is None

def test_contract_change_rejected(tmp_path,monkeypatch):
 fixture(tmp_path,monkeypatch);monkeypatch.setattr(checkpoint,'_rules_hash',lambda:'newcode')
 assert checkpoint.restore(REPORT,[],NOW) is None

def test_symlink_rejected(tmp_path,monkeypatch):
 _,_,f=fixture(tmp_path,monkeypatch);p=f/'QQQ.csv';body=p.read_bytes();p.unlink();other=tmp_path/'elsewhere';other.write_bytes(body);p.symlink_to(other)
 assert checkpoint.restore(REPORT,[],NOW) is None

def test_no_cache_by_default(monkeypatch):
 monkeypatch.delenv('PRICE_CHECKPOINT_DIR',raising=False)
 assert checkpoint.restore(REPORT,[],NOW) is None

def test_stale_verification_not_extended(tmp_path,monkeypatch):
 fixture(tmp_path,monkeypatch)
 assert checkpoint.restore(REPORT,[],NOW+pd.Timedelta(days=8)) is None

def test_network_outage_uses_same_session_only(tmp_path,monkeypatch):
 q,n,_=fixture(tmp_path,monkeypatch)
 import qqq_recovery
 def unavailable(*args):raise reliable_data.DataUnavailable('QQQ: required current close, latest prior session')
 monkeypatch.setattr(qqq_recovery,'get_qqq_reliable',unavailable)
 audit=[];a,b=reliable_data.load_prices(REPORT,audit,attempts=1)
 pd.testing.assert_series_equal(a,q,check_freq=False)
 assert audit[-1]['request']=='checkpoint_restore'
 with pytest.raises(reliable_data.DataUnavailable):reliable_data.load_prices(REPORT+pd.Timedelta(days=1),[],attempts=1)

def test_cache_cannot_overrule_observed_conflict(tmp_path,monkeypatch):
 fixture(tmp_path,monkeypatch)
 import qqq_recovery
 def conflict(*args):raise reliable_data.DataUnavailable('QQQ price-basis mismatch')
 monkeypatch.setattr(qqq_recovery,'get_qqq_reliable',conflict)
 with pytest.raises(reliable_data.DataUnavailable):reliable_data.load_prices(REPORT,[],attempts=1)


def test_checkpoint_preserves_datetime_storage_unit_and_name(tmp_path,monkeypatch):
 q,n,f=fixture(tmp_path,monkeypatch)
 q.index=q.index.as_unit('s');q.index.name='original_date'
 checkpoint.save(q,n,REPORT,[],NOW)
 got=checkpoint.restore(REPORT,[],NOW)
 pd.testing.assert_series_equal(got[0],q,check_freq=False)


def test_workflow_cache_never_saves_replay_or_failed_generation():
 text=(ROOT.parent/'.github/workflows/qld-tqqq-signal.yml').read_text()
 assert "steps.generate.outcome == 'success' && !inputs.report_date" in text
 assert "PRICE_CHECKPOINT_DIR: ${{ !inputs.report_date && 'price_checkpoint' || '' }}" in text
 assert text.index('Publish signal and recovery status') < text.index('Save verified full-history checkpoint')
 assert 'caa296126883cff596d87d8935842f9db880ef25' in text
 assert 'verified-prices-v1-${{ github.run_id }}-${{ github.run_attempt }}' in text


@pytest.mark.parametrize('message',['Observed daily close conflicts with finalized exchange close','Missing or conflicting explicitly finalized Nasdaq close'])
def test_conflicting_live_observation_also_blocks_direct_restore(tmp_path,monkeypatch,message):
 fixture(tmp_path,monkeypatch)
 assert checkpoint.restore(REPORT,[{'error':message}],NOW) is None
