"""QQQ daily-close recovery, preserving the original Yahoo price-only history.

Use Nasdaq's historical OHLC table only to append 1-5 missing tail sessions,
with 20 consecutive matching overlap closes. No quotes, interpolation, rescaling,
interior repairs, shortened EMA histories, or success on stale dates.
"""
from __future__ import annotations

from email.utils import parsedate_to_datetime
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import requests

import monitor
from ndx_fallback import prefix, sessions, utc

NASDAQ_URL = 'https://api.nasdaq.com/api/quote/QQQ/historical'
MAX_GAP = 5
OVERLAP = 20
TOLERANCE = 0.0001


class RecoveryError(ValueError):
    pass


def validate_anchor(history, report, calendar, now=None):
    report = pd.Timestamp(report)
    if (pd.isna(report) or report.tzinfo is not None or report != report.normalize()
            or not calendar.is_session(report)):
        raise RecoveryError('QQQ report must be an exchange-session date')
    if utc(now) < utc(calendar.session_close(report)) + pd.Timedelta(minutes=30):
        raise RecoveryError('QQQ report session has not completed')
    h = prefix(history, report).rename('QQQ')
    if len(h) < monitor.MIN_OBSERVATIONS:
        raise RecoveryError('QQQ anchor must preserve at least 500 historical observations')
    anchor = h.index[-1]
    if not calendar.is_session(anchor):
        raise RecoveryError('QQQ anchor ends on a non-session')
    required = sessions(calendar, anchor-pd.Timedelta(days=410), anchor)[-260:]
    if len(required) != 260 or len(required.difference(h.index)):
        raise RecoveryError('QQQ anchor has an interior session gap')
    missing = sessions(calendar, anchor, report)[1:]
    if not 1 <= len(missing) <= MAX_GAP:
        raise RecoveryError('QQQ recovery requires only 1-5 missing trailing sessions')
    return h, missing


def parse_history(payload, report, calendar, now=None):
    report = pd.Timestamp(report)
    if (not calendar.is_session(report)
            or utc(now) < utc(calendar.session_close(report)) + pd.Timedelta(minutes=30)):
        raise RecoveryError('QQQ requested close is not complete')
    if payload.get('status', {}).get('rCode') != 200:
        raise RecoveryError('Nasdaq historical response has an unsuccessful status')
    data = payload.get('data') or {}
    table = data.get('tradesTable') or {}
    expected = {'date':'Date', 'close':'Close/Last', 'open':'Open', 'high':'High', 'low':'Low'}
    if data.get('symbol') != 'QQQ' or any(table.get('headers', {}).get(k) != v for k,v in expected.items()):
        raise RecoveryError('Expected QQQ historical OHLC table, not a quote or another symbol')
    rows = table.get('rows')
    if not isinstance(rows, list) or not rows:
        raise RecoveryError('Nasdaq historical rows are unavailable')
    dates = pd.to_datetime([r['date'] for r in rows], format='%m/%d/%Y', errors='raise')
    # Only historical prefix participates in validation; never consume future values.
    frame = pd.DataFrame(rows, index=dates)
    frame = frame.loc[frame.index <= report].copy().sort_index()
    for field in ('open','high','low','close'):
        values = frame[field].astype(str).str.replace('$','',regex=False).str.replace(',','',regex=False)
        frame[field] = pd.to_numeric(values, errors='raise')
    a = frame[['open','high','low','close']].to_numpy(dtype=float)
    if (frame.empty or frame.index.has_duplicates or frame.index.hasnans
            or not np.isfinite(a).all() or (a <= 0).any()
            or (frame.low > frame.high).any() or (frame.open < frame.low).any()
            or (frame.open > frame.high).any() or (frame.close < frame.low).any()
            or (frame.close > frame.high).any()):
        raise RecoveryError('Invalid QQQ historical OHLC or duplicate dates')
    if report not in frame.index:
        raise RecoveryError(f'QQQ Nasdaq history is stale: required {report.date()}, latest {frame.index[-1].date()}')
    grid = sessions(calendar, frame.index[0], report)
    if len(frame.index.difference(grid)):
        raise RecoveryError('QQQ historical table contains non-session rows')
    return frame.close.rename('QQQ')


