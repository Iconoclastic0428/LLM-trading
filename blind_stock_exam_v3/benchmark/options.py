from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping

from .broker import (
    OptionBrokerGateway,
    OptionOrderIntent,
    OptionQuote,
    PlacedOrder,
)


@dataclass(frozen=True)
class OptionRiskLimits:
    approval_level: int
    max_debit_fraction_of_equity: Decimal = Decimal("0.02")
    maximum_quote_age_seconds: int = 10
    maximum_spread_bps: Decimal = Decimal("1500")
    minimum_open_interest: int = 100
    require_clean_review: bool = True

    def __post_init__(self) -> None:
        if self.approval_level not in {2, 3}:
            raise ValueError("option approval level must be 2 or 3")
        if not Decimal("0") < self.max_debit_fraction_of_equity <= Decimal("1"):
            raise ValueError("option debit budget fraction must be in (0, 1]")


def _opening_legs(intent: OptionOrderIntent):
    return tuple(leg for leg in intent.legs if leg.action.endswith("to_open"))


def validate_defined_risk_intent(
    intent: OptionOrderIntent,
    *,
    account_equity: Decimal,
    limits: OptionRiskLimits,
) -> Decimal:
    """Validate the deliberately small live option subset.

    Level 2 permits a purchased call or put.  Level 3 additionally permits a
    two-leg vertical debit spread.  Naked short contracts, ratio spreads,
    credit spreads, calendars, and more complex assignment paths are excluded
    from this executor.  The returned value is the maximum opening debit.
    """

    if account_equity <= 0:
        raise ValueError("account equity must be positive")
    opening = _opening_legs(intent)
    if not opening:
        return Decimal("0")

    if len(intent.legs) == 1:
        leg = intent.legs[0]
        if leg.action != "buy_to_open":
            raise ValueError("single-leg opening orders must purchase the option")
        multiplier = Decimal(leg.contract.multiplier)
        max_debit = intent.limit_price * multiplier * leg.quantity
    elif len(intent.legs) == 2:
        if limits.approval_level < 3:
            raise ValueError("vertical spreads require option approval level 3")
        first, second = intent.legs
        if {first.action, second.action} != {"buy_to_open", "sell_to_open"}:
            raise ValueError("a vertical debit spread needs one purchased and one written leg")
        if first.quantity != second.quantity:
            raise ValueError("ratio option spreads are outside the defined-risk executor")
        if first.contract.expiration != second.contract.expiration:
            raise ValueError("calendar spreads are outside the defined-risk executor")
        if first.contract.right != second.contract.right:
            raise ValueError("vertical spread legs must use the same option right")
        if first.contract.multiplier != second.contract.multiplier:
            raise ValueError("vertical spread multipliers must match")
        width = abs(first.contract.strike - second.contract.strike)
        if width <= 0 or intent.limit_price > width:
            raise ValueError("vertical debit must not exceed its strike width")
        max_debit = intent.limit_price * Decimal(first.contract.multiplier) * first.quantity
    else:
        raise ValueError("opening orders are limited to long options or two-leg debit spreads")

    budget = account_equity * limits.max_debit_fraction_of_equity
    if max_debit > budget:
        raise ValueError("option opening debit exceeds the configured equity budget")
    return max_debit


def validate_option_quotes(
    intent: OptionOrderIntent,
    quotes: Mapping[str, OptionQuote],
    *,
    limits: OptionRiskLimits,
    now_epoch_ms: int,
) -> None:
    for leg in intent.legs:
        contract_id = leg.contract.contract_id
        quote = quotes.get(contract_id)
        if quote is None:
            raise RuntimeError(f"missing option quote for {contract_id}")
        if quote.bid < 0 or quote.ask <= 0 or quote.ask < quote.bid:
            raise RuntimeError(f"invalid option quote for {contract_id}")
        age_ms = now_epoch_ms - quote.as_of_epoch_ms
        if age_ms < 0 or age_ms > limits.maximum_quote_age_seconds * 1000:
            raise RuntimeError(f"stale option quote for {contract_id}")
        midpoint = (quote.bid + quote.ask) / Decimal("2")
        if midpoint <= 0:
            raise RuntimeError(f"option quote has no positive midpoint for {contract_id}")
        spread_bps = (quote.ask - quote.bid) / midpoint * Decimal("10000")
        if spread_bps > limits.maximum_spread_bps:
            raise RuntimeError(f"option spread guard triggered for {contract_id}")
        if leg.action.endswith("to_open") and quote.open_interest < limits.minimum_open_interest:
            raise RuntimeError(f"option open-interest guard triggered for {contract_id}")


def review_and_place_option_order(
    gateway: OptionBrokerGateway,
    intent: OptionOrderIntent,
    *,
    account_equity: Decimal,
    limits: OptionRiskLimits,
    now_epoch_ms: int,
) -> PlacedOrder:
    validate_defined_risk_intent(intent, account_equity=account_equity, limits=limits)
    contract_ids = [leg.contract.contract_id for leg in intent.legs]
    quotes = gateway.get_option_quotes(contract_ids)
    validate_option_quotes(intent, quotes, limits=limits, now_epoch_ms=now_epoch_ms)
    review = gateway.review_option_order(intent)
    if not review.accepted:
        raise RuntimeError("broker review rejected the option order")
    if limits.require_clean_review and review.warnings:
        raise RuntimeError(f"broker review warning for option order: {'; '.join(review.warnings)}")
    return gateway.place_option_order(review, intent)
