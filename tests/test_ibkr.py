"""trading.execution.ibkr: the third broker adapter.

Same pattern as test_tradier.py: `httpx.request` is monkeypatched with a fake
that records what it was asked to do and returns a canned response. These
tests prove the adapter's own request-building and response-parsing logic is
internally consistent; they cannot prove IBKR's real Client Portal Gateway
matches these response shapes, and the module docstring calls this adapter's
wire format meaningfully less certain than Tradier's — it was reconstructed
from web search summaries, not read from IBKR's own docs (which 403'd).
"""
import httpx
import pytest

from trading.execution.base import (
    BrokerAuthError,
    BrokerError,
    ExecutionMode,
    OrderRequest,
    OrderSide,
    OrderType,
)
from trading.execution.ibkr import IBKRAdapter


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text or (str(json_data) if json_data is not None else "")
        self.content = b"1" if json_data is not None or text else b""

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


class FakeTransport:
    """Records every call and returns queued responses in order."""

    def __init__(self):
        self.calls = []
        self.responses = []

    def queue(self, response: FakeResponse):
        self.responses.append(response)
        return self

    def __call__(self, method, url, headers=None, timeout=None, verify=None, **kwargs):
        self.calls.append({"method": method, "url": url, "headers": headers, **kwargs})
        return self.responses.pop(0)


@pytest.fixture()
def transport(monkeypatch):
    fake = FakeTransport()
    monkeypatch.setattr(httpx, "request", fake)
    return fake


def adapter(mode=ExecutionMode.BROKER_PAPER):
    return IBKRAdapter(mode, base_url="https://localhost:5000/v1/api", account_id="DU123456")


def _queue_conid(transport, conid=265598):
    transport.queue(FakeResponse(json_data=[{"conid": conid, "symbol": "AAPL"}]))


def test_not_configured_without_an_account_id():
    a = IBKRAdapter(ExecutionMode.BROKER_PAPER, account_id="")
    assert a.configured is False


def test_configured_with_an_account_id():
    assert adapter().configured is True


def test_uses_separate_account_ids_for_live_mode():
    live = IBKRAdapter(ExecutionMode.LIVE, account_id="U7654321")
    assert live.account_id == "U7654321"
    paper = IBKRAdapter(ExecutionMode.BROKER_PAPER, account_id="DU123456")
    assert paper.account_id == "DU123456"


def test_missing_account_id_raises_before_any_request(transport):
    a = IBKRAdapter(ExecutionMode.BROKER_PAPER, account_id="")
    with pytest.raises(BrokerAuthError):
        a.get_account()
    assert transport.calls == []


def test_unauthenticated_session_raises_broker_auth_error(transport):
    transport.queue(FakeResponse(status_code=401, text="not authenticated"))
    with pytest.raises(BrokerAuthError):
        adapter().get_account()


def test_get_account_parses_summary_amounts(transport):
    transport.queue(FakeResponse(json_data={
        "netliquidation": {"amount": 50000.0},
        "totalcashvalue": {"amount": 10000.0},
        "buyingpower": {"amount": 20000.0},
    }))
    account = adapter().get_account()
    assert account.equity == 50000.0
    assert account.cash == 10000.0
    assert account.buying_power == 20000.0
    assert account.is_paper is True


def test_get_account_handles_plain_numeric_fields_too(transport):
    # Defensive parsing: some gateway versions may not wrap every field in
    # {"amount": ...}. Never confirmed against a real gateway.
    transport.queue(FakeResponse(json_data={
        "netliquidation": 1000.0, "totalcashvalue": 500.0, "buyingpower": 900.0,
    }))
    account = adapter().get_account()
    assert account.equity == 1000.0


def test_get_positions_filters_out_zero_quantity_rows(transport):
    transport.queue(FakeResponse(json_data=[
        {"ticker": "AAPL", "position": 10, "avgCost": 150.0},
        {"ticker": "MSFT", "position": 0, "avgCost": 0.0},
    ]))
    positions = adapter().get_positions()
    assert [p.symbol for p in positions] == ["AAPL"]
    assert positions[0].average_entry_price == 150.0


