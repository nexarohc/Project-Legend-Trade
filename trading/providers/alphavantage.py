"""Alpha Vantage — stocks, ETFs, FX and crypto on a free API key.

Included because its free tier is the easiest way for an individual to get a
real, quota-backed equity feed: signup takes a minute and needs no payment
details. That matters when the keyless Yahoo path is being throttled.

The free tier is genuinely small — 25 requests per day at the time of writing,
5 per minute — so this provider is positioned as a fallback rather than a
default. The platform prefers Polygon or Twelve Data when either is configured.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from trading.models import AssetClass, Candle, MarketDataError, Quote, Series, SymbolInfo, Timeframe
from trading.providers.base import MarketDataProvider, align_to_bucket

logger = logging.getLogger("legend.trading.alphavantage")

# Alpha Vantage splits intraday and daily+ across different functions.
_INTRADAY = {
    Timeframe.M1: "1min",
    Timeframe.M5: "5min",
    Timeframe.M15: "15min",
    Timeframe.M30: "30min",
    Timeframe.H1: "60min",
}
_DAILY = {
    Timeframe.D1: "TIME_SERIES_DAILY",
    Timeframe.W1: "TIME_SERIES_WEEKLY",
    Timeframe.MN1: "TIME_SERIES_MONTHLY",
}


class AlphaVantageProvider(MarketDataProvider):
    name = "alphavantage"
    supported_assets = (AssetClass.STOCK, AssetClass.ETF, AssetClass.FOREX, AssetClass.CRYPTO)
    supports_streaming = False
    max_bars_per_request = 5000

    BASE = "https://www.alphavantage.co/query"

    def __init__(self, api_key: str = ""):
        self.api_key = api_key

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _get(self, params: dict) -> dict:
        if not self.api_key:
            raise MarketDataError("ALPHAVANTAGE_API_KEY is not set")

        params = dict(params)
        params["apikey"] = self.api_key
        try:
            response = httpx.get(self.BASE, params=params, timeout=25)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            raise MarketDataError(f"Alpha Vantage unreachable: {exc}") from exc

        # Alpha Vantage returns errors inside a 200 response, with several
        # different key names depending on what went wrong.
        if "Error Message" in data:
            raise MarketDataError(f"Alpha Vantage: {data['Error Message']}")
        if "Note" in data:
            raise MarketDataError(
                "Alpha Vantage rate limit reached (the free tier allows 5 calls per minute "
                "and 25 per day). Wait, or configure a Polygon or Twelve Data key."
            )
        if "Information" in data:
            raise MarketDataError(f"Alpha Vantage: {data['Information']}")
        return data

    @staticmethod
    def _parse_time(raw: str) -> int:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return int(datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc).timestamp())
            except ValueError:
                continue
        raise MarketDataError(f"Unrecognised Alpha Vantage timestamp: {raw!r}")

    @staticmethod
    def _normalize(symbol: str) -> str:
        s = symbol.upper().strip()
        if ":" in s:
            s = s.split(":", 1)[1].strip()
        return s.replace(" ", "")

    def fetch_candles(
        self, symbol: str, timeframe: Timeframe, limit: int = 500, end_time: int | None = None
    ) -> Series:
        ticker = self._normalize(symbol)

        if timeframe in _INTRADAY:
            params = {
                "function": "TIME_SERIES_INTRADAY",
                "symbol": ticker,
                "interval": _INTRADAY[timeframe],
                "outputsize": "full" if limit > 100 else "compact",
            }
            bucket = None
        elif timeframe in _DAILY:
            params = {
                "function": _DAILY[timeframe],
                "symbol": ticker,
                "outputsize": "full" if limit > 100 else "compact",
            }
            bucket = None
        elif timeframe is Timeframe.H4:
            # No native 4-hour bar; aggregate from hourly.
            params = {
                "function": "TIME_SERIES_INTRADAY",
                "symbol": ticker,
                "interval": "60min",
                "outputsize": "full",
            }
            bucket = 14400
        else:
            raise MarketDataError(f"Alpha Vantage does not serve {timeframe.value} bars")

        data = self._get(params)

        series_key = next((k for k in data if "Time Series" in k or "Weekly" in k or "Monthly" in k), None)
        if not series_key:
            raise MarketDataError(f"Alpha Vantage returned no series for {ticker}")

        candles: list[Candle] = []
        for stamp, row in data[series_key].items():
            candles.append(Candle(
                timestamp=self._parse_time(stamp),
                open=float(row["1. open"]),
                high=float(row["2. high"]),
                low=float(row["3. low"]),
                close=float(row["4. close"]),
                volume=float(row.get("5. volume") or row.get("6. volume") or 0.0),
            ))

        candles.sort(key=lambda c: c.timestamp)
        if bucket is not None:
            candles = align_to_bucket(candles, bucket)
        if end_time is not None:
            candles = [c for c in candles if c.timestamp <= end_time]

        if not candles:
            raise MarketDataError(f"Alpha Vantage returned no bars for {ticker}")

        return Series(
            symbol=ticker,
            timeframe=timeframe,
            candles=candles[-limit:],
            asset_class=AssetClass.STOCK,
            provider=self.name,
        )

    def fetch_quote(self, symbol: str) -> Quote:
        ticker = self._normalize(symbol)
        data = self._get({"function": "GLOBAL_QUOTE", "symbol": ticker})
        quote = data.get("Global Quote") or {}
        price = quote.get("05. price")
        if not price:
            raise MarketDataError(f"Alpha Vantage returned no quote for {ticker}")

        change_percent = (quote.get("10. change percent") or "0%").rstrip("%")
        return Quote(
            symbol=ticker,
            price=float(price),
            timestamp=int(datetime.now(tz=timezone.utc).timestamp()),
            change=float(quote.get("09. change") or 0.0),
            change_percent=float(change_percent or 0.0),
            volume_24h=float(quote.get("06. volume") or 0.0) or None,
        )

    def search_symbols(self, query: str, limit: int = 20) -> list[SymbolInfo]:
        data = self._get({"function": "SYMBOL_SEARCH", "keywords": query})
        return [
            SymbolInfo(
                symbol=match["1. symbol"],
                name=f"{match['2. name']} ({match.get('4. region', '')})".strip(),
                asset_class=AssetClass.STOCK,
                provider=self.name,
                quote_currency=match.get("8. currency", "USD"),
            )
            for match in data.get("bestMatches", [])[:limit]
        ]
