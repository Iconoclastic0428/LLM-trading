from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping, Sequence

from .broker import EquityBrokerGateway, EquityOrderIntent, EquityQuote, PlacedOrder
from .robinhood import RobinhoodExecutionSettings, build_robinhood_rebalance_plan


TERMINAL_STATES = {"filled", "canceled", "cancelled", "rejected", "failed"}


@dataclass(frozen=True)
class LiveRebalanceReport:
    rebalance_id: str
    sell_orders: tuple[PlacedOrder, ...]
    buy_orders: tuple[PlacedOrder, ...]
    skipped: tuple[str, ...]


def validate_quotes(
    symbols: Sequence[str],
    quotes: Mapping[str, EquityQuote],
    *,
    now_epoch_ms: int,
    maximum_age_seconds: int = 15,
    maximum_spread_bps: Decimal = Decimal("50"),
) -> None:
    for symbol in symbols:
        quote = quotes.get(symbol)
        if quote is None:
            raise RuntimeError(f"missing live quote for {symbol}")
        if quote.bid <= 0 or quote.ask <= 0 or quote.ask < quote.bid:
            raise RuntimeError(f"invalid live quote for {symbol}")
        age_ms = now_epoch_ms - quote.as_of_epoch_ms
        if age_ms < 0 or age_ms > maximum_age_seconds * 1000:
            raise RuntimeError(f"stale live quote for {symbol}")
        spread_bps = (quote.ask - quote.bid) / quote.midpoint * Decimal("10000")
        if spread_bps > maximum_spread_bps:
            raise RuntimeError(f"spread guard triggered for {symbol}")


def review_and_place(
    gateway: EquityBrokerGateway,
    intents: Sequence[EquityOrderIntent],
    *,
    require_clean_review: bool,
) -> tuple[PlacedOrder, ...]:
    placed: list[PlacedOrder] = []
    for intent in intents:
        review = gateway.review_equity_order(intent)
        if not review.accepted:
            raise RuntimeError(f"broker review rejected {intent.side} {intent.symbol}")
        if require_clean_review and review.warnings:
            raise RuntimeError(f"broker review warning for {intent.symbol}: {'; '.join(review.warnings)}")
        # There is intentionally no automatic retry around placement: a timeout
        # must be reconciled by broker order ID before another order is sent.
        placed.append(gateway.place_equity_order(review, intent))
    return tuple(placed)


def wait_for_terminal_orders(
    gateway: EquityBrokerGateway,
    *,
    account_id: str,
    placed: Sequence[PlacedOrder],
    timeout_seconds: float = 120.0,
    poll_seconds: float = 2.0,
) -> tuple[PlacedOrder, ...]:
    if not placed:
        return ()
    order_ids = [order.order_id for order in placed]
    deadline = time.monotonic() + timeout_seconds
    while True:
        current = tuple(gateway.get_equity_orders(account_id, order_ids))
        by_id = {order.order_id: order for order in current}
        if set(by_id) == set(order_ids) and all(by_id[order_id].state.lower() in TERMINAL_STATES for order_id in order_ids):
            return tuple(by_id[order_id] for order_id in order_ids)
        if time.monotonic() >= deadline:
            for order_id in order_ids:
                state = by_id.get(order_id)
                if state is None or state.state.lower() not in TERMINAL_STATES:
                    gateway.cancel_equity_order(account_id, order_id)
            raise TimeoutError("equity orders did not reach a terminal state before the deadline")
        time.sleep(poll_seconds)


def execute_regular_session_rebalance(
    gateway: EquityBrokerGateway,
    *,
    target_weights: Mapping[str, float],
    settings: RobinhoodExecutionSettings,
    rebalance_id: str,
    regular_session_open: bool,
    now_epoch_ms: int,
) -> LiveRebalanceReport:
    """Review/place sells, reconcile fills, refresh, then review/place buys.

    `target_weights` must include every symbol in the managed universe, including
    explicit zeros. The gateway adapter maps this deterministic flow to the
    current Robinhood Agentic tool schemas.
    """

    if settings.regular_session_only and not regular_session_open:
        raise RuntimeError("regular equity session is closed")
    symbols = sorted(target_weights)
    portfolio = gateway.get_portfolio(settings.account_id)
    unmanaged = set(portfolio.positions) - set(symbols)
    if unmanaged:
        raise RuntimeError(f"dedicated account contains unmanaged equity positions: {sorted(unmanaged)}")
    execution_symbols = sorted(
        {symbol for symbol, weight in target_weights.items() if float(weight) > 1e-12}
        | set(portfolio.positions)
    )
    quotes = gateway.get_equity_quotes(execution_symbols) if execution_symbols else {}
    validate_quotes(execution_symbols, quotes, now_epoch_ms=now_epoch_ms)
    tradability = gateway.get_equity_tradability(execution_symbols) if execution_symbols else {}

    initial = build_robinhood_rebalance_plan(
        target_weights=target_weights,
        portfolio=portfolio,
        tradability=tradability,
        settings=settings,
        rebalance_id=rebalance_id,
    )
    sell_submitted = review_and_place(
        gateway,
        initial.sells,
        require_clean_review=settings.require_clean_review,
    )
    sell_terminal = wait_for_terminal_orders(
        gateway,
        account_id=settings.account_id,
        placed=sell_submitted,
    )
    failed_sells = [order for order in sell_terminal if order.state.lower() != "filled"]
    if failed_sells:
        raise RuntimeError("one or more sell orders did not fill; buys were not submitted")

    refreshed_portfolio = gateway.get_portfolio(settings.account_id)
    refreshed_symbols = sorted(
        {symbol for symbol, weight in target_weights.items() if float(weight) > 1e-12}
        | set(refreshed_portfolio.positions)
    )
    refreshed_tradability = gateway.get_equity_tradability(refreshed_symbols) if refreshed_symbols else {}
    refreshed = build_robinhood_rebalance_plan(
        target_weights=target_weights,
        portfolio=refreshed_portfolio,
        tradability=refreshed_tradability,
        settings=settings,
        rebalance_id=rebalance_id,
    )
    buy_submitted = review_and_place(
        gateway,
        refreshed.buys,
        require_clean_review=settings.require_clean_review,
    )
    buy_terminal = wait_for_terminal_orders(
        gateway,
        account_id=settings.account_id,
        placed=buy_submitted,
    )
    failed_buys = [order for order in buy_terminal if order.state.lower() != "filled"]
    if failed_buys:
        raise RuntimeError("one or more buy orders did not fill")
    return LiveRebalanceReport(
        rebalance_id=rebalance_id,
        sell_orders=sell_terminal,
        buy_orders=buy_terminal,
        skipped=tuple(sorted(set(refreshed.skipped))),
    )
