"""Yahoo Finance — every US-listed stock (including micro/small caps), ETFs,
indices, FX, futures and crypto, with no API key.

This is the provider that makes "supports every market" true on a fresh
install. Polygon and Twelve Data are better sources for production equity work
— documented rate limits, an SLA, corporate-action handling — but both require
a paid key, which would leave the platform unable to chart `NASDAQ:SERV` out of
the box. Yahoo covers the whole listed universe immediately.

Caveats worth knowing, and stated rather than hidden:

* It is an undocumented endpoint. It can change without notice, and it is
  rate-limited by IP. `provider_status()` reports it as best-effort.
* Intraday history is capped by Yahoo: roughly 7 days of 1-minute bars and
  2 years of hourly. Daily and weekly go back decades.
* Prices are consolidated-tape and split/dividend adjusted on the daily
  interval; do not expect them to tick-match a specific exchange feed.
"""
from __future__ import annotations

import logging
import time
from typing import AsyncIterator

import httpx

from trading.models import AssetClass, Candle, MarketDataError, Quote, Series, SymbolInfo, Timeframe
from trading.providers.base import MarketDataProvider, align_to_bucket

logger = logging.getLogger("legend.trading.yahoo")

# Yahoo's interval codes. It has no 4-hour bar, so that is aggregated from 1h.
_NATIVE = {
    Timeframe.M1: "1m",
    Timeframe.M5: "5m",
    Timeframe.M15: "15m",
    Timeframe.M30: "30m",
    Timeframe.H1: "60m",
    Timeframe.D1: "1d",
    Timeframe.W1: "1wk",
    Timeframe.MN1: "1mo",
}
_DERIVED = {Timeframe.H4: (Timeframe.H1, 14400)}

