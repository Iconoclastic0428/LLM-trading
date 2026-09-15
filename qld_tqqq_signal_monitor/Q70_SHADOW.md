# Q70 shadow deployment

Production remains the existing BASE complex monthly strategy. `monitor.py`,
`automation.py`, `exact_monitor.py`, `reliable_data.py` and `publish_status.py` are
unchanged. C20 and Q70 are separate hypothetical target comparisons, not trade
instructions. QLD and TQQQ represent alternative portfolios, not two weights to add.

## Frozen rules

At a completed month-end session t, H20 is the highest VIX close in the last 20
NDX sessions including t. p is its empirical midrank against the previous 252
session closes, excluding t. s is the sample standard deviation of log VIX
LEVELS over the previous 63 sessions, excluding t.

`C20 = max(0, 2*p - 1) * tanh(max(0, log(H20/VIX[t]) / s))`

`Q70 = C20 if p >= 0.70 else 0`

A zero scale yields zero amplitude as in R4. Incomplete/invalid history is
reported as unavailable rather than a valid zero signal. No missing VIX prices
are interpolated, forward-filled or silently dropped from the session grid.

Only a zero core trend score permits the overlay. For ETF leverage L:

`extra_weight = min(1, 0.60/(L*NDX_vol21), 0.5*amplitude/L)`

A positive core score leaves the original target exactly unchanged. The overlay
is never amplified, never rebalances intramonth, and never changes the core
volatility target. The 0.5 cap is index exposure, not a 50% ETF weight: QLD has at
most 25% overlay weight and TQQQ at most 16.667%, subject to the volatility cap.

## Operation

The existing `QLD TQQQ monthly signal` workflow still checks hourly at UTC minute
7 and only performs scheduled market-data work in the PT 18:00-06:59 window.
GitHub cron is best effort. No new exact-time execution guarantee is introduced.
The strategy still sets new targets only after a completed month-end, with the
next session open as the reference execution time.

After BASE publication and its audit upload finish, the shadow step reads that
run's `signal_output/status.json`, `month_end.json` and `verified_closes.csv`.
It verifies file hashes, live freshness, the calendar, core prices and the
snapshot schema. It does not recompute core EMAs from a shortened price history.
VIX comes from the Cboe daily CSV, with bounded FRED VIXCLS fallback. Source,
input hashes, rule version, commit and run ID are recorded.

The step writes only `shadow_output/shadow.json`, `report.md`, and `vix_used.csv`.
It never writes the production directory, publishes GitHub issues, connects to a
broker, or sends an order. `publish_notification` and `execution_authorized` are
always false. Production processing finishes before shadow processing begins.
Shadow failure is visible in its JSON, run summary and warning, but does not
invalidate or suppress BASE publication. A green overall production workflow
alone is NOT proof that shadow verification succeeded; check `shadow.json`.

The latest month-end target is explicitly historical, not a live account weight.
Between month-ends, the report continues to audit that target; it does not trade
again. Each run has a separate artifact named
`qld-tqqq-vix-shadow-<run_id>-<attempt>` retained for 90 days. Earlier runs are not
an immutable portfolio ledger: historical data can be revised. This deployment
records signals, not simulated fills, transaction costs, cash interest or NAV.
No post-deployment performance claim should be inferred from these reports.

## Disable and test

Set the repository Actions variable `VIX_SHADOW_ENABLED` to the string `false`
to disable the live overlay comparison without changing BASE. Removing the
variable or setting it to `true` enables shadow comparison. There is deliberately
no switch that promotes Q70 to production execution.

Local disabled check (does not access the network or require a baseline bundle):

```sh
VIX_SHADOW_ENABLED=false python qld_tqqq_signal_monitor/vix_shadow.py
```

Run tests:

```sh
python -m pytest qld_tqqq_signal_monitor/tests -q
python -m pytest qld_tqqq_signal_monitor/shadow_tests -q
```

The tests workflow also performs a separate read-only live-data smoke run. That
job has contents-read permission only and does not invoke the issue publisher.
Network failure is separately visible and is not counted as a successful smoke
test merely because the unit-test job passed.

## Validation and evidence boundary

Before upload, the extracted R4 frozen data through 2026-08-28 were compared with
this implementation. All 394 eligible complete-history month-end amplitudes and
BASE/C20/Q70 targets for both ETFs matched within 1.12e-16 absolute error. The
remaining 45 modern-VIX month-ends had insufficient/invalid lookbacks and are
reported unavailable by the live module. Five ordered historical windows are
included as regression fixtures. Synthetic tests cover future-data perturbation,
midranks, prior-only normalization, zero/positive core states, risk caps, missing
closes, source fallback, output isolation and disabling. Calendar integration
tests require the production modules and execute in GitHub CI.

This validates implementation parity, not future profitability. The R4 research
comparison did not establish a statistically confirmed replacement for BASE.
Q70's incremental historical gain over C20 is small and event-concentrated.
