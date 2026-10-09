"""Recover a missing QQQ close from a dated, finalized exchange close.

Last resort only: Nasdaq must explicitly label the SAME session 'Closed at',
Yahoo must contain the whole regular 5-minute session AND its exact end marker,
and the original complete history must agree with 20 Nasdaq historical closes.
Never use after-hours prices, a generic last quote, NAV, or a guessed close.
"""
from __future__ import annotations

from datetime import datetime
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re

import numpy as np
import pandas as pd
import requests

import monitor
from ndx_fallback import utc
from qqq_recovery import RecoveryError, validate_anchor, append_history, parse_history, NASDAQ_URL

INFO_URL = 'https://api.nasdaq.com/api/quote/QQQ/info'
PRICE_TOLERANCE = 0.005  # Half-cent: allow float32 representation, not price disagreement.
FIELDS = ('open', 'high', 'low', 'close')


def completed(report, calendar, now=None):
    report = pd.Timestamp(report)
    if (pd.isna(report) or report.tzinfo is not None or report != report.normalize()
            or not calendar.is_session(report)):
        raise RecoveryError('Closing recovery requires an exchange-session date')
    end = utc(calendar.session_close(report))
    if utc(now) < end + pd.Timedelta(minutes=30):
        raise RecoveryError('Closing recovery is not allowed before completion lag')
    return end


def exchange_close(payload, report, calendar, now=None):
    end = completed(report, calendar, now)
    data = payload.get('data') or {}
    if (payload.get('status', {}).get('rCode') != 200 or data.get('symbol') != 'QQQ'
            or data.get('assetClass') != 'ETF' or not str(data.get('exchange', '')).startswith('NASDAQ')):
        raise RecoveryError('Expected Nasdaq QQQ ETF close metadata')
    matched = []
    # After hours the finalized regular close may be secondaryData. Never assume
    # that primaryData is the close; it can be an actively changing extended quote.
    for slot in ('primaryData', 'secondaryData'):
        record = data.get(slot) or {}
        text = str(record.get('lastTradeTimestamp', ''))
        m = re.fullmatch(r'Closed at ([A-Z][a-z]{2} \d{1,2}, \d{4} \d{1,2}:\d{2} [AP]M) ET', text)
        if not m:
            continue
        stamp = pd.Timestamp(datetime.strptime(m[1], '%b %d, %Y %I:%M %p')).tz_localize('America/New_York')
        if utc(stamp) != end:
            raise RecoveryError('Nasdaq finalized close belongs to another session/time')
        money = str(record.get('lastSalePrice', ''))
        if not re.fullmatch(r'\$\d[\d,]*\.\d{2}', money):
            raise RecoveryError('Expected finite USD-cent regular closing price')
        price = float(money[1:].replace(',', ''))
        if not math.isfinite(price) or price <= 0:
            raise RecoveryError('Invalid finalized close')
        matched.append((price, slot, text))
    if not matched or len({v[0] for v in matched}) != 1:
        raise RecoveryError('Missing or conflicting explicitly finalized Nasdaq close')
    return matched[0]


def yahoo_session(payload, report, calendar, now=None):
    end = completed(report, calendar, now)
    chart = payload['chart']
    if chart.get('error'):
        raise RecoveryError('Yahoo intraday chart error')
    obj = chart['result'][0]
    meta = obj['meta']
    required = {'symbol': 'QQQ', 'instrumentType': 'ETF', 'currency': 'USD',
                'exchangeTimezoneName': 'America/New_York', 'dataGranularity': '5m'}
    if any(meta.get(k) != v for k, v in required.items()):
        raise RecoveryError('Expected USD QQQ regular-session 5-minute bars')
    tick = pd.Timestamp(meta['regularMarketTime'], unit='s', tz='UTC')
    if tick != end:
        raise RecoveryError('Yahoo regular-market timestamp is not the exact session close')
    ts, q = obj['timestamp'], obj['indicators']['quote'][0]
    if not ts or any(len(q[k]) != len(ts) for k in (*FIELDS, 'volume')):
        raise RecoveryError('Intraday OHLCV lengths differ')
    idx = pd.to_datetime(ts, unit='s', utc=True)
    if idx.hasnans or idx.has_duplicates or not idx.is_monotonic_increasing:
        raise RecoveryError('Invalid or duplicate intraday timestamps')
    start = utc(calendar.session_open(pd.Timestamp(report)))
    mask = (idx >= start) & (idx <= end)
    grid = pd.date_range(start, end, freq='5min')
    if not idx[mask].equals(grid):
        raise RecoveryError('Regular session or exact closing marker is missing')
    f = pd.DataFrame({k: np.asarray(q[k], dtype=float)[mask] for k in (*FIELDS, 'volume')}, index=grid)
    a = f[list(FIELDS)].to_numpy()
    if (not np.isfinite(a).all() or (a <= 0).any() or not np.isfinite(f.volume).all()
            or (f.volume < 0).any() or f.volume.iloc[:-1].sum() <= 0
            or (f.low > f.high).any() or (f.open < f.low).any() or (f.open > f.high).any()
            or (f.close < f.low).any() or (f.close > f.high).any()):
        raise RecoveryError('Invalid regular-session OHLCV')
    marker = f.iloc[-1]
    if marker.volume != 0 or np.ptp(marker[list(FIELDS)].to_numpy()) > 1e-8:
        raise RecoveryError('Unrecognized Yahoo end-of-session marker')
    meta_price = float(meta['regularMarketPrice'])
    price = float(marker.close)
    if not math.isfinite(meta_price) or abs(meta_price-price) > PRICE_TOLERANCE:
        raise RecoveryError('Yahoo regular closing marker conflicts with metadata')
    return price, f


