from __future__ import annotations

import pandas as pd
import pytest

from benchmark.data import MarketData
from benchmark.rules import BenchmarkRules, PortfolioRuleError, normalize_target_weights


def fixture() -> tuple[MarketData, BenchmarkRules]:
    index = pd.Index(range(3), name="bar_id")
    columns = ["A", "B"]
    prices = pd.DataFrame(10.0, index=index, columns=columns)
    volume = pd.DataFrame(100.0, index=index, columns=columns)
    tradable = pd.DataFrame(True, index=index, columns=columns)
    benchmark = pd.Series(10.0, index=index)
    data = MarketData(prices, prices, prices, prices, volume, tradable, benchmark, benchmark)
    rules = BenchmarkRules(252, 0.0, 0.0, "next_regular_session_open", 1.0, 2, 0.6, 1.0, 5, 5.0, 10000.0)
    return data, rules


def test_nan_warmup_becomes_cash() -> None:
    data, rules = fixture()
    raw = pd.DataFrame(float("nan"), index=data.index, columns=data.asset_ids)
    assert normalize_target_weights(raw, data, rules).sum().sum() == 0.0


@pytest.mark.parametrize("weights", [[-0.1, 0.0], [0.7, 0.0], [0.6, 0.6]])
def test_invalid_weights_are_rejected(weights) -> None:
    data, rules = fixture()
    raw = pd.DataFrame([weights] * len(data.index), index=data.index, columns=data.asset_ids)
    with pytest.raises(PortfolioRuleError):
        normalize_target_weights(raw, data, rules)


def test_nonzero_weight_on_unlisted_asset_is_rejected() -> None:
    data, rules = fixture()
    object.__setattr__(data, "tradable", data.tradable.assign(A=[False, True, True]))
    raw = pd.DataFrame(0.0, index=data.index, columns=data.asset_ids)
    raw.loc[0, "A"] = 0.5
    with pytest.raises(PortfolioRuleError, match="non-tradable"):
        normalize_target_weights(raw, data, rules)
