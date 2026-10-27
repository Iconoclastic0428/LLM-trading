"""Renew an NDX tail using Nasdaq historical OHLC AND Yahoo daily bars.

Fresh issuer history is mandatory on every run. A single latest Yahoo session
may follow the independently confirmed issuer date; it is explicitly labelled.
This does not extend the original FRED allowance or reset its origin.
The existing full-history checkpoint validates the immutable earlier history;
no shortened EMA seed, quotes, interpolation, rescaling or retrospective edits.
"""
from __future__ import annotations

from email.utils import parsedate_to_datetime
import hashlib
import json
import os
from pathlib import Path
import re

import numpy as np
import pandas as pd
import requests

import monitor
import price_checkpoint as cp
from ndx_fallback import (TailUnavailable, URLS, append_tail, daily_ndx,
                          sessions, utc, validate_anchor)

NASDAQ_URL = 'https://api.nasdaq.com/api/quote/NDX/historical'
MAX_BYTES = 2_000_000
TOLERANCE = 0.0001


class SourceConflict(TailUnavailable):
    """An observed contradiction must never be hidden by another source/cache."""


def cached_anchor(report, audit, now=None):
    """Validate the entire earlier checkpoint before using even one old value."""
    failures = ' '.join(str(x.get('error', '')).lower() for x in audit)
    if any(word in failures for word in ('conflict', 'mismatch', 'duplicate',
            'wrong or missing', 'wrong symbol', 'non-positive', 'unexpected columns')):
        raise SourceConflict('Observed data conflict forbids dual-source recovery')
    folder = cp.directory()
    if folder is None or folder.is_symlink():
        raise TailUnavailable('No safe verified checkpoint for dual-source recovery')
    doc = json.loads(cp._read(folder / 'manifest.json'))
    day = pd.Timestamp(doc['report_date'])
    if day.tzinfo is not None or day != day.normalize() or day >= report:
        raise TailUnavailable('Dual-source historical anchor must precede report')
    source = doc['prices']['NDX']['source']
    if source == monitor.FRED_NDX_URL:
        origin = day
    else:
        match = re.match(r'^FRED NASDAQ100 through (\d{4}-\d{2}-\d{2});', source)
        if not match:
            raise TailUnavailable('Unknown original NDX history provenance')
        origin = pd.Timestamp(match.group(1))
    cal = monitor._calendar()
    if origin > day or not cal.is_session(origin):
        raise TailUnavailable('Invalid original FRED date')
    local = []
    restored = cp.restore(day, local, now)
    audit.extend(local)
    if restored is None:
        raise TailUnavailable('Dual-source anchor failed full checkpoint validation')
    if json.loads(cp._read(folder / 'manifest.json')) != doc:
        raise SourceConflict('Checkpoint manifest changed during validation')
    h, added = validate_anchor(restored[1], report, cal, now)
    return h, added, origin, doc


