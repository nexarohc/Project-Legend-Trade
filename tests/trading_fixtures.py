"""Deterministic synthetic market data for the trading tests.

Every trading test runs against generated series rather than a live feed. Tests
that depend on the network are not tests — they fail when an exchange rate-limits
you, and they pass for the wrong reasons when a provider silently changes its
response shape.

The generators produce recognisable structures (a clean uptrend, a range, a
sweep, a gap) so a detector can be asserted against a known answer rather than
against whatever the market happened to do.
"""
from __future__ import annotations

import math
import random

from trading.models import Candle, Series, Timeframe

BASE_TIME = 1_700_000_000  # fixed epoch so bar timestamps are reproducible


def make_series(
    candles: list[Candle],
    symbol: str = "TESTUSDT",
    timeframe: Timeframe = Timeframe.H1,
) -> Series:
    return Series(symbol=symbol, timeframe=timeframe, candles=candles, provider="synthetic")


def candle(index: int, o: float, h: float, l: float, c: float, v: float = 1000.0,
           step: int = 3600) -> Candle:
    return Candle(BASE_TIME + index * step, o, h, l, c, v)


def trending_series(bars: int = 300, drift: float = 0.15, seed: int = 42,
                    start: float = 100.0) -> Series:
    """A noisy but persistent uptrend (or downtrend when drift is negative)."""
    rnd = random.Random(seed)
    candles: list[Candle] = []
    price = start
    for i in range(bars):
        price = max(1.0, price * (1 + drift / 100 + rnd.gauss(0, 0.003)))
        o = price * (1 + rnd.gauss(0, 0.001))
        c = price * (1 + rnd.gauss(0, 0.0015))
        h = max(o, c) * (1 + abs(rnd.gauss(0, 0.0015)))
        l = min(o, c) * (1 - abs(rnd.gauss(0, 0.0015)))
        candles.append(candle(i, o, h, l, c, abs(rnd.gauss(1000, 200)) + 100))
    return make_series(candles)


def ranging_series(bars: int = 300, seed: int = 7, centre: float = 100.0,
                   width: float = 4.0) -> Series:
    """Price oscillating inside a horizontal band — no trend to find."""
    rnd = random.Random(seed)
    candles: list[Candle] = []
    for i in range(bars):
        mid = centre + math.sin(i / 8) * width
        o = mid + rnd.gauss(0, 0.3)
        c = mid + rnd.gauss(0, 0.3)
        h = max(o, c) + abs(rnd.gauss(0, 0.4))
        l = min(o, c) - abs(rnd.gauss(0, 0.4))
        candles.append(candle(i, o, h, l, c, abs(rnd.gauss(1000, 150)) + 100))
    return make_series(candles)


def flat_series(bars: int = 60, price: float = 100.0) -> Series:
    """Perfectly flat bars — used to assert indicators degrade sanely."""
    return make_series([candle(i, price, price, price, price, 100.0) for i in range(bars)])


def random_walk_series(bars: int = 400, seed: int = 0, start: float = 100.0,
                       volatility: float = 0.004) -> Series:
    """A driftless geometric random walk — a market with no edge in it.

    Unlike `trending_series` and `ranging_series`, which contain a structure a
    detector is *supposed* to find, this contains nothing. Each bar's return is
    drawn from a zero-mean distribution, so no rule computed from past bars can
    predict the next one. That makes it the control condition: any strategy
    that appears profitable here is measuring a flaw in the backtester, not a
    property of the data.

    The intrabar path is built so the high and low bracket the open and close
    honestly, without either being systematically reachable first — a generator
    that always drew the favourable extreme would hand the backtester a bias
    the test is meant to detect.
    """
    rnd = random.Random(seed)
    candles: list[Candle] = []
    price = start
    for i in range(bars):
        o = price
        c = max(0.01, o * (1 + rnd.gauss(0, volatility)))
        h = max(o, c) * (1 + abs(rnd.gauss(0, volatility / 2)))
        l = min(o, c) * (1 - abs(rnd.gauss(0, volatility / 2)))
        candles.append(candle(i, o, h, l, c, abs(rnd.gauss(1000, 200)) + 100))
        price = c
    return make_series(candles)


def series_with_sweep() -> Series:
    """A swing high, a wick through it that closes back below, then continuation.

    Built by hand so the expected sweep level is known exactly.
    """
    bars: list[tuple[float, float, float, float]] = []

    # Rise into a swing high at 110.
    for i in range(12):
        base = 100 + i * 0.8
        bars.append((base, base + 0.6, base - 0.5, base + 0.4))
    bars.append((109.5, 110.0, 109.2, 109.8))    # the swing high (110.0)

    # Pull back so the high becomes a confirmed pivot.
    for i in range(10):
        base = 109 - i * 0.7
        bars.append((base, base + 0.4, base - 0.6, base - 0.3))

    # Recover toward the high.
    for i in range(8):
        base = 102 + i * 0.9
        bars.append((base, base + 0.5, base - 0.4, base + 0.4))

    # The sweep: wick above 110, close well below it.
    bars.append((109.0, 111.5, 108.8, 109.0))

    # Sell off afterwards.
    for i in range(12):
        base = 108 - i * 0.8
        bars.append((base, base + 0.4, base - 0.7, base - 0.5))

    return make_series([candle(i, o, h, l, c) for i, (o, h, l, c) in enumerate(bars)])


def series_with_gap() -> Series:
    """A series containing a large overnight-style gap, for gap-risk tests."""
    candles = [candle(i, 100 + i * 0.1, 100.5 + i * 0.1, 99.5 + i * 0.1, 100.2 + i * 0.1)
               for i in range(40)]
    # Gap down 8% on bar 40 and continue from there.
    candles.append(candle(40, 96.0, 96.2, 95.0, 95.5))
    candles.extend(
        candle(i, 95 + (i - 41) * 0.1, 95.5 + (i - 41) * 0.1,
               94.5 + (i - 41) * 0.1, 95.2 + (i - 41) * 0.1)
        for i in range(41, 80)
    )
    return make_series(candles)


def series_with_fvg() -> Series:
    """Three bars forming an unambiguous bullish fair value gap."""
    candles = [candle(i, 100, 100.5, 99.5, 100) for i in range(20)]
    candles.append(candle(20, 100.0, 100.5, 99.8, 100.4))   # bar i-1, high 100.5
    candles.append(candle(21, 100.5, 104.0, 100.4, 103.8))  # displacement bar
    candles.append(candle(22, 103.8, 104.5, 102.0, 104.2))  # bar i+1, low 102.0 > 100.5
    candles.extend(candle(i, 104, 104.5, 103.5, 104) for i in range(23, 40))
    return make_series(candles)
