"""Tradier broker adapter — US equities, long-only.

Second broker, chosen because its REST API needs only a bearer token (no
key/secret pair, no OAuth dance) and it offers a genuine sandbox with real
market behaviour, the same reason Alpaca was first: paper and live share one
API shape, so a sandbox-verified code path is evidence about the live one.

**This adapter has not been exercised against a real Tradier account from
this repository.** Alpaca's adapter carries the same caveat in
`docs/PROJECT_STATE.md` ("no order has been sent to Alpaca itself from here —
that needs credentials"); this one carries it more strongly, because Tradier's
wire format for multi-leg orders (below) was implemented from documented
shape, not verified against a live response. Every failure mode here is a
`BrokerError` surfaced up through the same guardrail path everything else
uses — a wrong field name fails the request loudly, it does not place a wrong
trade — but **run this against `TRADIER_PAPER_TOKEN` in sandbox mode and read
the resulting order records before ever pointing it at
`TRADIER_LIVE_TOKEN`.**

Two real differences from Alpaca that matter, both because Tradier's API is
shaped differently rather than through choices made here:

1. **Order submission is form-encoded (`application/x-www-form-urlencoded`),
   not JSON.** Sending JSON to Tradier's order endpoint is silently wrong in
   a way that would not be obvious from the interface alone.
2. **A protective stop is not attached with a simple field.** Tradier has no
   Alpaca-style single-call bracket; a stop rides along via its `otoco`/`oto`
   order classes, which take the legs as indexed form fields
   (`symbol[0]`, `side[0]`, `symbol[1]`, `side[1]`, ...). Implemented that way
   below so the entry and its stop are still one atomic submission, for the
   same reason the Alpaca adapter's docstring gives: a stop submitted as a
   *second, separate* call leaves a real window where the position is naked.

**Long-only.** Tradier distinguishes `sell` (closing a long) from
`sell_short` (opening a short) and `buy` from `buy_to_cover` — a distinction
`OrderSide` does not carry today. This adapter only ever sends `buy`/`sell`,
which is correct for long positions and fails closed for anything else:
Tradier rejects a `sell` against a symbol you don't hold long rather than
silently opening a short, so the wrong-side case surfaces as a refused order,
not a wrong trade — but shorting is simply not implemented here.

Account credentials come from the environment, never the database, for the
same reason as Alpaca: per-user broker keys would need encryption at rest
this codebase does not have.
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

logger = logging.getLogger("legend.execution.tradier")

SANDBOX_BASE = "https://sandbox.tradier.com/v1"
PRODUCTION_BASE = "https://api.tradier.com/v1"

_STATUS_MAP = {
    "pending": OrderStatus.PENDING,
    "open": OrderStatus.ACCEPTED,
    "accepted": OrderStatus.ACCEPTED,
    "partially_filled": OrderStatus.PARTIALLY_FILLED,
    "filled": OrderStatus.FILLED,
    "expired": OrderStatus.EXPIRED,
    "canceled": OrderStatus.CANCELLED,
    "cancelled": OrderStatus.CANCELLED,
    "rejected": OrderStatus.REJECTED,
    "error": OrderStatus.REJECTED,
    "calculated": OrderStatus.ACCEPTED,
    "pending_replace": OrderStatus.ACCEPTED,
    "replaced": OrderStatus.CANCELLED,
}

# Order types this adapter's OrderType maps onto Tradier's own vocabulary,
# which happens to use the same words for these four.
_TYPE_MAP = {
    OrderType.MARKET: "market",
    OrderType.LIMIT: "limit",
    OrderType.STOP: "stop",
    OrderType.STOP_LIMIT: "stop_limit",
}


class TradierAdapter(BrokerAdapter):
    name = "tradier"
    supports_bracket_orders = True
    supports_fractional = False

    def __init__(self, mode: ExecutionMode = ExecutionMode.BROKER_PAPER,
                 token: str | None = None, account_id: str | None = None):
        super().__init__(mode)

        # Separate env vars per mode, same reasoning as Alpaca: sharing one
        # pair makes it too easy to point live credentials at what you
        # believe is the sandbox.
        if mode is ExecutionMode.LIVE:
            self.token = token if token is not None else os.environ.get("TRADIER_LIVE_TOKEN", "")
            self.account_id = (
                account_id if account_id is not None
                else os.environ.get("TRADIER_LIVE_ACCOUNT_ID", "")
            )
            self.base_url = PRODUCTION_BASE
        else:
            self.token = token if token is not None else os.environ.get("TRADIER_PAPER_TOKEN", "")
            self.account_id = (
                account_id if account_id is not None
                else os.environ.get("TRADIER_PAPER_ACCOUNT_ID", "")
            )
            self.base_url = SANDBOX_BASE

    @property
    def configured(self) -> bool:
        return bool(self.token and self.account_id)

    @property
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }

    def _request(self, method: str, path: str, **kwargs) -> dict:
        if not self.configured:
            variables = (
                "TRADIER_LIVE_TOKEN / TRADIER_LIVE_ACCOUNT_ID"
                if self.mode is ExecutionMode.LIVE
                else "TRADIER_PAPER_TOKEN / TRADIER_PAPER_ACCOUNT_ID"
            )
            raise BrokerAuthError(f"Tradier credentials are not set. Expected {variables}.")

        url = f"{self.base_url}{path}"
        try:
            response = httpx.request(method, url, headers=self._headers, timeout=30, **kwargs)
        except httpx.HTTPError as exc:
            raise BrokerError(f"Tradier unreachable: {exc}") from exc

        if response.status_code in (401, 403):
            raise BrokerAuthError(
                f"Tradier rejected the credentials for {self.mode.value} mode "
                f"(HTTP {response.status_code}). Check the token matches the account."
            )
        if response.status_code >= 400:
            raise BrokerError(f"Tradier returned HTTP {response.status_code}: {response.text[:300]}")

        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise BrokerError(f"Tradier returned a non-JSON response: {response.text[:200]}") from exc

    # --- account and positions -------------------------------------------------

    def get_account(self) -> BrokerAccount:
        data = self._request("GET", f"/accounts/{self.account_id}/balances")
        balances = data.get("balances") if isinstance(data, dict) else None
        if not isinstance(balances, dict):
            raise BrokerError("Unexpected Tradier balances payload")

        # Cash and margin accounts nest buying power differently; a PDT
        # account nests it under "pdt" instead. Whichever is present wins,
        # falling back to total cash so this never raises on an unseen shape.
        sub = balances.get("margin") or balances.get("cash") or balances.get("pdt") or {}
        buying_power = (
            sub.get("stock_buying_power")
            or sub.get("cash_available")
            or balances.get("total_cash")
            or 0.0
        )

        return BrokerAccount(
            account_id=str(balances.get("account_number", self.account_id)),
            equity=float(balances.get("total_equity") or 0.0),
            cash=float(balances.get("total_cash") or 0.0),
            buying_power=float(buying_power or 0.0),
            currency="USD",
            is_paper=self.mode is not ExecutionMode.LIVE,
            trading_blocked=False,
            pattern_day_trader=bool(balances.get("account_type") == "pdt"),
            raw=balances,
        )

    def get_positions(self) -> list[BrokerPosition]:
        data = self._request("GET", f"/accounts/{self.account_id}/positions")
        rows = _unwrap(data, "positions", "position")
        positions = []
        for row in rows:
            quantity = float(row.get("quantity") or 0.0)
            cost_basis = float(row.get("cost_basis") or 0.0)
            positions.append(BrokerPosition(
                symbol=str(row.get("symbol", "")),
                quantity=quantity,
                average_entry_price=(cost_basis / quantity) if quantity else 0.0,
                raw=row,
            ))
        return positions

    # --- orders ------------------------------------------------------------------

    def get_orders(self, open_only: bool = True) -> list[Order]:
        data = self._request("GET", f"/accounts/{self.account_id}/orders")
        rows = _unwrap(data, "orders", "order")
        orders = [self._parse_order(row) for row in rows]
        if open_only:
            orders = [o for o in orders if o.status.is_working]
        return orders

    def submit_order(self, request: OrderRequest) -> Order:
        client_order_id = request.client_order_id or f"dex-{uuid.uuid4().hex[:20]}"
        order_type = _TYPE_MAP.get(request.order_type, "market")

        if request.stop_price is None:
            # Plain single-leg order — the common case for exits, which
            # legitimately carry no stop of their own.
            payload: dict = {
                "class": "equity",
                "symbol": request.symbol.upper(),
                "side": request.side.value,
                "quantity": str(request.quantity),
                "type": order_type,
                "duration": _duration(request),
                "tag": client_order_id,
            }
            if request.limit_price is not None:
                payload["price"] = str(request.limit_price)
        else:
            # Entry with a protective stop: one atomic multi-leg submission
            # (oto, or otoco with a take-profit leg too) rather than two
            # separate calls, so the position is never briefly unprotected.
            exit_side = "sell" if request.side is OrderSide.BUY else "buy"
            legs = [
                {"symbol": request.symbol.upper(), "side": request.side.value,
                 "quantity": str(request.quantity), "type": order_type,
                 "duration": _duration(request)},
                {"symbol": request.symbol.upper(), "side": exit_side,
                 "quantity": str(request.quantity), "type": "stop",
                 "duration": "gtc", "stop": str(request.stop_price)},
            ]
            if request.limit_price is not None:
                legs[0]["price"] = str(request.limit_price)

            if request.take_profit_price is not None:
                legs.append({
                    "symbol": request.symbol.upper(), "side": exit_side,
                    "quantity": str(request.quantity), "type": "limit",
                    "duration": "gtc", "price": str(request.take_profit_price),
                })
                order_class = "otoco"
            else:
                order_class = "oto"

            payload = {"class": order_class, "tag": client_order_id}
            for i, leg in enumerate(legs):
                for key, value in leg.items():
                    payload[f"{key}[{i}]"] = value

        logger.info(
            "submitting %s %s %s to tradier (%s)",
            request.side.value, request.quantity, request.symbol, self.mode.value,
        )
        data = self._request("POST", f"/accounts/{self.account_id}/orders", data=payload)
        order_data = data.get("order") if isinstance(data, dict) else None
        if not isinstance(order_data, dict) or not order_data.get("id"):
            raise BrokerError(f"Unexpected Tradier order response: {data!r}")

        # Multi-leg submission acknowledges the group, not each leg's own
        # detail — echo the request back rather than guessing at fields
        # Tradier didn't return, same as how a pending order looks before its
        # first status poll.
        return Order(
            broker_order_id=str(order_data["id"]),
            client_order_id=client_order_id,
            symbol=request.symbol.upper(),
            side=request.side,
            quantity=request.quantity,
            filled_quantity=0.0,
            status=OrderStatus.ACCEPTED if order_data.get("status") == "ok" else OrderStatus.PENDING,
            order_type=request.order_type,
            limit_price=request.limit_price,
            stop_price=request.stop_price,
            raw=order_data,
        )

    def cancel_order(self, broker_order_id: str) -> bool:
        try:
            self._request("DELETE", f"/accounts/{self.account_id}/orders/{broker_order_id}")
            return True
        except BrokerError as exc:
            logger.warning("could not cancel order %s: %s", broker_order_id, exc)
            return False

    def cancel_all_orders(self) -> int:
        # Tradier has no bulk-cancel endpoint — cancel each open order in turn.
        cancelled = 0
        for order in self.get_orders(open_only=True):
            if self.cancel_order(order.broker_order_id):
                cancelled += 1
        return cancelled

    def close_position(self, symbol: str) -> Order | None:
        position = next((p for p in self.get_positions() if p.symbol.upper() == symbol.upper()), None)
        if position is None or position.quantity == 0:
            return None
        if position.quantity < 0:
            # Shorts are outside this adapter's scope — see the module
            # docstring. Refusing loudly beats sending a mismapped side.
            raise BrokerError(
                f"{symbol} is short {abs(position.quantity)} shares; the Tradier adapter is "
                f"long-only and cannot close a short position."
            )
        return self.submit_order(OrderRequest(
            symbol=symbol, side=OrderSide.SELL, quantity=position.quantity,
            order_type=OrderType.MARKET, reason="close_position",
        ))

    def close_all_positions(self) -> list[Order]:
        # Cancel resting orders first: a stop or target leg left behind after
        # the position is gone can reopen it the moment it triggers — same
        # reasoning as Alpaca's cancel_orders=true on its bulk-close call.
        self.cancel_all_orders()
        orders = []
        for position in self.get_positions():
            if position.quantity == 0:
                continue
            try:
                order = self.close_position(position.symbol)
            except BrokerError as exc:
                logger.warning("could not close %s: %s", position.symbol, exc)
                continue
            if order is not None:
                orders.append(order)
        return orders

    # --- parsing -------------------------------------------------------------

    def _parse_order(self, row: dict) -> Order:
        raw_status = str(row.get("status", "")).lower()
        status = _STATUS_MAP.get(raw_status, OrderStatus.UNKNOWN)
        if status is OrderStatus.UNKNOWN and raw_status:
            logger.warning("unmapped Tradier order status %r — treating as unknown", raw_status)

        raw_side = str(row.get("side", "buy")).lower()
        side = OrderSide.SELL if raw_side in ("sell", "sell_short") else OrderSide.BUY

        try:
            order_type = OrderType(str(row.get("type", "market")).lower())
        except ValueError:
            order_type = OrderType.MARKET

        return Order(
            broker_order_id=str(row.get("id", "")),
            client_order_id=str(row.get("tag", "")),
            symbol=str(row.get("symbol", "")),
            side=side,
            quantity=float(row.get("quantity") or 0.0),
            filled_quantity=float(row.get("exec_quantity") or 0.0),
            status=status,
            order_type=order_type,
            limit_price=float(row["price"]) if row.get("price") else None,
            stop_price=float(row["stop_price"]) if row.get("stop_price") else None,
            average_fill_price=float(row["avg_fill_price"]) if row.get("avg_fill_price") else None,
            submitted_at=_parse_time(row.get("create_date")),
            filled_at=_parse_time(row.get("transaction_date")) if raw_status == "filled" else None,
            raw=row,
        )


def _duration(request: OrderRequest) -> str:
    return "gtc" if request.time_in_force.value in ("gtc", "fok", "ioc") else "day"


def _unwrap(data: dict, outer: str, inner: str) -> list[dict]:
    """Tradier wraps collections as `{outer: {inner: [...] | {...} | "null"}}`,
    returning the literal string `"null"` — not JSON null — for an empty one.
    """
    container = data.get(outer) if isinstance(data, dict) else None
    if not isinstance(container, dict):
        return []
    value = container.get(inner)
    if value is None or value == "null":
        return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    return []


def _parse_time(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None
