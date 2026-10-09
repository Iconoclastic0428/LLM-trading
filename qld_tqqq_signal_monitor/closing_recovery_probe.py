"""Read-only real-source fault injection for the finalized-close recovery path."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pandas as pd

import monitor
import price_checkpoint
import reliable_data as data
from automation import latest_session
from qqq_recovery import NASDAQ_URL, RecoveryError


def probe(output_dir: str) -> int:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    report = latest_session()
    record = {'status': 'error', 'report_date': str(report.date()), 'attempts': [],
              'execution_authorized': False, 'purpose': 'forced_finalized_close_then_same_session_checkpoint'}
    os.environ['SOURCE_EVIDENCE_DIR'] = str(out / 'source_evidence')
    os.environ['PRICE_CHECKPOINT_DIR'] = str(out / 'checkpoint')
    code = 2
    try:
        with data.http_session() as session:
            # Freeze the real full historical anchor for preservation verification.
            finish = report.tz_localize('America/New_York') + pd.Timedelta(days=1)
            response = session.get(monitor.YAHOO_QQQ_URLS[0], params={
                'period1': 920246400, 'period2': int(finish.timestamp()), 'interval': '1d',
                'events': 'div,splits', 'includePrePost': 'false'}, timeout=(8, 25))
            response.raise_for_status()
            raw = response.json()
            original = data.chart_series(raw).loc[:report]
            anchor = original.loc[original.index < report]
            (out/'original_daily_response.json').write_bytes(response.content)
            record['original_daily_response_sha256'] = hashlib.sha256(response.content).hexdigest()

            class MissingDailyClose:
                """Hide today's daily close only; retain genuine OHL and 5m prices."""
                def get(self, url, **kwargs):
                    r = session.get(url, **kwargs)
                    r.raise_for_status()
                    if url in monitor.YAHOO_QQQ_URLS and kwargs.get('params', {}).get('interval') == '1d':
                        obj = r.json()
                        result = obj['chart']['result'][0]
                        dates = pd.to_datetime(result['timestamp'], unit='s', utc=True).tz_convert('America/New_York')
                        for i, d in enumerate(dates):
                            if d.date() == report.date():
                                result['indicators']['quote'][0]['close'][i] = None
                        r._content = json.dumps(obj).encode()
                    elif url == NASDAQ_URL:
                        obj = r.json()
                        rows = obj['data']['tradesTable']['rows']
                        obj['data']['tradesTable']['rows'] = [row for row in rows
                            if pd.to_datetime(row['date'], format='%m/%d/%Y').date() < report.date()]
                        r._content = json.dumps(obj).encode()
                    return r
                def __enter__(self): return self
                def __exit__(self, *args): return False

            qqq, ndx = data.load_prices(report, record['attempts'], attempts=1, session_factory=MissingDailyClose)
            entry = next((a for a in record['attempts'] if a.get('request') == 'qqq_finalized_recovery'), None)
            if not entry:
                raise RecoveryError('Probe did not exercise the finalized-close branch')
            pd.testing.assert_series_equal(qqq.loc[anchor.index], anchor.rename('QQQ'), check_freq=False)
            if report in original.index and abs(float(original.loc[report]) - float(qqq.loc[report])) > 0.005:
                raise RecoveryError('Finalized recovery disagrees with the available genuine daily close')
            # Force transport failure only after this exact session has passed all
            # source and cross-source checks. This must use the real loader, not a
            # direct call that bypasses input validation.
            class Offline:
                def get(self, *args, **kwargs):
                    raise data.requests.ConnectionError('Deliberate transport outage in read-only probe')
                def __enter__(self): return self
                def __exit__(self, *args): return False
            offline_audit = []
            cq, cn = data.load_prices(report, offline_audit, attempts=1, session_factory=Offline)
            pd.testing.assert_series_equal(cq, qqq, check_freq=False)
            pd.testing.assert_series_equal(cn, ndx, check_freq=False)
            if monitor.build_decision(cq, cn, report) != monitor.build_decision(qqq, ndx, report):
                raise RecoveryError('Checkpoint changed model targets')
            next_session = monitor._calendar().next_session(report).tz_localize(None)
            if price_checkpoint.restore(next_session, []) is not None:
                raise RecoveryError('Checkpoint improperly authorized a different session')
            pd.concat([qqq, ndx], axis=1).tail(300).to_csv(out/'verified_closes.csv', index_label='date')
            record.update(status='ok', qqq_close=float(qqq.loc[report]), original_history_rows_preserved=len(anchor),
                          finalized_close_details=entry, checkpoint_fault_injection=offline_audit,
                          checkpoint_target_parity=True, new_session_checkpoint_rejected=True,
                          qqq_source=qqq.attrs.get('source'), ndx_source=ndx.attrs.get('source'))
            code = 0
    except Exception as exc:
        record['error'] = str(exc)
    (out/'probe.json').write_text(json.dumps(record, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(record, indent=2))
    return code


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir', default='finalized_close_probe')
    raise SystemExit(probe(p.parse_args().output_dir))
