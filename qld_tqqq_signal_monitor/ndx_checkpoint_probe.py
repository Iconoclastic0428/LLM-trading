"""Force a FRED transport outage through the real NDX loader, read-only."""
from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import json
import os
import pandas as pd
import requests
import monitor
import price_checkpoint as cp
import reliable_data as data
from ndx_loader import get_ndx_reliable
from ndx_fallback import utc
from automation import latest_session


def probe(output_dir):
    out=Path(output_dir);out.mkdir(parents=True,exist_ok=True)
    report=latest_session();audit=[]
    record={'status':'error','report_date':str(report.date()),'execution_authorized':False,'attempts':audit}
    old=os.environ.get('PRICE_CHECKPOINT_DIR');code=2
    try:
        # This reference is obtained by current production validation, not invented
        # or taken from a historical table lacking today's close.
        qqq,ndx=data.load_prices(report,audit,attempts=1)
        prior=monitor._normalize_sessions(monitor._calendar().sessions_in_range(report-pd.Timedelta(days=7),report))[-2]
        with TemporaryDirectory() as folder:
            os.environ['PRICE_CHECKPOINT_DIR']=folder
            h=ndx.loc[:prior].copy()
            # Retain original FRED provenance from the current verified inputs.
            if h.attrs.get('source')==monitor.FRED_NDX_URL:
                h.attrs['source']=monitor.FRED_NDX_URL
            cp.save(qqq.loc[:prior],h,prior,audit)
            with data.http_session() as real:
                class Outage:
                    def get(self,url,**kwargs):
                        if url==monitor.FRED_NDX_URL:
                            raise requests.Timeout('Deliberate FRED transport outage in read-only probe')
                        r=real.get(url,**kwargs)
                        if '%5ENDX' in url and r.status_code==200:
                            p=out/'live_ndx_response.json';p.write_bytes(r.content)
                            record['response_sha256']=hashlib.sha256(r.content).hexdigest()
                        return r
                got=get_ndx_reliable(Outage(),report,audit)
            pd.testing.assert_series_equal(got.loc[h.index],h,check_freq=False,check_exact=True)
            rel=abs(float(got.loc[report]/ndx.loc[report]-1))
            if rel>0.0001:raise ValueError('New recovered NDX differs from verified current reference')
            monitor.validate_cross_source(qqq,got,report)
            # Exact same source history should produce exactly identical targets.
            before=monitor.build_decision(qqq,ndx,report)
            after=monitor.build_decision(qqq,got,report)
            if rel==0 and before!=after:raise ValueError('Equal histories changed strategy targets')
            got.to_csv(out/'NDX.csv',index_label='date')
            if cp.restore(report,[],utc()) is not None:
                raise ValueError('Prior-only checkpoint improperly became a current signal')
            record.update(status='ok',seed_is_fault_injection=True,anchor_date=str(prior.date()),
                          withheld_relative_error=rel,preserved_rows=len(h),
                          appended_dates=[str(x.date()) for x in got.index.difference(h.index)],
                          current_close=float(got.loc[report]),cache_alone_rejected=True,
                          source=got.attrs.get('source'))
            code=0
    except Exception as exc:record['error']=str(exc)
    finally:
        if old is None:os.environ.pop('PRICE_CHECKPOINT_DIR',None)
        else:os.environ['PRICE_CHECKPOINT_DIR']=old
    (out/'probe.json').write_text(json.dumps(record,indent=2,allow_nan=False)+'\n')
    print(json.dumps(record,indent=2));return code

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--probe-dir',default='ndx_checkpoint_probe')
    raise SystemExit(probe(p.parse_args().probe_dir))
