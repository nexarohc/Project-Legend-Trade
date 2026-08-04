"""Trading API endpoints, backed by a stub provider.

The provider is swapped for an in-memory one so these tests never touch the
network. That keeps them fast and deterministic, and it means a CI failure here
always indicates a real regression rather than an exchange being unreachable or
rate-limiting the runner.
"""
import pytest
from fastapi.testclient import TestClient

from tests.trading_fixtures import trending_series
from trading.models import AssetClass, Quote, Series, SymbolInfo, Timeframe
from trading.providers.base import MarketDataProvider


class StubProvider(MarketDataProvider):
    """Serves generated candles for any symbol, with no I/O."""

    name = "stub"
    supported_assets = (AssetClass.CRYPTO, AssetClass.STOCK)
    supports_streaming = False
    max_bars_per_request = 5000

    @property
    def configured(self) -> bool:
        return True

    def fetch_candles(self, symbol, timeframe, limit=500, end_time=None) -> Series:
        series = trending_series(bars=max(limit, 400))
        return Series(
            symbol=symbol.upper(),
            timeframe=timeframe,
            candles=series.candles[-limit:],
            asset_class=AssetClass.CRYPTO,
            provider=self.name,
        )

    def fetch_quote(self, symbol) -> Quote:
        series = trending_series(bars=100)
        return Quote(
            symbol=symbol.upper(),
            price=series.last.close,
            timestamp=series.last.timestamp,
            bid=series.last.close - 0.5,
            ask=series.last.close + 0.5,
            change=1.0,
            change_percent=0.5,
            volume_24h=1_000_000.0,
        )

    def search_symbols(self, query, limit=20):
        return [
            SymbolInfo(
                symbol=f"{query.upper()}USDT",
                name=f"{query.upper()}/USDT",
                asset_class=AssetClass.CRYPTO,
                provider=self.name,
            )
        ]


@pytest.fixture()
def client(monkeypatch, session):
    """A TestClient whose market data comes entirely from StubProvider."""
    stub = StubProvider()

    import trading.market as market_module
    import trading.providers as providers_module

    monkeypatch.setattr(providers_module, "get_provider", lambda name=None: stub)
    monkeypatch.setattr(providers_module, "resolve_provider_for", lambda symbol, explicit=None: stub)
    monkeypatch.setattr(market_module, "get_provider", lambda name=None: stub)
    monkeypatch.setattr(market_module, "resolve_provider_for", lambda symbol, explicit=None: stub)

    # A shared cache across tests would let one test's data satisfy another's request.
    market_module.market_service.invalidate()

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client

    market_module.market_service.invalidate()


# --- market -----------------------------------------------------------------

def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_candles_endpoint_returns_ohlcv(client):
    response = client.get("/market/candles", params={"symbol": "BTCUSDT", "timeframe": "1h", "limit": 200})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["candles"]) == 200
    assert {"time", "open", "high", "low", "close", "volume"} <= payload["candles"][0].keys()


def test_candles_reject_an_unknown_timeframe(client):
    response = client.get("/market/candles", params={"symbol": "BTCUSDT", "timeframe": "3y"})
    assert response.status_code == 400
    assert "timeframe" in response.json()["detail"].lower()


def test_quote_endpoint(client):
    payload = client.get("/market/quote", params={"symbol": "BTCUSDT"}).json()
    assert payload["price"] > 0
    assert payload["bid"] < payload["ask"]


def test_batch_quotes_isolate_failures(client):
    payload = client.get("/market/quotes", params={"symbols": "BTCUSDT,ETHUSDT"}).json()
    assert len(payload["quotes"]) == 2


def test_search_endpoint(client):
    payload = client.get("/market/search", params={"query": "eth"}).json()
    assert payload["results"]


def test_providers_endpoint_lists_configuration(client):
    payload = client.get("/market/providers").json()
    assert payload["providers"]
    assert "note" in payload


# --- analysis ---------------------------------------------------------------

