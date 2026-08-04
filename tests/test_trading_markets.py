"""Symbol resolution and market coverage.

Covers the formats traders actually paste: TradingView exchange prefixes, class
shares, index carets, futures suffixes, FX pairs and crypto. Small and micro
caps get explicit cases because they are the ones most likely to be mishandled
by a resolver that special-cases well-known tickers.
"""
import pytest

from trading.markets import MARKETS, market_catalog, resolve_symbol
from trading.models import AssetClass
from trading.providers.yahoo import YahooProvider


# --- equities, including small caps -----------------------------------------

@pytest.mark.parametrize("raw", [
    "SERV", "NASDAQ:SERV", "NASDAQ: SERV", "nasdaq:serv", " serv ",
])
def test_small_cap_resolves_regardless_of_notation(raw):
    """NASDAQ:SERV is Serve Robotics, a genuine micro cap — all forms must work."""
    resolved = resolve_symbol(raw)
    assert resolved.ticker == "SERV"
    assert resolved.asset_class is AssetClass.STOCK


@pytest.mark.parametrize("ticker", [
    "AAPL", "MSFT", "NVDA",          # mega cap
    "PLTR", "RIVN", "SOFI",          # mid cap
    "IONQ", "RGTI", "LUNR", "ACHR",  # small / micro cap
])
def test_stocks_of_every_market_cap_resolve(ticker):
    resolved = resolve_symbol(ticker)
    assert resolved.ticker == ticker
    assert resolved.asset_class is AssetClass.STOCK


def test_exchange_prefix_is_captured_not_discarded_silently():
    resolved = resolve_symbol("NYSE:GME")
    assert resolved.exchange == "NYSE"
    assert resolved.ticker == "GME"


def test_class_shares_are_normalised():
    assert resolve_symbol("BRK.B").ticker == "BRK-B"
    assert resolve_symbol("NYSE:BRK.B").ticker == "BRK-B"


def test_international_suffixes_are_preserved():
    """A .TO or .L suffix identifies the exchange and must not be mangled."""
    for ticker in ("SHOP.TO", "BARC.L", "SAP.DE", "7203.T", "0700.HK"):
        assert resolve_symbol(ticker).ticker == ticker


# --- other asset classes ----------------------------------------------------

@pytest.mark.parametrize("ticker,expected", [
    ("^IXIC", AssetClass.INDEX),
    ("^RUT", AssetClass.INDEX),      # Russell 2000 — the small-cap benchmark
    ("^VIX", AssetClass.INDEX),
    ("ES=F", AssetClass.FUTURE),
    ("CL=F", AssetClass.FUTURE),
    ("GC=F", AssetClass.FUTURE),
    ("EURUSD", AssetClass.FOREX),
    ("USDJPY", AssetClass.FOREX),
    ("BTCUSDT", AssetClass.CRYPTO),
    ("ETHUSDT", AssetClass.CRYPTO),
    ("O:SPY241220C00500000", AssetClass.OPTION),
])
def test_asset_classes_are_identified(ticker, expected):
    assert resolve_symbol(ticker).asset_class is expected


def test_forex_gets_the_yahoo_suffix():
    assert resolve_symbol("EURUSD").ticker == "EURUSD=X"
    assert resolve_symbol("EURUSD=X").ticker == "EURUSD=X"


def test_crypto_routes_to_a_streaming_exchange():
    assert resolve_symbol("BTCUSDT").suggested_provider == "binance"


def test_equities_route_to_a_keyless_provider_by_default():
    """Small caps must be chartable with no API key at all."""
    assert resolve_symbol("SERV").suggested_provider == "yahoo"


def test_empty_symbol_is_rejected():
    with pytest.raises(ValueError):
        resolve_symbol("   ")


def test_every_resolution_explains_itself():
    for raw in ("SERV", "^RUT", "ES=F", "EURUSD", "BTCUSDT"):
        assert resolve_symbol(raw).note


# --- Yahoo ticker normalisation ---------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("NASDAQ: SERV", "SERV"),
    ("NYSE:BRK.B", "BRK-B"),
    ("^RUT", "^RUT"),
    ("ES=F", "ES=F"),
    ("EURUSD", "EURUSD=X"),
    ("BTCUSDT", "BTC-USD"),
    ("ETHUSDC", "ETH-USD"),
])
def test_yahoo_normalises_every_input_shape(raw, expected):
    assert YahooProvider.normalize(raw) == expected


@pytest.mark.parametrize("ticker,expected", [
    ("^GSPC", AssetClass.INDEX),
    ("EURUSD=X", AssetClass.FOREX),
    ("ES=F", AssetClass.FUTURE),
    ("BTC-USD", AssetClass.CRYPTO),
    ("SERV", AssetClass.STOCK),
])
def test_yahoo_classifies_by_ticker_shape(ticker, expected):
    assert YahooProvider.classify(ticker) is expected


# --- catalogue --------------------------------------------------------------

def test_catalog_covers_every_asset_class_in_the_spec():
    catalog = market_catalog()
    covered = {m["asset_class"] for m in catalog["markets"]}
    for required in ("stock", "etf", "index", "crypto", "forex", "commodity", "future", "option"):
        assert required in covered, f"{required} market is not documented"


def test_catalog_lists_small_cap_examples():
    us = next(m for m in market_catalog()["markets"] if m["key"] == "us_stocks")
    assert "SERV" in us["examples"]
    assert "small" in us["name"].lower() or "micro" in us["name"].lower()


def test_catalog_documents_symbol_formats():
    formats = market_catalog()["symbol_formats"]
    assert "exchange_prefixed" in formats
    assert "NASDAQ:SERV" in formats["exchange_prefixed"]


def test_every_market_names_a_provider():
    for market in MARKETS:
        assert market.providers, f"{market.key} lists no provider"
        assert market.examples, f"{market.key} lists no example symbols"
        assert market.notes
