# Candidate workspace

This directory is the complete visible workspace for one stock-strategy attempt.
Read `GOAL.md`, implement `strategy/candidate_strategy.py`, and run:

```powershell
python -m pytest -q
python run_visible.py
```

The data contains adjusted daily OHLCV for two episode-specific anonymized
individual-stock universes, each with at least 100 securities, plus one separate
market benchmark. Each universe is a point-in-time top-holdings snapshot fixed
immediately before that episode's holdout. The two independent visible episodes
cover older and recent regimes. Final intervals, actual symbols, dates, source
ranks, and transforms are outside this workspace and sealed without decryption
material.

Some constituents listed after an episode began. Their pre-listing OHLC values
are `NaN`, volume is zero, and `data.tradable` is false. Target weights must be
zero wherever `data.tradable` is false; rolling pandas features naturally remain
uninitialized until enough real history exists.

Only these submission files are intended to change:

- `strategy/candidate_strategy.py`
- `strategy/design.md`
- `strategy/self_assessment.md`
- `strategy/experiment_log.csv`

The engine returns complete target weights rather than pair-level entry signals.
That is the central migration required for a rebalanced stock portfolio and a
Robinhood adapter. The same equity path handles ETFs and individual stocks.
`benchmark/options.py` defines a separate whole-contract, defined-risk option
execution boundary; option positions are not part of this OHLCV benchmark.
