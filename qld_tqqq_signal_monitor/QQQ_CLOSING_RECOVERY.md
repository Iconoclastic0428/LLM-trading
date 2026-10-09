# QQQ finalized-close recovery and verified same-session checkpoint

## Incident

On October 8, 2026 PDT, scheduled run 37863713024 passed all 168 existing
production tests, then failed QQQ freshness checks. Both Yahoo hosts and multiple
query shapes returned a report-day bar with open/high/low/volume but a null
close. Nasdaq's daily historical table stopped at October 7. A retry alone could
not repair either missing daily-close publication.

Read-only source capture 37864949620 established a separate available record.
Nasdaq's QQQ info response explicitly labeled its secondaryData value $747.58 as
`Closed at Oct 8, 2026 4:00 PM ET`. Its primaryData value $747.7879 was a later
extended-session quote and is NOT the closing price. Yahoo's complete regular
5-minute session ended with an exact 16:00 marker of 747.5800170898438, consistent
with the exchange price within half a cent. No other quote or partial bar is used.

## New last-resort contract

The normal Yahoo daily-close path remains first choice. Nasdaq daily history is
still the first recovery path. Only when those fail does `qqq_tail.py` permit a
single missing final session to be appended, with ALL of these conditions:

- The original 500+ observation history and last 260 exchange sessions are
  complete. Every original historical close is preserved without scaling or
  reseeding the EMA. Only exactly one missing trailing session may be repaired.
- The calendar says the requested session ended at least 30 minutes ago.
- Nasdaq identifies QQQ/ETF/NASDAQ and explicitly labels an exact dated regular
  closing price `Closed at ... ET`. Its timestamp must equal the calendar close,
  including half-days and daylight-saving changes. Generic lastSalePrice,
  after-hours primaryData, stale dates and contradictory explicit closes fail.
- Yahoo identifies QQQ/USD/ETF/5m and supplies EVERY regular-session 5-minute bar,
  plus the exact closing marker. OHLCV, timestamps and volume must be complete
  and consistent. The marker and metadata must agree with the exchange price
  within $0.005. The last 15:55 bar is not treated as the exchange close.
- The finalized price is consistent with the original current daily open/high/
  low. An observed conflicting non-null daily close is an error, not overwritten.
- Twenty consecutive earlier Nasdaq historical closes agree with the preserved
  Yahoo history within the existing 0.01% tolerance. Conflicting published
  current historical data is also rejected. HTTP date/age must not predate close.

This is explicitly an additional validated finalized-close source, not a claim
that the delayed daily historical table was current. Raw response hashes, exact
selected field, close timestamp, matching marker and historical overlap are
recorded. Raw evidence is saved separately when SOURCE_EVIDENCE_DIR is set.
If these independent checks cannot be met, generation still fails closed.

## Full-history checkpoint across Actions runs

PRICE_CHECKPOINT_DIR is optional and unset in ordinary unit tests. Successful
source and cross-source checks stage the FULL QQQ/NDX histories with hashes,
original sources, report date, validation time, commit/run ID and code fingerprint.
No shortened 300-row report excerpt is used as an EMA seed.

Production restores/saves these files through separately pinned Actions cache
restore/save actions. Saves happen only after successful live generation, never
in a historical replay or the PR test workflow. Saving is optional and happens
after formal production publication. A failed cache service does not authorize
prices or prevent normal source-based generation.

If all current live attempts fail from missing/stale/unavailable data, a checkpoint
may be reused ONLY for the SAME requested report session, after file/shape/hash,
full-history date and cross-source checks. Original verification time and source
run remain explicit. It cannot turn yesterday's close into today's close, cannot
supply a missing future session, and cannot overrule an observed price conflict.
Rules changes invalidate older checkpoints. JSON/CSV only; no executable formats.
This is best-effort cached persistence with separate audit artifacts, not an
immutable market-data database or a guarantee of future cache availability.

## Verification and unchanged strategy

`closing_recovery_probe.py` is read-only. It deliberately removes today's QQQ
daily close from live Yahoo replies and today's row from Nasdaq daily history,
then exercises the actual load_prices entrypoint. It preserves the original
historical closes, validates the new finalized close, and stages a checkpoint.
A second simulated complete transport failure must restore that exact session
and reproduce the same model targets. Reuse for the next session must fail.
No issue publication, portfolio mutation or orders are performed by this probe.

The older standalone forced-Nasdaq-daily-table probe still checks its own source
and can fail when that table is not ready. That failure is not silently treated
as a successful fallback or a failure of a separately successful normal pipeline.

The strategy formulas, risk caps, month-end timing, publication deadline,
scheduler, dependencies and permission scope are unchanged. BASE stays formal;
C20/Q70 stay shadow-only. MO/PM, options and managed-futures research are not
promoted by this operational repair. An absent GitHub schedule event remains an
independent limitation; this change adds no external scheduler.
