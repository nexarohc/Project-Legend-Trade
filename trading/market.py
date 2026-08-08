"""Market data service: history caching and live stream fan-out.

Two jobs:

1. `MarketService` fetches and caches OHLCV so repeated analysis requests on the
   same symbol don't re-hit the vendor (and burn rate limit) for identical bars.
2. `StreamHub` keeps exactly one upstream subscription per symbol/timeframe no
   matter how many browser tabs are watching it, and rebroadcasts each update
   to every subscriber. Without this, ten open charts would open ten Binance
   sockets.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from trading.models import Candle, MarketDataError, Quote, Series, Timeframe
from trading.providers import get_provider, provider_chain_for, resolve_provider_for

logger = logging.getLogger("legend.trading.market")

# How long a cached history stays fresh, per timeframe. Intraday bars go stale
# fast; a daily bar barely changes within the same minute.
_CACHE_TTL = {
    Timeframe.M1: 5,
    Timeframe.M5: 15,
    Timeframe.M15: 30,
    Timeframe.M30: 60,
    Timeframe.H1: 120,
    Timeframe.H4: 300,
    Timeframe.D1: 900,
    Timeframe.W1: 3600,
    Timeframe.MN1: 3600,
}


@dataclass
class _CacheEntry:
    series: Series
    fetched_at: float


class MarketService:
    """Caching facade over the provider registry."""

    def __init__(self, ttl_override: int | None = None):
        self._cache: dict[tuple[str, str, str, int], _CacheEntry] = {}
        self._ttl_override = ttl_override

    def _ttl(self, timeframe: Timeframe) -> int:
        return self._ttl_override if self._ttl_override is not None else _CACHE_TTL[timeframe]

    def candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        limit: int = 500,
        provider: str | None = None,
        force: bool = False,
    ) -> Series:
        """Fetch `limit` bars, served from cache when still fresh.

        Walks the provider chain on failure, so a throttled or unreachable
        source falls through to the next one instead of blanking the chart.
        """
        chain = provider_chain_for(symbol, provider)
        failures: list[str] = []

        for source in chain:
            key = (source.name, symbol.upper(), timeframe.value, limit)
            entry = self._cache.get(key)
            if entry and not force and (time.time() - entry.fetched_at) < self._ttl(timeframe):
                return entry.series

            try:
                series = (
                    source.fetch_history(symbol, timeframe, limit)
                    if limit > source.max_bars_per_request
                    else source.fetch_candles(symbol, timeframe, limit)
                )
            except MarketDataError as exc:
                failures.append(f"{source.name}: {exc}")
                logger.info("provider %s failed for %s; trying next", source.name, symbol)
                continue

            if not series.candles:
                failures.append(f"{source.name}: returned no candles")
                continue

            self._cache[key] = _CacheEntry(series=series, fetched_at=time.time())
            return series

        raise MarketDataError(
            f"No provider could serve {symbol} {timeframe.value}. Tried "
            + "; ".join(failures or ["no eligible providers"])
        )

    def quote(self, symbol: str, provider: str | None = None) -> Quote:
        failures: list[str] = []
        for source in provider_chain_for(symbol, provider):
            try:
                return source.fetch_quote(symbol)
            except MarketDataError as exc:
                failures.append(f"{source.name}: {exc}")
        raise MarketDataError(
            f"No provider could quote {symbol}. Tried " + "; ".join(failures or ["none"])
        )

    def search(self, query: str, provider: str | None = None, limit: int = 20) -> list[dict]:
        source = get_provider(provider) if provider else resolve_provider_for(query)
        return [s.to_dict() for s in source.search_symbols(query, limit)]

    def multi_timeframe(
        self,
        symbol: str,
        timeframes: list[Timeframe],
        limit: int = 300,
        provider: str | None = None,
    ) -> dict[str, Series]:
        """Fetch several timeframes for one symbol — the input to HTF/LTF bias."""
        out: dict[str, Series] = {}
        for tf in timeframes:
            try:
                out[tf.value] = self.candles(symbol, tf, limit, provider)
            except MarketDataError as exc:
                logger.warning("MTF fetch failed for %s %s: %s", symbol, tf.value, exc)
        return out

    def invalidate(self, symbol: str | None = None) -> None:
        if symbol is None:
            self._cache.clear()
            return
        target = symbol.upper()
        for key in [k for k in self._cache if k[1] == target]:
            del self._cache[key]


@dataclass
class _StreamRoom:
    """One upstream subscription plus every queue listening to it."""

    symbol: str
    timeframe: Timeframe
    provider: str | None
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    task: asyncio.Task | None = None
    last: Candle | None = None


class StreamHub:
    """Fan-out hub for live candles.

    One upstream connection per (provider, symbol, timeframe); every websocket
    client attached to that room receives the same updates. Rooms shut their
    upstream down as soon as the last subscriber leaves.
    """

    # Providers that can't stream get polled at this cadence instead.
    POLL_SECONDS = 15
    RECONNECT_BACKOFF = (1, 2, 5, 10, 30)

    def __init__(self, market: MarketService | None = None):
        self.market = market or MarketService()
        self._rooms: dict[tuple[str, str, str], _StreamRoom] = {}
        self._lock = asyncio.Lock()

    def _key(self, symbol: str, timeframe: Timeframe, provider: str | None) -> tuple[str, str, str]:
        return (provider or "auto", symbol.upper(), timeframe.value)

    async def subscribe(
        self, symbol: str, timeframe: Timeframe, provider: str | None = None
    ) -> tuple[asyncio.Queue, Candle | None]:
        """Join a room. Returns the queue to read from plus the last known bar."""
        key = self._key(symbol, timeframe, provider)
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)

        async with self._lock:
            room = self._rooms.get(key)
            if room is None:
                room = _StreamRoom(symbol=symbol.upper(), timeframe=timeframe, provider=provider)
                self._rooms[key] = room
                room.task = asyncio.create_task(self._run(key, room))
            room.subscribers.add(queue)

        return queue, room.last

    async def unsubscribe(
        self, symbol: str, timeframe: Timeframe, queue: asyncio.Queue, provider: str | None = None
    ) -> None:
        key = self._key(symbol, timeframe, provider)
        async with self._lock:
            room = self._rooms.get(key)
            if room is None:
                return
            room.subscribers.discard(queue)
            if not room.subscribers:
                if room.task:
                    room.task.cancel()
                del self._rooms[key]
                logger.info("stream room closed: %s", key)

    def _publish(self, room: _StreamRoom, candle: Candle) -> None:
        room.last = candle
        for queue in list(room.subscribers):
            try:
                queue.put_nowait(candle)
            except asyncio.QueueFull:
                # A stalled client must never block the feed for everyone else;
                # drop its oldest update and keep going.
                try:
                    queue.get_nowait()
                    queue.put_nowait(candle)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    async def _run(self, key: tuple[str, str, str], room: _StreamRoom) -> None:
        """Maintain the upstream connection for one room, reconnecting on failure."""
        attempt = 0
        while True:
            try:
                source = resolve_provider_for(room.symbol, room.provider)
                if source.supports_streaming:
                    async for candle in source.stream_candles(room.symbol, room.timeframe):
                        attempt = 0  # a delivered candle proves the link is healthy
                        self._publish(room, candle)
                else:
                    await self._poll(room, source)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reconnect rather than kill the room
                delay = self.RECONNECT_BACKOFF[min(attempt, len(self.RECONNECT_BACKOFF) - 1)]
                attempt += 1
                logger.warning("stream %s failed (%s); reconnecting in %ss", key, exc, delay)
                await asyncio.sleep(delay)

    async def _poll(self, room: _StreamRoom, source) -> None:
        """REST fallback for providers without a socket."""
        while True:
            series = await asyncio.to_thread(
                source.fetch_candles, room.symbol, room.timeframe, 2
            )
            if series.candles:
                self._publish(room, series.candles[-1])
            await asyncio.sleep(self.POLL_SECONDS)

    @property
    def active_rooms(self) -> list[dict]:
        return [
            {
                "symbol": room.symbol,
                "timeframe": room.timeframe.value,
                "provider": room.provider or "auto",
                "subscribers": len(room.subscribers),
            }
            for room in self._rooms.values()
        ]


# Process-wide singletons — the backend routers share these.
market_service = MarketService()
stream_hub = StreamHub(market_service)