def test_get_positions_handles_a_short(transport):
    transport.queue(FakeResponse(json_data=[
        {"ticker": "TSLA", "position": -5, "avgCost": 200.0},
    ]))
    [position] = adapter().get_positions()
    assert position.side == "short"
    assert position.quantity == -5


def test_get_orders_filters_to_open_by_default(transport):
    transport.queue(FakeResponse(json_data={"orders": [
        {"orderId": "1", "ticker": "AAPL", "side": "BUY", "totalSize": 10,
         "orderType": "MKT", "status": "Submitted"},
        {"orderId": "2", "ticker": "AAPL", "side": "BUY", "totalSize": 10,
         "orderType": "MKT", "status": "Filled"},
    ]}))
    open_orders = adapter().get_orders(open_only=True)
    assert [o.broker_order_id for o in open_orders] == ["1"]


def test_get_orders_can_return_everything(transport):
    transport.queue(FakeResponse(json_data={"orders": [
        {"orderId": "1", "ticker": "AAPL", "side": "BUY", "totalSize": 10,
         "orderType": "MKT", "status": "Submitted"},
        {"orderId": "2", "ticker": "AAPL", "side": "BUY", "totalSize": 10,
         "orderType": "MKT", "status": "Filled"},
    ]}))
    assert len(adapter().get_orders(open_only=False)) == 2


def test_unmapped_status_becomes_unknown(transport):
    transport.queue(FakeResponse(json_data={"orders": [
        {"orderId": "1", "ticker": "AAPL", "side": "BUY", "totalSize": 1,
         "orderType": "MKT", "status": "SomeNewStatusIBKRAdded"},
    ]}))
    [order] = adapter().get_orders(open_only=False)
    assert order.status.value == "unknown"


def test_submit_order_without_a_stop_resolves_conid_and_sends_one_order(transport):
    _queue_conid(transport)
    transport.queue(FakeResponse(json_data=[{"order_id": "999"}]))

    order = adapter().submit_order(OrderRequest(
        symbol="aapl", side=OrderSide.SELL, quantity=5, order_type=OrderType.MARKET,
    ))

    assert order.broker_order_id == "999"
    # First call resolves the contract id.
    assert "/iserver/secdef/search" in transport.calls[0]["url"]
    # Second call places the order against that conid.
    body = transport.calls[1]["json"]
    assert body["orders"][0]["conid"] == 265598
    assert body["orders"][0]["side"] == "SELL"
    assert body["orders"][0]["quantity"] == 5
    assert len(body["orders"]) == 1  # no stop given, no bracket children


def test_submit_order_with_a_stop_sends_a_bracket(transport):
    _queue_conid(transport)
    transport.queue(FakeResponse(json_data=[{"order_id": "1000"}]))

    adapter().submit_order(OrderRequest(
        symbol="AAPL", side=OrderSide.BUY, quantity=10,
        order_type=OrderType.MARKET, stop_price=170.0,
    ))

    body = transport.calls[1]["json"]
    assert len(body["orders"]) == 2
    entry, stop = body["orders"]
    assert entry["side"] == "BUY"
    assert stop["side"] == "SELL"
    assert stop["orderType"] == "STP"
    assert stop["auxPrice"] == 170.0
    assert stop["parentId"] == entry["cOID"]


def test_submit_order_with_stop_and_target_sends_three_legs(transport):
    _queue_conid(transport)
    transport.queue(FakeResponse(json_data=[{"order_id": "1001"}]))

    adapter().submit_order(OrderRequest(
        symbol="AAPL", side=OrderSide.BUY, quantity=10, order_type=OrderType.MARKET,
        stop_price=170.0, take_profit_price=220.0,
    ))

    body = transport.calls[1]["json"]
    assert len(body["orders"]) == 3
    take_profit = body["orders"][2]
    assert take_profit["orderType"] == "LMT"
    assert take_profit["price"] == 220.0
    assert take_profit["side"] == "SELL"


def test_submit_order_confirms_a_question_before_returning(transport):
    _queue_conid(transport)
    # First response is a confirmation prompt, not a placed order.
    transport.queue(FakeResponse(json_data=[{"id": "q1", "message": ["price cap warning"]}]))
    transport.queue(FakeResponse(json_data=[{"order_id": "2000"}]))

    order = adapter().submit_order(OrderRequest(
        symbol="AAPL", side=OrderSide.BUY, quantity=1, order_type=OrderType.MARKET,
    ))

    assert order.broker_order_id == "2000"
    # conid lookup, initial order post, reply confirmation = 3 calls.
    assert len(transport.calls) == 3
    assert "/iserver/reply/q1" in transport.calls[2]["url"]


