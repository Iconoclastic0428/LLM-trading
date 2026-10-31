"""Only the reviewed predecessor may cross the closing-metadata fix boundary."""
import json
import pandas as pd
import pytest
from test_price_checkpoint import fixture, REPORT, NOW
import price_checkpoint as cp
from checkpoint_contract_compat import accepts_predecessor, PRIOR_RULES_SHA256, ACTIVE_RULES_SHA256


def prior_fixture(tmp_path, monkeypatch):
    q,n,folder=fixture(tmp_path,monkeypatch)
    path=folder/'manifest.json';doc=json.loads(path.read_text())
    doc['rules_sha256']=PRIOR_RULES_SHA256;path.write_text(json.dumps(doc))
    return q,n,folder


def test_transition_only_for_exact_reviewed_active_contract():
    assert cp._rules_hash()==ACTIVE_RULES_SHA256
    assert accepts_predecessor(PRIOR_RULES_SHA256,cp._rules_hash())
    assert not accepts_predecessor('unknown',cp._rules_hash())
    assert not accepts_predecessor(PRIOR_RULES_SHA256,'unreviewed future code')


def test_prior_checkpoint_revalidated_without_rewriting_metadata(tmp_path,monkeypatch):
    q,n,folder=prior_fixture(tmp_path,monkeypatch);before=(folder/'manifest.json').read_bytes();audit=[]
    got=cp.restore(REPORT,audit,NOW)
    assert got is not None
    pd.testing.assert_series_equal(got[0],q,check_freq=False)
    pd.testing.assert_series_equal(got[1],n,check_freq=False)
    assert (folder/'manifest.json').read_bytes()==before
    assert audit[-1]['reviewed_contract_transition'] is True
    assert audit[-1]['original_verified_at']==NOW.isoformat()
    assert audit[-1]['new_market_observation'] is False


@pytest.mark.parametrize('change',['date','age','bytes','contract','future_code','conflict','version'])
def test_transition_does_not_bypass_any_validation(tmp_path,monkeypatch,change):
    q,n,folder=prior_fixture(tmp_path,monkeypatch);report=REPORT;now=NOW;audit=[]
    path=folder/'manifest.json';doc=json.loads(path.read_text())
    if change=='date':report=REPORT+pd.Timedelta(days=1)
    elif change=='age':now=NOW+pd.Timedelta(days=8)
    elif change=='bytes':(folder/'NDX.csv').write_text((folder/'NDX.csv').read_text()+'\n')
    elif change=='contract':doc['rules_sha256']='unknown';path.write_text(json.dumps(doc))
    elif change=='future_code':monkeypatch.setattr(cp,'_rules_hash',lambda:'unreviewed new rules')
    elif change=='conflict':audit=[{'error':'Observed price conflict'}]
    elif change=='version':doc['version']='unknown';path.write_text(json.dumps(doc))
    assert cp.restore(report,audit,now) is None
