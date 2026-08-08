# Task: build a causal stock-selection strategy with durable alpha

Implement `CandidateStrategy.generate_target_weights()` in
`strategy/candidate_strategy.py`.

You receive two episode-specific point-in-time universes of at least 100 liquid
individual U.S. equities, a separate broad-market benchmark, and adjusted daily
OHLCV. Each universe is fixed immediately before its holdout, so the same
strategy must generalize across different constituent panels. Instrument labels,
source ranks, price and volume scales, dates, and both holdout intervals are
anonymized. The executable target is a long-equity Robinhood portfolio; the
research engine itself is broker-neutral.

## What changed after round 1

Round 1 stayed in cash for most of the stress year and still produced negative
stock-selection alpha. This round scores **selection** and **market exposure**
separately:

- `candidate` is the portfolio you actually requested, including cash timing.
- `fully_invested_selection_sleeve` rescales every nonzero target row to 100%
  gross exposure. It shows whether the chosen names were better than merely
  avoiding the market.
- `selection_sleeve_active_sessions` measures the same sleeve on sessions when
  a selection existed.
- active-session fraction and longest cash run are reported for every split.

A low-beta or mostly-cash result is useful risk control, but it is not positive
selection alpha. Prefer a strategy that remains economically engaged through
most regimes and whose fully invested sleeve is stable across visible periods.

## Visible episodes

The workspace contains two continuous episodes:

1. `pre_stress`: TRAIN followed by VALIDATION.
2. `recent`: RECENT_WARMUP followed by RECENT_VALIDATION.

`run_visible.py` scores TRAIN, VALIDATION, and RECENT_VALIDATION. The warmup
split exists only to initialize trailing features. Do not join the two episodes:
an isolated market interval lies between them.

An asset can be absent before its listing date or temporarily unavailable. Use
`data.tradable` as the eligibility mask and assign zero target weight wherever it
is false. Pre-listing OHLC is `NaN` and volume is zero; do not backward-fill it.

After the submission hash is frozen, the owner runs exactly one authenticated
evaluation on two sealed intervals. One is a diagnostic stress regime and the
other is the confirmatory recent holdout.

## Research process and compute budget

Use the longer budget for better reasoning and falsification rather than a much
larger parameter lottery. A good process is:

1. establish all-cash, equal-weight, and simple momentum baselines;
2. compare at least three economically distinct, simple families;
3. use rolling or expanding walk-forward folds inside visible data;
4. select by median/low-percentile alpha and stability, not the single best
   validation point;
5. perturb lookbacks, rebalance phase, costs, and starting dates;
6. freeze one strategy and document rejected families.

The later implementation task is intended to receive up to three hours of wall
clock time with maximum model reasoning. Stop searching when the effective
sample no longer supports the number of trials. Record every tried configuration
in `strategy/experiment_log.csv` and summarize the search count in `design.md`.

For model selection, first require at least 60% active-session coverage on both
visible validation periods. Then favor the lower of their selection-sleeve
annualized alpha values, with turnover and drawdown as tie-breakers. Treat this
as a disciplined selection rule, not a promise of future returns.

## Locked portfolio rules

- long-only weights in `[0, 0.20]`
- total risky weight at most `1.0`; unallocated weight is cash
- at most 10 nonzero positions
- no short stock, leverage, or option positions in the scored strategy
- daily bars; the engine rebalances every five sessions
- a decision at bar `t` is eligible for the next regular-session open
- zero commission plus 5 bps one-way modeled execution friction
- trailing information only: no centered windows, negative shifts, backward
  fills, future indexing, or full-sample normalization
- price-scale invariant: returns and ratios rather than nominal price thresholds
- asset-label invariant: no special case for an `ASSET_XXX` label
- deterministic: fix every random seed

Individual stocks and ETFs share this target-weight interface. An options
gateway exists for a later, separately tested defined-risk overlay; option chain
data is absent from this alpha exam, so options are outside the scored strategy.

## Interface

```python
class CandidateStrategy:
    def generate_target_weights(self, data: MarketData) -> pd.DataFrame:
        ...
```

The returned frame must have `data.index` and `data.asset_ids`. Rolling warmup
rows may be `NaN` and are treated as cash. The strategy may use only:

- `data.open`, `data.high`, `data.low`, `data.close`, `data.volume`
- `data.tradable`
- `data.benchmark_open`, `data.benchmark_close`

The evaluator checks full-vs-truncated causality, determinism, independent price
rescaling, and asset relabeling on both episodes.

## Deliverables

1. working strategy code
2. `strategy/design.md`: families tested, signals, exposure logic, turnover,
   parameters, walk-forward selection rule, and experiment count
3. `strategy/self_assessment.md`: weaknesses and expected failure regimes
4. `strategy/experiment_log.csv`: one row per materially different trial
5. passing tests and visible results from `python run_visible.py`

Do not tune against inferred dates, labels, or missing intervals. The final code
hash is frozen before either sealed interval is opened.
