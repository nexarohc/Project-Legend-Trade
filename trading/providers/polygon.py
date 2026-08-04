"""Polygon.io — stocks, options, forex, indices and crypto.

Requires `POLYGON_API_KEY`. Polygon streams over its own WebSocket cluster with
an auth handshake, which this provider performs before subscribing to
aggregate-per-second messages.
"""
from __future__ import annotations

import json
import logging
import time
from typing import AsyncIterator

import httpx

from trading.models import AssetClass, Candle, MarketDataError, Quote, Series, SymbolInfo, Timeframe
from trading.providers.base import MarketDataProvider

logger = logging.getLogger("legend.trading.polygon")

# Polygon expresses intervals as a (multiplier, timespan) pair.
_INTERVALS = {
    Timeframe.M1: (1, "minute"),
    Timeframe.M5: (5, "minute"),
    Timeframe.M15: (15, "minute"),
    Timeframe.M30: (30, "minute"),
    Timeframe.H1: (1, "hour"),
    Timeframe.H4: (4, "hour"),
    Timeframe.D1: (1, "day"),
    Timeframe.W1: (1, "week"),
    Timeframe.MN1: (1, "month"),
}


class PolygonProvider(MarketDataProvider):
    name = "polygon"
    supported_assets = (
        AssetClass.STOCK, AssetClass.ETF, AssetClass.INDEX,
        AssetClass.FOREX, AssetClass.CRYPTO, AssetClass.OPTION,
    )
    supports_streaming = True
    max_bars_per_request = 5000

    REST = "https://api.polygon.io"
    WS = "wss://socket.polygon.io"

    def __init__(self, api_key: str = ""):
        self.api_key = api_key

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _require_key(self) -> None:
        if not self.api_key:
            raise MarketDataError("POLYGON_API_KEY is not set")

    def _get(self, path: str, params: dict | None = None) -> dict:
        self._require_key()
        params = dict(params or {})
        params["apiKey"] = self.api_key
        try:
            response = httpx.get(f"{self.REST}{path}", params=params, timeout=20)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            raise MarketDataError(
                f"Polygon {path} returned {exc.response.status_code}: {exc.response.text[:200]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise MarketDataError(f"Polygon {path} unreachable: {exc}") from exc

    def fetch_candles(
        self, symbol: str, timeframe: Timeframe, limit: int = 500, end_time: int | None = None
    ) -> Series:
        mult, span = _INTERVALS[timeframe]
        end = end_time or int(time.time())
        # Reach back far enough to cover `limit` bars, with slack for weekends
        # and holidays when the market simply produces no bars at all.
        span_seconds = timeframe.seconds
        start = end - int(span_seconds * limit * 2.5) - 86400 * 5

        path = f"/v2/aggs/ticker/{symbol.upper()}/range/{mult}/{span}/{start * 1000}/{end * 1000}"
        data = self._get(path, {"adjusted": "true", "sort": "asc", "limit": self.max_bars_per_request})

        results = data.get("results") or []
        candles = [
            Candle(
                timestamp=int(r["t"]) // 1000,
                open=float(r["o"]),
                high=float(r["h"]),
                low=float(r["l"]),
                close=float(r["c"]),
                volume=float(r.get("v", 0.0)),
            )
            for r in results
        ]
        return Series(
            symbol=symbol.upper(),
            timeframe=timeframe,
            candles=candles[-limit:],
            asset_class=self._classify(symbol),
            provider=self.name,
        )

    def fetch_quote(self, symbol: str) -> Quote:
        data = self._get(f"/v2/aggs/ticker/{symbol.upper()}/prev", {"adjusted": "true"})
        results = data.get("results") or []
        if not results:
            raise MarketDataError(f"Polygon returned no quote for {symbol}")
        r = results[0]
        close, open_ = float(r["c"]), float(r["o"])
        return Quote(
            symbol=symbol.upper(),
            price=close,
            timestamp=int(r["t"]) // 1000,
            change=close - open_,
            change_percent=((close - open_) / open_ * 100) if open_ else None,
            volume_24h=float(r.get("v", 0.0)),
        )

    def search_symbols(self, query: str, limit: int = 20) -> list[SymbolInfo]:
        data = self._get("/v3/reference/tickers", {"search": query, "active": "true", "limit": limit})
        return [
            SymbolInfo(
                symbol=t["ticker"],
                name=t.get("name", t["ticker"]),
                asset_class=self._classify(t["ticker"], t.get("market")),
                provider=self.name,
                quote_currency=t.get("currency_name", "USD").upper(),
            )
            for t in data.get("results", [])
        ]

    @staticmethod
    def _classify(symbol: str, market: str | None = None) -> AssetClass:
        s = symbol.upper()
        if s.startswith("X:") or market == "crypto":
            return AssetClass.CRYPTO
        if s.startswith("C:") or market == "fx":
            return AssetClass.FOREX
        if s.startswith("I:") or market == "indices":
            return AssetClass.INDEX
        if s.startswith("O:"):
            return AssetClass.OPTION
        return AssetClass.STOCK

    def _ws_cluster(self, symbol: str) -> tuple[str, str]:
        """Polygon splits streaming by asset cluster; pick the right host + channel."""
        asset = self._classify(symbol)
        if asset is AssetClass.CRYPTO:
            return "crypto", f"XA.{symbol.upper().removeprefix('X:')}"
        if asset is AssetClass.FOREX:
            return "forex", f"CA.{symbol.upper().removeprefix('C:')}"
        return "stocks", f"AM.{symbol.upper()}"

    async def stream_candles(self, symbol: str, timeframe: Timeframe) -> AsyncIterator[Candle]:
        """Stream Polygon per-minute aggregates, folded into the requested timeframe.

        Polygon's aggregate channel emits one-minute bars; for anything higher
        we accumulate them into the enclosing bucket so the consumer still sees
        a single forming candle for its chosen timeframe.
        """
        import websockets

        self._require_key()
        cluster, channel = self._ws_cluster(symbol)
        url = f"{self.WS}/{cluster}"
        bucket = timeframe.seconds
        current: Candle | None = None

        async with websockets.connect(url, ping_interval=20) as ws:
            await ws.send(json.dumps({"action": "auth", "params": self.api_key}))
            await ws.send(json.dumps({"action": "subscribe", "params": channel}))
            logger.info("polygon stream open: %s", channel)

            async for raw in ws:
                try:
                    messages = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                for msg in messages if isinstance(messages, list) else [messages]:
                    if msg.get("ev") not in ("AM", "XA", "CA"):
                        continue
                    ts = int(msg.get("s", 0)) // 1000
                    if not ts:
                        continue
                    bucket_start = ts - (ts % bucket)
                    o, h, l, c = (
                        float(msg["o"]), float(msg["h"]), float(msg["l"]), float(msg["c"])
                    )
                    v = float(msg.get("v", 0.0))

                    if current is None or current.timestamp != bucket_start:
                        if current is not None:
                            # Previous bucket is now complete.
                            yield Candle(
                                current.timestamp, current.open, current.high,
                                current.low, current.close, current.volume, closed=True,
                            )
                        current = Candle(bucket_start, o, h, l, c, v, closed=False)
                    else:
                        current = Candle(
                            bucket_start,
                            current.open,
                            max(current.high, h),
                            min(current.low, l),
                            c,
                            current.volume + v,
                            closed=False,
                        )
                    yield current
