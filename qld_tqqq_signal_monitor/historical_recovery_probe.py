"""Read-only bounded common-session test; NOT a latest-session trading signal.

Production freshness remains in reliable_data.validate_asof and the required
live pipeline job. This diagnostic can exercise the daily-table fallback with
one withheld close on the latest common completed session, at most ONE exchange
session behind live. It separately reports current-provider readiness. A dated
historical replay is never relabelled as a successful current-data verification.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pandas as pd
import requests

import monitor
import reliable_data as data
from automation import latest_session
from ndx_fallback import sessions, utc
import qqq_recovery as recovery


def _json(path: Path, payload: dict) -> None:
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(payload, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temp.replace(path)


def _response(response: requests.Response, content: bytes | None = None) -> requests.Response:
    """Copy a recorded response, never mutate a provider reply in-place."""
    clone = requests.Response()
    clone.status_code = response.status_code
    clone.headers.update(response.headers)
    clone.url = response.url
    clone.encoding = response.encoding
    clone._content = response.content if content is None else content
    return clone


def select_session(history: pd.Series, payload: dict, report, calendar) -> pd.Timestamp:
    """Select by dates only; never step backwards to hide invalid prices."""
    report = pd.Timestamp(report)
    if report.tzinfo is not None or report != report.normalize() or not calendar.is_session(report):
        raise recovery.RecoveryError('Invalid live diagnostic report date')
    if payload.get('status', {}).get('rCode') != 200 or (payload.get('data') or {}).get('symbol') != 'QQQ':
        raise recovery.RecoveryError('Invalid Nasdaq diagnostic identity/status')
    rows = (payload['data'].get('tradesTable') or {}).get('rows')
    if not isinstance(rows, list) or not rows:
        raise recovery.RecoveryError('No Nasdaq historical rows for diagnostic')
    dates = pd.DatetimeIndex(pd.to_datetime([r['date'] for r in rows], format='%m/%d/%Y', errors='raise'))
    dates = dates[dates <= report]
    historical = history.loc[:report]
    if historical.empty or dates.empty:
        raise recovery.RecoveryError('No completed common-session history')
    candidate = min(historical.index[-1], dates.max())
    if not calendar.is_session(candidate):
        raise recovery.RecoveryError('Latest reference row is not an exchange session')
    lag = len(sessions(calendar, candidate, report)) - 1
    if lag not in (0, 1):
        raise recovery.RecoveryError('Historical fallback test is more than one session behind live')
    return candidate


def withhold_close(raw: dict, tested: pd.Timestamp) -> dict:
    obj = deepcopy(raw)
    result = obj['chart']['result'][0]
    stamps = result['timestamp']
    dates = pd.to_datetime(stamps, unit='s', utc=True).tz_convert('America/New_York')
    keep = [d.date() < tested.date() for d in dates]
    result['timestamp'] = [v for v, ok in zip(stamps, keep) if ok]
    for blocks in result['indicators'].values():
        for block in blocks:
            for field, values in block.items():
                if len(values) != len(stamps):
                    raise recovery.RecoveryError('Recorded Yahoo indicator length mismatch')
                block[field] = [v for v, ok in zip(values, keep) if ok]
    return obj


def probe(output_dir, *, now=None, session_factory=data.http_session) -> int:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name in ('verified_closes.csv', 'source_yahoo.json', 'source_nasdaq.json'):
        (out / name).unlink(missing_ok=True)
    clock = utc(now)
    report = latest_session(clock)
    cal = monitor._calendar()
    record = {'status': 'running', 'purpose': 'bounded_historical_daily_fallback_contract',
              'live_report_date': str(report.date()), 'tested_session': None,
              'execution_authorized': False, 'publish_notification': False,
              'live_pipeline_verified_by_this_probe': False, 'max_history_lag_sessions': 1,
              'attempts': [], 'current_daily_path_status': 'not_checked'}
    _json(out / 'probe.json', record)
    code = 2
    try:
        with session_factory() as session:
            finish = report.tz_localize('America/New_York') + pd.Timedelta(days=1)
            params = {'period1': 920246400, 'period2': int(finish.timestamp()), 'interval': '1d',
                      'events': 'div,splits', 'includePrePost': 'false'}
            source = None
            # Fetch a complete historical anchor, not data.get_qqq(report), whose
            # current-date prerequisite was the circular dependency being fixed.
            for url in monitor.YAHOO_QQQ_URLS:
                try:
                    r = session.get(url, params=params, timeout=(8, 25))
                    r.raise_for_status()
                    if len(r.content) > 6_000_000:
                        raise recovery.RecoveryError('Oversized Yahoo diagnostic history')
                    raw = r.json()
                    history = data.chart_series(raw).loc[:report]
                    source = (r, raw, history)
                    break
                except (requests.RequestException, ValueError, KeyError, TypeError, IndexError, data.DataUnavailable) as exc:
                    record['attempts'].append({'request': 'diagnostic_anchor', 'source': url, 'error': str(exc)})
            if source is None:
                raise recovery.RecoveryError('No valid Yahoo historical anchor for fallback diagnostic')
            yr, raw, history = source
            nr = session.get(recovery.NASDAQ_URL, params={'assetclass': 'etf',
                'fromdate': str((report-pd.Timedelta(days=90)).date()), 'todate': str(report.date()), 'limit': 100},
                timeout=(8, 25), headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json',
                'Origin': 'https://www.nasdaq.com', 'Referer': 'https://www.nasdaq.com/', 'Cache-Control': 'no-cache'})
            nr.raise_for_status()
            if len(nr.content) > 2_000_000:
                raise recovery.RecoveryError('Oversized Nasdaq diagnostic history')
            payload = nr.json()
            for name, response in (('source_yahoo.json', yr), ('source_nasdaq.json', nr)):
                (out / name).write_bytes(response.content)
            record['source_sha256'] = {name: hashlib.sha256((out/name).read_bytes()).hexdigest()
                for name in ('source_yahoo.json', 'source_nasdaq.json')}
            record['nasdaq_http_date'] = nr.headers.get('Date')
            record['nasdaq_http_age'] = nr.headers.get('Age', '0')
            tested = select_session(history, payload, report, cal)
            original = data.validate_asof(history, tested, 'QQQ historical diagnostic')
            anchor = original.loc[original.index < tested]
            record.update(tested_session=str(tested.date()),
                          history_lag_sessions=len(sessions(cal, tested, report))-1,
                          original_history_rows_preserved=len(anchor),
                          yahoo_latest_valid_close=str(history.index[-1].date()))

            class Recorded:
                def get(self, url, **kwargs):
                    if url == recovery.NASDAQ_URL:
                        return _response(nr)
                    raise recovery.RecoveryError('Unexpected transport in recorded Nasdaq test')

            # Readiness is deliberately separate from the historical assertion.
            # Only a genuinely absent current row is advisory. Bad observations,
            # metadata, HTTP times or mismatched prices are still hard failures.
            record['current_daily_path_status'] = 'unavailable'
            try:
                recovery.parse_history(payload, report, cal, clock)
            except recovery.RecoveryError as exc:
                if not str(exc).startswith('QQQ Nasdaq history is stale:'):
                    raise
                record['current_daily_path_reason'] = str(exc)
            else:
                current = recovery.fetch_tail(Recorded(), history.loc[history.index < report], report,
                    record['attempts'], cal, clock)
                if report in history.index and abs(current.loc[report]/history.loc[report]-1) > recovery.TOLERANCE:
                    raise recovery.RecoveryError('Current Yahoo/Nasdaq close mismatch')
                record['current_daily_path_status'] = 'verified'

            result = recovery.fetch_tail(Recorded(), anchor, tested, record['attempts'], cal, clock)
            data.validate_asof(result, tested, 'QQQ recovered historical diagnostic')
            pd.testing.assert_series_equal(result.loc[anchor.index], anchor, check_freq=False)
            error = abs(result.loc[tested]/original.loc[tested]-1)
            if not 0 <= error <= recovery.TOLERANCE:
                raise recovery.RecoveryError('Withheld Yahoo close disagrees with Nasdaq history')

            missing = json.dumps(withhold_close(raw, tested)).encode()
            class ForcedMissing:
                def get(self, url, **kwargs):
                    if url in monitor.YAHOO_QQQ_URLS:
                        return _response(yr, missing)
                    if url == recovery.NASDAQ_URL:
                        return _response(nr)
                    raise recovery.RecoveryError('Unexpected endpoint; daily fallback was not exercised')

            audit = []
            recovered = recovery.get_qqq_reliable(ForcedMissing(), tested, audit)
            if not any(x.get('request') == 'verified_qqq_tail' and x.get('status') == 'ok' for x in audit):
                raise recovery.RecoveryError('Forced source-wrapper run did not exercise Nasdaq daily recovery')
            pd.testing.assert_series_equal(recovered, result, check_freq=False)
            record.update(status='ok', withheld_relative_error=float(error),
                          historical_fallback_verified=True, qqq_source_wrapper_exercised=True,
                          recovered_close=float(recovered.loc[tested]), source_wrapper_audit=audit)
            recovered.to_csv(out/'verified_closes.csv', index_label='date')
            code = 0
    except Exception as exc:
        record.update(status='error', error=str(exc), historical_fallback_verified=False)
        (out/'verified_closes.csv').unlink(missing_ok=True)
    record['completed_at'] = utc().isoformat()
    _json(out / 'probe.json', record)
    if record['status'] == 'ok' and record['current_daily_path_status'] != 'verified':
        print('::warning::Nasdaq daily-table route is NOT current for ' + str(report.date())
              + '; only the bounded historical fallback test passed on ' + record['tested_session']
              + '. Latest-session production and finalized-close checks are separate required steps.')
    print(json.dumps(record, indent=2))
    return code


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe-dir', default='qqq_recovery_probe')
    raise SystemExit(probe(parser.parse_args().probe_dir))