def daily_evidence(payload, report, price):
    obj = payload['chart']['result'][0]
    if payload['chart'].get('error') or obj['meta'].get('symbol') != 'QQQ' or obj['meta'].get('dataGranularity') != '1d':
        raise RecoveryError('Expected QQQ daily evidence')
    ts, q = obj['timestamp'], obj['indicators']['quote'][0]
    if any(len(q[k]) != len(ts) for k in FIELDS):
        raise RecoveryError('Daily evidence lengths differ')
    dates = pd.to_datetime(ts, unit='s', utc=True).tz_convert('America/New_York').tz_localize(None).normalize()
    where = np.flatnonzero(dates == pd.Timestamp(report))
    if len(where) != 1:
        raise RecoveryError('Need one existing report-day daily bar')
    i = where[0]
    o, h, l = (float(q[k][i]) for k in ('open', 'high', 'low'))
    if not np.isfinite([o,h,l]).all() or min(o,h,l) <= 0 or not l <= o <= h or not l <= price <= h:
        raise RecoveryError('Finalized close conflicts with daily open/high/low')
    c = q['close'][i]
    if c is not None and (not math.isfinite(float(c)) or abs(float(c)-price) > PRICE_TOLERANCE):
        raise RecoveryError('Observed daily close conflicts with finalized exchange close')
    return {'open': o, 'high': h, 'low': l, 'original_close': c}


def _get(session, url, params, tag, audit, report, calendar, now=None):
    r = session.get(url, params=params, timeout=(8,25), headers={
        'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json', 'Cache-Control': 'no-cache',
        'Origin': 'https://www.nasdaq.com', 'Referer': 'https://www.nasdaq.com/'})
    r.raise_for_status()
    if len(r.content) > 3_000_000:
        raise RecoveryError('Oversized closing evidence')
    date = utc(parsedate_to_datetime(r.headers['Date']))
    age = int(r.headers.get('Age', '0'))
    if age < 0 or date > utc(now)+pd.Timedelta(minutes=5) or date-pd.Timedelta(seconds=age) < utc(calendar.session_close(report)):
        raise RecoveryError('Closing evidence response time is stale or future')
    audit.append({'request': tag, 'source': url, 'response_sha256': hashlib.sha256(r.content).hexdigest(),
                  'http_date': r.headers['Date'], 'http_age': age})
    folder = os.environ.get('SOURCE_EVIDENCE_DIR')
    if folder:
        p = Path(folder); p.mkdir(parents=True, exist_ok=True)
        (p/(tag+'.json')).write_bytes(r.content)
    return r.json()


def fetch_closing(session, history, report, audit, calendar, daily_payload, now=None):
    h, missing = validate_anchor(history, report, calendar, now)
    if len(missing) != 1:
        raise RecoveryError('Finalized-close recovery permits exactly one missing tail session')
    info = _get(session, INFO_URL, {'assetclass':'etf'}, 'qqq_finalized_close', audit, report, calendar, now)
    price, slot, label = exchange_close(info, report, calendar, now)
    evidence = daily_evidence(daily_payload, report, price)
    raw = _get(session, monitor.YAHOO_QQQ_URLS[0], {'interval':'5m','range':'1mo','includePrePost':'false'},
               'qqq_regular_session', audit, report, calendar, now)
    marker, bars = yahoo_session(raw, report, calendar, now)
    if abs(price-marker) > PRICE_TOLERANCE:
        raise RecoveryError('Nasdaq finalized close / Yahoo end marker price mismatch')
    params = {'assetclass':'etf','fromdate':str((pd.Timestamp(report)-pd.Timedelta(days=60)).date()),
              'todate':str(pd.Timestamp(report).date()),'limit':100}
    raw = _get(session, NASDAQ_URL, params, 'qqq_close_reference_history', audit, report, calendar, now)
    # Require complete consecutive historical cross-checks without pretending
    # Nasdaq's delayed daily table already has today's row.
    reference = parse_history(raw, h.index[-1], calendar, now)
    rows = (raw.get('data') or {}).get('tradesTable', {}).get('rows') or []
    current = [r for r in rows if pd.Timestamp(datetime.strptime(r['date'], '%m/%d/%Y')) == pd.Timestamp(report)]
    if len(current) > 1:
        raise RecoveryError('Duplicate report date in exchange reference table')
    if current:
        observed = float(str(current[0]['close']).replace('$','').replace(',',''))
        if not math.isfinite(observed) or abs(observed-price) > PRICE_TOLERANCE:
            raise RecoveryError('Exchange historical close conflicts with finalized close')
    addition = pd.Series([price], index=pd.DatetimeIndex([report]), name='QQQ')
    tail = pd.concat([reference, addition])
    result, detail = append_history(h, tail, report, calendar, now)
    detail.update(method='Nasdaq_dated_finalized_close_Yahoo_session_confirmation', request='qqq_finalized_recovery',
                  status='ok', selected_exchange_field=slot, exchange_close_label=label,
                  close=price, yahoo_end_marker=marker, regular_bars=len(bars)-1, daily_evidence=evidence)
    audit.append(detail)
    result.attrs['source'] = f'Yahoo QQQ through {h.index[-1].date()}; Nasdaq explicitly finalized {label}, confirmed by full Yahoo regular session'
    return result
