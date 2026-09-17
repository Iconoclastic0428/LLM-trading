# September 16, 2026: NDX close publication lag

## Incident

Scheduled main run 35163838933 began at 16:47 PDT on September 16. Eligibility
passed and all 58 pre-existing regression tests passed. Generation failed because
QQQ had September 16 data while FRED NASDAQ100's canonical, bounded and recent
exports all ended September 15. Three complete retries failed. The publisher
returned exit 2 because it recorded a data error, not because a second publication
API failure had been established. C20/Q70 correctly did not run without verified
core data. Node/upload-artifact deprecation warnings were not the failing step.

This differs from the prior scheduling incident. Moving checks earlier exposed a
single-provider publication dependency. Neither a longer polling window nor more
requests can manufacture an unpublished FRED close. We retain strict freshness
and introduce a verified same-index tail fallback instead of hiding the error.

## Data policy

`reliable_data.load_prices` now calls `ndx_loader.get_ndx_reliable`. The original
FRED reader and all its validation remain unchanged and run first. When they
cannot supply the requested session, the wrapper acquires a valid FRED historical
anchor. If FRED has meanwhile caught up, it is used without contacting Yahoo.
Otherwise `ndx_fallback` may append Yahoo Finance's daily **^NDX price index** bars.

Requirements for fallback:

- Valid FRED anchor with at least 500 observations and a complete recent 260-session
  grid. Missing interior history is not repaired using the fallback.
- Exactly one to five missing tail sessions, with all required tail dates present.
- Correct ^NDX / INDEX / USD / America/New_York / 1d metadata and valid OHLC bars.
- The report session must have closed at least 30 minutes earlier, and the
  provider's timestamp must be at or after that close. Same-date cached morning
  bars are rejected. `regularMarketPrice` is never used to fill a daily close.
- The previous 20 consecutive exchange-session closes must exist in both
  sources. Maximum relative difference must not exceed one basis point.
- All original FRED observations are preserved exactly. No QQQ substitution,
  extrapolation, forward fill, index rescaling or wholesale provider replacement.
- The resulting series must still pass the original complete-session validation
  and QQQ/NDX trailing-return cross-check before any strategy calculation.

The audit records source, FRED anchor end, overlap error, each appended date/value
and a response hash. Future runs prefer FRED again when it is complete. Providers
can revise history, so archived inputs remain essential. The added Yahoo endpoint
is an operational fallback, not a guarantee of independently verified official
same-day settlement. Both Yahoo hosts share a provider. Any failed validation
still stops signal generation; no previous close is relabeled as current.

## Verification

New tests include delayed FRED, append-only parity, incomplete bars, cached intraday
metadata, wrong instruments, insufficient overlap, gaps, excessive staleness and
failure of both fallback hosts. Original production and shadow suites are retained.

The read-only CI smoke job exercises both the real pipeline and an explicit
fallback probe. When FRED is current, that probe deliberately withholds its last
row, reconstructs it using the fallback and checks agreement. The probe is not a
production price override and never publishes a notification or places an order.
Its JSON identifies whether a FRED row was deliberately withheld.

The schedule, monthly timing, strategy formulas, risk caps, next-open cutoff,
publication deduplication, and Q70 shadow-only status are unchanged. No dependency
version, API key or broker connection is added.
