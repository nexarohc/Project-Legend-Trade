"""Core market-data value types shared by every trading module.

Everything downstream (indicators, structure detection, backtester, Pine
generator) consumes `Series` — a chronologically ordered list of `Candle`
objects plus the symbol/timeframe it describes. Keeping one canonical shape
means a provider swap never ripples into analysis code.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class AssetClass(str, Enum):
    CRYPTO = "crypto"
    FOREX = "forex"
    STOCK = "stock"
    INDEX = "index"
    COMMODITY = "commodity"
    FUTURE = "future"
    OPTION = "option"
    ETF = "etf"


class Timeframe(str, Enum):
    """Supported bar intervals.

    The string values are the canonical form used across the API and the UI;
    each provider maps them onto its own vendor-specific codes.
    """

    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"
    W1 = "1w"
    MN1 = "1M"

    @property
    def seconds(self) -> int:
        return {
            "1m": 60,
            "5m": 300,
            "15m": 900,
            "30m": 1800,
            "1h": 3600,
            "4h": 14400,
            "1d": 86400,
            "1w": 604800,
            "1M": 2592000,
        }[self.value]

    @property
    def minutes(self) -> int:
        return self.seconds // 60

    @classmethod
    def parse(cls, raw: str) -> "Timeframe":
        """Accept the canonical form plus common aliases (`60`, `1H`, `D`, `daily`)."""
        text = str(raw).strip()
        aliases = {
            "1": cls.M1, "5": cls.M5, "15": cls.M15, "30": cls.M30,
            "60": cls.H1, "240": cls.H4, "d": cls.D1, "daily": cls.D1,
            "w": cls.W1, "weekly": cls.W1, "m": cls.MN1, "monthly": cls.MN1,
        }
        for member in cls:
            if member.value.lower() == text.lower():
                return member
        key = text.lower()
        if key in aliases:
            return aliases[key]
        raise ValueError(f"Unsupported timeframe: {raw!r}")

    def higher(self) -> "Timeframe":
        """The conventional 'one step up' timeframe used for HTF bias."""
        ladder = [Timeframe.M1, Timeframe.M5, Timeframe.M15, Timeframe.M30,
                  Timeframe.H1, Timeframe.H4, Timeframe.D1, Timeframe.W1, Timeframe.MN1]
        idx = ladder.index(self)
        return ladder[min(idx + 2, len(ladder) - 1)]

    def lower(self) -> "Timeframe":
        ladder = [Timeframe.M1, Timeframe.M5, Timeframe.M15, Timeframe.M30,
                  Timeframe.H1, Timeframe.H4, Timeframe.D1, Timeframe.W1, Timeframe.MN1]
        idx = ladder.index(self)
        return ladder[max(idx - 2, 0)]


@dataclass(frozen=True)
class Candle:
    """One OHLCV bar. `timestamp` is the bar's OPEN time, in epoch seconds UTC."""

    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    closed: bool = True

    @property
    def datetime(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp, tz=timezone.utc)

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def bullish(self) -> bool:
        return self.close > self.open

    @property
    def bearish(self) -> bool:
        return self.close < self.open

    @property
    def typical_price(self) -> float:
        return (self.high + self.low + self.close) / 3

    def to_dict(self) -> dict:
        return {
            "time": self.timestamp,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "closed": self.closed,
        }


@dataclass
class Series:
    """An ordered OHLCV history for one symbol/timeframe."""

    symbol: str
    timeframe: Timeframe
    candles: list[Candle] = field(default_factory=list)
    asset_class: AssetClass = AssetClass.CRYPTO
    provider: str = ""

    def __len__(self) -> int:
        return len(self.candles)

    def __getitem__(self, idx):
        return self.candles[idx]

    def __iter__(self):
        return iter(self.candles)

    @property
    def closes(self) -> list[float]:
        return [c.close for c in self.candles]

    @property
    def opens(self) -> list[float]:
        return [c.open for c in self.candles]

    @property
    def highs(self) -> list[float]:
        return [c.high for c in self.candles]

    @property
    def lows(self) -> list[float]:
        return [c.low for c in self.candles]

    @property
    def volumes(self) -> list[float]:
        return [c.volume for c in self.candles]

    @property
    def last(self) -> Candle | None:
        return self.candles[-1] if self.candles else None

    def tail(self, n: int) -> "Series":
        return Series(self.symbol, self.timeframe, self.candles[-n:], self.asset_class, self.provider)

    def slice(self, start: int, end: int | None = None) -> "Series":
        return Series(self.symbol, self.timeframe, self.candles[start:end], self.asset_class, self.provider)

    def upsert(self, candle: Candle) -> bool:
        """Merge a streamed candle in place.

        Returns True when this replaced the in-flight bar, False when it
        appended a new one. Live streams re-send the forming bar many times a
        second, so replacing by timestamp (not appending) is what keeps the
        series correct.
        """
        if self.candles and self.candles[-1].timestamp == candle.timestamp:
            self.candles[-1] = candle
            return True
        if self.candles and candle.timestamp < self.candles[-1].timestamp:
            return True  # late/out-of-order tick for a closed bar: ignore
        self.candles.append(candle)
        return False

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe.value,
            "asset_class": self.asset_class.value,
            "provider": self.provider,
            "candles": [c.to_dict() for c in self.candles],
        }


@dataclass(frozen=True)
class Quote:
    """A top-of-book snapshot. Bid/ask are None on providers that don't stream them."""

    symbol: str
    price: float
    timestamp: int
    bid: float | None = None
    ask: float | None = None
    change: float | None = None
    change_percent: float | None = None
    volume_24h: float | None = None

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "price": self.price,
            "timestamp": self.timestamp,
            "bid": self.bid,
            "ask": self.ask,
            "change": self.change,
            "change_percent": self.change_percent,
            "volume_24h": self.volume_24h,
        }


@dataclass(frozen=True)
class SymbolInfo:
    symbol: str
    name: str
    asset_class: AssetClass
    provider: str
    tick_size: float = 0.01
    quote_currency: str = "USD"

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "asset_class": self.asset_class.value,
            "provider": self.provider,
            "tick_size": self.tick_size,
            "quote_currency": self.quote_currency,
        }


class MarketDataError(RuntimeError):
    """Raised when a provider cannot serve a request (bad symbol, no key, upstream down)."""
