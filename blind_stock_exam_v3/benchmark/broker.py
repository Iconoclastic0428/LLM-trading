from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal, Mapping, Protocol, Sequence


Side = Literal["buy", "sell"]
OrderType = Literal["market", "limit"]
TimeInForce = Literal["gfd", "gtc"]
OptionRight = Literal["call", "put"]
OptionAction = Literal["buy_to_open", "sell_to_open", "buy_to_close", "sell_to_close"]


@dataclass(frozen=True)
class EquityQuote:
    symbol: str
    bid: Decimal
    ask: Decimal
    last: Decimal
    as_of_epoch_ms: int

    @property
    def midpoint(self) -> Decimal:
        if self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / Decimal("2")
        return self.last


@dataclass(frozen=True)
class EquityPosition:
    symbol: str
    quantity: Decimal
    market_value: Decimal


@dataclass(frozen=True)
class PortfolioSnapshot:
    account_id: str
    equity: Decimal
    buying_power: Decimal
    positions: Mapping[str, EquityPosition] = field(default_factory=dict)


@dataclass(frozen=True)
class Tradability:
    symbol: str
    tradable: bool
    fractionally_tradable: bool


@dataclass(frozen=True)
class EquityOrderIntent:
    account_id: str
    symbol: str
    side: Side
    order_type: OrderType = "market"
    time_in_force: TimeInForce = "gfd"
    notional: Decimal | None = None
    quantity: Decimal | None = None
    limit_price: Decimal | None = None
    sell_all: bool = False
    rebalance_id: str = ""

    def __post_init__(self) -> None:
        sizing_fields = int(self.notional is not None) + int(self.quantity is not None) + int(self.sell_all)
        if sizing_fields != 1:
            raise ValueError("exactly one of notional, quantity, or sell_all is required")
        if self.side == "buy" and self.sell_all:
            raise ValueError("sell_all is valid only for sell orders")
        if self.order_type == "limit" and self.limit_price is None:
            raise ValueError("limit orders need a limit price")


@dataclass(frozen=True)
class OptionContract:
    contract_id: str
    underlying_symbol: str
    expiration: str
    strike: Decimal
    right: OptionRight
    multiplier: int = 100

    def __post_init__(self) -> None:
        if not self.contract_id or not self.underlying_symbol:
            raise ValueError("option contract identifiers are required")
        if self.strike <= 0 or self.multiplier <= 0:
            raise ValueError("option strike and multiplier must be positive")
        if self.right not in {"call", "put"}:
            raise ValueError("option right must be call or put")


@dataclass(frozen=True)
class OptionQuote:
    contract_id: str
    bid: Decimal
    ask: Decimal
    mark: Decimal
    as_of_epoch_ms: int
    volume: int = 0
    open_interest: int = 0


@dataclass(frozen=True)
class OptionLegIntent:
    contract: OptionContract
    action: OptionAction
    quantity: int

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("option leg quantity must be a positive whole contract count")


@dataclass(frozen=True)
class OptionOrderIntent:
    account_id: str
    legs: tuple[OptionLegIntent, ...]
    limit_price: Decimal
    time_in_force: TimeInForce = "gfd"
    rebalance_id: str = ""

    def __post_init__(self) -> None:
        if not 1 <= len(self.legs) <= 4:
            raise ValueError("an option order requires one to four legs")
        if self.limit_price <= 0:
            raise ValueError("option orders require a positive limit price")
        underlyings = {leg.contract.underlying_symbol for leg in self.legs}
        if len(underlyings) != 1:
            raise ValueError("all option legs must share one underlying")


@dataclass(frozen=True)
class OrderReview:
    review_id: str
    accepted: bool
    warnings: tuple[str, ...] = ()
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PlacedOrder:
    order_id: str
    state: str
    raw: Mapping[str, Any] = field(default_factory=dict)


class EquityBrokerGateway(Protocol):
    """Small deterministic boundary around a brokerage equity integration.

    A Robinhood-facing implementation may be an interactive review/export
    gateway or a documented broker interface.  Strategy and risk code do not
    depend on a particular transport or undocumented endpoint.
    """

    def get_portfolio(self, account_id: str) -> PortfolioSnapshot: ...

    def get_equity_quotes(self, symbols: Sequence[str]) -> Mapping[str, EquityQuote]: ...

    def get_equity_tradability(self, symbols: Sequence[str]) -> Mapping[str, Tradability]: ...

    def review_equity_order(self, intent: EquityOrderIntent) -> OrderReview: ...

    def place_equity_order(self, review: OrderReview, intent: EquityOrderIntent) -> PlacedOrder: ...

    def get_equity_orders(self, account_id: str, order_ids: Sequence[str]) -> Sequence[PlacedOrder]: ...

    def cancel_equity_order(self, account_id: str, order_id: str) -> None: ...


class OptionBrokerGateway(Protocol):
    """Broker boundary for approved, whole-contract option orders.

    The research strategy produces an option intent; a concrete adapter maps it
    to the broker's current review and placement interface.  Contract discovery
    and quote retrieval stay outside strategy code so stale chains cannot be
    mistaken for executable prices.
    """

    def get_option_quotes(self, contract_ids: Sequence[str]) -> Mapping[str, OptionQuote]: ...

    def review_option_order(self, intent: OptionOrderIntent) -> OrderReview: ...

    def place_option_order(self, review: OrderReview, intent: OptionOrderIntent) -> PlacedOrder: ...

    def get_option_orders(self, account_id: str, order_ids: Sequence[str]) -> Sequence[PlacedOrder]: ...

    def cancel_option_order(self, account_id: str, order_id: str) -> None: ...