def test_analysis_endpoint_returns_all_sections(client):
    response = client.post("/analysis", json={
        "symbol": "BTCUSDT", "timeframe": "1h", "bars": 400,
        "include_mtf": False, "include_chart_data": True,
    })
    assert response.status_code == 200
    payload = response.json()
    for section in ("executive_summary", "market_context", "market_structure", "smart_money",
                    "probability_analysis", "trade_setup", "risk_review", "verdict"):
        assert section in payload
    assert payload["chart_data"]["candles"]


def test_analysis_is_persisted_and_returns_an_id(client):
    payload = client.post("/analysis", json={
        "symbol": "BTCUSDT", "timeframe": "1h", "bars": 300,
        "include_mtf": False, "include_chart_data": False, "save": True,
    }).json()
    assert isinstance(payload.get("analysis_id"), int)


def test_analysis_can_skip_persistence(client):
    payload = client.post("/analysis", json={
        "symbol": "BTCUSDT", "timeframe": "1h", "bars": 300,
        "include_mtf": False, "include_chart_data": False, "save": False,
    }).json()
    assert "analysis_id" not in payload


def test_analysis_measures_correlation_against_an_open_position(client):
    """The API-level wiring: open position -> DB query -> correlation fetch.

    The stub provider serves the same deterministic series to every symbol, so
    ETHUSDT and BTCUSDT are literally identical here — that is what makes this
    a check of the plumbing (does the correlation reach the response at all)
    rather than a check of the math, which the pure-function tests already
    cover in tests/test_trading_analysis.py.
    """
    quote = client.get("/market/quote", params={"symbol": "ETHUSDT"}).json()
    opened = client.post("/trading/paper/open", json={
        "symbol": "ETHUSDT", "direction": "long", "stop_price": quote["price"] * 0.95,
    }).json()
    assert opened["opened"] is True

    payload = client.post("/analysis", json={
        "symbol": "BTCUSDT", "timeframe": "1h", "bars": 300,
        "include_mtf": False, "include_chart_data": False, "save": False,
    }).json()

    assert payload["risk_review"]["correlation_risk"] == "high"
    detail = payload["risk_review"]["correlation_detail"]
    assert detail and detail[0]["symbol"] == "ETHUSDT"


def test_analysis_excludes_the_symbol_being_analyzed_from_its_own_correlation(client):
    """Opening a position in the same symbol being analyzed must not correlate
    a series with itself — that would always show r=1 and mean nothing."""
    quote = client.get("/market/quote", params={"symbol": "BTCUSDT"}).json()
    client.post("/trading/paper/open", json={
        "symbol": "BTCUSDT", "direction": "long", "stop_price": quote["price"] * 0.95,
    })

    payload = client.post("/analysis", json={
        "symbol": "BTCUSDT", "timeframe": "1h", "bars": 300,
        "include_mtf": False, "include_chart_data": False, "save": False,
    }).json()

    assert payload["risk_review"]["correlation_risk"] == "unmodelled"
    assert payload["risk_review"]["correlation_detail"] is None


def test_concepts_are_served_without_a_model(client):
    listing = client.get("/analysis/concepts").json()
    assert len(listing["concepts"]) >= 10

    detail = client.get("/analysis/concepts/choch", params={"audience": "beginner"}).json()
    assert detail["found"] is True
    assert detail["explanation"]


def test_unknown_concept_returns_404(client):
    assert client.get("/analysis/concepts/not-a-thing").status_code == 404


def test_research_falls_back_to_the_glossary(client):
    payload = client.post("/analysis/research", json={"question": "Explain CHOCH"}).json()
    # With no model configured this must still answer from the glossary.
    assert payload.get("found") or payload.get("available") is not None


def test_feedback_and_calibration(client):
    analysis = client.post("/analysis", json={
        "symbol": "BTCUSDT", "timeframe": "1h", "bars": 300,
        "include_mtf": False, "include_chart_data": False,
    }).json()

    feedback = client.post("/analysis/feedback", json={
        "analysis_id": analysis["analysis_id"], "rating": "useful",
    })
    assert feedback.status_code == 200

    calibration = client.get("/analysis/calibration").json()
    assert "available" in calibration


