"""Broker interface and the value types execution is expressed in.

One deliberate asymmetry runs through this module: **the broker is the source
of truth, never our database.** Our records are a cache of what we asked for;
the broker knows what actually happened. Every reconciliation resolves
disagreements in the broker's favour, and no code path infers a fill from
having sent an order.

The other rule is that an adapter never decides *whether* an order should be
placed. That belongs to the guardrail layer. Adapters translate and transmit;
they do not judge.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class ExecutionMode(str, Enum):
    """Where orders go.

    `PAPER` is internal simulation and touches no broker. `BROKER_PAPER` uses
    the broker's own paper endpoint — real API, fake money. `LIVE` risks real
    money and is gated behind several independent switches.
    """

    PAPER = "paper"                # internal simulation, no network
    BROKER_PAPER = "broker_paper"  # broker sandbox, real API, fake money
    LIVE = "live"                  # real money

    @property
    def is_live(self) -> bool:
        return self is ExecutionMode.LIVE

    @property
    def touches_broker(self) -> bool:
        return self in (ExecutionMode.BROKER_PAPER, ExecutionMode.LIVE)


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class TimeInForce(str, Enum):
    DAY = "day"
    GTC = "gtc"
    IOC = "ioc"
    FOK = "fok"


class OrderStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    UNKNOWN = "unknown"

    @property
    def is_terminal(self) -> bool:
        return self in (
            OrderStatus.FILLED, OrderStatus.CANCELLED,
            OrderStatus.REJECTED, OrderStatus.EXPIRED,
        )

    @property
    def is_working(self) -> bool:
        return self in (OrderStatus.PENDING, OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED)


class BrokerError(RuntimeError):
    """Raised when a broker rejects a request or cannot be reached."""


class BrokerAuthError(BrokerError):
    """Credentials are missing, malformed or refused."""


@dataclass
class OrderRequest:
    """An order the platform wants to place.

    `stop_price` is not optional in spirit: the guardrails refuse any entry
    without protection. It lives here as a nullable field only because exit
    orders legitimately have no stop of their own.
    """

    symbol: str
    side: OrderSide
    quantity: float
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    stop_price: float | None = None
    take_profit_price: float | None = None
    time_in_force: TimeInForce = TimeInForce.DAY

    # Provenance, so every order can be traced back to what justified it.
    strategy_id: int | None = None
    analysis_id: int | None = None
    reason: str = ""
    client_order_id: str = ""

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "side": self.side.value,
            "quantity": self.quantity,
            "order_type": self.order_type.value,
            "limit_price": self.limit_price,
            "stop_price": self.stop_price,
            "take_profit_price": self.take_profit_price,
            "time_in_force": self.time_in_force.value,
            "strategy_id": self.strategy_id,
            "analysis_id": self.analysis_id,
            "reason": self.reason,
            "client_order_id": self.client_order_id,
        }


@dataclass
class Order:
    """An order as the broker reports it."""

    broker_order_id: str
    symbol: str
    side: OrderSide
    quantity: float
    filled_quantity: float
    status: OrderStatus
    order_type: OrderType
    limit_price: float | None = None
    stop_price: float | None = None
    average_fill_price: float | None = None
    submitted_at: datetime | None = None
    filled_at: datetime | None = None
    client_order_id: str = ""
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "broker_order_id": self.broker_order_id,
            "client_order_id": self.client_order_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "quantity": self.quantity,
            "filled_quantity": self.filled_quantity,
            "status": self.status.value,
            "order_type": self.order_type.value,
            "limit_price": self.limit_price,
            "stop_price": self.stop_price,
            "average_fill_price": self.average_fill_price,
            "submitted_at": self.submitted_at.isoformat() if self.submitted_at else None,
            "filled_at": self.filled_at.isoformat() if self.filled_at else None,
        }


@dataclass
class BrokerPosition:
    symbol: str
    quantity: float           # negative when short
    average_entry_price: float
    current_price: float | None = None
    market_value: float | None = None
    unrealized_pnl: float | None = None
    unrealized_pnl_percent: float | None = None
    raw: dict = field(default_factory=dict)

    @property
    def side(self) -> str:
        return "long" if self.quantity > 0 else "short"

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "quantity": self.quantity,
            "side": self.side,
            "average_entry_price": self.average_entry_price,
            "current_price": self.current_price,
            "market_value": self.market_value,
            "unrealized_pnl": self.unrealized_pnl,
            "unrealized_pnl_percent": self.unrealized_pnl_percent,
        }


@dataclass
class BrokerAccount:
    account_id: str
    equity: float
    cash: float
    buying_power: float
    currency: str = "USD"
    is_paper: bool = True
    # Brokers halt accounts for pattern-day-trading and margin breaches; if we
    # do not surface it, orders fail for reasons the user cannot see.
    trading_blocked: bool = False
    pattern_day_trader: bool = False
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "account_id": self.account_id,
            "equity": self.equity,
            "cash": self.cash,
            "buying_power": self.buying_power,
            "currency": self.currency,
            "is_paper": self.is_paper,
            "trading_blocked": self.trading_blocked,
            "pattern_day_trader": self.pattern_day_trader,
        }


class BrokerAdapter(ABC):
    """A broker connection. Translates and transmits; never decides."""

    name: str = "base"
    supports_bracket_orders: bool = False
    supports_fractional: bool = False

    def __init__(self, mode: ExecutionMode = ExecutionMode.BROKER_PAPER):
        self.mode = mode

    @property
    @abstractmethod
    def configured(self) -> bool:
        """True when credentials for this mode are present."""

    def credential_env_names(self) -> tuple[str, ...]:
        """Which environment variables this adapter reads, for *this* mode.

        Exists so a diagnostic can say "set ALPACA_PAPER_KEY_ID" rather than the
        useless "not configured". The adapter is the only thing that knows its
        own variable names, and each mode reads a different set — a duplicate
        list kept anywhere else drifts the moment a broker or a mode is added.

        **Names only. Never return values from here** — this output is printed.
        """
        return ()

    @abstractmethod
    def get_account(self) -> BrokerAccount:
        ...

    @abstractmethod
    def get_positions(self) -> list[BrokerPosition]:
        ...

    @abstractmethod
    def get_orders(self, open_only: bool = True) -> list[Order]:
        ...

    @abstractmethod
    def submit_order(self, request: OrderRequest) -> Order:
        ...

    @abstractmethod
    def cancel_order(self, broker_order_id: str) -> bool:
        ...

    @abstractmethod
    def cancel_all_orders(self) -> int:
        """Cancel every working order. Returns how many were cancelled."""

    @abstractmethod
    def close_position(self, symbol: str) -> Order | None:
        ...

    @abstractmethod
    def close_all_positions(self) -> list[Order]:
        """Flatten everything. Used by the kill switch."""

    def health(self) -> dict:
        """Connectivity and account state, for the UI banner and diagnostics."""
        if not self.configured:
            return {
                "broker": self.name,
                "mode": self.mode.value,
                "connected": False,
                "reason": f"No credentials configured for {self.name} in {self.mode.value} mode.",
            }
        try:
            account = self.get_account()
        except BrokerError as exc:
            return {
                "broker": self.name,
                "mode": self.mode.value,
                "connected": False,
                "reason": str(exc),
            }
        return {
            "broker": self.name,
            "mode": self.mode.value,
            "connected": True,
            "account": account.to_dict(),
        }
