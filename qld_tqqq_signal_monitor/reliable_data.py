"""Fresh, auditable daily closes; never replace a missing close with a quote."""
from __future__ import annotations

from datetime import datetime, timezone
from io import StringIO
import time
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import monitor


class DataUnavailable(monitor.MonitorError):
    pass


class ProviderSession(requests.Session):
    """Use the tested standard HTTP transport for the original FRED CSV URL."""

    def get(self, url, **kwargs):
        if url != monitor.FRED_NDX_URL:
            return super().get(url, **kwargs)
        params = kwargs.get('params')
        target = url + ('&' + urlencode(params) if params else '')
        # urllib does not automatically decode requests' Accept-Encoding list.
        # Request plain CSV and retain the same freshness checks after parsing.
        headers = {k: self.headers[k] for k in ('User-Agent', 'Cache-Control', 'Pragma')
                   if k in self.headers}
        timeout = kwargs.get('timeout', 25)
        if isinstance(timeout, tuple):
            timeout = max(timeout)
        try:
            with urlopen(Request(target, headers=headers), timeout=timeout) as raw:
                response = requests.Response()
                response.status_code = raw.status
                response.url = target
                response._content = raw.read()
                response.encoding = 'utf-8'
                return response
        except (URLError, OSError, TimeoutError) as exc:
            raise requests.RequestException(f'FRED CSV transport: {exc}') from exc


def http_session() -> requests.Session:
    s = ProviderSession()
    s.headers.update({'User-Agent': 'Mozilla/5.0 monthly-signal-monitor/2.0',
                      'Cache-Control': 'no-cache', 'Pragma': 'no-cache'})
    s.mount('https://', HTTPAdapter(max_retries=Retry(
        total=1, backoff_factor=1, status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({'GET'}))))
    return s


def clean(series: pd.Series, label: str, minimum: int = 5) -> pd.Series:
    # Null observations can be exchange holidays; missing trading sessions are
    # checked separately. Invalid non-null observations must not disappear.
    series = series.dropna().sort_index()
    if (series.index.hasnans or series.index.has_duplicates or len(series) < minimum
            or not np.isfinite(series.to_numpy()).all() or not (series > 0).all()):
        raise DataUnavailable(f'{label}: invalid/duplicate/insufficient prices')
    return series


def chart_series(payload: dict, label: str = 'QQQ') -> pd.Series:
    chart = payload['chart']
    if chart.get('error'):
        raise DataUnavailable(f'{label}: {chart["error"]}')
    result = chart['result'][0]
    if result.get('meta', {}).get('symbol') != label:
        raise DataUnavailable(f'{label}: wrong or missing symbol metadata')
    stamps = result['timestamp']
    values = result['indicators']['quote'][0]['close']
    if len(stamps) != len(values):
        raise DataUnavailable(f'{label}: timestamp/close length mismatch')
    dates = pd.to_datetime(stamps, unit='s', utc=True).tz_convert(
        'America/New_York').tz_localize(None).normalize()
    return clean(pd.Series(pd.to_numeric(values, errors='raise'), index=dates,
                           dtype=float, name=label), label)


def fred_series(text: str) -> pd.Series:
    df = pd.read_csv(StringIO(text), na_values=['.'])
    column = next((c for c in ('observation_date', 'DATE', 'date') if c in df), None)
    if column is None or 'NASDAQ100' not in df:
        raise DataUnavailable(f'FRED: unexpected columns {list(df)}')
    return clean(pd.Series(pd.to_numeric(df.NASDAQ100, errors='raise').to_numpy(),
                           index=pd.to_datetime(df[column], errors='raise'),
                           dtype=float, name='NDX'), 'FRED')


def merge_tail(history: pd.Series, tail: pd.Series, label: str) -> pd.Series:
    common = history.index.intersection(tail.index)
    if len(common) < 5:
        raise DataUnavailable(f'{label}: fewer than five overlapping closes')
    error = np.max(np.abs(history.loc[common] / tail.loc[common] - 1))
    if error > 0.0001:  # Same instrument, same close field, max 1 bp mismatch.
        raise DataUnavailable(f'{label}: history/recent-close mismatch {error:.6%}')
    return clean(pd.concat([history.loc[~history.index.isin(tail.index)], tail]), label)


