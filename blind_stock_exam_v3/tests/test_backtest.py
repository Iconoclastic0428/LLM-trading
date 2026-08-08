from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from benchmark.backtest import run_backtest
from benchmark.data import MarketData
from benchmark.rules import BenchmarkRules


def rules(**overrides) -> BenchmarkRules:
    values = {
        "annualization_sessions": 252,
        "cash_return_annual": 0.0,
        "commission_bps": 0.0,
        "execution": "next_regular_session_open",
        "max_gross_exposure": 1.0,
        "max_positions": 10,
        "max_weight_per_asset": 1.0,
        "min_order_notional_usd": 1.0,
        "rebalance_every_bars": 1,
        "slippage_bps": 0.0,
        "starting_equity_usd": 10_000.0,
    }
    values.update(overrides)
    return BenchmarkRules(**values)


def toy_data(opens: list[float]) -> MarketData:
    index = pd.Index(range(len(opens)), name="bar_id")
    frame = pd.DataFrame({"A": opens}, index=index, dtype=float)
    volume = pd.DataFrame({"A": np.full(len(opens), 1000.0)}, index=index)
    tradable = pd.DataFrame({"A": np.full(len(opens), True)}, index=index)
    benchmark = pd.Series(opens, index=index, dtype=float)
    return MarketData(frame, frame, frame, frame, volume, tradable, benchmark, benchmark)


def test_decision_executes_at_next_open() -> None:
    data = toy_data([100, 100, 110, 121])
    weights = pd.DataFrame({"A": [1.0, 0.0, 0.0, 0.0]}, index=data.index)
    result = run_backtest(data, weights, rules())
    assert result.daily.index.tolist() == [2, 3]
    assert result.daily.loc[2, "net_return"] == pytest.approx(0.1)
    assert result.daily.loc[3, "net_return"] == 0.0


def test_cost_is_charged_on_one_way_turnover() -> None:
    data = toy_data([100, 100, 100, 100])
    weights = pd.DataFrame({"A": [1.0, 0.0, 0.0, 0.0]}, index=data.index)
    result = run_backtest(data, weights, rules(slippage_bps=10.0))
    assert result.daily.loc[2, "turnover"] == 1.0
    assert result.daily.loc[2, "net_return"] == pytest.approx(-0.001)


def test_nonscheduled_bar_keeps_drifted_position() -> None:
    data = toy_data([100, 100, 110, 121, 133.1])
    weights = pd.DataFrame({"A": [1.0, 0.0, 0.0, 0.0, 0.0]}, index=data.index)
    result = run_backtest(data, weights, rules(rebalance_every_bars=5))
    assert np.allclose(result.daily["net_return"].to_numpy(), [0.1, 0.1, 0.1])


def test_sub_dollar_fractional_buy_is_skipped() -> None:
    data = toy_data([100, 100, 110, 121])
    weights = pd.DataFrame({"A": [0.00009, 0.0, 0.0, 0.0]}, index=data.index)
    result = run_backtest(data, weights, rules(starting_equity_usd=10_000.0))
    assert result.daily.loc[2, "turnover"] == 0.0
    assert result.daily.loc[2, "net_return"] == 0.0


def test_order_waits_when_next_open_is_not_tradable() -> None:
    data = toy_data([100, 100, 110, 121])
    object.__setattr__(
        data,
        "tradable",
        pd.DataFrame({"A": [True, False, True, True]}, index=data.index),
    )
    weights = pd.DataFrame({"A": [1.0, 0.0, 0.0, 0.0]}, index=data.index)
    result = run_backtest(data, weights, rules())
    assert result.daily.loc[2, "turnover"] == 0.0
    assert result.daily.loc[2, "net_return"] == 0.0
