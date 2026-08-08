"""Twelve Data — forex, stocks, indices, ETFs and commodities.

Requires `TWELVEDATA_API_KEY`. Useful as the non-crypto default because a
single account covers FX and equities with one symbol namespace (`EUR/USD`,
`AAPL`, `XAU/USD`).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import AsyncIterator

import httpx

from trading.models import AssetClass, Candle, MarketDataError, Quote, Series, SymbolInfo, Timeframe
from trading.providers.base import MarketDataProvider

logger = logging.getLogger("legend.trading.twelvedata")

_INTERVALS = {
    Timeframe.M1: "1min",
    Timeframe.M5: "5min",
    Timeframe.M15: "15min",
    Timeframe.M30: "30min",
    Timeframe.H1: "1h",
    Timeframe.H4: "4h",
    Timeframe.D1: "1day",
    Timeframe.W1: "1week",
    Timeframe.MN1: "1month",
}


class TwelveDataProvider(MarketDataProvider):
    name = "twelvedata"
    supported_assets = (
        AssetClass.FOREX, AssetClass.STOCK, AssetClass.INDEX,
        AssetClass.ETF, AssetClass.COMMODITY, AssetClass.CRYPTO,
    )
    supports_streaming = True
    max_bars_per_request = 5000

    REST = "https://api.twelvedata.com"
    WS = "wss://ws.twelvedata.com/v1/quotes/price"

    def __init__(self, api_key: str = ""):
        self.api_key = api_key

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _get(self, path: str, params: dict) -> dict:
        if not self.api_key:
            raise MarketDataError("TWELVEDATA_API_KEY is not set")
        params = dict(params)
        params["apikey"] = self.api_key
        try:
            response = httpx.get(f"{self.REST}{path}", params=params, timeout=20)
            # A 404 here means "cannot serve this instrument", not "the vendor is
            # down". Reporting it as unreachable sends whoever reads the log
            # hunting a network fault.
            #
            # The body carries the actual reason and the reasons are genuinely
            # different: `PSX:SERV` says "available starting with the Pro or
            # Venture plan" — the symbol exists and the account cannot reach it —
            # while a bad ticker says the symbol is invalid. Guessing "no such
            # listing" for both would state something false about the first, and
            # send someone hunting a symbol bug when they need a billing page. So
            # the vendor's own sentence wins whenever there is one.
            if response.status_code == 404:
                try:
                    vendor_message = (response.json() or {}).get("message", "")
                except ValueError:
                    vendor_message = ""
                if vendor_message:
                    raise MarketDataError(f"Twelve Data: {vendor_message}")
                where = f" on {params['exchange']}" if params.get("exchange") else ""
                raise MarketDataError(
                    f"Twelve Data cannot serve {params['symbol']}{where}."
                )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            raise MarketDataError(f"Twelve Data {path} unreachable: {exc}") from exc
        # Twelve Data reports errors inside a 200 response.
        if isinstance(data, dict) and data.get("status") == "error":
            raise MarketDataError(f"Twelve Data: {data.get('message', 'unknown error')}")
        return data

    @staticmethod
    def _split_exchange(symbol: str) -> tuple[str, str | None]:
        """Split `EXCHANGE:TICKER` into the two parameters Twelve Data wants.

        The rest of the platform speaks TradingView-style `NASDAQ:SERV`, which
        is what the Markets browser produces and what a user types. Twelve Data
        takes the ticker and the exchange as *separate* query parameters and
        returns a hard 404 for the colon form — not a JSON error body, an HTTP
        404, so it does not even reach the error handling in `_get`. Verified
        against the live API: `symbol=SERV&exchange=NASDAQ` serves data,
        `symbol=NASDAQ:SERV` 404s.

        FX pairs (`EUR/USD`) and bare tickers pass through untouched.
        """
        if ":" not in symbol:
            return symbol.upper(), None
        exchange, _, ticker = symbol.partition(":")
        # A ticker is required; `NASDAQ:` alone is malformed, and sending an
        # empty symbol would produce a confusing upstream error rather than an
        # obvious one here.
        if not ticker.strip():
            return symbol.upper(), None
        return ticker.strip().upper(), exchange.strip().upper() or None

    @staticmethod
    def _parse_time(raw: str) -> int:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return int(datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc).timestamp())
            except ValueError:
                continue
        raise MarketDataError(f"Unrecognised Twelve Data timestamp: {raw!r}")

    def fetch_candles(
        self, symbol: str, timeframe: Timeframe, limit: int = 500, end_time: int | None = None
    ) -> Series:
        ticker, exchange = self._split_exchange(symbol)
        params = {
            "symbol": ticker,
            "interval": _INTERVALS[timeframe],
            "outputsize": max(1, min(limit, self.max_bars_per_request)),
            "order": "ASC",
        }
        if exchange:
            params["exchange"] = exchange
        if end_time is not None:
            params["end_date"] = datetime.fromtimestamp(end_time, tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S"
            )

        data = self._get("/time_series", params)
        values = data.get("values") or []
        candles = [
            Candle(
                timestamp=self._parse_time(v["datetime"]),
                open=float(v["open"]),
                high=float(v["high"]),
                low=float(v["low"]),
                close=float(v["close"]),
                volume=float(v.get("volume") or 0.0),
            )
            for v in values
        ]
        return Series(
            symbol=symbol.upper(),
            timeframe=timeframe,
            candles=candles,
            asset_class=self._classify(symbol),
            provider=self.name,
        )

    def fetch_quote(self, symbol: str) -> Quote:
        ticker, exchange = self._split_exchange(symbol)
        params = {"symbol": ticker}
        if exchange:
            params["exchange"] = exchange
        data = self._get("/quote", params)
        price = float(data.get("close") or data.get("price") or 0.0)
        return Quote(
            symbol=symbol.upper(),
            price=price,
            timestamp=int(data.get("timestamp") or 0),
            change=float(data["change"]) if data.get("change") is not None else None,
            change_percent=(
                float(data["percent_change"]) if data.get("percent_change") is not None else None
            ),
            volume_24h=float(data["volume"]) if data.get("volume") else None,
        )

    def search_symbols(self, query: str, limit: int = 20) -> list[SymbolInfo]:
        """Search, returning symbols that are unambiguous enough to chart.

        A search for `SERV` returns four Common Stocks on four exchanges in four
        countries — a US robotics company, a Pakistani footwear maker, a Swedish
        listing and a Canadian one. Returned as bare tickers they are four
        identical-looking rows, and whichever the user picks charts whatever the
        vendor happens to default to.

        So each result is qualified with its exchange (`NASDAQ:SERV`), which
        `_split_exchange` turns back into an exact lookup. The exchange and
        country ride along separately so the UI can label the rows too — the
        symbol being right is necessary but not sufficient if a person still
        cannot tell which one to click.
        """
        data = self._get("/symbol_search", {"symbol": query, "outputsize": limit})
        results = []
        for m in data.get("data", [])[:limit]:
            ticker = m["symbol"]
            exchange = (m.get("exchange") or "").strip()
            results.append(
                SymbolInfo(
                    symbol=f"{exchange}:{ticker}" if exchange else ticker,
                    name=m.get("instrument_name") or ticker,
                    asset_class=self._classify(ticker, m.get("instrument_type")),
                    provider=self.name,
                    quote_currency=m.get("currency", "USD"),
                    exchange=exchange,
                    country=(m.get("country") or "").strip(),
                )
            )
        return results

    @staticmethod
    def _classify(symbol: str, instrument_type: str | None = None) -> AssetClass:
        kind = (instrument_type or "").lower()
        if "etf" in kind:
            return AssetClass.ETF
        if "index" in kind:
            return AssetClass.INDEX
        if "crypto" in kind or "digital" in kind:
            return AssetClass.CRYPTO
        if "/" in symbol:
            base = symbol.split("/")[0].upper()
            return AssetClass.COMMODITY if base in {"XAU", "XAG", "XPT", "WTI", "BRENT"} else AssetClass.FOREX
        return AssetClass.STOCK

    async def stream_candles(self, symbol: str, timeframe: Timeframe) -> AsyncIterator[Candle]:
        """Build live bars from Twelve Data's price stream.

        The socket delivers trade prices rather than bars, so we aggregate them
        into the requested timeframe bucket here — the first price in a bucket
        opens the bar and each subsequent tick extends its high/low/close.
        """
        import websockets

        if not self.api_key:
            raise MarketDataError("TWELVEDATA_API_KEY is not set")

        url = f"{self.WS}?apikey={self.api_key}"
        bucket = timeframe.seconds
        current: Candle | None = None
        # The socket takes a bare ticker and has no exchange parameter at all.
        # Subscribing with the colon form fails silently — the socket stays open
        # and simply never sends a price, which looks like a dead market rather
        # than a bad subscription.
        ticker, _ = self._split_exchange(symbol)

        async with websockets.connect(url, ping_interval=20) as ws:
            await ws.send(json.dumps({
                "action": "subscribe",
                "params": {"symbols": ticker},
            }))
            logger.info("twelvedata stream open: %s", symbol)

            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if msg.get("event") != "price":
                    continue
                price = float(msg.get("price", 0) or 0)
                ts = int(msg.get("timestamp") or 0)
                if not price or not ts:
                    continue

                bucket_start = ts - (ts % bucket)
                if current is None or current.timestamp != bucket_start:
                    if current is not None:
                        yield Candle(
                            current.timestamp, current.open, current.high,
                            current.low, current.close, current.volume, closed=True,
                        )
                    current = Candle(bucket_start, price, price, price, price, 0.0, closed=False)
                else:
                    current = Candle(
                        bucket_start,
                        current.open,
                        max(current.high, price),
                        min(current.low, price),
                        price,
                        current.volume,
                        closed=False,
                    )
                yield current
