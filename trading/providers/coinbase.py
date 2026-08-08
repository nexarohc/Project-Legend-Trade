"""Coinbase Exchange public market data — keyless, and reachable where Binance is not.

Binance geo-blocks a number of regions (HTTP 451), so a platform whose "works
out of the box" promise rests on a single keyless source is fragile. Coinbase's
public endpoints need no credentials either and serve a different set of
jurisdictions, which is why both ship and why `providers/__init__` will fail
over between them.

Coinbase only offers a few native granularities, so the 30m/4h/1w/1M bars are
aggregated from the exchange's own lower-interval data rather than approximated.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import AsyncIterator

import httpx

from trading.models import AssetClass, Candle, MarketDataError, Quote, Series, SymbolInfo, Timeframe
from trading.providers.base import MarketDataProvider, align_to_bucket

logger = logging.getLogger("legend.trading.coinbase")

# Granularities Coinbase serves directly, in seconds.
_NATIVE = {
    Timeframe.M1: 60,
    Timeframe.M5: 300,
    Timeframe.M15: 900,
    Timeframe.H1: 3600,
    Timeframe.D1: 86400,
}

# Everything else is built from a native interval: (source timeframe, bucket seconds).
_DERIVED = {
    Timeframe.M30: (Timeframe.M5, 1800),
    Timeframe.H4: (Timeframe.H1, 14400),
    Timeframe.W1: (Timeframe.D1, 604800),
    Timeframe.MN1: (Timeframe.D1, 2592000),
}

_QUOTES = ("USDT", "USDC", "USD", "EUR", "GBP", "BTC", "ETH", "DAI")


class CoinbaseProvider(MarketDataProvider):
    name = "coinbase"
    supported_assets = (AssetClass.CRYPTO,)
    supports_streaming = True
    max_bars_per_request = 300  # Coinbase's hard per-response cap

    REST = "https://api.exchange.coinbase.com"
    WS = "wss://ws-feed.exchange.coinbase.com"

    def __init__(self, rest_base: str | None = None, ws_base: str | None = None):
        self.rest_base = rest_base or self.REST
        self.ws_base = ws_base or self.WS
        self._products: list[SymbolInfo] = []

    @property
    def configured(self) -> bool:
        return True

    @classmethod
    def _normalize(cls, symbol: str) -> str:
        """`BTCUSDT`, `BTC/USD`, `BINANCE:BTCUSDT` -> `BTC-USD`."""
        s = symbol.upper().strip()
        if ":" in s:
            s = s.split(":", 1)[1]
        if "-" in s:
            return s
        if "/" in s:
            return s.replace("/", "-")

        for quote in _QUOTES:
            if s.endswith(quote) and len(s) > len(quote):
                base = s[: -len(quote)]
                # Coinbase quotes in USD far more widely than USDT; prefer it.
                return f"{base}-{'USD' if quote == 'USDT' else quote}"
        return s

    def _get(self, path: str, params: dict | None = None) -> object:
        try:
            response = httpx.get(
                f"{self.rest_base}{path}",
                params=params or {},
                timeout=20,
                headers={"User-Agent": "dex-trading/1.0"},
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            raise MarketDataError(
                f"Coinbase {path} returned {exc.response.status_code}: {exc.response.text[:200]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise MarketDataError(f"Coinbase {path} unreachable: {exc}") from exc

    def _fetch_native(
        self, product: str, granularity: int, limit: int, end_time: int | None
    ) -> list[Candle]:
        """One page of native-granularity candles, oldest first."""
        end = end_time or int(time.time())
        start = end - granularity * min(limit, self.max_bars_per_request)
        params = {
            "granularity": granularity,
            "start": datetime.fromtimestamp(start, tz=timezone.utc).isoformat(),
            "end": datetime.fromtimestamp(end, tz=timezone.utc).isoformat(),
        }
        rows = self._get(f"/products/{product}/candles", params)
        if not isinstance(rows, list):
            raise MarketDataError(f"Unexpected Coinbase candle payload for {product}")

        # Coinbase returns [time, low, high, open, close, volume], newest first.
        candles = [
            Candle(
                timestamp=int(r[0]),
                open=float(r[3]),
                high=float(r[2]),
                low=float(r[1]),
                close=float(r[4]),
                volume=float(r[5]),
            )
            for r in rows
        ]
        return sorted(candles, key=lambda c: c.timestamp)

    def fetch_candles(
        self, symbol: str, timeframe: Timeframe, limit: int = 500, end_time: int | None = None
    ) -> Series:
        product = self._normalize(symbol)

        if timeframe in _NATIVE:
            source_tf, granularity, bucket = timeframe, _NATIVE[timeframe], None
            needed = limit
        else:
            source_tf, bucket = _DERIVED[timeframe]
            granularity = _NATIVE[source_tf]
            # Fetch enough source bars to build `limit` aggregated ones.
            needed = limit * (bucket // granularity)

        # Page backwards until we have enough source bars.
        collected: list[Candle] = []
        cursor = end_time
        while len(collected) < needed:
            page = self._fetch_native(
                product, granularity, min(needed - len(collected), self.max_bars_per_request), cursor
            )
            if not page:
                break
            collected = page + collected
            cursor = page[0].timestamp - 1
            if len(page) < 2:
                break

        if bucket is not None:
            collected = align_to_bucket(collected, bucket)

        return Series(
            symbol=product,
            timeframe=timeframe,
            candles=collected[-limit:],
            asset_class=AssetClass.CRYPTO,
            provider=self.name,
        )

    def fetch_quote(self, symbol: str) -> Quote:
        product = self._normalize(symbol)
        ticker = self._get(f"/products/{product}/ticker")
        stats = self._get(f"/products/{product}/stats")
        if not isinstance(ticker, dict):
            raise MarketDataError(f"Unexpected Coinbase ticker payload for {product}")

        price = float(ticker["price"])
        open_24h = float(stats.get("open", 0) or 0) if isinstance(stats, dict) else 0.0
        return Quote(
            symbol=product,
            price=price,
            timestamp=int(time.time()),
            bid=float(ticker["bid"]) if ticker.get("bid") else None,
            ask=float(ticker["ask"]) if ticker.get("ask") else None,
            change=(price - open_24h) if open_24h else None,
            change_percent=((price - open_24h) / open_24h * 100) if open_24h else None,
            volume_24h=float(ticker.get("volume", 0) or 0),
        )

    def search_symbols(self, query: str, limit: int = 20) -> list[SymbolInfo]:
        if not self._products:
            data = self._get("/products")
            rows = data if isinstance(data, list) else []
            self._products = [
                SymbolInfo(
                    symbol=p["id"],
                    name=f"{p['base_currency']}/{p['quote_currency']}",
                    asset_class=AssetClass.CRYPTO,
                    provider=self.name,
                    tick_size=float(p.get("quote_increment", 0.01)),
                    quote_currency=p["quote_currency"],
                )
                for p in rows
                if p.get("status") == "online" and not p.get("trading_disabled")
            ]

        needle = query.upper().replace("/", "-")
        matches = [p for p in self._products if needle.replace("-", "") in p.symbol.replace("-", "")]
        matches.sort(key=lambda s: (s.symbol != needle, len(s.symbol)))
        return matches[:limit]

    async def stream_candles(self, symbol: str, timeframe: Timeframe) -> AsyncIterator[Candle]:
        """Build live bars from Coinbase's public ticker channel.

        The feed publishes every match (trade), so the forming bar updates
        tick-by-tick exactly as it does on the exchange's own chart.
        """
        import websockets

        product = self._normalize(symbol)
        bucket = timeframe.seconds
        current: Candle | None = None

        subscribe = json.dumps({
            "type": "subscribe",
            "product_ids": [product],
            "channels": ["ticker"],
        })

        async with websockets.connect(self.ws_base, ping_interval=20, ping_timeout=20) as ws:
            await ws.send(subscribe)
            logger.info("coinbase stream open: %s", product)

            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if msg.get("type") != "ticker" or "price" not in msg:
                    continue

                price = float(msg["price"])
                size = float(msg.get("last_size", 0) or 0)
                ts = int(time.time())
                bucket_start = ts - (ts % bucket)

                if current is None or current.timestamp != bucket_start:
                    if current is not None:
                        yield Candle(
                            current.timestamp, current.open, current.high,
                            current.low, current.close, current.volume, closed=True,
                        )
                    current = Candle(bucket_start, price, price, price, price, size, closed=False)
                else:
                    current = Candle(
                        bucket_start,
                        current.open,
                        max(current.high, price),
                        min(current.low, price),
                        price,
                        current.volume + size,
                        closed=False,
                    )
                yield current
