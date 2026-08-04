"""Scanner API endpoints, backed by the same stub provider as the rest of the
trading API tests — no network access.
"""
from tests.test_trading_api import client  # noqa: F401 - shared fixture


def test_fields_endpoint_lists_every_field_and_operator(client):
    payload = client.get("/scanner/fields").json()
    assert "price" in payload["fields"]
    assert "trend" in payload["fields"]
    assert "between" in payload["operators"]


def test_scan_returns_matches_for_a_passing_filter(client):
    # Stock symbols, not crypto: the crypto path's healthy-exchange probe does
    # its own raw network call (see trading/providers/healthy_crypto_provider)
    # and bypasses this fixture's stubbing, so a crypto symbol here would hit
    # the real network. Stocks route cleanly through the patched get_provider.
    #
    # The stub serves the same trending series for every symbol, which is a
    # bullish market — this proves the request reaches the engine and the
    # response is shaped correctly, not the scanner's own filter math (that's
    # covered directly in tests/test_trading_scanner.py).
    payload = client.post("/scanner", json={
        "symbols": ["AAPL", "MSFT"],
        "timeframe": "1h",
        "filters": [{"field": "trend", "op": "eq", "value": "bullish"}],
    }).json()

    assert payload["scanned"] == 2
    assert payload["matched"] == 2
    assert {m["symbol"] for m in payload["matches"]} == {"AAPL", "MSFT"}


def test_scan_excludes_symbols_that_fail_a_filter(client):
    payload = client.post("/scanner", json={
        "symbols": ["AAPL"],
        "timeframe": "1h",
        "filters": [{"field": "price", "op": "gt", "value": 1_000_000_000}],
    }).json()

    assert payload["matched"] == 0
    assert payload["scanned"] == 1


def test_scan_rejects_an_unknown_field(client):
    response = client.post("/scanner", json={
        "symbols": ["AAPL"],
        "filters": [{"field": "not_a_real_field", "op": "gt", "value": 1}],
    })
    assert response.status_code == 400
    assert "not_a_real_field" in response.json()["detail"]


def test_scan_rejects_an_unknown_operator(client):
    response = client.post("/scanner", json={
        "symbols": ["AAPL"],
        "filters": [{"field": "rsi", "op": "not_a_real_op", "value": 1}],
    })
    assert response.status_code == 400
    assert "not_a_real_op" in response.json()["detail"]


def test_scan_rejects_between_without_value2(client):
    response = client.post("/scanner", json={
        "symbols": ["AAPL"],
        "filters": [{"field": "rsi", "op": "between", "value": 30}],
    })
    assert response.status_code == 400
    assert "value2" in response.json()["detail"]


def test_scan_rejects_more_than_the_symbol_cap(client):
    from trading.scanner import MAX_SCAN_SYMBOLS

    oversized = [f"SYM{i}" for i in range(MAX_SCAN_SYMBOLS + 1)]
    response = client.post("/scanner", json={"symbols": oversized})
    assert response.status_code == 422  # pydantic max_length rejection


def test_scan_requires_at_least_one_symbol(client):
    response = client.post("/scanner", json={"symbols": []})
    assert response.status_code == 422
