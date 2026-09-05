"""Download the original FRED CSV using only the runner's system Python.

This process transports bytes, never calculates or substitutes prices. The caller
still parses, validates date coverage and checks the series against QQQ.
"""
from __future__ import annotations

import sys
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen


def download(url: str, timeout: float) -> bytes:
    parts = urlparse(url)
    query = parse_qs(parts.query)
    if (parts.scheme != 'https' or parts.netloc != 'fred.stlouisfed.org'
            or parts.path != '/graph/fredgraph.csv'
            or query.get('id') != ['NASDAQ100']
            or not set(query).issubset({'id', 'cosd', 'coed'})):
        raise ValueError('Only the original FRED NASDAQ100 CSV is allowed')
    headers = {'User-Agent': 'Mozilla/5.0 monthly-signal-monitor/2.0',
               'Cache-Control': 'no-cache', 'Pragma': 'no-cache'}
    with urlopen(Request(url, headers=headers), timeout=timeout) as response:
        if response.status != 200:
            raise ValueError(f'FRED returned HTTP {response.status}')
        body = response.read(5_000_001)
    if len(body) > 5_000_000:
        raise ValueError('FRED response exceeds expected CSV size')
    return body


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        if len(args) != 2:
            raise ValueError('Expected URL and timeout')
        timeout = float(args[1])
        if not 0 < timeout <= 60:
            raise ValueError('Invalid timeout')
        sys.stdout.buffer.write(download(args[0], timeout))
        return 0
    except Exception as exc:
        print(f'FRED download failed: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
