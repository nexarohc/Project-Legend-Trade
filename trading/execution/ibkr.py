"""Interactive Brokers broker adapter — Client Portal Web API.

**Read this before touching `IBKR_LIVE_ACCOUNT_ID`.** This adapter carries a
stronger version of the caveat `tradier.py` states for itself. Tradier's
wire format was implemented from IBKR's — sorry, Tradier's — own documented
API reference, read directly. This adapter's wire format was reconstructed
from third-party summaries and community client libraries found via web
search, because Interactive Brokers' own API reference pages returned
`403 Forbidden` when fetched directly during development. **Treat every
field name, status string and response shape below as meaningfully less
certain than Tradier's, and verify a real sandbox round trip — reading the
actual JSON IBKR returns — before ever pointing this at a live account.**

Three things make this fundamentally different from Alpaca and Tradier, not
just less certain:

1. **There is no bearer-token REST API.** IBKR's Client Portal Web API is
   served by the "Client Portal Gateway," a small local proxy process you
   must download and run yourself (`IBKR_GATEWAY_URL`, default
   `https://localhost:5000/v1/api`) and authenticate by opening it in a
   browser and logging in — this adapter cannot do that step, and there is
   no way for a plain API key to replace it. If the gateway is not running
   and authenticated, every call here fails with `BrokerAuthError`.
2. **The session times out.** IBKR expires an idle gateway session after
   roughly 5-6 minutes; any authenticated request resets that timer, but
   nothing in this app calls one automatically faster than the scheduled
   broker reconcile (`EXECUTION_RECONCILE_INTERVAL_SECONDS`, default 300s —
   right at the edge of the timeout). If you rely on IBKR, either lower that
   interval well below 300s or keep the gateway's own browser tab open.
   Nothing here runs IBKR's recommended `/tickle` keep-alive on its own.
3. **Orders are placed by numeric contract ID (`conid`), not by ticker
   symbol.** `_resolve_conid` looks one up via `/iserver/secdef/search` and
   picks the first equity match — a real, unverified assumption for tickers
   with ambiguous listings (dual-class shares, multiple exchanges).

Also unverified: the exact field names in `/portfolio/{id}/summary`'s
response (parsed defensively, several fallback keys tried); IBKR's order
"question/reply" flow, where placing an order can come back as a list of
confirmation prompts (e.g. "no market data subscription") that must be
approved via `POST /iserver/reply/{id}` before the order actually goes in —
handled here with a bounded retry loop, never exercised against a real
prompt; and the exact set of order-status strings IBKR returns (mapped
defensively, unmapped values become `OrderStatus.UNKNOWN` rather than
guessed at, same policy as Tradier).

Unlike Tradier, this adapter is **not long-only** — IBKR supports shorting
natively and `close_position` sends whichever side flattens the position,
long or short.
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

logger = logging.getLogger("legend.execution.ibkr")

DEFAULT_GATEWAY_URL = "https://localhost:5000/v1/api"

# IBKR's own order-status vocabulary (Client Portal Web API), mapped
# defensively — see the module docstring's confidence caveat.
_STATUS_MAP = {
    "pendingsubmit": OrderStatus.PENDING,
    "pendingcancel": OrderStatus.ACCEPTED,   # still working until cancel confirms
    "apipending": OrderStatus.PENDING,
    "presubmitted": OrderStatus.PENDING,
    "submitted": OrderStatus.ACCEPTED,
    "filled": OrderStatus.FILLED,
    "cancelled": OrderStatus.CANCELLED,
    "apicancelled": OrderStatus.CANCELLED,
    "inactive": OrderStatus.REJECTED,
}

_TYPE_TO_IBKR = {
    OrderType.MARKET: "MKT",
    OrderType.LIMIT: "LMT",
    OrderType.STOP: "STP",
    OrderType.STOP_LIMIT: "STOP_LIMIT",
}

_TYPE_FROM_IBKR = {v: k for k, v in _TYPE_TO_IBKR.items()}


class IBKRAdapter(BrokerAdapter):
    name = "ibkr"
    supports_bracket_orders = True
    supports_fractional = False

    def __init__(self, mode: ExecutionMode = ExecutionMode.BROKER_PAPER,
                 base_url: str | None = None, account_id: str | None = None):
        super().__init__(mode)

        # One gateway serves both paper and live — which account it's
        # actually authenticated as depends on which username was used to
        # log into the gateway in a browser, something this adapter cannot
        # control. These env vars only select which account_id our requests
        # address; a mismatch between them and the gateway's real session
        # surfaces as an IBKR error, not a wrong trade.
        self.base_url = base_url if base_url is not None else os.environ.get(
            "IBKR_GATEWAY_URL", DEFAULT_GATEWAY_URL
        )
        if mode is ExecutionMode.LIVE:
            self.account_id = (
                account_id if account_id is not None
                else os.environ.get("IBKR_LIVE_ACCOUNT_ID", "")
            )
        else:
            self.account_id = (
                account_id if account_id is not None
                else os.environ.get("IBKR_PAPER_ACCOUNT_ID", "")
            )
        # The gateway's default install uses a self-signed certificate.
        # Only disable verification for that documented default; if you've
        # put a real certificate in front of it, set this true.
        self.verify_ssl = os.environ.get("IBKR_VERIFY_SSL", "false").lower() == "true"

    @property
    def configured(self) -> bool:
        return bool(self.account_id)

    def _request(self, method: str, path: str, **kwargs) -> dict | list:
        if not self.configured:
            variable = "IBKR_LIVE_ACCOUNT_ID" if self.mode is ExecutionMode.LIVE else "IBKR_PAPER_ACCOUNT_ID"
            raise BrokerAuthError(f"IBKR account is not configured. Expected {variable}.")

        url = f"{self.base_url}{path}"
        try:
            response = httpx.request(
                method, url, timeout=30, verify=self.verify_ssl,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                **kwargs,
            )
        except httpx.HTTPError as exc:
            raise BrokerError(
                f"IBKR Client Portal Gateway unreachable at {self.base_url}: {exc}. "
                f"Is the gateway running and did you log in via browser?"
            ) from exc

        if response.status_code in (401, 403):
            raise BrokerAuthError(
                f"IBKR gateway session is not authenticated (HTTP {response.status_code}). "
                f"Log into {self.base_url.rsplit('/v1/api', 1)[0]} in a browser and try again."
            )
        if response.status_code >= 400:
            raise BrokerError(f"IBKR gateway returned HTTP {response.status_code}: {response.text[:300]}")

        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise BrokerError(f"IBKR gateway returned a non-JSON response: {response.text[:200]}") from exc

    def _resolve_conid(self, symbol: str) -> str:
        results = self._request("GET", "/iserver/secdef/search", params={"symbol": symbol.upper()})
        if not isinstance(results, list) or not results:
            raise BrokerError(f"IBKR could not resolve a contract for {symbol!r}.")
        # First equity ("STK") match — ambiguous for dual-listed or
        # multi-class tickers, see the module docstring.
        for row in results:
            if isinstance(row, dict) and row.get("conid"):
                return str(row["conid"])
        raise BrokerError(f"IBKR's symbol search for {symbol!r} returned no usable contract id.")

    # --- account and positions -------------------------------------------------

    def get_account(self) -> BrokerAccount:
        data = self._request("GET", f"/portfolio/{self.account_id}/summary")
        if not isinstance(data, dict):
            raise BrokerError("Unexpected IBKR account summary payload")

        def amount(key: str) -> float:
            value = data.get(key)
            if isinstance(value, dict):
                value = value.get("amount")
            try:
                return float(value or 0.0)
            except (TypeError, ValueError):
                return 0.0

        return BrokerAccount(
            account_id=self.account_id,
            equity=amount("netliquidation"),
            cash=amount("totalcashvalue"),
            buying_power=amount("buyingpower"),
            currency="USD",
            is_paper=self.mode is not ExecutionMode.LIVE,
            trading_blocked=False,
            pattern_day_trader=False,
            raw=data,
        )

    def get_positions(self) -> list[BrokerPosition]:
        data = self._request("GET", f"/portfolio/{self.account_id}/positions/0")
        rows = data if isinstance(data, list) else []
        positions = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            quantity = float(row.get("position") or 0.0)
            if quantity == 0:
                continue
            positions.append(BrokerPosition(
                symbol=str(row.get("ticker") or row.get("contractDesc") or ""),
                quantity=quantity,
                average_entry_price=float(row.get("avgCost") or 0.0),
                current_price=float(row["mktPrice"]) if row.get("mktPrice") is not None else None,
                market_value=float(row["mktValue"]) if row.get("mktValue") is not None else None,
                unrealized_pnl=float(row["unrealizedPnl"]) if row.get("unrealizedPnl") is not None else None,
                raw=row,
            ))
        return positions

    # --- orders ------------------------------------------------------------------

    def get_orders(self, open_only: bool = True) -> list[Order]:
        data = self._request("GET", "/iserver/account/orders")
        rows = data.get("orders") if isinstance(data, dict) else None
        orders = [self._parse_order(row) for row in (rows or []) if isinstance(row, dict)]
        if open_only:
            orders = [o for o in orders if o.status.is_working]
        return orders

    def submit_order(self, request: OrderRequest) -> Order:
        conid = self._resolve_conid(request.symbol)
        client_order_id = request.client_order_id or f"dex-{uuid.uuid4().hex[:20]}"
        order_type = _TYPE_TO_IBKR.get(request.order_type, "MKT")
        tif = "GTC" if request.time_in_force.value in ("gtc", "fok", "ioc") else "DAY"

        entry: dict = {
            "conid": int(conid),
            "orderType": order_type,
            "side": request.side.value.upper(),
            "quantity": request.quantity,
            "tif": tif,
            "cOID": client_order_id,
        }
        if request.limit_price is not None:
            entry["price"] = request.limit_price

        orders = [entry]
        if request.stop_price is not None:
            # A bracket: child orders reference the parent's client order id
            # via parentId, the documented (never verified) IBKR pattern for
            # linking an entry to its protective exit in one submission — the
            # same reasoning as Tradier's oto/otoco: a stop sent as a second,
            # later call leaves a real window where the position is naked.
            exit_side = "SELL" if request.side is OrderSide.BUY else "BUY"
            stop_child_id = f"{client_order_id}-stop"
            orders.append({
                "conid": int(conid), "orderType": "STP", "side": exit_side,
                "quantity": request.quantity, "tif": "GTC",
                "auxPrice": request.stop_price,
                "cOID": stop_child_id, "parentId": client_order_id,
            })
            if request.take_profit_price is not None:
                orders.append({
                    "conid": int(conid), "orderType": "LMT", "side": exit_side,
                    "quantity": request.quantity, "tif": "GTC",
                    "price": request.take_profit_price,
                    "cOID": f"{client_order_id}-tp", "parentId": client_order_id,
                })

        logger.info(
            "submitting %s %s %s to ibkr (%s)",
            request.side.value, request.quantity, request.symbol, self.mode.value,
        )
        data = self._request("POST", f"/iserver/account/{self.account_id}/orders", json={"orders": orders})
        data = self._confirm_questions(data)

        rows = data if isinstance(data, list) else []
        placed = next((r for r in rows if isinstance(r, dict) and r.get("order_id")), None)
        if placed is None:
            raise BrokerError(f"Unexpected IBKR order response: {data!r}")

        return Order(
            broker_order_id=str(placed["order_id"]),
            client_order_id=client_order_id,
            symbol=request.symbol.upper(),
            side=request.side,
            quantity=request.quantity,
            filled_quantity=0.0,
            status=OrderStatus.PENDING,
            order_type=request.order_type,
            limit_price=request.limit_price,
            stop_price=request.stop_price,
            raw=placed,
        )

    def _confirm_questions(self, data, max_rounds: int = 5):
        """IBKR can return a list of confirmation prompts instead of placed
        orders (e.g. a market-data or price-cap warning) that must be
        approved via `/iserver/reply/{id}` before the order actually goes
        in. Never exercised against a real prompt — see the module
        docstring. Bounded so a genuinely stuck confirmation loop fails
        loudly instead of hanging."""
        rounds = 0
        while isinstance(data, list) and data and all(
            isinstance(r, dict) and "id" in r and "order_id" not in r for r in data
        ):
            if rounds >= max_rounds:
                raise BrokerError("IBKR order confirmation loop did not resolve after several replies.")
            reply_id = data[0]["id"]
            data = self._request("POST", f"/iserver/reply/{reply_id}", json={"confirmed": True})
            rounds += 1
        return data

    def cancel_order(self, broker_order_id: str) -> bool:
        try:
            self._request("DELETE", f"/iserver/account/{self.account_id}/order/{broker_order_id}")
            return True
        except BrokerError as exc:
            logger.warning("could not cancel IBKR order %s: %s", broker_order_id, exc)
            return False

    def cancel_all_orders(self) -> int:
        # No documented bulk-cancel endpoint — cancel each open order in turn,
        # same approach as Tradier.
        cancelled = 0
        for order in self.get_orders(open_only=True):
            if self.cancel_order(order.broker_order_id):
                cancelled += 1
        return cancelled

    def close_position(self, symbol: str) -> Order | None:
        position = next((p for p in self.get_positions() if p.symbol.upper() == symbol.upper()), None)
        if position is None or position.quantity == 0:
            return None
        # Unlike Tradier, IBKR supports shorting natively, so both
        # directions flatten the same way: send the opposite side.
        side = OrderSide.SELL if position.quantity > 0 else OrderSide.BUY
        return self.submit_order(OrderRequest(
            symbol=symbol, side=side, quantity=abs(position.quantity),
            order_type=OrderType.MARKET, reason="close_position",
        ))

    def close_all_positions(self) -> list[Order]:
        self.cancel_all_orders()
        orders = []
        for position in self.get_positions():
            if position.quantity == 0:
                continue
            try:
                order = self.close_position(position.symbol)
            except BrokerError as exc:
                logger.warning("could not close IBKR position %s: %s", position.symbol, exc)
                continue
            if order is not None:
                orders.append(order)
        return orders

    # --- parsing -------------------------------------------------------------

    def _parse_order(self, row: dict) -> Order:
        raw_status = str(row.get("status", "")).lower()
        status = _STATUS_MAP.get(raw_status, OrderStatus.UNKNOWN)
        if status is OrderStatus.UNKNOWN and raw_status:
            logger.warning("unmapped IBKR order status %r — treating as unknown", raw_status)

        raw_side = str(row.get("side", "BUY")).upper()
        side = OrderSide.SELL if raw_side == "SELL" else OrderSide.BUY
        order_type = _TYPE_FROM_IBKR.get(str(row.get("orderType", "MKT")).upper(), OrderType.MARKET)

        return Order(
            broker_order_id=str(row.get("orderId", "")),
            client_order_id=str(row.get("cOID", "") or row.get("order_ccp_status", "")),
            symbol=str(row.get("ticker", "")),
            side=side,
            quantity=float(row.get("totalSize") or row.get("remainingQuantity") or 0.0),
            filled_quantity=float(row.get("filledQuantity") or 0.0),
            status=status,
            order_type=order_type,
            limit_price=float(row["price"]) if row.get("price") is not None else None,
            stop_price=float(row["auxPrice"]) if row.get("auxPrice") is not None else None,
            average_fill_price=float(row["avgPrice"]) if row.get("avgPrice") is not None else None,
            submitted_at=_parse_time(row.get("lastExecutionTime_r")),
            filled_at=_parse_time(row.get("lastExecutionTime_r")) if raw_status == "filled" else None,
            raw=row,
        )


def _parse_time(value) -> datetime | None:
    if not value:
        return None
    try:
        # IBKR reports several timestamp shapes across endpoints; the "_r"
        # (raw) suffix on some fields is epoch milliseconds.
        return datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc)
    except (TypeError, ValueError):
        return None
