"""Read-only fault injection for prolonged FRED failure with independent closes."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd
import requests

import monitor
import ndx_dual_source as dual
import price_checkpoint as cp
import reliable_data as data
from automation import latest_session
from ndx_fallback import TailUnavailable, sessions, utc
from ndx_checkpoint_probe import reference_source


def probe(output_dir):
    out = Path(output_dir); out.mkdir(parents=True, exist_ok=True)
    report = latest_session(); audit = []
    record = {'status': 'error', 'report_date': str(report.date()),
              'execution_authorized': False, 'attempts': audit}
    old = os.environ.get('PRICE_CHECKPOINT_DIR')
    old_evidence = os.environ.get('SOURCE_EVIDENCE_DIR')
    code = 2
    try:
        restored = cp.restore(report, audit)
        if restored is None:
            raise TailUnavailable('Latest-session reference must already be verified by normal generation')
        qqq, ndx = restored
        source = reference_source(ndx, report)
        prior = sessions(monitor._calendar(), report-pd.Timedelta(days=7), report)[-2]
        with TemporaryDirectory() as folder:
            os.environ['PRICE_CHECKPOINT_DIR'] = folder
            os.environ['SOURCE_EVIDENCE_DIR'] = str(out/'responses')
            history = ndx.loc[:prior].copy(); history.attrs['source'] = source
            cp.save(qqq.loc[:prior], history, prior, audit)
            with data.http_session() as real:
                class Outage:
                    def get(self, url, **kwargs):
                        if url == monitor.FRED_NDX_URL:
                            raise requests.Timeout('Fault injection: all FRED transports unavailable')
                        return real.get(url, **kwargs)
                # Also disable the short-outage route so this checks the real
                # wrapper's independent route even after FRED has recovered.
                with patch('ndx_checkpoint.recover', side_effect=TailUnavailable(
                        'Fault injection: single-source allowance unavailable')):
                    got = get_ndx_reliable(Outage(), report, audit)
            if not any(x.get('request')=='ndx_dual_source_append' and x.get('status')=='ok' for x in audit):
                raise ValueError('Actual loader did not exercise dual-source append')
            pd.testing.assert_series_equal(got.loc[history.index], history, check_freq=False, check_exact=True)
            rel = abs(float(got.loc[report]/ndx.loc[report]-1))
            if rel > dual.TOLERANCE:
                raise ValueError('Recovered price conflicts with independent current reference')
            monitor.validate_cross_source(qqq, got, report)
            before = monitor.build_decision(qqq, ndx, report)
            after = monitor.build_decision(qqq, got, report)
            if rel == 0 and before != after:
                raise ValueError('Identical histories changed strategy targets')
            if cp.restore(report, [], utc()) is not None:
                raise ValueError('Yesterday-only cache incorrectly satisfies current report')
            class Offline:
                def get(self,*args,**kwargs):raise requests.Timeout('All independent sources offline')
            try:
                dual.recover(Offline(), report, [])
            except requests.RequestException:
                record['missing_independent_source_rejected'] = True
            else:
                raise ValueError('Recovery succeeded without independent current evidence')
            got.to_csv(out/'NDX.csv', index_label='date')
            record.update(status='ok',preserved_rows=len(history),
                          recovered_close=float(got.loc[report]),withheld_relative_error=rel,
                          anchor_date=str(prior.date()),source=got.attrs['source'],
                          independent_evidence=[x for x in audit if x.get('request')=='ndx_dual_source_append' and x.get('status')=='ok'][-1],
                          cache_alone_rejected=True,full_loader_exercised=True)
            code = 0
    except Exception as exc:
        record['error'] = str(exc)
    finally:
        for key,value in (('PRICE_CHECKPOINT_DIR',old),('SOURCE_EVIDENCE_DIR',old_evidence)):
            if value is None:os.environ.pop(key,None)
            else:os.environ[key]=value
    (out/'probe.json').write_text(json.dumps(record,indent=2,allow_nan=False)+'\n')
    print(json.dumps(record,indent=2));return code


if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe-dir',default='ndx_dual_source_probe')
    raise SystemExit(probe(parser.parse_args().probe_dir))
