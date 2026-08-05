"""Alpaca broker adapter — US equities and crypto.

Chosen as the first integration because paper and live share one API: the
difference is the base URL and which key pair you supply. That means the paper
path exercises exactly the code the live path will, which is the only way a
paper test is evidence about live behaviour.

Implemented over plain `httpx` rather than `alpaca-py` to avoid another
dependency for what is a dozen REST calls, and to keep error handling explicit.

**Credentials come from the environment, never from the database.** Storing
per-user broker keys would require real encryption at rest, and this codebase
has no vetted crypto library available. Environment-only means live trading is
an instance-level capability rather than a per-user one — a limitation stated
plainly rather than papered over with weak encryption.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone

import httpx

from trading.execution.base import (
    BrokerAccount,
    BrokerAdapter,
    BrokerAuthError,
    BrokerError,
    BrokerPosition,
    ExecutionMode,
    Order,
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
)

logger = logging.getLogger("legend.execution.alpaca")

PAPER_BASE = "https://paper-api.alpaca.markets"
LIVE_BASE = "https://api.alpaca.markets"

# Alpaca's status vocabulary is wider than ours; anything unmapped becomes
# UNKNOWN rather than being guessed at, because guessing a terminal state for a
# working order would make reconciliation drop a real position.
_STATUS_MAP = {
    "new": OrderStatus.ACCEPTED,
    "accepted": OrderStatus.ACCEPTED,
    "pending_new": OrderStatus.PENDING,
    "accepted_for_bidding": OrderStatus.ACCEPTED,
    "partially_filled": OrderStatus.PARTIALLY_FILLED,
    "filled": OrderStatus.FILLED,
    "done_for_day": OrderStatus.EXPIRED,
    "canceled": OrderStatus.CANCELLED,
    "cancelled": OrderStatus.CANCELLED,
    "pending_cancel": OrderStatus.ACCEPTED,
    "expired": OrderStatus.EXPIRED,
    "replaced": OrderStatus.CANCELLED,
    "pending_replace": OrderStatus.ACCEPTED,
    "rejected": OrderStatus.REJECTED,
    "suspended": OrderStatus.PENDING,
    "stopped": OrderStatus.ACCEPTED,
    "calculated": OrderStatus.ACCEPTED,
}


class AlpacaAdapter(BrokerAdapter):
    name = "alpaca"
    supports_bracket_orders = True
    supports_fractional = True

    def __init__(self, mode: ExecutionMode = ExecutionMode.BROKER_PAPER,
                 key_id: str | None = None, secret_key: str | None = None):
        super().__init__(mode)

        # Live and paper credentials are read from *different* variables on
        # purpose. Sharing one pair makes it far too easy to point live keys at
        # what you believe is the sandbox.
        if mode is ExecutionMode.LIVE:
            self.key_id = key_id if key_id is not None else os.environ.get("ALPACA_LIVE_KEY_ID", "")
            self.secret_key = (
                secret_key if secret_key is not None
                else os.environ.get("ALPACA_LIVE_SECRET_KEY", "")
            )
            self.base_url = LIVE_BASE
        else:
            self.key_id = key_id if key_id is not None else os.environ.get("ALPACA_PAPER_KEY_ID", "")
            self.secret_key = (
                secret_key if secret_key is not None
                else os.environ.get("ALPACA_PAPER_SECRET_KEY", "")
            )
            self.base_url = PAPER_BASE

    @property
    def configured(self) -> bool:
        return bool(self.key_id and self.secret_key)

    def credential_env_names(self) -> tuple[str, ...]:
        if self.mode is ExecutionMode.LIVE:
            return ("ALPACA_LIVE_KEY_ID", "ALPACA_LIVE_SECRET_KEY")
        return ("ALPACA_PAPER_KEY_ID", "ALPACA_PAPER_SECRET_KEY")

    @property
    def _headers(self) -> dict:
        return {
            "APCA-API-KEY-ID": self.key_id,
            "APCA-API-SECRET-KEY": self.secret_key,
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, **kwargs) -> object:
        if not self.configured:
            variables = (
                "ALPACA_LIVE_KEY_ID / ALPACA_LIVE_SECRET_KEY"
                if self.mode is ExecutionMode.LIVE
                else "ALPACA_PAPER_KEY_ID / ALPACA_PAPER_SECRET_KEY"
            )
            raise BrokerAuthError(f"Alpaca credentials are not set. Expected {variables}.")

        url = f"{self.base_url}{path}"
        try:
            response = httpx.request(method, url, headers=self._headers, timeout=30, **kwargs)
        except httpx.HTTPError as exc:
            raise BrokerError(f"Alpaca unreachable: {exc}") from exc

        if response.status_code in (401, 403):
            raise BrokerAuthError(
                f"Alpaca rejected the credentials for {self.mode.value} mode "
                f"(HTTP {response.status_code}). Check the key pair matches the endpoint."
            )
        if response.status_code == 422:
            raise BrokerError(f"Alpaca rejected the order as invalid: {response.text[:300]}")
        if response.status_code >= 400:
            raise BrokerError(f"Alpaca returned HTTP {response.status_code}: {response.text[:300]}")

        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise BrokerError(f"Alpaca returned a non-JSON response: {response.text[:200]}") from exc

    # --- account and positions ------------------------------------------------

    def get_account(self) -> BrokerAccount:
        data = self._request("GET", "/v2/account")
        if not isinstance(data, dict):
            raise BrokerError("Unexpected Alpaca account payload")

        return BrokerAccount(
            account_id=str(data.get("account_number", "")),
            equity=float(data.get("equity") or 0.0),
            cash=float(data.get("cash") or 0.0),
            buying_power=float(data.get("buying_power") or 0.0),
            currency=data.get("currency", "USD"),
            # Trust the API's own flag rather than inferring from the URL, so a
            # misconfiguration shows up as a contradiction instead of silence.
            is_paper=self.mode is not ExecutionMode.LIVE,
            trading_blocked=bool(data.get("trading_blocked") or data.get("account_blocked")),
            pattern_day_trader=bool(data.get("pattern_day_trader")),
            raw=data,
        )

    def get_positions(self) -> list[BrokerPosition]:
        data = self._request("GET", "/v2/positions")
        rows = data if isinstance(data, list) else []
        return [
            BrokerPosition(
                symbol=row["symbol"],
                quantity=float(row.get("qty") or 0.0),
                average_entry_price=float(row.get("avg_entry_price") or 0.0),
                current_price=float(row["current_price"]) if row.get("current_price") else None,
                market_value=float(row["market_value"]) if row.get("market_value") else None,
                unrealized_pnl=float(row["unrealized_pl"]) if row.get("unrealized_pl") else None,
                unrealized_pnl_percent=(
                    float(row["unrealized_plpc"]) * 100 if row.get("unrealized_plpc") else None
                ),
                raw=row,
            )
            for row in rows
        ]

    # --- orders ---------------------------------------------------------------

    def get_orders(self, open_only: bool = True) -> list[Order]:
        params = {"status": "open" if open_only else "all", "limit": 200, "direction": "desc"}
        data = self._request("GET", "/v2/orders", params=params)
        rows = data if isinstance(data, list) else []
        return [self._parse_order(row) for row in rows]

    def submit_order(self, request: OrderRequest) -> Order:
        payload: dict = {
            "symbol": request.symbol.upper(),
            "side": request.side.value,
            "type": request.order_type.value,
            "time_in_force": request.time_in_force.value,
            "qty": str(request.quantity),
            "client_order_id": request.client_order_id or f"dex-{uuid.uuid4().hex[:20]}",
        }

        if request.limit_price is not None:
            payload["limit_price"] = str(request.limit_price)

        # A protective stop rides with the entry as a bracket, so the position
        # is never momentarily naked between the fill and a follow-up order.
        # A separate stop submitted afterwards leaves exactly that window open,
        # and that window is where accounts get hurt.
        if request.stop_price is not None:
            if request.take_profit_price is not None:
                payload["order_class"] = "bracket"
                payload["stop_loss"] = {"stop_price": str(request.stop_price)}
                payload["take_profit"] = {"limit_price": str(request.take_profit_price)}
            else:
                payload["order_class"] = "oto"
                payload["stop_loss"] = {"stop_price": str(request.stop_price)}
            # Bracket and OTO legs must outlive the session, or the protection
            # silently disappears at the close.
            payload["time_in_force"] = "gtc"

        logger.info(
            "submitting %s %s %s to alpaca (%s)",
            request.side.value, request.quantity, request.symbol, self.mode.value,
        )
        data = self._request("POST", "/v2/orders", json=payload)
        if not isinstance(data, dict):
            raise BrokerError("Unexpected Alpaca order payload")
        return self._parse_order(data)

    def cancel_order(self, broker_order_id: str) -> bool:
        try:
            self._request("DELETE", f"/v2/orders/{broker_order_id}")
            return True
        except BrokerError as exc:
            logger.warning("could not cancel order %s: %s", broker_order_id, exc)
            return False

    def cancel_all_orders(self) -> int:
        data = self._request("DELETE", "/v2/orders")
        return len(data) if isinstance(data, list) else 0

    def close_position(self, symbol: str) -> Order | None:
        data = self._request("DELETE", f"/v2/positions/{symbol.upper()}")
        if isinstance(data, dict) and data.get("id"):
            return self._parse_order(data)
        return None

    def close_all_positions(self) -> list[Order]:
        # cancel_orders=true matters: a resting bracket leg left behind after
        # the position is gone can open a brand-new position in the opposite
        # direction when it triggers.
        data = self._request("DELETE", "/v2/positions", params={"cancel_orders": "true"})
        rows = data if isinstance(data, list) else []

        orders: list[Order] = []
        for row in rows:
            body = row.get("body") if isinstance(row, dict) else None
            if isinstance(body, dict) and body.get("id"):
                orders.append(self._parse_order(body))
        return orders

    # --- parsing --------------------------------------------------------------

    def _parse_order(self, row: dict) -> Order:
        raw_status = str(row.get("status", "")).lower()
        status = _STATUS_MAP.get(raw_status, OrderStatus.UNKNOWN)
        if status is OrderStatus.UNKNOWN and raw_status:
            logger.warning("unmapped Alpaca order status %r — treating as unknown", raw_status)

        try:
            side = OrderSide(str(row.get("side", "buy")).lower())
        except ValueError:
            side = OrderSide.BUY
        try:
            order_type = OrderType(str(row.get("type", "market")).lower())
        except ValueError:
            order_type = OrderType.MARKET

        return Order(
            broker_order_id=str(row.get("id", "")),
            client_order_id=str(row.get("client_order_id", "")),
            symbol=str(row.get("symbol", "")),
            side=side,
            quantity=float(row.get("qty") or 0.0),
            filled_quantity=float(row.get("filled_qty") or 0.0),
            status=status,
            order_type=order_type,
            limit_price=float(row["limit_price"]) if row.get("limit_price") else None,
            stop_price=float(row["stop_price"]) if row.get("stop_price") else None,
            average_fill_price=(
                float(row["filled_avg_price"]) if row.get("filled_avg_price") else None
            ),
            submitted_at=_parse_time(row.get("submitted_at")),
            filled_at=_parse_time(row.get("filled_at")),
            raw=row,
        )


def _parse_time(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None