def validate_asof(series: pd.Series, report: pd.Timestamp, label: str) -> pd.Series:
    series = clean(series.loc[:report], label, monitor.MIN_OBSERVATIONS)
    if report not in series.index:
        raise DataUnavailable(f'{label}: required {report.date()}, latest {series.index.max().date()}')
    cal = monitor._calendar()
    recent = monitor._normalize_sessions(cal.sessions_in_range(
        report - pd.Timedelta(days=410), report))[-260:]
    missing = recent.difference(series.index)
    if len(missing):
        raise DataUnavailable(f'{label}: missing trading closes {missing.strftime("%Y-%m-%d").tolist()}')
    return series


def get_qqq(session, report: pd.Timestamp, audit: list[dict]) -> pd.Series:
    finish = report.tz_localize('America/New_York') + pd.Timedelta(days=1)
    for url in monitor.YAHOO_QQQ_URLS:
        base = {'period1': 920246400, 'period2': int(finish.timestamp()),
                'interval': '1d', 'events': 'div,splits', 'includePrePost': 'false'}
        try:
            r = session.get(url, params=base, timeout=(8, 25))
            r.raise_for_status()
            history = chart_series(r.json()).loc[:report]
            audit.append({'source': url, 'request': 'full',
                          'latest': str(history.index.max().date())})
            try:
                result = validate_asof(history, report, 'QQQ')
            except DataUnavailable:
                # Full-history responses can be HTTP 200 but stale. A different
                # request/cache key refreshes only actual daily bar closes.
                recent = dict(base, period1=int((finish - pd.Timedelta(days=90)).timestamp()))
                r = session.get(url, params=recent, timeout=(8, 25))
                r.raise_for_status()
                tail = chart_series(r.json()).loc[:report]
                audit.append({'source': url, 'request': 'recent',
                              'latest': str(tail.index.max().date())})
                result = validate_asof(merge_tail(history, tail, 'QQQ'), report, 'QQQ')
            result.attrs['source'] = url
            return result
        except (requests.RequestException, ValueError, KeyError, TypeError, IndexError,
                DataUnavailable) as exc:
            audit.append({'source': url, 'error': str(exc)})
    raise DataUnavailable('QQQ: both endpoints failed freshness/quality validation')


def get_ndx(session, report: pd.Timestamp, audit: list[dict]) -> pd.Series:
    url = monitor.FRED_NDX_URL
    # The canonical series CSV can be cached when date-bounded export requests
    # are slow. Validate its content date, never equate HTTP 200 with freshness.
    requests_to_try = [
        ('canonical', None),
        ('bounded', {'cosd': '1985-01-01', 'coed': report.strftime('%Y-%m-%d')}),
    ]
    errors = []
    for request_name, params in requests_to_try:
        try:
            r = session.get(url, params=params, timeout=(8, 25))
            r.raise_for_status()
            history = fred_series(r.text).loc[:report]
            audit.append({'source': url, 'request': request_name,
                          'latest': str(history.index.max().date())})
            try:
                result = validate_asof(history, report, 'NDX')
            except DataUnavailable:
                recent = {'cosd': (report - pd.Timedelta(days=90)).strftime('%Y-%m-%d'),
                          'coed': report.strftime('%Y-%m-%d')}
                r = session.get(url, params=recent, timeout=(8, 25))
                r.raise_for_status()
                tail = fred_series(r.text).loc[:report]
                audit.append({'source': url, 'request': 'recent',
                              'latest': str(tail.index.max().date())})
                result = validate_asof(merge_tail(history, tail, 'NDX'), report, 'NDX')
            result.attrs['source'] = url
            return result
        except (requests.RequestException, ValueError, KeyError, TypeError, IndexError,
                DataUnavailable) as exc:
            audit.append({'source': url, 'request': request_name, 'error': str(exc)})
            errors.append(str(exc))
    raise DataUnavailable('FRED NDX: all requests failed: ' + '; '.join(errors))


def load_prices(report: pd.Timestamp, audit: list[dict], attempts: int = 3,
                sleeper=time.sleep, session_factory=http_session):
    for attempt in range(1, attempts + 1):
        try:
            with session_factory() as session:
                audit.append({'attempt': attempt, 'started_at': datetime.now(timezone.utc).isoformat()})
                qqq = get_qqq(session, report, audit)
                ndx = get_ndx(session, report, audit)
                monitor.validate_cross_source(qqq, ndx, report)
                return qqq, ndx
        except (requests.RequestException, monitor.MonitorError) as exc:
            audit.append({'attempt': attempt, 'error': str(exc)})
            if attempt == attempts:
                raise DataUnavailable(f'Fresh inputs unavailable after {attempts} attempts: {exc}') from exc
            sleeper((20, 60)[min(attempt - 1, 1)])
    raise ValueError('attempts must be positive')
