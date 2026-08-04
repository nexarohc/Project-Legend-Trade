"""Finnhub — stocks, forex and crypto, with a trade-level WebSocket.

Requires `FINNHUB_API_KEY`. Included mainly as a second equities source so a
Polygon outage or rate-limit doesn't take the terminal offline.
"""
from __future__ import annotations

import json
import logging
import time
from typing import AsyncIterator

import httpx

from trading.models import AssetClass, Candle, MarketDataError, Quote, Series, SymbolInfo, Timeframe
from trading.providers.base import MarketDataProvider

logger = logging.getLogger("legend.trading.finnhub")

_RESOLUTIONS = {
    Timeframe.M1: "1",
    Timeframe.M5: "5",
    Timeframe.M15: "15",
    Timeframe.M30: "30",
    Timeframe.H1: "60",
    Timeframe.H4: "240",
    Timeframe.D1: "D",
    Timeframe.W1: "W",
    Timeframe.MN1: "M",
}


class FinnhubProvider(MarketDataProvider):
    name = "finnhub"
    supported_assets = (AssetClass.STOCK, AssetClass.FOREX, AssetClass.CRYPTO, AssetClass.ETF)
    supports_streaming = True
    max_bars_per_request = 5000

    REST = "https://finnhub.io/api/v1"
    WS = "wss://ws.finnhub.io"

    def __init__(self, api_key: str = ""):
        self.api_key = api_key

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _get(self, path: str, params: dict) -> dict:
        if not self.api_key:
            raise MarketDataError("FINNHUB_API_KEY is not set")
        params = dict(params)
        params["token"] = self.api_key
        try:
            response = httpx.get(f"{self.REST}{path}", params=params, timeout=20)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            raise MarketDataError(f"Finnhub {path} unreachable: {exc}") from exc

    def fetch_candles(
        self, symbol: str, timeframe: Timeframe, limit: int = 500, end_time: int | None = None
    ) -> Series:
        end = end_time or int(time.time())
        start = end - int(timeframe.seconds * limit * 2.5) - 86400 * 5
        data = self._get(
            "/stock/candle",
            {"symbol": symbol.upper(), "resolution": _RESOLUTIONS[timeframe], "from": start, "to": end},
        )
        if data.get("s") != "ok":
            raise MarketDataError(f"Finnhub returned no data for {symbol} ({data.get('s')})")

        candles = [
            Candle(
                timestamp=int(t),
                open=float(o),
                high=float(h),
                low=float(l),
                close=float(c),
                volume=float(v),
            )
            for t, o, h, l, c, v in zip(
                data["t"], data["o"], data["h"], data["l"], data["c"], data["v"]
            )
        ]
        return Series(
            symbol=symbol.upper(),
            timeframe=timeframe,
            candles=candles[-limit:],
            asset_class=AssetClass.STOCK,
            provider=self.name,
        )

    def fetch_quote(self, symbol: str) -> Quote:
        data = self._get("/quote", {"symbol": symbol.upper()})
        price = float(data.get("c") or 0.0)
        if not price:
            raise MarketDataError(f"Finnhub returned no quote for {symbol}")
        return Quote(
            symbol=symbol.upper(),
            price=price,
            timestamp=int(data.get("t") or 0),
            change=float(data.get("d") or 0.0),
            change_percent=float(data.get("dp") or 0.0),
        )

    def search_symbols(self, query: str, limit: int = 20) -> list[SymbolInfo]:
        data = self._get("/search", {"q": query})
        return [
            SymbolInfo(
                symbol=r["symbol"],
                name=r.get("description", r["symbol"]),
                asset_class=AssetClass.STOCK,
                provider=self.name,
            )
            for r in data.get("result", [])[:limit]
        ]

    async def stream_candles(self, symbol: str, timeframe: Timeframe) -> AsyncIterator[Candle]:
        """Aggregate Finnhub's trade feed into bars of the requested timeframe."""
        import websockets

        if not self.api_key:
            raise MarketDataError("FINNHUB_API_KEY is not set")

        bucket = timeframe.seconds
        current: Candle | None = None

        async with websockets.connect(f"{self.WS}?token={self.api_key}", ping_interval=20) as ws:
            await ws.send(json.dumps({"type": "subscribe", "symbol": symbol.upper()}))
            logger.info("finnhub stream open: %s", symbol)

            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if msg.get("type") != "trade":
                    continue
                for trade in msg.get("data", []):
                    price = float(trade.get("p", 0) or 0)
                    ts = int(trade.get("t", 0)) // 1000
                    size = float(trade.get("v", 0) or 0)
                    if not price or not ts:
                        continue

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
