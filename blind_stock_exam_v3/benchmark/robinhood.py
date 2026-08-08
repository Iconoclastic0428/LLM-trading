from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Mapping

from .broker import (
    EquityOrderIntent,
    EquityPosition,
    PortfolioSnapshot,
    Tradability,
)


CENT = Decimal("0.01")


@dataclass(frozen=True)
class RobinhoodExecutionSettings:
    account_id: str
    buying_power_buffer: Decimal = Decimal("5.00")
    minimum_fractional_notional: Decimal = Decimal("1.00")
    regular_session_only: bool = True
    require_clean_review: bool = True


@dataclass(frozen=True)
class RebalancePlan:
    sells: tuple[EquityOrderIntent, ...]
    buys: tuple[EquityOrderIntent, ...]
    skipped: tuple[str, ...]


def _floor_cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_DOWN)


def _position_value(positions: Mapping[str, EquityPosition], symbol: str) -> Decimal:
    position = positions.get(symbol)
    return position.market_value if position is not None else Decimal("0")


def validate_live_targets(target_weights: Mapping[str, float]) -> None:
    if any(float(weight) < 0 for weight in target_weights.values()):
        raise ValueError("live target weights must be long-only")
    if any(float(weight) > 0.2 + 1e-12 for weight in target_weights.values()):
        raise ValueError("live target exceeds the 20% single-name cap")
    if sum(float(weight) for weight in target_weights.values()) > 1.0 + 1e-12:
        raise ValueError("live target exceeds available equity")
    if sum(float(weight) > 1e-12 for weight in target_weights.values()) > 10:
        raise ValueError("live target exceeds the 10-position cap")


def build_robinhood_rebalance_plan(
    *,
    target_weights: Mapping[str, float],
    portfolio: PortfolioSnapshot,
    tradability: Mapping[str, Tradability],
    settings: RobinhoodExecutionSettings,
    rebalance_id: str,
) -> RebalancePlan:
    """Convert target weights to Robinhood-shaped dollar orders.

    This function uses broker-reported equity and position market values. It does
    not use adjusted research prices. Buy capacity is conservatively limited to
    current buying power; sell proceeds are handled by a later refresh/replan.
    """

    validate_live_targets(target_weights)
    sells: list[EquityOrderIntent] = []
    buys: list[EquityOrderIntent] = []
    provisional_buys: list[tuple[str, Decimal]] = []
    skipped: list[str] = []
    buy_capacity = max(Decimal("0"), portfolio.buying_power - settings.buying_power_buffer)

    # Only the explicitly managed universe may be traded. A manual or unrelated
    # account position is never interpreted as an implicit zero-weight target.
    symbols = sorted(target_weights)
    for symbol in symbols:
        target_weight = Decimal(str(float(target_weights.get(symbol, 0.0))))
        target_value = _floor_cents(portfolio.equity * target_weight)
        current_value = _position_value(portfolio.positions, symbol)
        delta = target_value - current_value
        status = tradability.get(symbol)
        if status is None or not status.tradable:
            if abs(delta) >= settings.minimum_fractional_notional:
                skipped.append(f"{symbol}: not tradable")
            continue

        if delta < 0:
            reduction = _floor_cents(-delta)
            if target_value == 0 and symbol in portfolio.positions:
                sells.append(
                    EquityOrderIntent(
                        account_id=settings.account_id,
                        symbol=symbol,
                        side="sell",
                        order_type="market",
                        time_in_force="gfd",
                        sell_all=True,
                        rebalance_id=rebalance_id,
                    )
                )
            elif reduction >= settings.minimum_fractional_notional and status.fractionally_tradable:
                sells.append(
                    EquityOrderIntent(
                        account_id=settings.account_id,
                        symbol=symbol,
                        side="sell",
                        order_type="market",
                        time_in_force="gfd",
                        notional=reduction,
                        rebalance_id=rebalance_id,
                    )
                )
            else:
                skipped.append(f"{symbol}: reduction below fractional-order minimum")
        elif delta > 0:
            addition = _floor_cents(delta)
            if addition >= settings.minimum_fractional_notional and status.fractionally_tradable:
                provisional_buys.append((symbol, addition))
            elif addition > 0:
                skipped.append(f"{symbol}: addition below minimum or fractional trading unavailable")

    requested_buys = sum((notional for _, notional in provisional_buys), Decimal("0"))
    allocation_scale = min(Decimal("1"), buy_capacity / requested_buys) if requested_buys > 0 else Decimal("0")
    for symbol, requested in provisional_buys:
        addition = _floor_cents(requested * allocation_scale)
        if addition < settings.minimum_fractional_notional:
            skipped.append(f"{symbol}: pro-rata buy below fractional-order minimum")
            continue
        buys.append(
            EquityOrderIntent(
                account_id=settings.account_id,
                symbol=symbol,
                side="buy",
                order_type="market",
                time_in_force="gfd",
                notional=addition,
                rebalance_id=rebalance_id,
            )
        )

    return RebalancePlan(sells=tuple(sells), buys=tuple(buys), skipped=tuple(skipped))
