"""Missing optional VIX statistics never become a fabricated zero amplitude."""
import copy
import hashlib
import json
import numpy as np
import pytest
import vix_shadow as shadow
from test_vix_shadow import example, core, fake_bundle, verified_real_bundle


def missing(*args):
    raise shadow.ShadowError('No current VIX close')


@pytest.mark.parametrize('score', [1/3, 2/3, 1])
@pytest.mark.parametrize('vol', [.1, .4, 2])
def test_none_features_positive_core_same_for_every_amplitude(score, vol):
    v,grid=example(); f=shadow.features(v,grid,grid[-1]); snapshot=core(f,score,vol)
    original=copy.deepcopy(snapshot); known=shadow.targets(snapshot,None)
    for amp in (0,.1,.5,1):
        changed={**f,'c20_amplitude':amp,'q70_amplitude':amp}
        assert shadow.targets(snapshot,changed)==known
    assert snapshot==original
    assert known['BASE']==known['C20']==known['Q70']


@pytest.mark.parametrize('score', [0, -1, float('nan'), float('inf'), 2])
def test_no_optional_path_for_zero_or_invalid_core(score):
    with pytest.raises(shadow.ShadowError):
        shadow.targets(core({'signal_date':'2026-08-31'}, score=score),None)


@pytest.mark.parametrize('change', ['weight','leverage','products','vol','date'])
def test_inconsistent_core_still_rejected_without_vix(change):
    snapshot=core({'signal_date':'2026-08-31'},score=1)
    if change=='weight':snapshot['products'][0]['etf_weight']=.2
    if change=='leverage':snapshot['products'][0]['leverage']=3
    if change=='products':snapshot['products'].pop()
    if change=='vol':snapshot['realized_volatility']=0
    if change=='date':snapshot['signal_date']='2026-08-31T12:00:00'
    with pytest.raises(shadow.ShadowError):shadow.targets(snapshot,None)


def positive_fixture(tmp_path):
    base,out,verify,loader=fake_bundle(tmp_path)
    status,snapshot,grid,audit=verify(base)
    snapshot=core({'signal_date':snapshot['signal_date']},score=1)
    return base,out,lambda *args:(status,snapshot,grid,audit),loader


def test_unavailable_stats_are_null_and_success_does_not_authorize_orders(tmp_path):
    base,out,verify,loader=positive_fixture(tmp_path)
    assert shadow.generate(base,out,verifier=verify,loader=loader)==0
    assert (out/'vix_used.csv').exists()
    before=(base/'status.json').read_bytes()
    assert shadow.generate(base,out,verifier=verify,loader=missing)==0
    result=json.loads((out/'shadow.json').read_text())
    assert result['status']=='ok'
    assert result['comparison_basis']=='positive_core_overlay_disabled'
    assert result['vix_data_status']=='unavailable_not_required_for_positive_core'
    assert result['vix_error']=='No current VIX close'
    assert result['vix_features'] is result['vix_source'] is result['vix_window_sha256'] is None
    assert not (out/'vix_used.csv').exists()
    assert result['publish_notification'] is result['execution_authorized'] is False
    assert result['models']['BASE']==result['models']['C20']==result['models']['Q70']
    assert (base/'status.json').read_bytes()==before
    assert '没有将缺失值填成零' in (out/'report.md').read_text()


def test_missing_vix_zero_core_erases_previous_targets(tmp_path):
    base,out,verify,loader=fake_bundle(tmp_path)
    assert shadow.generate(base,out,verifier=verify,loader=loader)==0
    assert shadow.generate(base,out,verifier=verify,loader=missing)==2
    assert json.loads((out/'shadow.json').read_text())['models'] is None
    assert not (out/'vix_used.csv').exists()


def test_other_program_errors_are_not_downgraded(tmp_path):
    base,out,verify,loader=positive_fixture(tmp_path)
    def broken(*args):raise RuntimeError('unexpected bug')
    assert shadow.generate(base,out,verifier=verify,loader=broken)==2
    assert json.loads((out/'shadow.json').read_text())['models'] is None


def test_invalid_base_cannot_recover_using_positive_core(tmp_path):
    base,now=verified_real_bundle(tmp_path)
    (base/'verified_closes.csv').write_text('corrupt')
    assert shadow.generate(base,tmp_path/'shadow',now=now,loader=missing)==2
    assert 'hash mismatch' in json.loads((tmp_path/'shadow/shadow.json').read_text())['error']


def test_loader_mutation_of_base_is_detected(tmp_path):
    base,out,verify,loader=positive_fixture(tmp_path)
    def changed(*args):
        (base/'status.json').write_text('changed')
        raise shadow.ShadowError('missing source')
    assert shadow.generate(base,out,verifier=verify,loader=changed)==2
    assert json.loads((out/'shadow.json').read_text())['models'] is None


def test_incomplete_history_statistics_remain_unavailable(tmp_path):
    base,out,verify,loader=positive_fixture(tmp_path)
    def incomplete(*args):
        v,source=loader(*args)
        return v.iloc[:-1],source
    assert shadow.generate(base,out,verifier=verify,loader=incomplete)==0
    result=json.loads((out/'shadow.json').read_text())
    assert result['vix_features'] is None
    assert 'Missing/invalid' in result['vix_error']
