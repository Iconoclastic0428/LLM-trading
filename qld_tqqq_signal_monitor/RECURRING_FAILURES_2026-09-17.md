# Recurring operational failures: September 17, 2026 PT

## Findings supported by artifacts, not just green run badges

Main scheduled run 35280011040 verified the September 17 session at 15:03:28 PDT.
Its BASE artifact 10522476145 reports QQQ 716.9199829101562 and NDX 29446.98046875.
NDX used the previously deployed FRED-to-Yahoo append-only fallback.

Two hours later, main scheduled run 35289594665 (17:04 PDT) passed 96 tests but
failed before reaching NDX. Artifact 10525398137 shows both Yahoo QQQ hosts and
both bounded full/recent requests returning September 16 across three attempts.
This is a non-monotonic provider response/freshness failure, not evidence that
September 17 data had never become available. Cache/backend inconsistency is a
plausible explanation, but the artifacts do not reveal the provider's internals.

At about 19:47 PDT, the runs API returned zero scheduled runs created since
17:30 PDT. The six visible scheduled runs that day before inspection were at
02:55, 07:50, 11:55, 15:03, and 17:04 PDT (five on September 17 itself), with the
prior run at September 16 21:41 PDT. This does not match the requested :07/:37
half-hour cadence. A manual/push recovery is not evidence of cron reliability.

Read-only diagnostic run 35300793938 at 19:48 PDT obtained September 17 QQQ data
from all five Yahoo request variants and the Nasdaq historical table. This
shows sources were available during that probe, not that they were available
at the earlier failure time. The archived recent-runs metadata supports the
trigger-gap investigation independently of a latest-run badge.

## Changes

QQQ remains Yahoo-first. After primary failure, qqq_recovery.py obtains the
original price-only historical anchor and appends only missing tail closes
from Nasdaq's QQQ historical OHLC endpoint. It requires 500 history observations,
a complete recent 260-session grid, only 1-5 missing trailing sessions, and 20
consecutive overlapping closes agreeing within 0.01%. Duplicate, non-session,
invalid OHLC, stale and obviously pre-close cached responses are rejected.
Every original historical anchor value remains unchanged. A missing anchor or
both unavailable sources still blocks generation. This is not a whole-provider
replacement or a way to use yesterday's price under today's date.

Local verification against the captured actual responses, withholding only the
September 17 Yahoo row, appended Nasdaq close 716.92. Maximum 20-session overlap
relative error was 4.157236210833304e-08; the withheld-row relative error was
2.3837867679787905e-08. Core product targets at August 31 and September 17 were
unchanged in this check. This checks these observations, not every possible
future corporate action or a provider service-level agreement.

The bot heartbeat now carries a strictly parsed last-verified checkpoint.
Later failures remain failed and cannot create signals, but the display retains
the last successful session, verification timestamp and run. This checkpoint is
REPORTING ONLY: it never supplies prices, targets, or permission to trade. Replay,
untrusted comments, malformed and future checkpoints cannot advance it.

A read-only live CI probe withholds the last Yahoo row and deliberately makes
all QQQ primary replies stale within that probe. It then invokes the actual
load_prices entrypoint to require real Nasdaq recovery and the existing NDX
loader/cross-source validation. No production feed or account is mutated.
The unmodified primary path is exercised separately by the normal smoke run.

Local combined suite: 187 tests passed (134 production/data/health and 53 shadow).
Cloud test and live probe results are recorded in PR #10 when actually observed.
Existing monthly timing, trend/VIX formulas, limits, deadline and publication
deduplication are unchanged. C20/Q70 remain shadow-only. No orders are placed.

## Remaining reliability gaps

The scheduler is still GitHub cron, with no independent dispatcher. The code
cannot make an absent workflow execute. Adding more minutes to the same cron
has not demonstrated reliable delivery. An independent trigger and deadline
monitor are the next infrastructure change; none was silently provisioned here.

No durable full-history market cache is restored across workflow runners. The
new last-verified checkpoint prevents status loss, not all provider outages.
The QQQ fallback still requires a valid Yahoo history anchor. Total provider
outages remain explicit failures. Public website endpoints have no contracted
freshness/availability guarantee established by this work.

An overall workflow success, especially a skipped daytime run or a smoke job
with continue-on-error, is not sufficient health evidence. Review report_date,
expected_session, status, shadow.json, the actual step outcome and run trigger.
A current monthly signal_date in the prior month is normal between month-ends.
