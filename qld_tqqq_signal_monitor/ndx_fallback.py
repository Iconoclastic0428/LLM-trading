"""Append verified daily ^NDX bars when the FRED NASDAQ100 tail is delayed.

FRED remains the historical anchor. No QQQ proxy, quote, forward fill, scaling,
interior repair, or wholesale provider replacement is permitted here.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import requests

URLS = ("https://query1.finance.yahoo.com/v8/finance/chart/%5ENDX",
        "https://query2.finance.yahoo.com/v8/finance/chart/%5ENDX")
MAX_TAIL_SESSIONS = 5
OVERLAP_SESSIONS = 20
MAX_UNAVAILABLE_OVERLAP = 5
MAX_RELATIVE_ERROR = 0.0001  # one basis point, same price index


class TailUnavailable(ValueError):
    pass


def utc(value=None):
    stamp = pd.Timestamp(datetime.now(timezone.utc) if value is None else value)
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise TailUnavailable("An explicit UTC offset is required")
    return stamp.tz_convert("UTC")


def sessions(calendar, start, end):
    idx = pd.DatetimeIndex(calendar.sessions_in_range(start, end))
    return idx.tz_localize(None).normalize() if idx.tz is not None else idx.normalize()


def prefix(series, cutoff):
    out = series.copy()
    out.index = pd.DatetimeIndex(out.index)
    if out.index.tz is not None or out.index.hasnans or not out.index.equals(out.index.normalize()):
        raise TailUnavailable("Invalid daily dates")
    out = out.loc[out.index <= cutoff].sort_index()
    if (out.empty or out.index.has_duplicates or not np.isfinite(out.to_numpy(dtype=float)).all()
            or (out <= 0).any()):
        raise TailUnavailable("Invalid/duplicate/non-positive daily closes")
    return out.astype(float)


def validate_anchor(history, report, calendar, now=None):
    report = pd.Timestamp(report)
    if pd.isna(report) or report.tzinfo is not None or report != report.normalize() or not calendar.is_session(report):
        raise TailUnavailable("Report must be an exchange-session date")
    # Same minimum publication lag as automation.latest_session, not an intraday quote.
    if utc(now) < utc(calendar.session_close(report)) + pd.Timedelta(minutes=30):
        raise TailUnavailable("Requested report session is not complete")
    h = prefix(history, report)
    if len(h) < 500:
        raise TailUnavailable("FRED anchor needs at least 500 valid observations")
    anchor = h.index[-1]
    if not calendar.is_session(anchor):
        raise TailUnavailable("FRED anchor does not end on an exchange session")
    expected = sessions(calendar, anchor - pd.Timedelta(days=410), anchor)[-260:]
    if len(expected.difference(h.index)):
        raise TailUnavailable("FRED anchor has missing interior trading closes")
    added = sessions(calendar, anchor, report)[1:]
    if not 1 <= len(added) <= MAX_TAIL_SESSIONS:
        raise TailUnavailable("Fallback requires a delayed FRED tail of one to five sessions")
    return h, added


def daily_ndx(payload, report, calendar, now=None, *, anchor_end=None):
    """Decode a daily response, separating reference closes from new OHLC.

    With a validated FRED anchor, historical OHLC is not imported. Missing
    historical Yahoo closes remain NaN until append_tail audits their exclusion.
    Without an anchor this retains the original strict all-OHLC contract.
    """
    chart = payload["chart"]
    if chart.get("error"):
        raise TailUnavailable(f"Yahoo chart error: {chart['error']}")
    result = chart["result"][0]
    meta = result["meta"]
    required = {"symbol": "^NDX", "instrumentType": "INDEX", "currency": "USD",
                "exchangeTimezoneName": "America/New_York", "dataGranularity": "1d"}
    if any(meta.get(k) != v for k, v in required.items()):
        raise TailUnavailable("Expected daily USD ^NDX index metadata, not an ETF or quote")
    # Same-date cached morning bars must not pass merely because the runner is now after close.
    tick = pd.Timestamp(meta["regularMarketTime"], unit="s", tz="UTC")
    if pd.isna(tick) or tick < utc(calendar.session_close(report)) or tick > utc(now) + pd.Timedelta(minutes=5):
        raise TailUnavailable("Provider timestamp does not attest a completed report session")
    stamps = result["timestamp"]
    quote = result["indicators"]["quote"][0]
    if not stamps or any(len(quote[k]) != len(stamps) for k in ("open", "high", "low", "close")):
        raise TailUnavailable("Daily OHLC/timestamp lengths differ")
    idx = pd.to_datetime(stamps, unit="s", utc=True).tz_convert("America/New_York").tz_localize(None).normalize()
    frame = pd.DataFrame({k: quote[k] for k in ("open", "high", "low", "close")}, index=idx)
    frame = frame.loc[frame.index <= report].sort_index()
    if frame.empty or frame.index.hasnans or frame.index.has_duplicates:
        raise TailUnavailable("Invalid/duplicate daily ^NDX OHLC dates")
    if anchor_end is not None:
        anchor_end = pd.Timestamp(anchor_end)
        if (anchor_end.tzinfo is not None or anchor_end != anchor_end.normalize()
                or anchor_end >= report or not calendar.is_session(anchor_end)):
            raise TailUnavailable("Invalid FRED anchor end for daily ^NDX validation")
        start = sessions(calendar, anchor_end - pd.Timedelta(days=60), anchor_end)[
            -(OVERLAP_SESSIONS + MAX_UNAVAILABLE_OVERLAP)]
        frame = frame.loc[frame.index >= start].copy()
        # These closes only cross-check the immutable FRED anchor. OHLC fields
        # on those rows are never used as strategy prices or new market bars.
        historical = frame.loc[frame.index <= anchor_end, "close"]
        numeric = pd.to_numeric(historical, errors="raise")
        invalid = numeric.notna() & (~np.isfinite(numeric) | (numeric <= 0))
        if invalid.any():
            raise TailUnavailable("Invalid historical ^NDX close: " +
                                  ", ".join(str(d.date()) for d in numeric.index[invalid]))
        frame.loc[numeric.index, "close"] = numeric
        new = frame.loc[frame.index > anchor_end].copy()
    else:
        new = frame.copy()
    for field in ("open", "high", "low", "close"):
        new[field] = pd.to_numeric(new[field], errors="raise")
    a = new.to_numpy(dtype=float)
    bad = pd.Series(~np.isfinite(a).all(axis=1) | (a <= 0).any(axis=1), index=new.index)
    bad |= ((new.low > new.high) | (new.close < new.low) | (new.close > new.high)
            | (new.open < new.low) | (new.open > new.high))
    if new.empty or bad.any() or report not in new.index:
        dates = ", ".join(str(d.date()) for d in new.index[bad])
        raise TailUnavailable("Invalid or incomplete daily ^NDX OHLC: " +
                              (dates or "missing report session " + str(report.date())))
    return pd.to_numeric(frame.close, errors="raise").astype(float).rename("NDX")


def append_tail(history, tail, report, calendar, now=None, *, allow_sparse_overlap=False):
    h, added = validate_anchor(history, report, calendar, now)
    if allow_sparse_overlap:
        tail = tail.copy()
        tail.index = pd.DatetimeIndex(tail.index)
        tail = tail.loc[tail.index <= report]
        if (tail.index.tz is not None or tail.index.hasnans or tail.index.has_duplicates
                or not tail.index.equals(tail.index.normalize())):
            raise TailUnavailable("Invalid/duplicate daily ^NDX reference dates")
        # Missing *new* closes always fail. Historical missing closes are not
        # imputed: the matching price continues to come exclusively from FRED.
        missing_values = tail.index[tail.isna()]
        if len(missing_values.difference(h.index)):
            raise TailUnavailable("Missing new ^NDX close outside the FRED anchor")
        tail = prefix(tail.dropna(), pd.Timestamp(report))
        window = sessions(calendar, h.index[-1] - pd.Timedelta(days=60), h.index[-1])[
            -(OVERLAP_SESSIONS + MAX_UNAVAILABLE_OVERLAP):]
        unavailable = window.difference(tail.index)
        overlap = window.intersection(tail.index)
        if len(unavailable) > MAX_UNAVAILABLE_OVERLAP or len(overlap) < OVERLAP_SESSIONS:
            raise TailUnavailable("Need at least 20 verified overlapping closes in the last 25 sessions")
    else:
        tail = prefix(tail, pd.Timestamp(report))
        overlap = sessions(calendar, h.index[-1] - pd.Timedelta(days=60), h.index[-1])[-OVERLAP_SESSIONS:]
        unavailable = pd.DatetimeIndex([])
    if (len(overlap) < OVERLAP_SESSIONS or len(overlap.difference(h.index))
            or len(overlap.difference(tail.index)) or len(added.difference(tail.index))):
        raise TailUnavailable("Need 20 overlapping closes and every missing tail session")
    # Compare EVERY available close in the window, not a cherry-picked subset.
    error = float(np.max(np.abs(h.loc[overlap].to_numpy() / tail.loc[overlap].to_numpy() - 1)))
    if not np.isfinite(error) or error > MAX_RELATIVE_ERROR:
        raise TailUnavailable(f"FRED/Yahoo ^NDX overlap mismatch: {error:.6%}")
    addition = tail.loc[added].copy()
    addition.index = addition.index.as_unit(h.index.unit)
    out = pd.concat([h, addition]).rename("NDX")
    detail = {"method": "FRED_anchor_append_only", "anchor_end": str(h.index[-1].date()),
              "overlap_sessions": len(overlap), "minimum_overlap_sessions": OVERLAP_SESSIONS,
              "max_relative_overlap_error": error,
              "validation_policy": "scoped_fred_anchor_v2" if allow_sparse_overlap else "strict_v1",
              "unavailable_yahoo_overlap_dates": [str(d.date()) for d in unavailable],
              "retained_fred_closes": {str(d.date()): float(h.loc[d]) for d in unavailable},
              "appended": {str(d.date()): float(tail.loc[d]) for d in added}}
    return out, detail


def fetch_tail(session, history, report, audit, calendar, now=None):
    h, _ = validate_anchor(history, report, calendar, now)
    finish = pd.Timestamp(report).tz_localize("America/New_York") + pd.Timedelta(days=1)
    params = {"period1": int((finish - pd.Timedelta(days=90)).timestamp()),
              "period2": int(finish.timestamp()), "interval": "1d",
              "events": "div,splits", "includePrePost": "false"}
    errors = []
    for url in URLS:
        try:
            r = session.get(url, params=params, timeout=(8, 25))
            r.raise_for_status()
            raw = r.json()
            tail = daily_ndx(raw, pd.Timestamp(report), calendar, now, anchor_end=h.index[-1])
            result, detail = append_tail(h, tail, report, calendar, now, allow_sparse_overlap=True)
            detail.update(source=url, request="verified_ndx_tail", status="ok",
                          retrieved_at=utc(now).isoformat(),
                          response_sha256=hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest())
            audit.append(detail)
            result.attrs["source"] = ("FRED NASDAQ100 through " + detail["anchor_end"]
                                       + "; append-only daily ^NDX from " + url)
            return result
        except (requests.RequestException, ValueError, KeyError, TypeError, IndexError) as exc:
            errors.append(str(exc))
            audit.append({"source": url, "request": "verified_ndx_tail", "error": str(exc)})
    raise TailUnavailable("Verified ^NDX fallback failed: " + "; ".join(errors))


def probe(output_dir):
    """Read-only live probe, also exercises fallback when FRED has already caught up."""
    import monitor
    import reliable_data as data
    from automation import latest_session
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    report = latest_session()
    record = {"status": "error", "report_date": str(report.date()), "attempts": [],
              "execution_authorized": False, "purpose": "read_only_fallback_probe"}
    code = 2
    try:
        with data.http_session() as session:
            r = session.get(monitor.FRED_NDX_URL, timeout=(8, 25))
            r.raise_for_status()
            original = data.fred_series(r.text).loc[:report]
            withheld = report in original.index
            anchor = original.loc[original.index < report] if withheld else original
            record["withheld_latest_fred_row_for_probe"] = withheld
            prices = fetch_tail(session, anchor, report, record["attempts"], monitor._calendar())
            data.validate_asof(prices, report, "NDX fallback probe")
            from qqq_recovery import get_qqq_reliable
            qqq = get_qqq_reliable(session, report, record["attempts"])
            monitor.validate_cross_source(qqq, prices, report)
            pd.testing.assert_series_equal(prices.loc[anchor.index], anchor.rename("NDX"), check_freq=False)
            if withheld:
                rel_error = abs(prices.loc[report] / original.loc[report] - 1)
                if rel_error > MAX_RELATIVE_ERROR:
                    raise TailUnavailable("Withheld FRED close disagrees with fallback")
                record["withheld_fred_close_relative_error"] = float(rel_error)
            pd.concat([qqq.rename("QQQ"), prices.rename("NDX")], axis=1).tail(300).to_csv(out / "closes.csv", index_label="date")
            record.update(status="ok", ndx_close=float(prices.loc[report]), source=prices.attrs["source"])
            code = 0
    except Exception as exc:
        record["error"] = str(exc)
    (out / "probe.json").write_text(json.dumps(record, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2))
    return code


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--probe-dir", required=True)
    raise SystemExit(probe(p.parse_args().probe_dir))
