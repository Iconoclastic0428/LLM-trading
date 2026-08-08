# Design

## Research protocol

Research used only the two local continuous episodes and their declared split
boundaries. The episodes were never joined. I first measured all-cash and the
engine's equal-weight-universe comparator, then screened economically distinct
families: absolute and multi-horizon momentum, beta-residual momentum,
volatility-adjusted momentum, low volatility, short-term reversal, distance
from a trailing high, intraday/overnight persistence, and simple rank blends.

There are **78 materially different rows** in `experiment_log.csv`: 62
baseline/signal configurations, four market-exposure variants, and 12
falsification variants covering rebalance phase, friction, and scored starting
bar. The broad screen was intentionally small; after residual momentum won, the
remaining search was a local stability study rather than a larger factor
lottery.

For walk-forward falsification I divided the usable history into 12 contiguous
roughly 252-session blocks: seven TRAIN blocks after feature warmup, three
VALIDATION blocks, and two RECENT_VALIDATION blocks. Features for each block
were initialized only by its episode's earlier bars. The frozen signal had:

- median block annualized selection alpha: **0.1857**;
- 20th-percentile block alpha: **0.0150**;
- positive alpha in **10 of 12** blocks;
- worst block alpha: **-0.0240**.

Every candidate first had to exceed 60% active-session coverage on both visible
validations. Among eligible candidates I favored the lower of VALIDATION and
RECENT_VALIDATION selection-sleeve alpha, treating differences of about two
annualized percentage points as estimation noise and then preferring lower
turnover, shallower drawdown, and a broad favorable parameter neighborhood.
This rejected isolated concentration optima and selected the central 140--154
session residual-momentum region. The final 147-session point was not the
highest single validation observation.

## Signal

At close of bar `t`, for each stock `i`:

1. Estimate trailing beta from 126 close-to-close returns:
   `beta_i = cov(r_i, r_market) / var(r_market)`, clipped to `[-1, 3]`.
2. Measure 147-session momentum while skipping the latest five sessions:
   `mom_i = close_i[t-5] / close_i[t-147] - 1`.
3. Remove the same-horizon benchmark component:
   `score_i = mom_i - beta_i * mom_market`.
4. Rank eligible stocks cross-sectionally from highest to lowest score.

The subtraction targets persistent stock-specific strength rather than merely
selecting high-beta names after a broad market rally. All inputs are trailing
returns or ratios, so the calculation is price-scale invariant. No expanding
or full-sample normalization is used.

## Portfolio construction and turnover

The portfolio holds up to ten names at 10% each before the exposure multiplier.
Membership is reconsidered on the engine's five-session decision schedule. A
current holding remains while its score rank is 15 or better; vacated slots are
filled from the best-ranked eligible non-holdings. This rank buffer reduced
visible annualized one-way turnover from roughly 19--20 for the unbuffered
signal to **9.41** on VALIDATION and **11.17** on RECENT_VALIDATION, while
improving the lower validation selection alpha.

Exact boundary ties are handled without an asset-label tie-break: a boundary
tie is included as a group only when the group fits, and otherwise is omitted.
Every selected name receives a constant 10% base weight, so even a partially
filled row respects the 20% cap. Non-tradable cells are always zero. Warmup rows
remain cash.

## Market exposure

Selection and market timing are deliberately separate. The selected sleeve is
fully invested when the benchmark close is at or above its trailing 200-session
mean and is multiplied by **0.70** otherwise. Before the mean initializes, the
same 0.70 floor applies. Thus a populated portfolio always remains active and
never uses an all-cash market-timing state.

On visible validations, this throttle kept candidate active coverage at 100%,
reduced maximum drawdown relative to the unthrottled selected sleeve, and left
the engine's fully invested selection test unchanged. Candidate average gross
exposure was 0.963 on VALIDATION and 0.977 on RECENT_VALIDATION.

## Frozen visible results

All figures include the locked five-basis-point one-way friction.

| Split | Candidate alpha | Candidate CAGR | Candidate max DD | Selection alpha | Selection turnover | Selection coverage |
|---|---:|---:|---:|---:|---:|---:|
| TRAIN | 0.1558 | 0.2808 | 0.1699 | 0.1606 | 9.29 | 0.934 |
| VALIDATION | 0.2334 | 0.5469 | 0.2066 | 0.2289 | 9.41 | 1.000 |
| RECENT_VALIDATION | 0.2446 | 0.6045 | 0.2122 | 0.2364 | 11.17 | 1.000 |

The TRAIN coverage shortfall is solely the initial 150-session signal warmup;
both model-selection periods are fully active with no cash run.

## Robustness checks

- Lookbacks 140, 147, and 154 with the same buffer all produced positive alpha
  on both validations; their lower validation alphas were 0.1745, 0.2289, and
  0.2197.
- Five- and ten-session skips remained favorable across the local region.
- All four alternate five-session rebalance phases retained positive lower
  validation alpha; the weakest was 0.1626.
- Raising one-way friction from 5 bps to 10, 15, and 25 bps left lower visible
  validation selection alpha at 0.2242, 0.2195, and 0.2101.
- Delaying both scored starts by 5, 21, 42, or 63 sessions left lower validation
  alpha between 0.2191 and 0.2400.
- The engine's determinism, truncation-causality, independent price-rescaling,
  and asset-relabeling audits pass on both episodes.

The strategy is now frozen for one sealed evaluation; no hidden-outcome
adaptation is part of the design.