def historical_ndx(payload, report, anchor, calendar, now=None):
    """Only the dated NDX historical table; never live/previous-close quotes."""
    report, anchor = pd.Timestamp(report), pd.Timestamp(anchor)
    if (not calendar.is_session(report) or
            utc(now) < utc(calendar.session_close(report)) + pd.Timedelta(minutes=30)):
        raise TailUnavailable('Report close is not complete')
    if payload.get('status', {}).get('rCode') != 200:
        raise TailUnavailable('Nasdaq NDX historical response unsuccessful')
    data = payload.get('data') or {}
    table = data.get('tradesTable') or {}
    expected = {'date': 'Date', 'close': 'Close/Last', 'open': 'Open',
                'high': 'High', 'low': 'Low'}
    if data.get('symbol') != 'NDX' or any(table.get('headers', {}).get(k) != v
                                         for k, v in expected.items()):
        raise SourceConflict('Wrong symbol or schema for Nasdaq NDX historical table')
    rows = table.get('rows')
    if not isinstance(rows, list) or not rows:
        raise TailUnavailable('Nasdaq NDX historical rows unavailable')
    frame = pd.DataFrame(rows)
    frame.index = pd.to_datetime(frame['date'], format='%m/%d/%Y', errors='raise')
    start = sessions(calendar, anchor - pd.Timedelta(days=60), anchor)[-25]
    frame = frame.loc[(frame.index >= start) & (frame.index <= report)].sort_index()
    if frame.empty or frame.index.hasnans or frame.index.has_duplicates:
        raise SourceConflict('Invalid/duplicate Nasdaq historical dates')
    if len(frame.index.difference(sessions(calendar, start, report))):
        raise SourceConflict('Nasdaq historical table has non-session dates')
    # Reference closes only cross-check old history. Never import old OHLC.
    close = pd.to_numeric(frame.close.astype(str).str.replace(',', '', regex=False), errors='raise')
    if not np.isfinite(close.to_numpy()).all() or (close <= 0).any():
        raise SourceConflict('Non-positive or non-finite Nasdaq historical close')
    frame['close'] = close
    issuer_date = frame.index[-1]
    minimum_date = sessions(calendar, report-pd.Timedelta(days=7), report)[-2]
    if issuer_date < minimum_date:
        raise TailUnavailable('Nasdaq history is more than one exchange session behind report')
    # Recheck any previously provisional last checkpoint close as well as new
    # rows. A present invalid current row must not be dropped in favour of yesterday.
    new = frame.loc[frame.index >= anchor, ['open', 'high', 'low', 'close']].copy()
    for k in ('open', 'high', 'low', 'close'):
        new[k] = pd.to_numeric(new[k].astype(str).str.replace(',', '', regex=False), errors='raise')
    values = new.to_numpy(dtype=float)
    if (new.empty or not np.isfinite(values).all() or
            (values <= 0).any() or (new.low > new.high).any() or
            (new.open < new.low).any() or (new.open > new.high).any() or
            (new.close < new.low).any() or (new.close > new.high).any()):
        raise TailUnavailable('Incomplete or invalid new Nasdaq historical OHLC')
    return frame.close.astype(float).rename('NDX')


def check_response(response, report, calendar, now=None):
    response.raise_for_status()
    if len(response.content) > MAX_BYTES:
        raise TailUnavailable('Nasdaq NDX response is too large')
    stamp = utc(parsedate_to_datetime(response.headers['Date']))
    age = int(response.headers.get('Age', '0'))
    if (age < 0 or stamp > utc(now) + pd.Timedelta(minutes=5) or
            stamp - pd.Timedelta(seconds=age) < utc(calendar.session_close(report))):
        raise TailUnavailable('Nasdaq historical HTTP representation predates requested close')


def evidence(name, body):
    """Save exact public response bytes, no executable serialization or secrets."""
    path = os.environ.get('SOURCE_EVIDENCE_DIR')
    if path:
        root = Path(path)
        if root.is_symlink():
            raise TailUnavailable('Unsafe source-evidence directory')
        root.mkdir(parents=True, exist_ok=True)
        dest = root / name
        if dest.is_symlink():
            raise TailUnavailable('Unsafe source-evidence file')
        dest.write_bytes(body)
    return hashlib.sha256(body).hexdigest()


