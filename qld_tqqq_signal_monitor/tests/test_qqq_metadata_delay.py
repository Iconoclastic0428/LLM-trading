"""A delayed regular-market metadata clock cannot replace closing evidence."""
import copy
import pandas as pd
import pytest
from test_qqq_finalized_close import Source, load, REPORT, NOW, CAL
import qqq_tail as tail
import qqq_recovery as recovery
import reliable_data as data


def delayed(seconds=1):
    p = load('intraday')
    m = p['chart']['result'][0]['meta']
    end = CAL.session_close(REPORT)
    m['regularMarketTime'] = int(end.timestamp()) + seconds
    m['currentTradingPeriod'] = {'regular': {
        'start': int(CAL.session_open(REPORT).timestamp()), 'end': int(end.timestamp())}}
    return p


@pytest.mark.parametrize('seconds', [0, 1, 2, 5])
def test_bounded_metadata_keeps_exact_price_marker(seconds):
    price, bars = tail.yahoo_session(delayed(seconds), REPORT, CAL, NOW)
    assert price == pytest.approx(747.58, abs=.0001)
    assert bars.index[-1] == CAL.session_close(REPORT)
    assert bars.attrs['regular_metadata_lag_seconds'] == seconds


@pytest.mark.parametrize('seconds', [-86400, -300, -1, 6, 60, 300, 86400])
def test_preclose_stale_or_later_quote_rejected(seconds):
    with pytest.raises(recovery.RecoveryError, match='bounded close window'):
        tail.yahoo_session(delayed(seconds), REPORT, CAL, NOW)


@pytest.mark.parametrize('field', ['start', 'end'])
@pytest.mark.parametrize('value', [None, True, '1791489600', 1])
def test_delayed_metadata_requires_calendar_matched_session(field, value):
    p = delayed()
    p['chart']['result'][0]['meta']['currentTradingPeriod']['regular'][field] = value
    with pytest.raises(recovery.RecoveryError, match='matching regular session'):
        tail.yahoo_session(p, REPORT, CAL, NOW)


@pytest.mark.parametrize('value', [None, True, '1791489601', float('nan'), 1791489601.5])
def test_invalid_metadata_timestamp_rejected(value):
    p = delayed(); p['chart']['result'][0]['meta']['regularMarketTime'] = value
    with pytest.raises(recovery.RecoveryError, match='Invalid Yahoo'):
        tail.yahoo_session(p, REPORT, CAL, NOW)


def test_delayed_metadata_does_not_allow_missing_or_shifted_end_marker():
    for change in ('missing', 'shift'):
        p = delayed(); obj = p['chart']['result'][0]
        if change == 'missing':
            obj['timestamp'].pop()
            for vals in obj['indicators']['quote'][0].values(): vals.pop()
        else:
            obj['timestamp'][-1] += 1
        with pytest.raises(recovery.RecoveryError, match='closing marker'):
            tail.yahoo_session(p, REPORT, CAL, NOW)


def test_full_recovery_requires_independent_price_and_preserves_history():
    s = Source(); s.intraday = delayed(); h = data.chart_series(s.daily); audit=[]
    got = tail.fetch_closing(s, h, REPORT, audit, CAL, s.daily, NOW)
    pd.testing.assert_series_equal(got.loc[h.index], h, check_freq=False)
    assert got.loc[REPORT] == 747.58 and len(got) == len(h) + 1
    entry = audit[-1]
    assert entry['yahoo_regular_metadata_lag_seconds'] == 1
    assert entry['yahoo_exact_marker_time'] == CAL.session_close(REPORT).isoformat()
    s.info['data']['secondaryData']['lastSalePrice'] = '$748.00'
    with pytest.raises(recovery.RecoveryError, match='mismatch'):
        tail.fetch_closing(s, h, REPORT, [], CAL, s.daily, NOW)


@pytest.mark.parametrize('change', ['meta_price', 'marker_price', 'volume', 'gap'])
def test_delayed_metadata_preserves_other_price_and_grid_guards(change):
    p = delayed(); obj=p['chart']['result'][0]; q=obj['indicators']['quote'][0]
    if change == 'meta_price': obj['meta']['regularMarketPrice'] += 1
    if change == 'marker_price':
        for field in tail.FIELDS: q[field][-1] += 1
    if change == 'volume': q['volume'][-1] = 100
    if change == 'gap':
        obj['timestamp'].pop(5)
        for vals in q.values(): vals.pop(5)
    with pytest.raises(recovery.RecoveryError):
        tail.yahoo_session(p, REPORT, CAL, NOW)


@pytest.mark.parametrize('day', ['2025-11-28', '2026-11-02'])
def test_half_day_and_dst_follow_exchange_calendar(day):
    report = pd.Timestamp(day); start=CAL.session_open(report); end=CAL.session_close(report)
    p=delayed(); obj=p['chart']['result'][0]; meta=obj['meta']
    grid=pd.date_range(start,end,freq='5min'); n=len(grid)
    obj['timestamp']=[int(x.timestamp()) for x in grid]
    obj['indicators']['quote'][0]={key:[747.58]*n for key in tail.FIELDS}
    obj['indicators']['quote'][0]['volume']=[100]*(n-1)+[0]
    meta['regularMarketTime']=int(end.timestamp())+1
    meta['regularMarketPrice']=747.58
    meta['currentTradingPeriod']['regular']={'start':int(start.timestamp()), 'end':int(end.timestamp())}
    _, bars=tail.yahoo_session(p, report, CAL, end+pd.Timedelta(hours=1))
    assert bars.index[-1]==end
    meta['currentTradingPeriod']['regular']['end'] += 3600
    with pytest.raises(recovery.RecoveryError, match='matching regular session'):
        tail.yahoo_session(p, report, CAL, end+pd.Timedelta(hours=1))


def test_delay_never_bypasses_completion_lag():
    with pytest.raises(recovery.RecoveryError, match='completion lag'):
        tail.yahoo_session(delayed(), REPORT, CAL, CAL.session_close(REPORT)+pd.Timedelta(minutes=29))