def test_submit_order_raises_if_confirmation_loop_never_resolves(transport):
    _queue_conid(transport)
    for _ in range(6):
        transport.queue(FakeResponse(json_data=[{"id": "q1", "message": ["still asking"]}]))

    with pytest.raises(BrokerError, match="confirmation loop"):
        adapter().submit_order(OrderRequest(
            symbol="AAPL", side=OrderSide.BUY, quantity=1, order_type=OrderType.MARKET,
        ))


def test_submit_order_raises_when_symbol_cannot_be_resolved(transport):
    transport.queue(FakeResponse(json_data=[]))
    with pytest.raises(BrokerError, match="could not resolve"):
        adapter().submit_order(OrderRequest(
            symbol="NOTREAL", side=OrderSide.BUY, quantity=1, order_type=OrderType.MARKET,
        ))


def test_cancel_order_returns_true_on_success(transport):
    transport.queue(FakeResponse(json_data={"msg": "cancelled"}))
    assert adapter().cancel_order("1") is True


def test_cancel_order_returns_false_when_the_gateway_refuses(transport):
    transport.queue(FakeResponse(status_code=400, text="cannot cancel a filled order"))
    assert adapter().cancel_order("1") is False


def test_cancel_all_orders_cancels_every_open_order(transport):
    (transport
        .queue(FakeResponse(json_data={"orders": [
            {"orderId": "1", "ticker": "AAPL", "side": "BUY", "totalSize": 1,
             "orderType": "MKT", "status": "Submitted"},
            {"orderId": "2", "ticker": "MSFT", "side": "BUY", "totalSize": 1,
             "orderType": "MKT", "status": "Submitted"},
        ]}))
        .queue(FakeResponse(json_data={"msg": "cancelled"}))
        .queue(FakeResponse(json_data={"msg": "cancelled"})))

    assert adapter().cancel_all_orders() == 2


def test_close_position_sells_a_long(transport):
    (transport
        .queue(FakeResponse(json_data=[{"ticker": "AAPL", "position": 10, "avgCost": 150.0}]))
        .queue(FakeResponse(json_data=[{"conid": 265598, "symbol": "AAPL"}]))
        .queue(FakeResponse(json_data=[{"order_id": "5"}])))

    order = adapter().close_position("AAPL")
    assert order.side == OrderSide.SELL
    assert order.quantity == 10


def test_close_position_buys_back_a_short(transport):
    """Unlike Tradier, IBKR is not long-only — closing a short buys it back
    rather than refusing."""
    (transport
        .queue(FakeResponse(json_data=[{"ticker": "TSLA", "position": -5, "avgCost": 200.0}]))
        .queue(FakeResponse(json_data=[{"conid": 76792991, "symbol": "TSLA"}]))
        .queue(FakeResponse(json_data=[{"order_id": "6"}])))

    order = adapter().close_position("TSLA")
    assert order.side == OrderSide.BUY
    assert order.quantity == 5


def test_close_position_returns_none_when_nothing_is_held(transport):
    transport.queue(FakeResponse(json_data=[]))
    assert adapter().close_position("AAPL") is None


def test_close_all_positions_cancels_orders_first_then_flattens(transport):
    (transport
        .queue(FakeResponse(json_data={"orders": []}))  # cancel_all_orders' get_orders
        .queue(FakeResponse(json_data=[{"ticker": "AAPL", "position": 10, "avgCost": 150.0}]))
        .queue(FakeResponse(json_data=[{"ticker": "AAPL", "position": 10, "avgCost": 150.0}]))  # close_position lookup
        .queue(FakeResponse(json_data=[{"conid": 265598, "symbol": "AAPL"}]))
        .queue(FakeResponse(json_data=[{"order_id": "9"}])))

    orders = adapter().close_all_positions()
    assert len(orders) == 1
    assert orders[0].symbol == "AAPL"
