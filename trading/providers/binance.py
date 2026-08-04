"""Binance spot market data — real candles and a real WebSocket tick stream.

Binance's public market-data endpoints need no API key, which makes this the
provider the platform can run against out of the box: install, start, and the
charts are live on real prices rather than anything simulated.
"""
from __future__ import annotations

import json
import logging
from typing import AsyncIterator

import httpx

from trading.models import AssetClass, Candle, MarketDataError, Quote, Series, SymbolInfo, Timeframe
from trading.providers.base import MarketDataProvider

logger = logging.getLogger("legend.trading.binance")

# Binance uses the same interval codes we do apart from the monthly bar.
_INTERVALS = {
    Timeframe.M1: "1m",
    Timeframe.M5: "5m",
    Timeframe.M15: "15m",
    Timeframe.M30: "30m",
    Timeframe.H1: "1h",
    Timeframe.H4: "4h",
    Timeframe.D1: "1d",
    Timeframe.W1: "1w",
    Timeframe.MN1: "1M",
}


class BinanceProvider(MarketDataProvider):
    name = "binance"
    supported_assets = (AssetClass.CRYPTO,)
    supports_streaming = True
    max_bars_per_request = 1000

    REST = "https://api.binance.com"
    WS = "wss://stream.binance.com:9443/ws"

    def __init__(self, rest_base: str | None = None, ws_base: str | None = None):
        self.rest_base = rest_base or self.REST
        self.ws_base = ws_base or self.WS
        self._symbol_cache: list[SymbolInfo] = []

    @property
    def configured(self) -> bool:
        # Public market data requires no credentials.
        return True

    @staticmethod
    def _normalize(symbol: str) -> str:
        """`BTC/USDT`, `btc-usdt`, `BINANCE:BTCUSDT` -> `BTCUSDT`."""
        s = symbol.upper().strip()
        if ":" in s:
            s = s.split(":", 1)[1]
        return s.replace("/", "").replace("-", "").replace("_", "")

    def _get(self, path: str, params: dict) -> object:
        url = f"{self.rest_base}{path}"
        try:
            response = httpx.get(url, params=params, timeout=15)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:200]
            raise MarketDataError(f"Binance {path} returned {exc.response.status_code}: {body}") from exc
        except httpx.HTTPError as exc:
            raise MarketDataError(f"Binance {path} unreachable: {exc}") from exc

    def fetch_candles(
        self, symbol: str, timeframe: Timeframe, limit: int = 500, end_time: int | None = None
    ) -> Series:
        params = {
            "symbol": self._normalize(symbol),
            "interval": _INTERVALS[timeframe],
            "limit": max(1, min(limit, self.max_bars_per_request)),
        }
        if end_time is not None:
            params["endTime"] = end_time * 1000

        rows = self._get("/api/v3/klines", params)
        if not isinstance(rows, list):
            raise MarketDataError(f"Unexpected Binance kline payload for {symbol}")

        candles = [
            Candle(
                timestamp=int(row[0]) // 1000,
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
                closed=True,
            )
            for row in rows
        ]
        return Series(
            symbol=self._normalize(symbol),
            timeframe=timeframe,
            candles=candles,
            asset_class=AssetClass.CRYPTO,
            provider=self.name,
        )

    def fetch_quote(self, symbol: str) -> Quote:
        pair = self._normalize(symbol)
        ticker = self._get("/api/v3/ticker/24hr", {"symbol": pair})
        book = self._get("/api/v3/ticker/bookTicker", {"symbol": pair})
        if not isinstance(ticker, dict):
            raise MarketDataError(f"Unexpected Binance ticker payload for {symbol}")
        return Quote(
            symbol=pair,
            price=float(ticker["lastPrice"]),
            timestamp=int(ticker.get("closeTime", 0)) // 1000,
            bid=float(book["bidPrice"]) if isinstance(book, dict) else None,
            ask=float(book["askPrice"]) if isinstance(book, dict) else None,
            change=float(ticker["priceChange"]),
            change_percent=float(ticker["priceChangePercent"]),
            volume_24h=float(ticker["quoteVolume"]),
        )

    def search_symbols(self, query: str, limit: int = 20) -> list[SymbolInfo]:
        if not self._symbol_cache:
            data = self._get("/api/v3/exchangeInfo", {})
            symbols = data.get("symbols", []) if isinstance(data, dict) else []
            self._symbol_cache = [
                SymbolInfo(
                    symbol=s["symbol"],
                    name=f"{s['baseAsset']}/{s['quoteAsset']}",
                    asset_class=AssetClass.CRYPTO,
                    provider=self.name,
                    tick_size=self._tick_size(s),
                    quote_currency=s["quoteAsset"],
                )
                for s in symbols
                if s.get("status") == "TRADING"
            ]
        needle = query.upper().replace("/", "")
        matches = [s for s in self._symbol_cache if needle in s.symbol]
        # Exact matches first, then shortest (BTCUSDT before BTCUSDTUP etc).
        matches.sort(key=lambda s: (s.symbol != needle, len(s.symbol)))
        return matches[:limit]

    @staticmethod
    def _tick_size(spec: dict) -> float:
        for f in spec.get("filters", []):
            if f.get("filterType") == "PRICE_FILTER":
                return float(f.get("tickSize", 0.01))
        return 0.01

    async def stream_candles(self, symbol: str, timeframe: Timeframe) -> AsyncIterator[Candle]:
        """Subscribe to Binance's kline stream and yield each update.

        Binance re-sends the forming bar on every trade, so consumers get
        genuine tick-by-tick movement; `closed` flips True on the final update
        for a bar, which is the signal downstream code uses to run analysis on
        confirmed data only.
        """
        import websockets

        stream = f"{self._normalize(symbol).lower()}@kline_{_INTERVALS[timeframe]}"
        url = f"{self.ws_base}/{stream}"

        async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
            logger.info("binance stream open: %s", stream)
            async for raw in ws:
                try:
                    payload = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                k = payload.get("k")
                if not k:
                    continue
                yield Candle(
                    timestamp=int(k["t"]) // 1000,
                    open=float(k["o"]),
                    high=float(k["h"]),
                    low=float(k["l"]),
                    close=float(k["c"]),
                    volume=float(k["v"]),
                    closed=bool(k["x"]),
                )
