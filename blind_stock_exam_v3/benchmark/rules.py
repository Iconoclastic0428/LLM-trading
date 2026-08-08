from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .data import MarketData


class PortfolioRuleError(ValueError):
    pass


@dataclass(frozen=True)
class BenchmarkRules:
    annualization_sessions: int
    cash_return_annual: float
    commission_bps: float
    execution: str
    max_gross_exposure: float
    max_positions: int
    max_weight_per_asset: float
    min_order_notional_usd: float
    rebalance_every_bars: int
    slippage_bps: float
    starting_equity_usd: float

    @classmethod
    def from_json(cls, path: str | Path) -> "BenchmarkRules":
        with Path(path).open("r", encoding="utf-8") as handle:
            return cls(**json.load(handle))

    @property
    def one_way_cost_rate(self) -> float:
        return (self.commission_bps + self.slippage_bps) / 10_000.0


def normalize_target_weights(
    raw: pd.DataFrame,
    data: MarketData,
    rules: BenchmarkRules,
    *,
    tolerance: float = 1e-10,
) -> pd.DataFrame:
    if not isinstance(raw, pd.DataFrame):
        raise PortfolioRuleError("strategy output must be a pandas DataFrame")
    if not raw.index.equals(data.index):
        raise PortfolioRuleError("strategy output index must exactly match data.index")
    if set(raw.columns) != set(data.asset_ids):
        raise PortfolioRuleError("strategy output columns must exactly match data.asset_ids")

    weights = raw.reindex(columns=data.asset_ids).astype(float)
    if np.isinf(weights.to_numpy()).any():
        raise PortfolioRuleError("strategy output contains infinite weights")
    weights = weights.fillna(0.0)
    values = weights.to_numpy()
    if (values < -tolerance).any():
        row, col = np.argwhere(values < -tolerance)[0]
        raise PortfolioRuleError(f"negative weight at bar={weights.index[row]}, asset={weights.columns[col]}")
    if (values > rules.max_weight_per_asset + tolerance).any():
        row, col = np.argwhere(values > rules.max_weight_per_asset + tolerance)[0]
        raise PortfolioRuleError(
            f"asset weight exceeds cap at bar={weights.index[row]}, asset={weights.columns[col]}"
        )
    unavailable = (weights > tolerance) & ~data.tradable
    if unavailable.any().any():
        row, col = np.argwhere(unavailable.to_numpy())[0]
        raise PortfolioRuleError(
            f"nonzero weight on a non-tradable asset at bar={weights.index[row]}, "
            f"asset={weights.columns[col]}"
        )
    gross = weights.sum(axis=1)
    if (gross > rules.max_gross_exposure + tolerance).any():
        bar = gross[gross > rules.max_gross_exposure + tolerance].index[0]
        raise PortfolioRuleError(f"gross exposure exceeds cap at bar={bar}")
    positions = (weights > tolerance).sum(axis=1)
    if (positions > rules.max_positions).any():
        bar = positions[positions > rules.max_positions].index[0]
        raise PortfolioRuleError(f"position count exceeds cap at bar={bar}")
    return weights.clip(lower=0.0)
