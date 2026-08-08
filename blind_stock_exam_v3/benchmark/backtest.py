from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .data import MarketData
from .rules import BenchmarkRules, normalize_target_weights


@dataclass(frozen=True)
class BacktestResult:
    daily: pd.DataFrame
    target_weights: pd.DataFrame

    def between(self, first_bar: int, last_bar: int) -> "BacktestResult":
        mask = self.daily.index.to_series().between(first_bar, last_bar).to_numpy()
        return BacktestResult(self.daily.loc[mask].copy(), self.target_weights.copy())


def _quantize_robinhood_target(
    desired: np.ndarray,
    current: np.ndarray,
    executable: np.ndarray,
    equity_usd: float,
    rules: BenchmarkRules,
) -> np.ndarray:
    """Apply penny notional rounding and the $1 fractional-order minimum.

    A zero target is treated as Sell All, including residuals below $1. Other
    reductions and additions are dollar orders rounded down to cents.
    """

    if equity_usd <= 0:
        raise RuntimeError("portfolio equity must remain positive")
    executed = current.copy()
    deltas = np.where(executable, desired - current, 0.0)
    for index in np.flatnonzero(deltas < 0):
        if desired[index] <= 1e-15:
            executed[index] = 0.0
            continue
        notional = np.floor((-deltas[index] * equity_usd) * 100.0 + 1e-9) / 100.0
        if notional >= rules.min_order_notional_usd:
            executed[index] -= notional / equity_usd
    buy_capacity = max(0.0, (1.0 - float(executed.sum())) * equity_usd)
    buy_indexes = np.flatnonzero(deltas > 0)
    requested_total = float(deltas[buy_indexes].sum() * equity_usd)
    allocation_scale = min(1.0, buy_capacity / requested_total) if requested_total > 0 else 0.0
    for index in buy_indexes:
        requested = deltas[index] * equity_usd * allocation_scale
        notional = np.floor(requested * 100.0 + 1e-9) / 100.0
        if notional >= rules.min_order_notional_usd:
            executed[index] += notional / equity_usd
    return np.clip(executed, 0.0, None)


def run_backtest(
    data: MarketData,
    raw_target_weights: pd.DataFrame,
    rules: BenchmarkRules,
) -> BacktestResult:
    """Simulate close decision -> next-open rebalance -> open-to-open holding.

    At open `j`, a scheduled rebalance uses the target computed at close `j-1`.
    The resulting position earns adjusted-open return from `j` to `j+1`. Between
    scheduled rebalances, drifted holdings remain untouched.
    """

    weights = normalize_target_weights(raw_target_weights, data, rules)
    if len(data.index) < 3:
        raise ValueError("at least three bars are required")

    open_values = data.open.to_numpy(dtype=float)
    tradable_values = data.tradable.to_numpy(dtype=bool)
    benchmark_open = data.benchmark_open.to_numpy(dtype=float)
    bar_ids = data.index.to_numpy(dtype=np.int64)
    assets = list(data.asset_ids)
    held_weights = np.zeros(len(assets), dtype=float)
    equity = 1.0
    first_bar = int(bar_ids[0])
    records: list[dict[str, float | int | bool]] = []

    for entry_pos in range(1, len(bar_ids) - 1):
        signal_pos = entry_pos - 1
        signal_bar = int(bar_ids[signal_pos])
        scheduled = (signal_bar - first_bar) % rules.rebalance_every_bars == 0

        if scheduled:
            desired = weights.iloc[signal_pos].to_numpy(dtype=float)
            executed = _quantize_robinhood_target(
                desired,
                held_weights,
                tradable_values[entry_pos],
                rules.starting_equity_usd * equity,
                rules,
            )
            turnover = float(np.abs(executed - held_weights).sum())
            trading_cost = turnover * rules.one_way_cost_rate
            held_weights = executed
        else:
            turnover = 0.0
            trading_cost = 0.0

        current_open = open_values[entry_pos]
        next_open = open_values[entry_pos + 1]
        valid_return = np.isfinite(current_open) & np.isfinite(next_open)
        if ((held_weights > 1e-10) & ~valid_return).any():
            symbol = assets[int(np.flatnonzero((held_weights > 1e-10) & ~valid_return)[0])]
            raise RuntimeError(f"held asset has no valuation price: {symbol}")
        asset_returns = np.zeros(len(assets), dtype=float)
        asset_returns[valid_return] = next_open[valid_return] / current_open[valid_return] - 1.0
        gross_return = float(np.dot(held_weights, asset_returns))
        net_return = float((1.0 - trading_cost) * (1.0 + gross_return) - 1.0)
        equity *= 1.0 + net_return

        cash_weight = max(0.0, 1.0 - float(held_weights.sum()))
        gross_multiplier = cash_weight + float(np.dot(held_weights, 1.0 + asset_returns))
        if gross_multiplier <= 0:
            raise RuntimeError("portfolio value became nonpositive")
        held_weights = held_weights * (1.0 + asset_returns) / gross_multiplier

        benchmark_return = float(benchmark_open[entry_pos + 1] / benchmark_open[entry_pos] - 1.0)
        records.append(
            {
                "bar_id": int(bar_ids[entry_pos + 1]),
                "signal_bar_id": signal_bar,
                "entry_bar_id": int(bar_ids[entry_pos]),
                "scheduled_rebalance": scheduled,
                "gross_return": gross_return,
                "trading_cost": trading_cost,
                "net_return": net_return,
                "benchmark_return": benchmark_return,
                "turnover": turnover,
                "gross_exposure": float(held_weights.sum()),
                "position_count": int((held_weights > 1e-10).sum()),
                "equity": equity,
            }
        )

    daily = pd.DataFrame.from_records(records).set_index("bar_id")
    daily.index = daily.index.astype("int64")
    daily.index.name = "bar_id"
    return BacktestResult(daily=daily, target_weights=weights)


def equal_weight_targets(data: MarketData, rules: BenchmarkRules) -> pd.DataFrame:
    counts = data.tradable.sum(axis=1).astype(float)
    per_asset = (1.0 / counts.where(counts > 0)).clip(upper=rules.max_weight_per_asset)
    return data.tradable.astype(float).mul(per_asset, axis=0).fillna(0.0)


def fully_invested_selection_targets(
    data: MarketData,
    raw_target_weights: pd.DataFrame,
    rules: BenchmarkRules,
) -> pd.DataFrame:
    """Remove the strategy's market-exposure choice while preserving selection.

    Nonzero rows are rescaled to 100% gross exposure.  Zero rows remain cash and
    are reported through selection coverage, so a strategy receives no apparent
    stock-picking credit merely for sitting out a falling market.
    """

    weights = normalize_target_weights(raw_target_weights, data, rules)
    gross = weights.sum(axis=1)
    return weights.div(gross.where(gross > 1e-12), axis=0).fillna(0.0)
