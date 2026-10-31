# QQQ finalized-close metadata delay, 2026-10-30

## Captured failure
Main scheduled run 41151182932 passed 343 production tests but could not generate the October month-end signal. Both daily sources lacked the current close. Nasdaq explicitly reported `Closed at Oct 30, 2026 4:00 PM ET` at USD 690.49. Yahoo supplied all 78 regular 5-minute bars and its exact 16:00 zero-volume closing marker at 690.489990234375, but regularMarketTime was 16:00:01. Exact equality of that metadata clock to the exchange close rejected valid matching price evidence.

Captured Yahoo response SHA256: d7af00b0cd032b739289d1f98f28733e1e7fa053fa2cec3f17ec2a473aef894e. Nasdaq response SHA256: cd344bcdf8ef7ab7e4af51be2cd872b31dc6606561cd67494d0ae07fc20c7bdf. Identical captured responses reproduce the old rejection and pass the corrected closing-evidence parser. A later full public-source capture also reproduces old failure and new complete closing recovery while preserving all 6,954 prior QQQ rows. Production recovery/publication must additionally pass current live validation.

## Narrow correction
Allow regular-market metadata at the close or at most five seconds later. A nonzero delay additionally requires currentTradingPeriod.regular.start and end to match the exchange calendar exactly. Pre-close, old-session, non-integer, and later timestamps remain invalid. This does not move the price marker or authorize an after-hours price.

The complete regular-session grid, exact exchange-close marker, zero marker volume, OHLC integrity, metadata/marker price agreement, independent dated Nasdaq final close, daily OHL evidence, 20 consecutive historical overlap closes, HTTP freshness, and 30-minute post-close completion lag remain mandatory. Any observed price conflict fails closed. The metadata time, lag, and exact price-marker timestamp are audited separately.

## Reviewed checkpoint compatibility
Changing qqq_tail changes the existing checkpoint contract fingerprint and would reject the only available NDX historical seed during the ongoing FRED outage. checkpoint_contract_compat pins exactly one old-to-new complete rules fingerprint pair. The predecessor's stricter redundant metadata check admitted a subset of the new rule. No other old or future contract is accepted. All price bytes/hashes, schema, session grid, date, original verification age and cross-source checks run unchanged. The original manifest is not rewritten or relabelled. A restored old session is still NOT a new market observation; fresh report-day data is required to advance. Restore audits explicitly record both contract fingerprints. Any subsequent change to a hashed rule file disables this transition unless separately reviewed.

The actual Oct29 checkpoint was revalidated locally under this exact transition: 6,954 QQQ and 10,288 NDX rows, unchanged file bytes and verification time. Requesting Oct30 from that cache alone remains rejected.

## Verification and boundaries
Local full suite: 438 passed, retaining all previous 396 cases and adding 42. Tests cover both timestamp-window boundaries, old/future/pre-close times, malformed session metadata, missing/shifted markers, conflicts, half-days, DST, completion lag, cache corruption, wrong dates, old verification time, and unrelated future code changes.

Strategy formulas, signal frequency, NDX source rules, scheduling windows, publication deadline, deduplication logic, permissions and brokerage behavior remain unchanged. A fresh run on the merged commit is required; rerunning an older workflow still uses that older commit. Final live verification and publication status are recorded in the pull request.
