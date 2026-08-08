"""The market WebSocket stream, focused on the `snapshot.live` field.

`live` tells the client whether the candle frames that follow are a real
upstream push or a 15-second REST poll relayed over the same socket — see the
docstring in backend/app/routers/market.py. Everything else about the
protocol (snapshot seeding, subscribe/unsubscribe, reconnection) is exercised
end-to-end in the browser per docs/PROJECT_STATE.md; this file only covers
what a plain HTTP test can assert without a real upstream connection.
"""
from tests.test_trading_api import StubProvider, client  # noqa: F401 - shared fixture


class StreamingStubProvider(StubProvider):
    """Same as StubProvider, but declares a real streaming capability — the
    fixture below registers it as the real "stub" name so
    get_provider("stub") in the router resolves it instead of raising."""

    supports_streaming = True


def test_snapshot_reports_live_false_for_a_non_streaming_provider(client):
    with client.websocket_connect("/market/ws") as ws:
        ws.send_json({"action": "subscribe", "symbol": "BTCUSDT", "timeframe": "1h"})
        snapshot = ws.receive_json()
        assert snapshot["type"] == "snapshot"
        # StubProvider.supports_streaming is False, and its name ("stub") isn't
        # a registered factory either — both paths land on the same honest
        # answer: this is not a real-time push.
        assert snapshot["live"] is False


def test_snapshot_reports_live_true_for_a_registered_streaming_provider(monkeypatch, client):
    import trading.providers as providers_module
    import app.routers.market as market_router

    streaming = StreamingStubProvider()
    streaming.name = "stub"

    # The router's own `get_provider` (imported by name, not patchable via the
    # providers module) is what actually decides `live` — patch that directly
    # rather than the module it was imported from.
    monkeypatch.setattr(market_router, "get_provider", lambda name=None: streaming)

    with client.websocket_connect("/market/ws") as ws:
        ws.send_json({"action": "subscribe", "symbol": "BTCUSDT", "timeframe": "1h"})
        snapshot = ws.receive_json()
        assert snapshot["type"] == "snapshot"
        assert snapshot["live"] is True


def test_snapshot_fails_closed_when_the_provider_name_is_unrecognised(client):
    """A Series whose .provider isn't a registered factory name (only possible
    via a test stub or a genuinely unexpected state) must not crash the
    subscribe — it should read as "not streaming", not raise."""
    with client.websocket_connect("/market/ws") as ws:
        ws.send_json({"action": "subscribe", "symbol": "AAPL", "timeframe": "1d"})
        snapshot = ws.receive_json()
        assert snapshot["type"] == "snapshot"
        assert snapshot["live"] is False


def test_error_frames_carry_the_timeframe_they_belong_to(client, monkeypatch):
    """A frame the client cannot route is a frame the user never sees.

    The browser dispatches every stream message by `SYMBOL:TIMEFRAME`. An error
    frame that omitted the timeframe keyed to "NUVOCO:" while the subscriber was
    registered under "NUVOCO:1h", so no handler matched and the failure was
    dropped on the floor — the chart sat on "Loading…" indefinitely while the
    real reason was displayed in a different panel. The timeframe is routing
    information here, not decoration.
    """
    from trading.models import MarketDataError

    def refuse(*args, **kwargs):
        raise MarketDataError("no provider could serve this")

    import app.routers.market as market_router
    monkeypatch.setattr(market_router.market_service, "candles", refuse)

    with client.websocket_connect("/market/ws") as ws:
        ws.send_json({"action": "subscribe", "symbol": "NUVOCO", "timeframe": "1h"})
        message = ws.receive_json()

    assert message["type"] == "error"
    assert message["symbol"] == "NUVOCO"
    assert message["timeframe"] == "1h", (
        "error frames must name their timeframe or the browser cannot route "
        "them to the panel that is waiting on that subscription"
    )
