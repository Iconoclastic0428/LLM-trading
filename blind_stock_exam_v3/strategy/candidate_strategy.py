from __future__ import annotations

import numpy as np
import pandas as pd

from benchmark.data import MarketData


class CandidateStrategy:
    """Beta-residual momentum with a rank buffer and a modest trend throttle."""

    MOMENTUM_LOOKBACK = 147
    MOMENTUM_SKIP = 5
    BETA_LOOKBACK = 126
    POSITION_COUNT = 10
    RETENTION_RANK = 15
    REBALANCE_BARS = 5
    MARKET_TREND_LOOKBACK = 200
    DEFENSIVE_EXPOSURE = 0.70

    @staticmethod
    def _fill_open_slots(
        eligible: np.ndarray,
        ranks: np.ndarray,
        retained: np.ndarray,
        slots: int,
    ) -> np.ndarray:
        """Select the best ranks without using a label-dependent tie break.

        Exact ties at the last open slot are admitted together when they fit.
        Otherwise the entire boundary tie is omitted. This preserves the
        position cap and makes relabeling irrelevant, at the small cost of a
        partially filled portfolio on a pathological tied row.
        """

        additions = np.zeros_like(retained)
        candidates = eligible & ~retained & np.isfinite(ranks)
        candidate_ranks = np.sort(ranks[candidates])
        if slots <= 0 or len(candidate_ranks) == 0:
            return additions

        boundary = candidate_ranks[min(slots, len(candidate_ranks)) - 1]
        strictly_better = candidates & (ranks < boundary)
        remaining = slots - int(strictly_better.sum())
        tied_at_boundary = candidates & (ranks == boundary)
        if int(tied_at_boundary.sum()) <= remaining:
            additions = strictly_better | tied_at_boundary
        else:
            additions = strictly_better
        return additions

    def generate_target_weights(self, data: MarketData) -> pd.DataFrame:
        close = data.close
        stock_returns = close.pct_change(fill_method=None)
        benchmark_returns = data.benchmark_close.pct_change(fill_method=None)

        beta_covariance = stock_returns.rolling(
            self.BETA_LOOKBACK,
            min_periods=self.BETA_LOOKBACK,
        ).cov(benchmark_returns)
        benchmark_variance = benchmark_returns.rolling(
            self.BETA_LOOKBACK,
            min_periods=self.BETA_LOOKBACK,
        ).var()
        beta = beta_covariance.div(benchmark_variance, axis=0).clip(-1.0, 3.0)

        stock_momentum = (
            close.shift(self.MOMENTUM_SKIP) / close.shift(self.MOMENTUM_LOOKBACK) - 1.0
        )
        benchmark_momentum = (
            data.benchmark_close.shift(self.MOMENTUM_SKIP)
            / data.benchmark_close.shift(self.MOMENTUM_LOOKBACK)
            - 1.0
        )
        score = stock_momentum - beta.mul(benchmark_momentum, axis=0)

        eligible = data.tradable & score.notna()
        ranks = score.where(eligible).rank(
            axis=1,
            ascending=False,
            method="average",
        )

        # The engine's schedule is part of the locked interface. Updating the
        # buffered membership only on those decision rows avoids fictitious
        # turnover decisions on sessions when no order can be sent.
        membership = np.zeros((len(data.index), len(data.asset_ids)), dtype=bool)
        held = np.zeros(len(data.asset_ids), dtype=bool)
        first_bar = int(data.index[0])

        for row_position, bar_id in enumerate(data.index):
            if (int(bar_id) - first_bar) % self.REBALANCE_BARS == 0:
                row_eligible = eligible.iloc[row_position].to_numpy(dtype=bool)
                row_ranks = ranks.iloc[row_position].to_numpy(dtype=float)
                retained = held & row_eligible & (row_ranks <= self.RETENTION_RANK)
                slots = self.POSITION_COUNT - int(retained.sum())
                additions = self._fill_open_slots(
                    row_eligible,
                    row_ranks,
                    retained,
                    slots,
                )
                held = retained | additions
            membership[row_position] = held

        # A constant per-name allocation keeps every possible row under the
        # 20% cap even if missing data or a boundary tie leaves fewer names.
        weights = pd.DataFrame(
            membership.astype(float) / self.POSITION_COUNT,
            index=data.index,
            columns=data.asset_ids,
        ).where(data.tradable, 0.0)

        market_average = data.benchmark_close.rolling(
            self.MARKET_TREND_LOOKBACK,
            min_periods=self.MARKET_TREND_LOOKBACK,
        ).mean()
        exposure = pd.Series(
            np.where(
                data.benchmark_close >= market_average,
                1.0,
                self.DEFENSIVE_EXPOSURE,
            ),
            index=data.index,
            dtype=float,
        )
        exposure = exposure.where(market_average.notna(), self.DEFENSIVE_EXPOSURE)
        return weights.mul(exposure, axis=0)
