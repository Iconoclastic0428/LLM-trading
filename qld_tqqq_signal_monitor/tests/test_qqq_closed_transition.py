"""Captured exchange Closed-state transition and same-session cache regression."""
import json
import pandas as pd
import pytest
from test_qqq_finalized_close import Source, FIX, REPORT, NOW, CAL, data, tail, recovery
from test_price_checkpoint import fixture as cache_fixture
import qqq_recovery
import reliable_data


def closed_record():
    return json.loads((FIX.parent/'qqq_market_closed_20261008.json').read_text())


def test_exact_captured_market_closed_transition():
    p = closed_record()
    price, field, label = tail.exchange_close(p, REPORT, CAL, NOW)
    assert price == 747.58 and field == 'primaryData'
    assert label == 'Closed market; Oct 8, 2026'
    source = Source(); source.info = p
    history = data.chart_series(source.daily)
    audit = []
    actual = tail.fetch_closing(source, history, REPORT, audit, CAL, source.daily, NOW)
    pd.testing.assert_series_equal(actual.loc[history.index], history, check_freq=False)
    assert len(actual) == len(history)+1 and actual.loc[REPORT] == 747.58
    assert audit[-1]['exchange_record_type'] == 'closed_market_date'


@pytest.mark.parametrize('state', ['After-Hours', 'Open', 'Pre-Market', '', None])
def test_date_only_primary_never_accepted_outside_explicit_closed_state(state):
    p = closed_record(); p['data']['marketStatus'] = state
    with pytest.raises(recovery.RecoveryError, match='unavailable'):
        tail.exchange_close(p, REPORT, CAL, NOW)


@pytest.mark.parametrize('value', [True, None, 0, 'false'])
def test_date_only_close_must_explicitly_be_non_real_time(value):
    p = closed_record(); p['data']['primaryData']['isRealTime'] = value
    with pytest.raises(recovery.RecoveryError, match='unavailable'):
        tail.exchange_close(p, REPORT, CAL, NOW)


@pytest.mark.parametrize('date', ['Oct 7, 2026', 'Oct 9, 2026', 'Oct 8, 2026 7:58 PM ET'])
def test_closed_primary_must_be_exact_requested_date(date):
    p = closed_record(); p['data']['primaryData']['lastTradeTimestamp'] = date
    with pytest.raises(recovery.RecoveryError):
        tail.exchange_close(p, REPORT, CAL, NOW)


def test_closed_date_record_still_requires_independent_price_agreement():
    source = Source(); source.info = closed_record()
    source.info['data']['primaryData']['lastSalePrice'] = '$748.00'
    with pytest.raises(recovery.RecoveryError, match='mismatch'):
        tail.fetch_closing(source, data.chart_series(source.daily), REPORT, [], CAL, source.daily, NOW)


def test_missing_final_record_error_never_claims_price_conflict():
    p = closed_record(); p['data']['primaryData'] = None
    with pytest.raises(recovery.RecoveryError) as err:
        tail.exchange_close(p, REPORT, CAL, NOW)
    assert 'unavailable' in str(err.value) and 'conflict' not in str(err.value).lower()



def test_missing_final_record_does_not_block_same_session_checkpoint(tmp_path, monkeypatch):
    q, n, _ = cache_fixture(tmp_path, monkeypatch)
    def missing(*args):
        raise reliable_data.DataUnavailable('QQQ independent recovery: Finalized Nasdaq closing record unavailable')
    monkeypatch.setattr(qqq_recovery, 'get_qqq_reliable', missing)
    audit = []
    a, b = reliable_data.load_prices(REPORT, audit, attempts=1)
    pd.testing.assert_series_equal(a, q, check_freq=False)
    pd.testing.assert_series_equal(b, n, check_freq=False)
    assert audit[-1]['request'] == 'checkpoint_restore'
    assert audit[-1]['new_market_observation'] is False
