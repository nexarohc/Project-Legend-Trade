"""trading.execution.tradier: the second broker adapter.

No test contacts the network — `httpx.request` is monkeypatched with a fake
that records what it was asked to do and returns a canned response, the same
pattern the rest of the suite uses to stub a market data provider or broker.
These tests prove the adapter's own request-building and response-parsing
logic is internally consistent; they cannot prove Tradier's real API matches
these response shapes exactly, which is why the module docstring calls this
adapter unverified against a live account.
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
from trading.execution.tradier import TradierAdapter


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

    def __call__(self, method, url, headers=None, timeout=None, **kwargs):
        self.calls.append({"method": method, "url": url, "headers": headers, **kwargs})
        return self.responses.pop(0)


@pytest.fixture()
def transport(monkeypatch):
    fake = FakeTransport()
    monkeypatch.setattr(httpx, "request", fake)
    return fake


def adapter(mode=ExecutionMode.BROKER_PAPER):
    return TradierAdapter(mode, token="sandbox-token", account_id="ACC123")


def test_not_configured_without_credentials():
    a = TradierAdapter(ExecutionMode.BROKER_PAPER, token="", account_id="")
    assert a.configured is False


def test_configured_with_both_token_and_account():
    assert adapter().configured is True


def test_uses_separate_credentials_for_live_mode():
    live = TradierAdapter(ExecutionMode.LIVE, token="live-token", account_id="LIVE1")
    assert live.base_url == "https://api.tradier.com/v1"
    paper = TradierAdapter(ExecutionMode.BROKER_PAPER, token="p", account_id="P1")
    assert paper.base_url == "https://sandbox.tradier.com/v1"


def test_unauthorized_response_raises_broker_auth_error(transport):
    transport.queue(FakeResponse(status_code=401, text="unauthorized"))
    with pytest.raises(BrokerAuthError):
        adapter().get_account()


def test_missing_credentials_raise_before_any_request(transport):
    a = TradierAdapter(ExecutionMode.BROKER_PAPER, token="", account_id="")
    with pytest.raises(BrokerAuthError):
        a.get_account()
    assert transport.calls == []


def test_get_account_parses_margin_balances(transport):
    transport.queue(FakeResponse(json_data={
        "balances": {
            "account_number": "ACC123",
            "total_equity": 50000.0,
            "total_cash": 10000.0,
            "margin": {"stock_buying_power": 20000.0},
        },
    }))
    account = adapter().get_account()
    assert account.equity == 50000.0
    assert account.cash == 10000.0
    assert account.buying_power == 20000.0
    assert account.is_paper is True


def test_get_account_parses_cash_account_balances(transport):
    transport.queue(FakeResponse(json_data={
        "balances": {
            "account_number": "ACC123",
            "total_equity": 5000.0,
            "total_cash": 5000.0,
            "cash": {"cash_available": 4500.0},
        },
    }))
    account = adapter().get_account()
    assert account.buying_power == 4500.0


def test_get_account_falls_back_to_total_cash_for_an_unseen_shape(transport):
    transport.queue(FakeResponse(json_data={
        "balances": {"account_number": "ACC123", "total_equity": 100.0, "total_cash": 100.0},
    }))
    account = adapter().get_account()
    assert account.buying_power == 100.0


def test_get_positions_handles_the_null_string_quirk(transport):
    transport.queue(FakeResponse(json_data={"positions": "null"}))
    assert adapter().get_positions() == []


def test_get_positions_handles_a_single_position_as_a_bare_dict(transport):
    transport.queue(FakeResponse(json_data={
        "positions": {"position": {"symbol": "AAPL", "quantity": 10, "cost_basis": 1500.0}},
    }))
    positions = adapter().get_positions()
    assert len(positions) == 1
    assert positions[0].symbol == "AAPL"
    assert positions[0].average_entry_price == 150.0


def test_get_positions_handles_a_list_of_positions(transport):
    transport.queue(FakeResponse(json_data={
        "positions": {"position": [
            {"symbol": "AAPL", "quantity": 10, "cost_basis": 1500.0},
            {"symbol": "MSFT", "quantity": 5, "cost_basis": 1000.0},
        ]},
    }))
    positions = adapter().get_positions()
    assert {p.symbol for p in positions} == {"AAPL", "MSFT"}


def test_get_orders_filters_to_open_by_default(transport):
    transport.queue(FakeResponse(json_data={
        "orders": {"order": [
            {"id": 1, "symbol": "AAPL", "side": "buy", "quantity": 10, "type": "market", "status": "open"},
            {"id": 2, "symbol": "AAPL", "side": "buy", "quantity": 10, "type": "market", "status": "filled"},
        ]},
    }))
    open_orders = adapter().get_orders(open_only=True)
    assert [o.broker_order_id for o in open_orders] == ["1"]


def test_get_orders_can_return_everything(transport):
    transport.queue(FakeResponse(json_data={
        "orders": {"order": [
            {"id": 1, "symbol": "AAPL", "side": "buy", "quantity": 10, "type": "market", "status": "open"},
            {"id": 2, "symbol": "AAPL", "side": "buy", "quantity": 10, "type": "market", "status": "filled"},
        ]},
    }))
    all_orders = adapter().get_orders(open_only=False)
    assert len(all_orders) == 2


def test_unmapped_status_becomes_unknown(transport):
    transport.queue(FakeResponse(json_data={
        "orders": {"order": {"id": 1, "symbol": "AAPL", "side": "buy", "quantity": 1,
                              "type": "market", "status": "some_new_status_tradier_added"}},
    }))
    [order] = adapter().get_orders(open_only=False)
    assert order.status.value == "unknown"


def test_submit_order_without_a_stop_is_a_single_form_encoded_leg(transport):
    transport.queue(FakeResponse(json_data={"order": {"id": 999, "status": "ok"}}))
    order = adapter().submit_order(OrderRequest(
        symbol="aapl", side=OrderSide.SELL, quantity=5, order_type=OrderType.MARKET,
    ))

    assert order.broker_order_id == "999"
    call = transport.calls[0]
    assert call["method"] == "POST"
    assert "json" not in call  # form-encoded, not JSON
    body = call["data"]
    assert body["class"] == "equity"
    assert body["symbol"] == "AAPL"
    assert body["side"] == "sell"
    assert body["quantity"] == "5"


def test_submit_order_with_a_stop_only_is_an_atomic_oto(transport):
    transport.queue(FakeResponse(json_data={"order": {"id": 1000, "status": "ok"}}))
    adapter().submit_order(OrderRequest(
        symbol="AAPL", side=OrderSide.BUY, quantity=10,
        order_type=OrderType.MARKET, stop_price=170.0,
    ))

    body = transport.calls[0]["data"]
    assert body["class"] == "oto"
    # Entry leg.
    assert body["symbol[0]"] == "AAPL"
    assert body["side[0]"] == "buy"
    assert body["type[0]"] == "market"
    # Stop leg exits the opposite side, GTC, carrying the stop price.
    assert body["symbol[1]"] == "AAPL"
    assert body["side[1]"] == "sell"
    assert body["type[1]"] == "stop"
    assert body["stop[1]"] == "170.0"
    assert body["duration[1]"] == "gtc"


def test_submit_order_with_stop_and_target_is_an_atomic_otoco(transport):
    transport.queue(FakeResponse(json_data={"order": {"id": 1001, "status": "ok"}}))
    adapter().submit_order(OrderRequest(
        symbol="AAPL", side=OrderSide.BUY, quantity=10, order_type=OrderType.MARKET,
        stop_price=170.0, take_profit_price=220.0,
    ))

    body = transport.calls[0]["data"]
    assert body["class"] == "otoco"
    assert body["type[2]"] == "limit"
    assert body["price[2]"] == "220.0"
    assert body["side[2]"] == "sell"


def test_submit_order_raises_on_an_unexpected_response_shape(transport):
    transport.queue(FakeResponse(json_data={"unexpected": True}))
    with pytest.raises(BrokerError):
        adapter().submit_order(OrderRequest(
            symbol="AAPL", side=OrderSide.BUY, quantity=1, order_type=OrderType.MARKET,
        ))


def test_cancel_order_returns_true_on_success(transport):
    transport.queue(FakeResponse(json_data={"order": {"id": 1, "status": "ok"}}))
    assert adapter().cancel_order("1") is True


def test_cancel_order_returns_false_when_the_broker_refuses(transport):
    transport.queue(FakeResponse(status_code=400, text="cannot cancel a filled order"))
    assert adapter().cancel_order("1") is False


def test_cancel_all_orders_cancels_every_open_order(transport):
    (transport
        .queue(FakeResponse(json_data={"orders": {"order": [
            {"id": 1, "symbol": "AAPL", "side": "buy", "quantity": 1, "type": "market", "status": "open"},
            {"id": 2, "symbol": "MSFT", "side": "buy", "quantity": 1, "type": "market", "status": "open"},
        ]}}))
        .queue(FakeResponse(json_data={"order": {"id": 1, "status": "ok"}}))
        .queue(FakeResponse(json_data={"order": {"id": 2, "status": "ok"}})))

    assert adapter().cancel_all_orders() == 2


def test_close_position_sells_the_full_long_quantity(transport):
    (transport
        .queue(FakeResponse(json_data={"positions": {"position": {
            "symbol": "AAPL", "quantity": 10, "cost_basis": 1500.0,
        }}}))
        .queue(FakeResponse(json_data={"order": {"id": 5, "status": "ok"}})))

    order = adapter().close_position("AAPL")
    assert order.side == OrderSide.SELL
    assert order.quantity == 10


def test_close_position_returns_none_when_nothing_is_held(transport):
    transport.queue(FakeResponse(json_data={"positions": "null"}))
    assert adapter().close_position("AAPL") is None


def test_close_position_refuses_a_short_rather_than_mismap_the_side(transport):
    transport.queue(FakeResponse(json_data={"positions": {"position": {
        "symbol": "AAPL", "quantity": -10, "cost_basis": -1500.0,
    }}}))
    with pytest.raises(BrokerError, match="long-only"):
        adapter().close_position("AAPL")


def test_close_all_positions_cancels_orders_first_then_flattens(transport):
    (transport
        .queue(FakeResponse(json_data={"orders": {"order": "null"}}))     # cancel_all_orders' get_orders
        .queue(FakeResponse(json_data={"positions": {"position": [
            {"symbol": "AAPL", "quantity": 10, "cost_basis": 1500.0},
        ]}}))
        .queue(FakeResponse(json_data={"positions": {"position": {
            "symbol": "AAPL", "quantity": 10, "cost_basis": 1500.0,
        }}}))  # close_position's own lookup
        .queue(FakeResponse(json_data={"order": {"id": 9, "status": "ok"}})))

    orders = adapter().close_all_positions()
    assert len(orders) == 1
    assert orders[0].symbol == "AAPL"
