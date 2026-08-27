from __future__ import annotations

import numpy as np
import pandas as pd

from benchmark.data import MarketData


class CandidateStrategy:
    """Cross-sectional intermediate-momentum policy with a market brake.

    Every feature is a ratio or a return.  Cross-sectional percentile ranks
    make the two selection inputs comparable without using asset identities or
    nominal price levels.  Ties use ``method="max"`` at the selection boundary:
    a tied group that would cross the five-position limit is omitted rather
    than resolved with column order.
    """

    MOMENTUM_LOOKBACK = 63
    RECENT_SKIP = 21
    VOLATILITY_LOOKBACK = 126
    VOLATILITY_TILT = 0.15
    MARKET_TREND_LOOKBACK = 252
    DEFENSIVE_EXPOSURE = 0.50
    POSITION_COUNT = 5
    POSITION_WEIGHT = 0.20
    RETAIN_THROUGH_RANK = 10
    REBALANCE_INTERVAL = 5

    def generate_target_weights(self, data: MarketData) -> pd.DataFrame:
        close = data.close.astype(float)

        # Intermediate momentum deliberately omits the most recent 21 bars,
        # where short-horizon reversal is liable to contaminate the signal.
        momentum = np.log(
            close.shift(self.RECENT_SKIP) / close.shift(self.MOMENTUM_LOOKBACK)
        )
        log_return = np.log(close / close.shift(1))
        realized_volatility = log_return.rolling(
            self.VOLATILITY_LOOKBACK,
            min_periods=self.VOLATILITY_LOOKBACK,
        ).std()

        momentum_rank = momentum.rank(axis=1, pct=True, method="average")
        volatility_rank = realized_volatility.rank(
            axis=1, pct=True, method="average"
        )
        score = momentum_rank + self.VOLATILITY_TILT * volatility_rank

        # Positive absolute momentum keeps the policy from buying the least
        # weak names when the whole cross-section is declining.
        eligible = (
            data.tradable
            & momentum.gt(0.0)
            & realized_volatility.notna()
            & score.notna()
        )
        selection_rank = score.where(eligible).rank(
            axis=1,
            ascending=False,
            method="max",
        )

        # The anonymous broad benchmark supplies only a regime control.  The
        # sleeve remains half invested in a nonpositive long trend, avoiding a
        # brittle all-in/all-out threshold while retaining meaningful defense.
        benchmark_momentum = np.log(
            data.benchmark_close.astype(float)
            / data.benchmark_close.astype(float).shift(self.MARKET_TREND_LOOKBACK)
        )
        exposure = pd.Series(
            self.DEFENSIVE_EXPOSURE,
            index=data.index,
            dtype=float,
        )
        exposure.loc[benchmark_momentum.gt(0.0)] = 1.0

        # A rank buffer avoids replacing an incumbent for an immaterial score
        # crossing.  State changes only on the same five-bar grid used by the
        # simulator.  Candidate ranks also use ``max`` so a boundary tie can
        # leave a slot empty but can never breach the position limit.
        selected = pd.Series(False, index=data.asset_ids, dtype=bool)
        selection_history = pd.DataFrame(
            False,
            index=data.index,
            columns=data.asset_ids,
            dtype=bool,
        )
        for position in range(len(data.index)):
            if position % self.REBALANCE_INTERVAL == 0:
                row_eligible = eligible.iloc[position]
                retained = (
                    selected
                    & row_eligible
                    & selection_rank.iloc[position].le(self.RETAIN_THROUGH_RANK)
                )
                vacancies = self.POSITION_COUNT - int(retained.sum())
                candidates = row_eligible & ~retained
                candidate_rank = score.iloc[position].where(candidates).rank(
                    ascending=False,
                    method="max",
                )
                entrants = candidates & candidate_rank.le(vacancies)
                selected = retained | entrants
            selection_history.iloc[position] = selected & data.tradable.iloc[position]

        weights = selection_history.astype(float).mul(
            self.POSITION_WEIGHT * exposure, axis=0
        )
        return weights.reindex(index=data.index, columns=data.asset_ids).fillna(0.0)