def recover(session, report, audit, now=None):
    import reliable_data as data
    report = pd.Timestamp(report)
    cal = monitor._calendar()
    h, added, origin, doc = cached_anchor(report, audit, now)
    params = {'assetclass': 'index', 'fromdate': str((report-pd.Timedelta(days=90)).date()),
              'todate': str(report.date()), 'limit': 120}
    official = session.get(NASDAQ_URL, params=params, timeout=(8, 25), headers={
        'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json',
        'Origin': 'https://www.nasdaq.com', 'Referer': 'https://www.nasdaq.com/',
        'Cache-Control': 'no-cache'})
    check_response(official, report, cal, now)
    official_hash = evidence('ndx_nasdaq_' + str(report.date()) + '.json', official.content)
    issuer = historical_ndx(official.json(), report, h.index[-1], cal, now)
    issuer_date = issuer.index[-1]
    independently_added = added[added <= issuer_date]
    reference_grid = sessions(cal, h.index[-1]-pd.Timedelta(days=60), h.index[-1])[-25:]
    available = reference_grid.intersection(issuer.index)
    if (len(available) < 20 or len(independently_added.difference(issuer.index)) or
            h.index[-1] not in issuer.index):
        raise TailUnavailable('Nasdaq must verify anchor and every added historical session')
    ref_error = float(np.max(np.abs(h.loc[available].to_numpy()/issuer.loc[available].to_numpy()-1)))
    if not np.isfinite(ref_error) or ref_error > TOLERANCE:
        raise SourceConflict(f'Nasdaq/checkpoint NDX price mismatch: {ref_error:.6%}')
    issuer_detail = {'verified_through': str(issuer_date.date()),
                    'overlap_sessions': len(available), 'max_relative_overlap_error': ref_error}
    unconfirmed = added[added > issuer_date]
    if len(unconfirmed) > 1 or (len(unconfirmed) and unconfirmed[0] != report):
        raise TailUnavailable('Only the latest report session may lack issuer history')
    finish = report.tz_localize('America/New_York') + pd.Timedelta(days=1)
    params = {'period1': int((finish-pd.Timedelta(days=90)).timestamp()),
              'period2': int(finish.timestamp()), 'interval': '1d',
              'events': 'div,splits', 'includePrePost': 'false'}
    errors = []
    for url in URLS:
        try:
            response = session.get(url, params=params, timeout=(8, 25))
            response.raise_for_status()
            if len(response.content) > MAX_BYTES:
                raise TailUnavailable('Yahoo NDX response is too large')
            yahoo_hash = evidence('ndx_yahoo_' + str(report.date()) + '.json', response.content)
            yahoo = daily_ndx(response.json(), report, cal, now, anchor_end=h.index[-1])
            result, detail = append_tail(h, yahoo, report, cal, now, allow_sparse_overlap=True)
            common = issuer.index.intersection(yahoo.dropna().index)
            # Every shared prior close is checked. At most one explicitly marked
            # current session may rely on complete finalized Yahoo daily OHLC.
            if len(common) < 20 + len(independently_added) or len(independently_added.difference(common)):
                raise TailUnavailable('Insufficient independent NDX overlap/new sessions')
            error = float(np.max(np.abs(yahoo.loc[common].to_numpy()/issuer.loc[common].to_numpy()-1)))
            if not np.isfinite(error) or error > TOLERANCE:
                raise SourceConflict(f'Nasdaq/Yahoo NDX price conflict: {error:.6%}')
            result = data.validate_asof(result, report, 'NDX dual-source verified history')
            pd.testing.assert_series_equal(result.loc[h.index], h, check_freq=False, check_exact=True)
            result.attrs['source'] = (f'FRED NASDAQ100 through {origin.date()}; verified checkpoint through '
                f'{h.index[-1].date()}; dual Nasdaq historical/Yahoo daily ^NDX through {issuer_date.date()}; '
                f'Yahoo daily ^NDX through {report.date()}')
            audit.append({'request': 'ndx_dual_source_append', 'status': 'ok',
                'report_date': str(report.date()), 'fred_origin_date': str(origin.date()),
                'checkpoint_date': str(h.index[-1].date()), 'preserved_rows': len(h),
                'independently_verified_through': str(issuer_date.date()),
                'single_source_latest_dates': [str(d.date()) for d in unconfirmed],
                'current_close_independently_confirmed': len(unconfirmed) == 0,
                'appended_dates': [str(d.date()) for d in added],
                'appended': {str(d.date()): float(result.loc[d]) for d in added},
                'independent_overlap_count': len(common), 'max_independent_error': error,
                'nasdaq_anchor_check': issuer_detail, 'yahoo_anchor_check': detail,
                'nasdaq_url': NASDAQ_URL, 'yahoo_url': url,
                'nasdaq_sha256': official_hash, 'yahoo_sha256': yahoo_hash,
                'nasdaq_http_date': official.headers['Date'],
                'nasdaq_http_age': official.headers.get('Age', '0'),
                'original_verified_at': doc['verified_at'], 'original_run_id': doc.get('run_id'),
                'retrieved_at': utc(now).isoformat(), 'execution_authorized': False})
            return result
        except SourceConflict:
            raise
        except (requests.RequestException, ValueError, KeyError, TypeError, IndexError) as exc:
            # Identity/price conflicts may not fall through to a different cache key.
            if any(x in str(exc).lower() for x in ('mismatch', 'conflict', 'duplicate',
                                                  'metadata', 'non-positive')):
                raise SourceConflict(str(exc)) from exc
            errors.append(str(exc))
            audit.append({'request': 'ndx_dual_source_append', 'source': url, 'error': str(exc)})
    raise TailUnavailable('Dual-source NDX recovery unavailable: ' + '; '.join(errors))