# How far back Yahoo will serve each interval. Asking for more returns an error,
# so requests are clamped to these windows.
_MAX_LOOKBACK_SECONDS = {
    "1m": 7 * 86400,
    "5m": 60 * 86400,
    "15m": 60 * 86400,
    "30m": 60 * 86400,
    "60m": 730 * 86400,
    "1d": 50 * 365 * 86400,
    "1wk": 50 * 365 * 86400,
    "1mo": 50 * 365 * 86400,
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


class YahooProvider(MarketDataProvider):
    name = "yahoo"
    supported_assets = (
        AssetClass.STOCK, AssetClass.ETF, AssetClass.INDEX, AssetClass.FOREX,
        AssetClass.FUTURE, AssetClass.COMMODITY, AssetClass.CRYPTO,
    )
    supports_streaming = False   # no public socket; the hub polls instead
    max_bars_per_request = 5000

    CHART = "https://query1.finance.yahoo.com/v8/finance/chart"
    SEARCH = "https://query2.finance.yahoo.com/v1/finance/search"

    @property
    def configured(self) -> bool:
        return True

    @staticmethod
    def normalize(symbol: str) -> str:
        """Accept TradingView-style input and return a Yahoo ticker.

        `NASDAQ: SERV` -> `SERV`, `NYSE:BRK.B` -> `BRK-B`, `EURUSD` -> `EURUSD=X`,
        `BTCUSDT` -> `BTC-USD`.
        """
        s = symbol.strip().upper()
        if ":" in s:
            s = s.split(":", 1)[1].strip()
        s = s.replace(" ", "")
        if not s:
            raise MarketDataError("Empty symbol")

        # Already a Yahoo-shaped suffix symbol.
        if s.endswith(("=X", "=F")) or s.startswith("^"):
            return s

        # Crypto pairs quoted in a stablecoin map onto Yahoo's USD pairs.
        for quote in ("USDT", "USDC", "BUSD"):
            if s.endswith(quote) and len(s) > len(quote):
                return f"{s[:-len(quote)]}-USD"

        # Six-letter FX pairs like EURUSD / GBPJPY.
        if len(s) == 6 and s.isalpha() and s[3:] in {
            "USD", "EUR", "JPY", "GBP", "CHF", "CAD", "AUD", "NZD"
        }:
            return f"{s}=X"
        if "/" in symbol and len(s.replace("/", "")) == 6:
            return f"{s.replace('/', '')}=X"

        # Class shares use a hyphen at Yahoo (BRK.B -> BRK-B).
        if "." in s and not s.endswith((".US",)):
            return s.replace(".", "-")
        return s

    @staticmethod
    def classify(symbol: str, instrument_type: str | None = None) -> AssetClass:
        kind = (instrument_type or "").upper()
        mapping = {
            "EQUITY": AssetClass.STOCK,
            "ETF": AssetClass.ETF,
            "MUTUALFUND": AssetClass.ETF,
            "INDEX": AssetClass.INDEX,
            "CURRENCY": AssetClass.FOREX,
            "FUTURE": AssetClass.FUTURE,
            "CRYPTOCURRENCY": AssetClass.CRYPTO,
            "OPTION": AssetClass.OPTION,
        }
        if kind in mapping:
            return mapping[kind]

        s = symbol.upper()
        if s.startswith("^"):
            return AssetClass.INDEX
        if s.endswith("=X"):
            return AssetClass.FOREX
        if s.endswith("=F"):
            return AssetClass.FUTURE
        if s.endswith("-USD"):
            return AssetClass.CRYPTO
        return AssetClass.STOCK

    # Yahoo throttles per IP and is quick to hard-block a burst. A minimum gap
    # between requests costs almost nothing on interactive use and is the
    # difference between working and being blocked when the analysis pipeline
    # fetches three timeframes back to back.
    MIN_REQUEST_GAP = 0.35
    RETRY_BACKOFF = (1.0, 3.0, 7.0)

    _last_request_at = 0.0

    @classmethod
    def _throttle(cls) -> None:
        elapsed = time.time() - cls._last_request_at
        if elapsed < cls.MIN_REQUEST_GAP:
            time.sleep(cls.MIN_REQUEST_GAP - elapsed)
        cls._last_request_at = time.time()

    def _get(self, url: str, params: dict) -> dict:
        last_error: Exception | None = None

        for attempt in range(len(self.RETRY_BACKOFF) + 1):
            self._throttle()
            try:
                response = httpx.get(url, params=params, timeout=25, headers=_HEADERS,
                                     follow_redirects=True)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status == 404:
                    raise MarketDataError(
                        "Yahoo Finance does not recognise that symbol. Check the ticker — "
                        "US listings use the plain ticker (SERV for NASDAQ:SERV), indices use "
                        "a caret (^RUT), futures a =F suffix (ES=F)."
                    ) from exc
                if status in (429, 999) and attempt < len(self.RETRY_BACKOFF):
                    # Transient throttle: back off and try again before giving up.
                    time.sleep(self.RETRY_BACKOFF[attempt])
                    last_error = exc
                    continue
                if status in (429, 999):
                    raise MarketDataError(
                        "Yahoo Finance is rate-limiting this network and did not recover after "
                        f"{len(self.RETRY_BACKOFF)} retries. Yahoo is a best-effort keyless "
                        "source; for reliable equity data set POLYGON_API_KEY, "
                        "TWELVEDATA_API_KEY, FINNHUB_API_KEY or ALPHAVANTAGE_API_KEY and the "
                        "platform will prefer it automatically."
                    ) from exc
                raise MarketDataError(f"Yahoo Finance returned HTTP {status}") from exc
            except httpx.HTTPError as exc:
                if attempt < len(self.RETRY_BACKOFF):
                    time.sleep(self.RETRY_BACKOFF[attempt])
                    last_error = exc
                    continue
                raise MarketDataError(f"Yahoo Finance unreachable: {exc}") from exc

        raise MarketDataError(f"Yahoo Finance request failed: {last_error}")

    def _fetch_native(
        self, ticker: str, interval: str, limit: int, seconds: int, end_time: int | None
    ) -> tuple[list[Candle], dict]:
        end = end_time or int(time.time())
        # Ask for well over the bar count so weekends and holidays still leave
        # `limit` real bars, then clamp to what Yahoo will serve.
        span = min(int(seconds * limit * 2.2) + 7 * 86400, _MAX_LOOKBACK_SECONDS[interval])
        params = {
            "period1": max(0, end - span),
            "period2": end,
            "interval": interval,
            "includePrePost": "false",
            "events": "div,splits",
        }

        payload = self._get(f"{self.CHART}/{ticker}", params)
        chart = payload.get("chart") or {}
        if chart.get("error"):
            raise MarketDataError(f"Yahoo Finance: {chart['error'].get('description', 'unknown error')}")

        results = chart.get("result") or []
        if not results:
            raise MarketDataError(f"Yahoo Finance returned no data for {ticker}")

        result = results[0]
        meta = result.get("meta", {})
        stamps = result.get("timestamp") or []
        quote_blocks = (result.get("indicators") or {}).get("quote") or [{}]
        quote = quote_blocks[0]

        opens = quote.get("open") or []
        highs = quote.get("high") or []
        lows = quote.get("low") or []
        closes = quote.get("close") or []
        volumes = quote.get("volume") or []

        candles: list[Candle] = []
        for i, stamp in enumerate(stamps):
            o, h, l, c = (
                _at(opens, i), _at(highs, i), _at(lows, i), _at(closes, i)
            )
            # Yahoo emits nulls for halted or untraded bars; skip rather than
            # forward-fill, which would invent price action that never happened.
            if None in (o, h, l, c):
                continue
            candles.append(Candle(
                timestamp=int(stamp),
                open=float(o), high=float(h), low=float(l), close=float(c),
                volume=float(_at(volumes, i) or 0.0),
            ))

        return candles, meta

    def fetch_candles(
        self, symbol: str, timeframe: Timeframe, limit: int = 500, end_time: int | None = None
    ) -> Series:
        ticker = self.normalize(symbol)

        if timeframe in _NATIVE:
            interval = _NATIVE[timeframe]
            candles, meta = self._fetch_native(
                ticker, interval, limit, timeframe.seconds, end_time
            )
            bucket = None
        else:
            source_tf, bucket = _DERIVED[timeframe]
            interval = _NATIVE[source_tf]
            needed = limit * (bucket // source_tf.seconds)
            candles, meta = self._fetch_native(
                ticker, interval, needed, source_tf.seconds, end_time
            )

        if bucket is not None:
            candles = align_to_bucket(candles, bucket)

        if not candles:
            raise MarketDataError(
                f"Yahoo Finance returned no usable bars for {ticker} at {timeframe.value}. "
                f"Intraday history is limited (about 7 days of 1-minute and 2 years of hourly)."
            )

        return Series(
            symbol=ticker,
            timeframe=timeframe,
            candles=candles[-limit:],
            asset_class=self.classify(ticker, meta.get("instrumentType")),
            provider=self.name,
        )

    def fetch_quote(self, symbol: str) -> Quote:
        ticker = self.normalize(symbol)
        payload = self._get(f"{self.CHART}/{ticker}", {"interval": "1d", "range": "5d"})
        results = (payload.get("chart") or {}).get("result") or []
        if not results:
            raise MarketDataError(f"Yahoo Finance returned no quote for {ticker}")

        meta = results[0].get("meta", {})
        price = meta.get("regularMarketPrice")
        previous = meta.get("chartPreviousClose") or meta.get("previousClose")
        if price is None:
            raise MarketDataError(f"Yahoo Finance returned no price for {ticker}")

        price = float(price)
        change = (price - float(previous)) if previous else None
        return Quote(
            symbol=ticker,
            price=price,
            timestamp=int(meta.get("regularMarketTime") or time.time()),
            bid=float(meta["bid"]) if meta.get("bid") else None,
            ask=float(meta["ask"]) if meta.get("ask") else None,
            change=change,
            change_percent=(change / float(previous) * 100) if (change is not None and previous) else None,
            volume_24h=float(meta.get("regularMarketVolume") or 0.0) or None,
        )

    def search_symbols(self, query: str, limit: int = 20) -> list[SymbolInfo]:
        """Search the full listed universe by ticker or company name.

        This resolves plain-English lookups — "serve robotics" finds SERV — which
        matters most for small caps whose tickers nobody has memorised.
        """
        payload = self._get(self.SEARCH, {
            "q": query,
            "quotesCount": limit,
            "newsCount": 0,
            "listsCount": 0,
        })

        out: list[SymbolInfo] = []
        for row in payload.get("quotes", [])[:limit]:
            ticker = row.get("symbol")
            if not ticker:
                continue
            exchange = row.get("exchDisp") or row.get("exchange") or ""
            name = row.get("longname") or row.get("shortname") or ticker
            out.append(SymbolInfo(
                symbol=ticker,
                name=f"{name} ({exchange})" if exchange else name,
                asset_class=self.classify(ticker, row.get("quoteType")),
                provider=self.name,
                quote_currency=row.get("currency", "USD") or "USD",
            ))
        return out

    async def stream_candles(self, symbol: str, timeframe: Timeframe) -> AsyncIterator[Candle]:
        """Not supported — the stream hub polls this provider over REST instead."""
        raise NotImplementedError(
            "Yahoo Finance has no public streaming socket; the platform polls it on a timer."
        )
        yield  # pragma: no cover


def _at(values: list, index: int):
    return values[index] if index < len(values) else None