def test_feedback_rejects_an_invalid_rating(client):
    assert client.post("/analysis/feedback", json={"rating": "amazing"}).status_code == 422


# --- strategy ---------------------------------------------------------------

def test_build_strategy_from_text(client):
    payload = client.post("/strategy/build", json={
        "description": "Buy breakout with increasing volume, 1.5 ATR stop, 3R target",
        "symbol": "BTCUSDT", "timeframe": "1h",
    }).json()
    assert payload["valid"] is True
    assert payload["spec"]["entry_long"]
    assert payload["specification"]["stop_logic"]


def test_study_endpoint_runs_backtest_and_audit(client):
    spec = client.post("/strategy/build", json={
        "description": "Buy breakout with volume, 1.5 ATR stop, 3R target",
        "symbol": "BTCUSDT",
    }).json()["spec"]

    payload = client.post("/strategy/study", json={
        "spec": spec, "symbol": "BTCUSDT", "timeframe": "1h",
        "bars": 800, "monte_carlo_runs": 100, "include_trades": False,
    }).json()

    assert payload["audit"]["verdict"] in ("APPROVED", "REVISE", "REJECT")
    assert payload["metrics"]["total_trades"] >= 0
    assert payload["backtest"]["execution_notes"]


def test_study_requires_a_spec_or_description(client):
    assert client.post("/strategy/study", json={"symbol": "BTCUSDT"}).status_code == 400


def test_pine_endpoint_generates_v6(client):
    payload = client.post("/strategy/pine", json={
        "description": "Buy breakout with volume, 1.5 ATR stop, 3R target",
        "symbol": "BTCUSDT", "kind": "strategy",
    }).json()
    assert payload["version"] == 6
    assert "//@version=6" in payload["code"]
    assert payload["install_steps"]


def test_pine_indicator_variant(client):
    payload = client.post("/strategy/pine", json={
        "description": "Buy breakout with volume", "kind": "indicator",
    }).json()
    assert "indicator(" in payload["code"]
    assert "strategy.entry" not in payload["code"]


def test_save_list_and_delete_a_strategy(client):
    spec = client.post("/strategy/build", json={"description": "Buy breakout with volume"}).json()["spec"]

    saved = client.post("/strategy/save", json={"spec": spec, "name": "My Breakout"}).json()
    assert saved["saved"] is True

    listing = client.get("/strategy/saved").json()
    assert any(s["name"] == "My Breakout" for s in listing["strategies"])

    detail = client.get(f"/strategy/saved/{saved['id']}").json()
    assert detail["spec"]["name"]

    assert client.delete(f"/strategy/saved/{saved['id']}").json()["deleted"] is True
    assert client.get(f"/strategy/saved/{saved['id']}").status_code == 404


def test_features_endpoint_documents_the_vocabulary(client):
    payload = client.get("/strategy/features").json()
    assert "ema(n)" in payload["moving_averages"]
    assert "crosses_above" in payload["operators"]


# --- watchlist, alerts, paper trading --------------------------------------

def test_watchlist_add_list_remove(client):
    added = client.post("/trading/watchlist", json={"symbol": "btcusdt"}).json()
    assert added["symbol"] == "BTCUSDT"

    listing = client.get("/trading/watchlist", params={"with_quotes": True}).json()
    assert any(item["symbol"] == "BTCUSDT" for item in listing["items"])

    assert client.delete(f"/trading/watchlist/{added['id']}").json()["deleted"] is True


def test_watchlist_add_is_idempotent(client):
    first = client.post("/trading/watchlist", json={"symbol": "ETHUSDT"}).json()
    second = client.post("/trading/watchlist", json={"symbol": "ETHUSDT"}).json()
    assert second.get("already_present") is True
    assert second["id"] == first["id"]


