# Closed-market source transition and missing-evidence classification


Production run 37866960362 attempt 1 succeeded and saved its full checkpoint.
Attempt 2 actually restored that Actions cache, but failed when Nasdaq changed
its response after extended trading: marketStatus became Closed, secondaryData
became null, and primaryData became the non-real-time dated October 8 closing
record of $747.58. The old parser's combined "Missing or conflicting" error also
incorrectly caused a missing-label failure to be treated as an observed conflict.

The follow-up separates unavailable final evidence from conflicting prices.
It also recognizes this captured Closed/non-real-time/exact-date primary format,
only when secondaryData is null. This format does not independently attest a
16:00 timestamp. The full Yahoo regular-session grid, exact close marker, half-cent
agreement, daily OHL and 20 historical overlap checks are STILL required before
importing any price. Open/Pre-Market/After-Hours or real-time date-only primary
prices are rejected. All true conflicts still prohibit checkpoint fallback.

The failed repeat and its exact raw response are retained as regression evidence.
The code fingerprint naturally invalidates earlier-contract checkpoints; they
are not silently relabeled as current. No strategy formula or permission changes.
