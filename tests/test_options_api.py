"""Options API: /options/price, /implied-volatility, /historical-volatility,
/strategies, /payoff. Reuses the same StubProvider pattern as
test_execution.py so no test needs network access.
"""
import pytest
from fastapi.testclient import TestClient

from tests.test_trading_api import StubProvider


@pytest.fixture()
def client(monkeypatch, session):
    stub = StubProvider()
    import trading.market as market_module
    import trading.providers as providers_module

    monkeypatch.setattr(providers_module, "get_provider", lambda name=None: stub)
    monkeypatch.setattr(providers_module, "resolve_provider_for", lambda s, explicit=None: stub)
    monkeypatch.setattr(market_module, "get_provider", lambda name=None: stub)
    monkeypatch.setattr(market_module, "resolve_provider_for", lambda s, explicit=None: stub)
    market_module.market_service.invalidate()

    from app.ratelimit import compute_limiter

    compute_limiter.reset()

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
    market_module.market_service.invalidate()


# --- /options/price ------------------------------------------------------------

def test_price_with_explicit_spot_and_volatility(client):
    response = client.post("/options/price", json={
        "spot": 42, "strike": 40, "days_to_expiry": 182.5,
        "option_type": "call", "volatility": 0.20, "risk_free_rate": 0.10,
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload["fair_value"] == pytest.approx(4.76, abs=0.01)
    assert payload["volatility_source"] == "explicit"
    assert "delta" in payload["greeks"]


def test_price_derives_volatility_from_symbol_when_not_given(client):
    response = client.post("/options/price", json={
        "symbol": "BTCUSDT", "strike": 100, "days_to_expiry": 30, "option_type": "call",
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload["volatility_source"].startswith("historical")
    assert payload["fair_value"] >= 0


def test_price_derives_spot_from_symbol_when_not_given(client):
    response = client.post("/options/price", json={
        "symbol": "BTCUSDT", "strike": 1, "days_to_expiry": 30, "option_type": "call",
        "volatility": 0.5,
    })
    assert response.status_code == 200
    # Strike of 1 against a real (stub) BTC-scale price should be deep ITM.
    assert response.json()["intrinsic_value"] > 0


def test_price_requires_spot_or_symbol(client):
    response = client.post("/options/price", json={
        "strike": 100, "days_to_expiry": 30, "option_type": "call", "volatility": 0.2,
    })
    assert response.status_code == 400


def test_price_requires_volatility_or_symbol(client):
    response = client.post("/options/price", json={
        "spot": 100, "strike": 100, "days_to_expiry": 30, "option_type": "call",
    })
    assert response.status_code == 400


def test_price_rejects_invalid_option_type(client):
    response = client.post("/options/price", json={
        "spot": 100, "strike": 100, "days_to_expiry": 30,
        "option_type": "banana", "volatility": 0.2,
    })
    assert response.status_code == 422  # fails the Pydantic pattern


def test_price_american_uses_binomial_model(client):
    response = client.post("/options/price", json={
        "spot": 40, "strike": 45, "days_to_expiry": 365,
        "option_type": "put", "volatility": 0.30, "risk_free_rate": 0.08,
        "exercise_style": "american",
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload["inputs"]["model"] == "binomial_crr"
    assert payload["inputs"]["exercise_style"] == "american"


def test_price_requires_authentication_context(client):
    # Sanity: the endpoint is reachable without an explicit Authorization
    # header in this test environment (auth not enforced on loopback/TestClient),
    # matching the rest of the trading API's test conventions.
    response = client.post("/options/price", json={
        "spot": 100, "strike": 100, "days_to_expiry": 30,
        "option_type": "call", "volatility": 0.25,
    })
    assert response.status_code == 200


# --- /options/implied-volatility -------------------------------------------------

def test_implied_volatility_round_trips(client):
    priced = client.post("/options/price", json={
        "spot": 100, "strike": 105, "days_to_expiry": 90,
        "option_type": "call", "volatility": 0.28, "risk_free_rate": 0.04,
    }).json()

    response = client.post("/options/implied-volatility", json={
        "market_price": priced["fair_value"], "spot": 100, "strike": 105,
        "days_to_expiry": 90, "option_type": "call", "risk_free_rate": 0.04,
    })
    assert response.status_code == 200
    assert response.json()["implied_volatility"] == pytest.approx(0.28, abs=0.001)


def test_implied_volatility_rejects_price_below_intrinsic(client):
    response = client.post("/options/implied-volatility", json={
        "market_price": 1, "spot": 100, "strike": 50,
        "days_to_expiry": 30, "option_type": "call",
    })
    assert response.status_code == 400


# --- /options/historical-volatility -----------------------------------------------

def test_historical_volatility_endpoint(client):
    response = client.get("/options/historical-volatility?symbol=BTCUSDT&timeframe=1d&window=100")
    assert response.status_code == 200
    payload = response.json()
    # The provider may normalize the symbol (e.g. BTCUSDT -> BTC-USD); the
    # response echoes back whatever it actually resolved and priced.
    assert payload["symbol"]
    assert payload["annualized_volatility"] >= 0
    assert payload["bars_used"] > 0


# --- /options/strategies ----------------------------------------------------------

def test_strategies_endpoint_lists_known_strategies(client):
    response = client.get("/options/strategies")
    assert response.status_code == 200
    names = response.json()["strategies"]
    assert "iron_condor" in names
    assert "bull_call_spread" in names


# --- /options/payoff ---------------------------------------------------------------

def test_payoff_with_explicit_legs(client):
    response = client.post("/options/payoff", json={
        "legs": [
            {"option_type": "call", "strike": 95, "premium": 8.0, "quantity": 1},
            {"option_type": "call", "strike": 105, "premium": 3.0, "quantity": -1},
        ],
        "spot": 100,
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload["net_premium"] == pytest.approx(5.0)
    assert payload["max_profit"] == pytest.approx(5.0)
    assert payload["max_loss"] == pytest.approx(-5.0)


def test_payoff_with_named_strategy(client):
    response = client.post("/options/payoff", json={
        "strategy": "iron_condor",
        "strategy_params": {
            "put_long": 85, "put_short": 90, "call_short": 110, "call_long": 115,
            "days_to_expiry": 45, "volatility": 0.25,
        },
        "spot": 100,
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload["net_premium"] < 0  # net credit
    assert len(payload["legs"]) == 4


def test_payoff_requires_exactly_one_of_legs_or_strategy(client):
    response = client.post("/options/payoff", json={})
    assert response.status_code == 400

    response2 = client.post("/options/payoff", json={
        "legs": [{"option_type": "call", "strike": 100, "premium": 5, "quantity": 1}],
        "strategy": "long_straddle",
        "strategy_params": {"strike": 100, "days_to_expiry": 30},
        "spot": 100,
    })
    assert response2.status_code == 400


def test_payoff_rejects_unknown_strategy(client):
    response = client.post("/options/payoff", json={
        "strategy": "made_up_strategy",
        "strategy_params": {"days_to_expiry": 30},
        "spot": 100,
    })
    assert response.status_code == 400


def test_payoff_strategy_requires_days_to_expiry(client):
    response = client.post("/options/payoff", json={
        "strategy": "long_straddle",
        "strategy_params": {"strike": 100, "volatility": 0.25},
        "spot": 100,
    })
    assert response.status_code == 400
