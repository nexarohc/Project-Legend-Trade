"""Twelve Data provider — symbol translation between two namespaces.

The platform speaks TradingView-style `EXCHANGE:TICKER` everywhere: it is what
the Markets browser emits, what the watchlist stores, and what a user types.
Twelve Data speaks ticker-plus-exchange-parameter. Every test here exists
because the boundary between those two namespaces is where this provider broke:
`NASDAQ:SERV` was sent through verbatim and got an HTTP 404 from the vendor, so
a keyed instance could chart a bare ticker but not the exact symbol its own UI
produced.

These tests stub the HTTP layer rather than calling the vendor. The wire format
they assert against was verified by hand first — `symbol=SERV&exchange=NASDAQ`
returns data and `symbol=NASDAQ:SERV` returns 404 — so the stub encodes an
observed contract rather than an assumed one.
"""
from trading.models import MarketDataError, Timeframe
from trading.providers.twelvedata import TwelveDataProvider


def _provider_capturing_params(payload=None):
    """A provider whose `_get` records its arguments instead of making a call."""
    provider = TwelveDataProvider(api_key="test-key")
    captured = {}

    def fake_get(path, params):
        captured["path"] = path
        captured["params"] = params
        return payload if payload is not None else {"values": []}

    provider._get = fake_get
    return provider, captured


# --- the split itself -------------------------------------------------------


def test_bare_ticker_passes_through_with_no_exchange():
    assert TwelveDataProvider._split_exchange("AAPL") == ("AAPL", None)


def test_exchange_prefix_is_split_into_two_parts():
    assert TwelveDataProvider._split_exchange("NASDAQ:SERV") == ("SERV", "NASDAQ")


def test_forex_pairs_are_not_touched():
    """`EUR/USD` uses a slash, not a colon, and is already what the vendor wants."""
    assert TwelveDataProvider._split_exchange("EUR/USD") == ("EUR/USD", None)


def test_lowercase_input_is_normalised():
    assert TwelveDataProvider._split_exchange("nasdaq:serv") == ("SERV", "NASDAQ")


def test_surrounding_whitespace_is_stripped():
    assert TwelveDataProvider._split_exchange(" NYSE : GME ") == ("GME", "NYSE")


def test_a_prefix_with_no_ticker_is_left_alone():
    """`NASDAQ:` is malformed.

    Splitting it would send an empty `symbol` to the vendor and surface as a
    confusing upstream error. Passing it through unchanged fails at the same
    place any other unknown symbol does, with the symbol visible in the message.
    """
    assert TwelveDataProvider._split_exchange("NASDAQ:") == ("NASDAQ:", None)


# --- the split applied at each call site ------------------------------------


def test_fetch_candles_sends_ticker_and_exchange_separately():
    provider, captured = _provider_capturing_params()

    provider.fetch_candles("NASDAQ:SERV", Timeframe.D1, limit=30)

    assert captured["path"] == "/time_series"
    assert captured["params"]["symbol"] == "SERV"
    assert captured["params"]["exchange"] == "NASDAQ"
    # The colon form is what produced the 404. It must not survive anywhere.
    assert ":" not in captured["params"]["symbol"]


def test_fetch_candles_omits_exchange_when_there_is_none():
    """An empty `exchange=` is not the same as no `exchange` — don't send one."""
    provider, captured = _provider_capturing_params()

    provider.fetch_candles("AAPL", Timeframe.D1, limit=30)

    assert captured["params"]["symbol"] == "AAPL"
    assert "exchange" not in captured["params"]


def test_fetch_quote_sends_ticker_and_exchange_separately():
    provider, captured = _provider_capturing_params(
        {"close": "5.72", "timestamp": 1, "change": "0.05", "percent_change": "0.9"}
    )

    provider.fetch_quote("NASDAQ:SERV")

    assert captured["path"] == "/quote"
    assert captured["params"]["symbol"] == "SERV"
    assert captured["params"]["exchange"] == "NASDAQ"


def test_the_returned_series_keeps_the_symbol_the_caller_asked_for():
    """Translation is for the wire only.

    The chart, the watchlist and the analysis record all key off the symbol the
    user chose. Returning the stripped ticker would silently rename `NASDAQ:SERV`
    to `SERV` halfway through the stack and break the round trip.
    """
    provider, _ = _provider_capturing_params(
        {
            "values": [
                {
                    "datetime": "2026-08-04",
                    "open": "5.67",
                    "high": "5.82",
                    "low": "5.61",
                    "close": "5.72",
                    "volume": "100400",
                }
            ]
        }
    )

    series = provider.fetch_candles("NASDAQ:SERV", Timeframe.D1, limit=1)

    assert series.symbol == "NASDAQ:SERV"
    assert len(series.candles) == 1
    assert series.candles[0].close == 5.72


# --- error messages ---------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = body

    def raise_for_status(self):
        raise AssertionError("should not be reached for a 404")

    def json(self):
        if self._body is None:
            raise ValueError("not JSON")
        return self._body


def _provider_returning_404(monkeypatch, body=None):
    provider = TwelveDataProvider(api_key="test-key")
    monkeypatch.setattr(
        "trading.providers.twelvedata.httpx.get",
        lambda *args, **kwargs: _FakeResponse(404, body),
    )
    return provider


def _error_from(provider, symbol):
    try:
        provider.fetch_candles(symbol, Timeframe.D1, limit=5)
    except MarketDataError as exc:
        return str(exc)
    raise AssertionError("expected a MarketDataError")


def test_a_plan_restriction_is_reported_as_a_plan_restriction(monkeypatch):
    """`PSX:SERV` exists; this account just cannot reach it.

    Twelve Data answers with HTTP 404 but a body saying so. Substituting our own
    "no such listing" here would state something false and send someone hunting
    a symbol bug when what they need is a billing page — so the vendor's own
    sentence wins whenever there is one.
    """
    provider = _provider_returning_404(
        monkeypatch,
        {
            "code": 404,
            "status": "error",
            "message": "This symbol is available starting with the Pro or Venture plan.",
        },
    )

    message = _error_from(provider, "PSX:SERV")

    assert "Pro or Venture plan" in message
    assert "no SERV" not in message  # never claim the listing doesn't exist
    assert "unreachable" not in message.lower()


def test_a_404_with_no_usable_body_still_says_what_was_asked_for(monkeypatch):
    """The fallback must name the symbol and exchange, and must not say 'unreachable'.

    `NASDAQ:IONQ` failing is correct — IONQ is NYSE-listed, and `NYSE:IONQ`
    works. Retrying without the exchange is deliberately NOT done: two companies
    can share a ticker across exchanges, so dropping the qualifier could
    silently chart the wrong instrument. A clear failure beats a confident wrong
    answer.
    """
    provider = _provider_returning_404(monkeypatch)

    message = _error_from(provider, "NASDAQ:IONQ")

    assert "IONQ" in message
    assert "NASDAQ" in message
    assert "unreachable" not in message.lower()


def test_a_404_without_an_exchange_names_the_bare_symbol(monkeypatch):
    provider = _provider_returning_404(monkeypatch)

    message = _error_from(provider, "NOSUCHTICKER")

    assert "NOSUCHTICKER" in message
    assert "unreachable" not in message.lower()
