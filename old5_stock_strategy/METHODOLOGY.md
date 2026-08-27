# Methodology and interpretation

## Frozen policy

The file in strategy/candidate_strategy.py is the frozen old five-stock policy. Its hash is checked by scripts/verify_package.py. The strategy ranks intermediate momentum and realized volatility cross-sectionally, applies a positive-momentum gate, retains qualifying incumbents through rank 10, and fills up to five 20% positions every five sessions.

## Real-500 replay

The packaged performance uses the audited point-in-time S&P 500 replay from January 3, 2000 through August 25, 2026. Membership is point-in-time. Signals use historical information available before the next-open execution. Costs are modeled at five basis points per unit of one-way L1 turnover.

The performance replay's holding path matches the frozen submission through August 25, 2026. The exact frozen submission's August 26 signal, effective at the August 27 open, is stored in signals/current_target_after_2026-08-26.json.

## Experimental roles

The annual directories preserve the original visibility labels:

- **Training fit:** visible anonymous fit data.
- **Validation check:** visible anonymous validation data.
- **Sealed test year:** one-shot hidden episode. The original sealed episode used a separate 60-stock universe; the annual real-500 number in this package is the continuous transfer replay for that calendar year.
- **Unrevealed retrospective holdout:** data that was outside the original visible fit/check panels and was evaluated later.
- **Forward period:** post-freeze data.

The role labels describe data visibility. They do not transform a retrospective result into a live track record.

## Data limitations

The stock panel uses split-neutral OHLC histories and reviewed supplements. Stock dividends are excluded, while SPY and QQQ benchmark series are adjusted. Taxes, borrow costs, and market impact beyond the stated turnover charge are outside this package. The 2026 performance render ends August 25; weekly holdings extend through August 26.