def append_history(history, tail, report, calendar, now=None):
    h, missing = validate_anchor(history, report, calendar, now)
    tail = prefix(tail, pd.Timestamp(report))
    overlap = sessions(calendar, h.index[-1]-pd.Timedelta(days=60), h.index[-1])[-OVERLAP:]
    if (len(overlap) != OVERLAP or len(overlap.difference(h.index))
            or len(overlap.difference(tail.index)) or len(missing.difference(tail.index))):
        raise RecoveryError('QQQ needs 20 consecutive overlapping closes and all missing tail sessions')
    error = float(np.max(np.abs(h.loc[overlap].to_numpy()/tail.loc[overlap].to_numpy()-1)))
    if not np.isfinite(error) or error > TOLERANCE:
        raise RecoveryError(f'QQQ Yahoo/Nasdaq price-basis mismatch: {error:.6%}')
    addition = tail.loc[missing].copy()
    # Daily dates are exact in every supported storage unit. Keep the anchor's
    # unit when joining parsed strings to epoch-derived Yahoo timestamps.
    addition.index = addition.index.as_unit(h.index.unit)
    result = pd.concat([h, addition]).rename('QQQ')
    detail = {'method':'QQQ_Yahoo_anchor_Nasdaq_append_only','anchor_end':str(h.index[-1].date()),
              'overlap_sessions':OVERLAP, 'max_relative_overlap_error':error,
              'appended':{str(d.date()):float(tail.loc[d]) for d in missing}}
    return result, detail


def fetch_tail(session, history, report, audit, calendar, now=None):
    h, _ = validate_anchor(history, report, calendar, now)
    params = {'assetclass':'etf', 'fromdate':str((pd.Timestamp(report)-pd.Timedelta(days=90)).date()),
              'todate':str(pd.Timestamp(report).date()), 'limit':100}
    r = session.get(NASDAQ_URL, params=params, timeout=(8,25), headers={
        'User-Agent':'Mozilla/5.0', 'Accept':'application/json', 'Origin':'https://www.nasdaq.com',
        'Referer':'https://www.nasdaq.com/', 'Cache-Control':'no-cache'})
    r.raise_for_status()
    if len(r.content) > 2_000_000:
        raise RecoveryError('Nasdaq QQQ response is unexpectedly large')
    # Reject obviously pre-close cached HTTP representations. This is not a
    # claim of independent exchange settlement attestation for every row.
    stamp = parsedate_to_datetime(r.headers['Date'])
    age = int(r.headers.get('Age','0'))
    if stamp.tzinfo is None or age < 0:
        raise RecoveryError('Invalid Nasdaq HTTP response time')
    origin = utc(stamp) - pd.Timedelta(seconds=age)
    if origin < utc(calendar.session_close(report)) or utc(stamp) > utc(now)+pd.Timedelta(minutes=5):
        raise RecoveryError('Nasdaq HTTP representation predates the requested close')
    payload = r.json()
    tail = parse_history(payload, report, calendar, now)
    result, detail = append_history(h, tail, report, calendar, now)
    detail.update(source=NASDAQ_URL, request='verified_qqq_tail', status='ok', retrieved_at=utc(now).isoformat(),
                  response_sha256=hashlib.sha256(r.content).hexdigest(), http_date=r.headers['Date'], http_age=age)
    audit.append(detail)
    result.attrs['source'] = f'Yahoo QQQ through {detail["anchor_end"]}; append-only Nasdaq historical QQQ from {NASDAQ_URL}'
    return result


def get_qqq_reliable(session, report, audit):
    import reliable_data as data
    try:
        return data.get_qqq(session, report, audit)
    except data.DataUnavailable as primary_error:
        errors = []
        finish = pd.Timestamp(report).tz_localize('America/New_York')+pd.Timedelta(days=1)
        params = {'period1':920246400,'period2':int(finish.timestamp()),'interval':'1d',
                  'events':'div,splits','includePrePost':'false'}
        for url in monitor.YAHOO_QQQ_URLS:
            try:
                r = session.get(url, params=params, timeout=(8,25))
                r.raise_for_status()
                daily_payload = r.json()
                history = data.chart_series(daily_payload).loc[:report]
                audit.append({'source':url,'request':'qqq_recovery_anchor','latest':str(history.index[-1].date())})
                # Provider may have caught up. Prefer original history when current.
                try:
                    result = data.validate_asof(history, report, 'QQQ')
                except data.DataUnavailable:
                    try:
                        result = fetch_tail(session, history, report, audit, monitor._calendar())
                    except (requests.RequestException, ValueError, KeyError, TypeError, IndexError) as exc:
                        audit.append({'request': 'qqq_nasdaq_daily_unavailable', 'error': str(exc)})
                        from qqq_tail import fetch_closing
                        result = fetch_closing(session, history, report, audit, monitor._calendar(), daily_payload)
                    return data.validate_asof(result, report, 'QQQ verified recovery')
                result.attrs['source'] = url
                return result
            except (requests.RequestException, ValueError, KeyError, TypeError, IndexError, data.DataUnavailable) as exc:
                errors.append(str(exc))
                audit.append({'source':url,'request':'qqq_recovery','error':str(exc)})
        raise data.DataUnavailable(f'{primary_error}; QQQ independent recovery: ' + '; '.join(errors)) from primary_error


def probe(output_dir):
    """Read-only bounded historical contract test; report live readiness separately."""
    from historical_recovery_probe import probe as run_probe
    return run_probe(output_dir)

if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--probe-dir', required=True)
    raise SystemExit(probe(p.parse_args().probe_dir))
