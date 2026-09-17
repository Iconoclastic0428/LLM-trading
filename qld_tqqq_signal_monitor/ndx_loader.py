"""FRED-first NDX loading with a strictly validated, append-only fallback."""
import requests
from ndx_fallback import fetch_tail

def get_ndx_reliable(session, report, audit):
    """Preserve the existing FRED reader and its tests; fall back only on failure."""
    import monitor
    import reliable_data as data
    try:
        return data.get_ndx(session, report, audit)
    except data.DataUnavailable as primary_error:
        try:
            # The original reader deliberately returns no invalid series. Acquire
            # an anchor separately rather than weaken that reader's contract.
            r = session.get(monitor.FRED_NDX_URL, timeout=(8, 25))
            r.raise_for_status()
            history = data.fred_series(r.text).loc[:report]
            audit.append({"source": monitor.FRED_NDX_URL, "request": "fallback_anchor",
                          "latest": str(history.index[-1].date())})
            # FRED may have caught up during the preceding retries.
            try:
                result = data.validate_asof(history, report, "NDX")
            except data.DataUnavailable:
                result = fetch_tail(session, history, report, audit, monitor._calendar())
                return data.validate_asof(result, report, "NDX verified fallback")
            result.attrs["source"] = monitor.FRED_NDX_URL
            return result
        except (requests.RequestException, ValueError, KeyError, TypeError, IndexError,
                data.DataUnavailable) as fallback_error:
            audit.append({"request": "ndx_fallback", "error": str(fallback_error)})
            raise data.DataUnavailable(f"{primary_error}; fallback: {fallback_error}") from fallback_error

