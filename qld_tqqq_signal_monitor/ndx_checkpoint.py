"""Use a verified earlier FRED history only as an anchor for NEW daily NDX bars.

The old same-session checkpoint contract is unchanged. This route requires live
post-close ^NDX OHLC, matching overlap, and at most five sessions since the last
FRED-origin observation; rolling checkpoints cannot extend that allowance.
"""
from __future__ import annotations
import json
import re
from pathlib import Path
import pandas as pd
import monitor
import price_checkpoint as checkpoint
from ndx_fallback import TailUnavailable, fetch_tail, sessions, utc


def recover(session, report, audit, now=None):
    import reliable_data as data
    report = pd.Timestamp(report)
    failures = ' '.join(str(x.get('error', '')).lower() for x in audit)
    unsafe = ('conflict', 'mismatch', 'duplicate', 'wrong or missing', 'wrong symbol',
              'non-positive', 'unexpected columns', 'invalid/duplicate')
    if any(word in failures for word in unsafe):
        raise TailUnavailable('Observed data conflict forbids a cached NDX anchor')
    folder = checkpoint.directory()
    if folder is None:
        raise TailUnavailable('No verified checkpoint configured for NDX anchor')
    if folder.is_symlink():
        raise TailUnavailable('Checkpoint directory cannot be a symlink')
    doc = json.loads(checkpoint._read(folder / 'manifest.json'))
    anchor_day = pd.Timestamp(doc['report_date'])
    if anchor_day.tzinfo is not None or anchor_day != anchor_day.normalize() or anchor_day >= report:
        raise TailUnavailable('Cached NDX anchor must precede the requested session')
    source = doc['prices']['NDX']['source']
    if source == monitor.FRED_NDX_URL:
        origin = anchor_day
    else:
        match = re.match(r'^FRED NASDAQ100 through (\d{4}-\d{2}-\d{2});', source)
        if not match:
            raise TailUnavailable('Cached NDX source has no recognized FRED provenance')
        origin = pd.Timestamp(match.group(1))
    cal = monitor._calendar()
    if (origin > anchor_day or not cal.is_session(origin)
            or not 1 <= len(sessions(cal, origin, report)) - 1 <= 5):
        raise TailUnavailable('More than five sessions since original FRED anchor; no rolling extension')
    local_audit = []
    restored = checkpoint.restore(anchor_day, local_audit, now)
    if restored is None:
        audit.extend(local_audit)
        raise TailUnavailable('Cached NDX anchor failed its existing full-history validation')
    history = restored[1]
    # Read the same manifest again so file changes cannot swap provenance.
    if json.loads(checkpoint._read(folder / 'manifest.json')) != doc:
        raise TailUnavailable('Checkpoint manifest changed during validation')
    result = fetch_tail(session, history, report, audit, cal, now)
    result = data.validate_asof(result, report, 'NDX live tail from verified cached history')
    pd.testing.assert_series_equal(result.loc[history.index], history, check_freq=False, check_exact=True)
    result.attrs['source'] = (f'FRED NASDAQ100 through {origin.date()}; verified checkpoint through '
                              f'{anchor_day.date()}; append-only daily ^NDX live-validated through {report.date()}')
    audit.append({'request':'ndx_checkpoint_live_append', 'status':'ok',
                  'report_date':str(report.date()), 'checkpoint_date':str(anchor_day.date()),
                  'fred_origin_date':str(origin.date()), 'original_verified_at':doc['verified_at'],
                  'original_run_id':doc.get('run_id'), 'preserved_rows':len(history),
                  'appended_dates':[str(d.date()) for d in result.index.difference(history.index)],
                  'live_tail_required':True, 'execution_authorized':False})
    return result