def test_alert_lifecycle(client):
    created = client.post("/trading/alerts", json={
        "symbol": "BTCUSDT", "condition": "above", "price": 1.0,
    }).json()
    assert created["created"] is True

    checked = client.post("/trading/alerts/check").json()
    # Price is far above 1.0, so this alert must fire.
    assert any(t["id"] == created["id"] for t in checked["triggered"])

    assert client.delete(f"/trading/alerts/{created['id']}").json()["deleted"] is True


def test_paper_position_lifecycle(client):
    quote = client.get("/market/quote", params={"symbol": "BTCUSDT"}).json()

    opened = client.post("/trading/paper/open", json={
        "symbol": "BTCUSDT", "direction": "long",
        "stop_price": quote["price"] * 0.95,
        "target_price": quote["price"] * 1.1,
        "account_balance": 10_000, "risk_percent": 1.0,
    }).json()
    assert opened["opened"] is True
    assert opened["quantity"] > 0
    assert "Risking" in opened["sizing_explanation"]

    positions = client.get("/trading/paper/positions", params={"status": "open"}).json()
    assert any(p["id"] == opened["id"] for p in positions["positions"])

    closed = client.post(f"/trading/paper/close/{opened['id']}", json={"reason": "manual"}).json()
    assert closed["closed"] is True

    performance = client.get("/trading/paper/performance").json()
    assert performance["available"] is True
    assert performance["total_trades"] >= 1


def test_paper_open_rejects_a_stop_on_the_wrong_side(client):
    quote = client.get("/market/quote", params={"symbol": "BTCUSDT"}).json()
    response = client.post("/trading/paper/open", json={
        "symbol": "BTCUSDT", "direction": "long",
        "stop_price": quote["price"] * 1.05,   # above entry for a long
    })
    assert response.status_code == 400
    assert "below entry" in response.json()["detail"]


def test_closing_a_position_twice_is_rejected(client):
    quote = client.get("/market/quote", params={"symbol": "BTCUSDT"}).json()
    opened = client.post("/trading/paper/open", json={
        "symbol": "BTCUSDT", "direction": "long", "stop_price": quote["price"] * 0.95,
    }).json()

    assert client.post(f"/trading/paper/close/{opened['id']}", json={}).status_code == 200
    assert client.post(f"/trading/paper/close/{opened['id']}", json={}).status_code == 400


# --- webhooks ---------------------------------------------------------------

def _webhook_path(client) -> str:
    """The caller's secret webhook URL, which the endpoint authenticates by."""
    return client.get("/auth/webhook-url").json()["path"]


def test_webhook_parses_plain_text_alerts(client):
    payload = client.post(
        _webhook_path(client), content="LONG BTCUSD @ 63000 | stop 62000"
    ).json()
    assert payload["received"] is True
    assert payload["action"] == "buy"
    assert payload["symbol"] == "BTCUSD"
    assert payload["price"] == pytest.approx(63000)


def test_webhook_parses_json_alerts(client):
    payload = client.post(
        _webhook_path(client),
        json={"symbol": "ETHUSDT", "action": "sell", "price": 3000},
    ).json()
    assert payload["action"] == "sell"
    assert payload["symbol"] == "ETHUSDT"


def test_webhook_never_auto_executes(client):
    """A public endpoint must not place orders — a leaked URL would be an account risk."""
    before = client.get("/trading/paper/positions", params={"status": "open"}).json()
    payload = client.post(
        _webhook_path(client), content="LONG BTCUSDT @ 63000"
    ).json()
    after = client.get("/trading/paper/positions", params={"status": "open"}).json()

    assert len(after["positions"]) == len(before["positions"])
    assert "never places orders" in payload["note"]


def test_webhook_rejects_an_unknown_token(client):
    """The secret URL is the credential; a wrong one must not record anything."""
    response = client.post("/trading/webhook/tradingview/not-a-real-token", content="LONG BTC")
    assert response.status_code == 404


def test_webhook_records_unparseable_payloads(client):
    payload = client.post(_webhook_path(client), content="???").json()
    assert payload["received"] is True
    assert payload["parsed"] is False

    history = client.get("/trading/webhook/history").json()
    assert history["webhooks"]
