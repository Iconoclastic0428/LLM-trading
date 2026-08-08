from __future__ import annotations

from decimal import Decimal

import pytest

from benchmark.broker import EquityPosition, EquityQuote, PortfolioSnapshot, Tradability
from benchmark.live_rebalance import validate_quotes
from benchmark.robinhood import RobinhoodExecutionSettings, build_robinhood_rebalance_plan


def test_dollar_orders_round_to_cents_and_sell_first() -> None:
    portfolio = PortfolioSnapshot(
        account_id="acct",
        equity=Decimal("1000.00"),
        buying_power=Decimal("100.00"),
        positions={"OLD": EquityPosition("OLD", Decimal("1.5"), Decimal("150.00"))},
    )
    status = {
        "OLD": Tradability("OLD", True, True),
        "NEW": Tradability("NEW", True, True),
    }
    settings = RobinhoodExecutionSettings(account_id="acct", buying_power_buffer=Decimal("5.00"))
    plan = build_robinhood_rebalance_plan(
        target_weights={"OLD": 0.0, "NEW": 0.2},
        portfolio=portfolio,
        tradability=status,
        settings=settings,
        rebalance_id="r1",
    )
    assert plan.sells[0].symbol == "OLD"
    assert plan.sells[0].sell_all is True
    assert plan.buys[0].symbol == "NEW"
    assert plan.buys[0].notional == Decimal("95.00")


def test_fractional_minimum_is_enforced() -> None:
    portfolio = PortfolioSnapshot("acct", Decimal("10.00"), Decimal("10.00"), {})
    status = {"TINY": Tradability("TINY", True, True)}
    settings = RobinhoodExecutionSettings(account_id="acct", buying_power_buffer=Decimal("0"))
    plan = build_robinhood_rebalance_plan(
        target_weights={"TINY": 0.05},
        portfolio=portfolio,
        tradability=status,
        settings=settings,
        rebalance_id="r2",
    )
    assert not plan.buys


def test_quote_age_and_spread_are_checked() -> None:
    quote = EquityQuote(
        symbol="ETF",
        bid=Decimal("99.99"),
        ask=Decimal("100.01"),
        last=Decimal("100.00"),
        as_of_epoch_ms=10_000,
    )
    validate_quotes(["ETF"], {"ETF": quote}, now_epoch_ms=11_000)
    with pytest.raises(RuntimeError, match="stale"):
        validate_quotes(["ETF"], {"ETF": quote}, now_epoch_ms=30_001)
