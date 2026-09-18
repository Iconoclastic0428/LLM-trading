"""Read-only provider diagnostics. Never generate or publish a trading signal."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import os

import pandas as pd
import requests
from automation import latest_session


def main():
    out = Path('provider_diagnostics'); out.mkdir(exist_ok=True)
    report = latest_session()
    finish = report.tz_localize('America/New_York') + pd.Timedelta(days=1)
    common = {'interval': '1d', 'events': 'div,splits', 'includePrePost': 'false'}
    base = 'https://query1.finance.yahoo.com/v8/finance/chart/QQQ'
    probes = [
        ('yahoo_full', base, dict(common, period1=920246400, period2=int(finish.timestamp()))),
        ('yahoo_bounded90', base, dict(common, period1=int((finish-pd.Timedelta(days=90)).timestamp()), period2=int(finish.timestamp()))),
        ('yahoo_range1mo', base, dict(common, range='1mo')),
        ('yahoo_range3mo', base, dict(common, range='3mo')),
        ('yahoo2_range3mo', base.replace('query1.', 'query2.'), dict(common, range='3mo')),
        ('nasdaq_history', 'https://api.nasdaq.com/api/quote/QQQ/historical',
         {'assetclass':'etf', 'fromdate':str((report-pd.Timedelta(days=90)).date()), 'todate':str(report.date()), 'limit':100}),
    ]
    records = []
    headers = {'User-Agent':'Mozilla/5.0', 'Accept':'application/json', 'Origin':'https://www.nasdaq.com',
               'Referer':'https://www.nasdaq.com/', 'Cache-Control':'no-cache'}
    for name, url, params in probes:
        record = {'name':name, 'url':url, 'params':params}
        try:
            r = requests.get(url, params=params, headers=headers, timeout=(8,20))
            record.update(http_status=r.status_code, bytes=len(r.content),
                          sha256=hashlib.sha256(r.content).hexdigest(),
                          response_headers={k:v for k,v in r.headers.items() if k.lower() in ('date','age','cache-control','content-type')})
            r.raise_for_status()
            j = r.json()
            (out / (name+'.json')).write_text(json.dumps(j), encoding='utf-8')
            if 'chart' in j:
                result = j['chart']['result'][0]
                ts = result.get('timestamp', [])
                dates = pd.to_datetime(ts,unit='s',utc=True).tz_convert('America/New_York')
                record.update(rows=len(ts), latest_bar=str(dates[-1]), metadata=result.get('meta'),
                              last_closes=result['indicators']['quote'][0]['close'][-3:])
            else:
                record['sample'] = str(j)[:1600]
            record['status']='downloaded'
        except Exception as exc:
            record.update(status='error', error=str(exc))
        records.append(record)
        print(json.dumps(record, default=str))
    # Public repository run metadata only, no credentials or arbitrary endpoints.
    try:
        r = requests.get('https://api.github.com/repos/Iconoclastic0428/LLM-trading/actions/runs',
                         params={'per_page':100}, timeout=(8,20))
        r.raise_for_status()
        runs=[{k:v.get(k) for k in ('id','path','event','run_attempt','created_at','run_started_at','updated_at','conclusion','status','head_sha')}
              for v in r.json()['workflow_runs']]
        (out/'recent_runs.json').write_text(json.dumps(runs,indent=2),encoding='utf-8')
    except Exception as exc:
        records.append({'name':'run_history','error':str(exc)})
    summary={'report_date':str(report.date()), 'at':datetime.now(timezone.utc).isoformat(),
             'execution_authorized':False,'probes':records}
    (out/'summary.json').write_text(json.dumps(summary,indent=2,default=str),encoding='utf-8')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
