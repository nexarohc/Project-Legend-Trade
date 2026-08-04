"""Provider interface every market-data source implements.

The contract is deliberately small — history, quote, search, and an async
stream — so that swapping Binance for Polygon (or adding Interactive Brokers)
never touches analysis, backtesting, or UI code.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator

from trading.models import AssetClass, Candle, Quote, Series, SymbolInfo, Timeframe


def aggregate_candles(candles: list[Candle], factor: int) -> list[Candle]:
    """Fold every `factor` consecutive bars into one.

    Needed because vendors rarely offer every interval natively — Coinbase has
    no 4-hour bar, for instance. Aggregating from the exchange's own 1-hour
    bars is exact, unlike interpolating, and the bucket is anchored to the
    timeframe boundary so bars line up with every other chart of that interval.
    """
    if factor <= 1 or not candles:
        return list(candles)

    out: list[Candle] = []
    for start in range(0, len(candles), factor):
        chunk = candles[start : start + factor]
        if not chunk:
            continue
        out.append(Candle(
            timestamp=chunk[0].timestamp,
            open=chunk[0].open,
            high=max(c.high for c in chunk),
            low=min(c.low for c in chunk),
            close=chunk[-1].close,
            volume=sum(c.volume for c in chunk),
            closed=all(c.closed for c in chunk),
        ))
    return out


def align_to_bucket(candles: list[Candle], bucket_seconds: int) -> list[Candle]:
    """Group bars into fixed timestamp buckets (e.g. every 4h boundary).

    Unlike `aggregate_candles`, this keys off wall-clock boundaries rather than
    array position, so the result is stable no matter where the fetched window
    happens to start.
    """
    if not candles:
        return []

    buckets: dict[int, list[Candle]] = {}
    for c in candles:
        key = c.timestamp - (c.timestamp % bucket_seconds)
        buckets.setdefault(key, []).append(c)

    out: list[Candle] = []
    for key in sorted(buckets):
        chunk = sorted(buckets[key], key=lambda c: c.timestamp)
        out.append(Candle(
            timestamp=key,
            open=chunk[0].open,
            high=max(c.high for c in chunk),
            low=min(c.low for c in chunk),
            close=chunk[-1].close,
            volume=sum(c.volume for c in chunk),
            closed=all(c.closed for c in chunk),
        ))
    return out


class MarketDataProvider(ABC):
    """Base class for a real-time market data source."""

    name: str = "base"
    supported_assets: tuple[AssetClass, ...] = ()
    supports_streaming: bool = False

    @property
    def configured(self) -> bool:
        """True when this provider has whatever credentials it needs to run.

        Keyless public APIs override this to always return True.
        """
        return False

    @abstractmethod
    def fetch_candles(
        self, symbol: str, timeframe: Timeframe, limit: int = 500, end_time: int | None = None
    ) -> Series:
        """Return the most recent `limit` bars, oldest first."""

    @abstractmethod
    def fetch_quote(self, symbol: str) -> Quote:
        """Return the latest price snapshot for `symbol`."""

    def search_symbols(self, query: str, limit: int = 20) -> list[SymbolInfo]:
        """Symbol lookup. Providers without a search endpoint return []."""
        return []

    async def stream_candles(
        self, symbol: str, timeframe: Timeframe
    ) -> AsyncIterator[Candle]:
        """Yield live bars (including the forming one) as the market moves.

        Providers that cannot stream should leave `supports_streaming = False`;
        the stream manager then falls back to timed REST polling.
        """
        raise NotImplementedError(f"{self.name} does not support streaming")
        yield  # pragma: no cover - makes this an async generator for type checkers

    def fetch_history(
        self, symbol: str, timeframe: Timeframe, bars: int
    ) -> Series:
        """Fetch more bars than one request allows by paging backwards.

        Backtests routinely want several thousand bars while most vendors cap a
        single response at 500–1000, so page until we have enough or the
        provider stops returning older data.
        """
        page_limit = self.max_bars_per_request
        series = self.fetch_candles(symbol, timeframe, limit=min(bars, page_limit))
        while len(series) < bars:
            oldest = series.candles[0].timestamp if series.candles else None
            if oldest is None:
                break
            older = self.fetch_candles(
                symbol, timeframe, limit=min(bars - len(series), page_limit), end_time=oldest - 1
            )
            if not older.candles:
                break
            series.candles = older.candles + series.candles
            if len(older.candles) < 2:
                break
        return series

    max_bars_per_request: int = 500
