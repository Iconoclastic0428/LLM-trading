# QQQ finalized-close metadata delay, 2026-10-30

## Captured failure
Main scheduled run 41151182932 passed 343 production tests but could not generate the October month-end signal. Both daily sources lacked the current close. Nasdaq explicitly reported `Closed at Oct 30, 2026 4:00 PM ET` at USD 690.49. Yahoo supplied all 78 regular 5-minute bars and its exact 16:00 zero-volume closing marker at 690.489990234375, but regularMarketTime was 16:00:01. Exact equality of that metadata clock to the exchange close rejected valid matching price evidence.

Captured Yahoo response SHA256: d7af00b0cd032b739289d1f98f28733e1e7fa053fa2cec3f17ec2a473aef894e. Nasdaq response SHA256: cd344bcdf8ef7ab7e4af51be2cd872b31dc6606561cd67494d0ae07fc20c7bdf. Identical captured responses reproduce the old rejection and pass the corrected closing-evidence parser. Full production recovery and publication must additionally pass current live validation; parser replay alone is not publication success.

## Narrow correction
Allow regular-market metadata at the close or at most five seconds later. A nonzero delay additionally requires currentTradingPeriod.regular.start and end to match the exchange calendar exactly. Pre-close, old-session, non-integer, and later timestamps remain invalid. This does not move the price marker or authorize an after-hours price.

The complete regular-session grid, exact exchange-close marker, zero marker volume, OHLC integrity, metadata/marker price agreement, independent dated Nasdaq final close, daily OHL evidence, 20 consecutive historical overlap closes, HTTP freshness, and 30-minute post-close completion lag remain mandatory. Any observed price conflict fails closed. The selected metadata time, lag, and exact price-marker timestamp are added to the audit.

## Verification and boundaries
Local full suite: 429 passed, retaining all previous 396 cases and adding 33. New tests include one-second replay, both window boundaries, old/future/pre-close times, malformed session metadata, missing/shifted markers, price conflicts, half-days, daylight-saving changes and the completion lag.

Only the closing-evidence parser, its tests, and this note change. No strategy formulas, signal frequency, NDX source rules, scheduling windows, publication deadline, deduplication logic, permissions or brokerage behavior change. A fresh run on the merged commit is required; re-running an older workflow continues to use that older commit. Publication status and live checks will be recorded in the pull request.
