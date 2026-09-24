# NDX source recovery

FRED NASDAQ100 remains the complete historical anchor. Only the missing one to
five trailing sessions may come from validated Yahoo daily ^NDX price-index bars.
The strategy, risk limits, monthly timing, publisher and Q70 shadow boundary are
unchanged. No proxy, interpolation, rescaling or wholesale source replacement.

## September 24, 2026 regression

Scheduled run 36068761560 at 15:40 PDT passed 137 tests but failed on the NDX
fallback. FRED ended September 23; Yahoo had valid September 24 OHLC but returned
all-null OHLC for September 22, which FRED already supplied as 30732.40.

Read-only diagnostics run 36070251013 captured both Yahoo hosts with that same
null historical row. Its archive SHA256 is
725473624f4a0b84cbf90796c83e993fd3c54888d2a7310a4b580fd5038a6147.
Replaying those exact bytes locally reproduces the old all-OHLC rejection.
The old 90-day response validator incorrectly made importing a new daily bar
contingent on redundant historical OHLC that never enters the strategy.

## Revised validation contract

1. Validate the FRED anchor first: at least 500 observations, complete recent
   260-session grid, no interior repair, and at most five missing tail sessions.
2. Validate exact ^NDX / INDEX / USD / America/New_York / 1d identity and the
   provider timestamp. The report must be at least 30 minutes past session close.
3. Every new tail session must have finite, positive, internally consistent OHLC.
   Missing new dates or fields still fail. Metadata quotes are never used as closes.
4. Historical Yahoo rows only cross-check the FRED closes. Within the last 25
   anchor sessions, require at least 20 non-missing comparison closes and no more
   than five unavailable dates. Compare ALL available closes, not a selected
   best-matching subset; the maximum relative error remains 0.01% (one basis point).
   A present but invalid or disagreeing reference close still fails. Null or absent
   reference dates are individually recorded along with the retained FRED values.
   Their Yahoo OHLC is neither repaired nor imported.
5. Append only missing new closes. Preserve all FRED values, dates and datetime
   storage resolution exactly. Run the original final date/grid and QQQ/NDX
   cross-checks before calculating any target.

This deliberately changes the reference-overlap rule from 20 consecutive complete
Yahoo rows to at least 20 matching closes within a bounded 25-session window.
It does NOT loosen completeness for the final strategy input or for any newly
imported daily bar. With insufficient overlap or bad new data, generation fails.
Direct daily_ndx calls without an anchor and append_tail calls without the scoped
option retain their earlier strict behavior; production opts in only after a valid
FRED anchor has been established.

The exact September 24 replay compares 24 available sessions (one null reference),
with maximum relative difference 3.2525474158440204e-07, and appends only September
24 close 30478.85546875. All 10,262 FRED observations remain identical. An ordinary
future run can still prefer FRED when it has caught up.

## Verification and diagnostics

31 additional cases cover the historical-null incident, missing historical OHLC,
one/five/six unavailable references, mismatching non-null closes, every new OHLC
field, missing new timestamps, duplicates, future-data perturbation, exact anchor
preservation and the actual load_prices entrypoint. The local combined suite is
221 passing tests (all existing tests retained). Cloud CI and live probe results
are recorded in PR #11 rather than assumed from local execution.

provider_diagnostics.py now captures exact bad OHLC dates and fields for both
Yahoo NDX hosts and the FRED anchor. It is read-only. The existing live NDX probe
uses the normal QQQ recovery wrapper, rather than accidentally bypassing the QQQ
fallback while testing NDX.

## Remaining operational limits

This repairs the reproduced validation defect, not all provider or scheduler
outages. GitHub cron remains best effort. There is still no independently scheduled
failover or restored durable full-history market cache. A successful push run does
not guarantee later schedule delivery. Data errors must remain explicit, and no
stale daily value can be relabeled as the requested close.

## Earlier incident

On September 16, run 35163838933 exposed FRED's publication lag. PR #9 introduced
the same-index append-only fallback. PR #10 independently addressed stale QQQ
responses. This revision keeps those source priorities and final input checks;
it corrects an overly broad Yahoo validation dependency in the NDX fallback.
