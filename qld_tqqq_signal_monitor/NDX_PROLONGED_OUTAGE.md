# Prolonged NDX source outage, 2026-10-27

## Observed failure

Main run 40505044057 (Oct27 05:36 PDT) required Oct26. The FRED CSV
transport timed out on every attempt. The restored checkpoint covered Oct22,
but its original FRED observation was Oct15. PR16 correctly refused to renew
its five-session Yahoo-only allowance. The latest green run 40557872491 was
an ineligible time-window skip, not a successful new market-data check.

## Independent recovery contract

The FRED-first and bounded Yahoo-only routes are unchanged. A separate fallback
requires fresh Nasdaq NDX historical OHLC plus current Yahoo daily USD ^NDX
index bars. Official historical data may trail by at most ONE exchange session;
that exception is explicit, unlike the previous five-session Yahoo-only window.

1. Revalidate the old full-history checkpoint using its original byte hashes,
   validation contract, verification timestamp, complete recent session grid,
   and QQQ/NDX cross-check. Recognized FRED origin is mandatory and retained.
2. Extend no more than five exchange sessions from that verified checkpoint.
   The checkpoint verification must still be less than seven calendar days old.
3. Nasdaq response must identify NDX and the historical-table schema, contain
   complete OHLC for all issuer-published new sessions, and pass post-close
   HTTP-date/age checks. Its latest date must be report day or the immediately
   preceding exchange session. A present malformed current row is an error,
   not permission to discard it and retreat to yesterday.
4. Yahoo must identify a daily USD ^NDX index in New York time, attest a completed
   session, and contain complete new-session OHLC. Quotes are not substituted.
5. Each source must agree with at least 20 of the last 25 anchor-session closes.
   Both sources must agree on at least 20 shared prior closes and EVERY new
   issuer-covered close, maximum relative difference 0.01%. At most the current
   report session can use finalized Yahoo daily OHLC without issuer confirmation.
   Its date is saved in single_source_latest_dates and confirmation=false.
   Every available close is checked;
   disagreements are not discarded to obtain a passing subset.
6. Preserve every old close exactly. Append Yahoo daily precision only after
   Nasdaq confirms its history; one latest Yahoo session is explicitly labelled.
   Do not rescale history, repair interior
   gaps, shorten the EMA seed, or change the original FRED date.
7. Save both response byte hashes, prices, prior source metadata, dates and all
   validation results. Every subsequent extension needs new issuer evidence.
   If the issuer date stops advancing, the next report fails after one session.
   Revalidate previously provisional cached closes against new issuer records.
   Loss of either source is a failure, not permission to reuse yesterday's close. An observed identity/price conflict fails closed.

A live network capture on Oct27 obtained current Nasdaq historical rows while
FRED CSV, text, and Excel export requests all timed out. Exact replay against
the failed run's checkpoint preserves 10,283 old closes and appends Oct23 and
Oct26. It verifies 27 shared closes, with maximum source discrepancy about
0.00070232%, and the Oct26 Yahoo close 29273.400390625 against Nasdaq 29273.40.

## Verification

The original 348 tests remain; 48 added tests cover the prolonged outage,
actual loader routing, all appended dates, shared overlap, preserved history,
metadata/price conflicts, invalid OHLC, stale HTTP and checkpoint timestamps,
corrupt/hash-invalid caches, future-data exclusion and missing second-source
rejection. The local suite passes 396 tests. Exact captured-response replay
makes the old route fail and the new independent route pass.

The read-only CI probe additionally disables FRED and the short-outage route,
withholds the latest verified observation, invokes the actual NDX wrapper,
requires the new dual-source route, compares the withheld close and targets,
and proves cache alone or total provider loss cannot produce a new close.
The probe cannot publish issues, save production caches or place orders.

## Current-session follow-up

The first PR run recovered Oct26 but crossed the 20:30 UTC completed-session
boundary before shadow validation, which correctly rejected its now-old date.
A repeat for Oct27 exposed the issuer table's ordinary one-session publication
lag: Nasdaq ended Oct26; Yahoo had complete Oct27 OHLC and a post-close metadata
time. A separate read-only capture preserves these responses. The subsequent
contract permits only that single latest session from finalized Yahoo daily
bars. It never calls an undated/live Nasdaq quote a closing-price confirmation.
Exact replay preserves 10,283 prior rows, confirms Oct23/26 with both providers,
and appends Yahoo's Oct27 close 29651.44140625 with confirmation=false. Added
regressions prove a two-session issuer lag, a malformed current row, and a
later issuer correction to the cached provisional close all fail closed.
This is analogous to a freshly independently checked history plus a bounded
current daily tail, not an unlimited renewal of the original FRED allowance.

## Unchanged and unresolved

No signal formula, BASE/C20/Q70 weight, risk cap, publication deadline, schedule,
permissions or brokerage behavior changes. This is signal monitoring only.
Independent feeds can still disagree or lag. If a fresh dual check cannot be
completed the pipeline stops; it does not invent a value. Actions cache is
best-effort storage, and losing the full historical anchor during an extended
primary outage still prevents this recovery. No independent scheduler is added.
