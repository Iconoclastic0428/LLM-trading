from __future__ import annotations

from decimal import Decimal

import pytest

from benchmark.broker import OptionContract, OptionLegIntent, OptionOrderIntent
from benchmark.options import OptionRiskLimits, validate_defined_risk_intent


def contract(contract_id: str, strike: str, right: str = "put") -> OptionContract:
    return OptionContract(
        contract_id=contract_id,
        underlying_symbol="SPY",
        expiration="2026-10-16",
        strike=Decimal(strike),
        right=right,
    )


def test_level_two_long_put_has_capped_debit() -> None:
    intent = OptionOrderIntent(
        account_id="A",
        legs=(OptionLegIntent(contract("P500", "500"), "buy_to_open", 1),),
        limit_price=Decimal("1.50"),
    )
    risk = validate_defined_risk_intent(
        intent,
        account_equity=Decimal("10000"),
        limits=OptionRiskLimits(approval_level=2),
    )
    assert risk == Decimal("150.00")


def test_level_three_vertical_debit_spread_is_supported() -> None:
    intent = OptionOrderIntent(
        account_id="A",
        legs=(
            OptionLegIntent(contract("P500", "500"), "buy_to_open", 1),
            OptionLegIntent(contract("P490", "490"), "sell_to_open", 1),
        ),
        limit_price=Decimal("1.75"),
    )
    risk = validate_defined_risk_intent(
        intent,
        account_equity=Decimal("10000"),
        limits=OptionRiskLimits(approval_level=3),
    )
    assert risk == Decimal("175.00")


def test_naked_short_option_is_rejected() -> None:
    intent = OptionOrderIntent(
        account_id="A",
        legs=(OptionLegIntent(contract("P500", "500"), "sell_to_open", 1),),
        limit_price=Decimal("1.00"),
    )
    with pytest.raises(ValueError, match="purchase"):
        validate_defined_risk_intent(
            intent,
            account_equity=Decimal("10000"),
            limits=OptionRiskLimits(approval_level=3),
        )


def test_option_debit_budget_is_enforced() -> None:
    intent = OptionOrderIntent(
        account_id="A",
        legs=(OptionLegIntent(contract("P500", "500"), "buy_to_open", 1),),
        limit_price=Decimal("3.00"),
    )
    with pytest.raises(ValueError, match="budget"):
        validate_defined_risk_intent(
            intent,
            account_equity=Decimal("10000"),
            limits=OptionRiskLimits(approval_level=2),
        )
