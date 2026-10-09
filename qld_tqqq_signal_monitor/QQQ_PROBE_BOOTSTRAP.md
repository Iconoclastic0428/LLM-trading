# QQQ provider-probe bootstrap repair (2026-10-08 PT)

PR13/14 fixed the actual current-close failure and added same-session verified
full-history checkpoints. Main run 37867935913 passed twice, including the new
Closed/non-real-time exchange format and a real cross-run cache restore. The
remaining red smoke step was the legacy daily-table diagnostic: it called
`data.get_qqq(report)` before withholding that close, so it could not test a
primary outage until the primary had already recovered.

## Separate three claims

1. **Latest-session production health.** `exact_monitor.py` and `vix_shadow.py`
   must verify the latest completed session and the original monthly target.
   These remain required live steps. No report-date, stale-data, price-conflict,
   risk-budget or monthly execution check was relaxed.
2. **Current finalized-close/checkpoint recovery.** The existing forced probe
   hides today's daily closes and Nasdaq table row, requires actual validated
   closing evidence, then tests same-session offline checkpoint recovery and
   rejection of next-session use. It remains required and unchanged.
3. **Historical daily-table contract.** `historical_recovery_probe.py` obtains
   full Yahoo history without requiring today's primary close. It tests the
   latest common completed session, at most ONE exchange session behind the
   live report. It withholds exactly that close and later data, invokes the
   actual QQQ source wrapper, requires Nasdaq daily recovery, and checks all
   preserved historical prices and the independently withheld close.

The third item writes `live_report_date`, `tested_session`, lag, source hashes,
HTTP timestamps, `current_daily_path_status`, and the forced wrapper audit.
`status=ok` means that explicitly dated historical test passed. It NEVER means
that the current daily table is ready, or that the diagnostic verified the
current production pipeline. `live_pipeline_verified_by_this_probe`, execution
and publication authorization remain false. A current daily table missing only
the latest row produces an explicit warning. Malformed data, conflicting prices,
invalid HTTP dates, missing historical sessions, or more than one session of
lag are still failures; the diagnostic never retreats to older dates to hide
bad observations.

Recorded responses are copied, not changed in place. A later failed diagnostic
clears earlier success output. The existing `qqq_recovery.py --probe-dir` command
delegates to the repaired diagnostic for compatibility.

The live smoke job no longer has job-level `continue-on-error`. Real production,
recovery, or contract-check failures therefore remain failed workflow checks.
The current-provider warning is visible in both logs and the run summary.

## Verification scope

Local combined test suite: 323 passed, comprising 298 retained tests and 25 new
cases. Current/live, one-session-stale, malformed/contradictory, HTTP-time,
output reset, source immutability and workflow failure behavior are covered.
Cloud and post-merge verification are recorded in PR15, not presumed here.

Only diagnostics, tests and their workflow are changed. No strategy calculator,
production schedule, publisher, provider acceptance threshold, permission,
brokerage execution, MO/PM allocation, managed-futures allocation or options
module is changed. The temporary read-only source-snapshot workflow used for
reproduction is removed before merge.

Checkpoints are optional GitHub Actions caches, not a durable market database.
A previously verified date cannot satisfy a new session. GitHub cron is still
the only scheduler; this change does not establish an independent watchdog.
