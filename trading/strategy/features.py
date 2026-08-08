"""Named feature series that strategy rules are written against.

A rule says `ema(20) crosses_above ema(50)`; this module turns those names into
aligned float arrays computed from a `Series`. Everything is computed once per
backtest and cached, because a naive implementation recomputes a 200-period EMA
inside the bar loop and turns a one-second backtest into a one-minute one.

Every feature is `None` during its warm-up period, and the rule evaluator
treats `None` as "condition not met" rather than as zero — an unwarmed
indicator must never generate a trade.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from trading import indicators as ind
from trading.models import Series

# feature name with optional integer arguments, e.g. "ema(20)", "highest(50)", "close"
_FEATURE_RE = re.compile(r"^([a-z_]+)(?:\((\d+(?:\s*,\s*\d+)*)\))?$", re.IGNORECASE)


class UnknownFeature(ValueError):
    """Raised when a rule references a feature the resolver cannot build."""


@dataclass
class FeatureSet:
    """Lazily-computed, cached named series for one price series."""

    series: Series
    _cache: dict[str, list] = field(default_factory=dict)

    def get(self, name: str) -> list:
        key = name.strip().lower()
        if key not in self._cache:
            self._cache[key] = self._build(key)
        return self._cache[key]

    def value(self, name: str, index: int) -> float | None:
        """Feature value at a bar, or None if unavailable/unwarmed."""
        data = self.get(name)
        if index < 0 or index >= len(data):
            return None
        return data[index]

    # -- construction --------------------------------------------------------

    def _build(self, key: str) -> list:
        match = _FEATURE_RE.match(key)
        if not match:
            raise UnknownFeature(f"Cannot parse feature {key!r}")

        name = match.group(1).lower()
        args = [int(a) for a in match.group(2).split(",")] if match.group(2) else []
        s = self.series

        # Raw price/volume.
        if name in ("close", "price"):
            return s.closes
        if name == "open":
            return s.opens
        if name == "high":
            return s.highs
        if name == "low":
            return s.lows
        if name == "volume":
            return s.volumes
        if name in ("hl2", "median"):
            return [(c.high + c.low) / 2 for c in s.candles]
        if name in ("hlc3", "typical"):
            return [c.typical_price for c in s.candles]

        # Moving averages.
        if name == "ema":
            return ind.ema(s.closes, args[0] if args else 20)
        if name == "sma":
            return ind.sma(s.closes, args[0] if args else 20)
        if name == "rma":
            return ind.rma(s.closes, args[0] if args else 14)
        if name == "vwap":
            return ind.vwap(s)

        # Oscillators.
        if name == "rsi":
            return ind.rsi(s.closes, args[0] if args else 14)
        if name == "cci":
            return ind.cci(s, args[0] if args else 20)
        if name == "mfi":
            return ind.mfi(s, args[0] if args else 14)
        if name == "atr":
            return ind.atr(s, args[0] if args else 14)
        if name == "obv":
            return ind.obv(s)

        if name in ("macd", "macd_signal", "macd_hist"):
            fast, slow, signal = (args + [12, 26, 9])[:3] if len(args) >= 3 else (12, 26, 9)
            line, sig, hist = ind.macd(s.closes, fast, slow, signal)
            return {"macd": line, "macd_signal": sig, "macd_hist": hist}[name]

        if name in ("adx", "plus_di", "minus_di"):
            a, p, m = ind.adx(s, args[0] if args else 14)
            return {"adx": a, "plus_di": p, "minus_di": m}[name]

        if name in ("bb_upper", "bb_middle", "bb_lower"):
            period = args[0] if args else 20
            upper, middle, lower = ind.bollinger(s.closes, period)
            return {"bb_upper": upper, "bb_middle": middle, "bb_lower": lower}[name]

        if name in ("stoch_k", "stoch_d"):
            k, d = ind.stochastic(s, args[0] if args else 14)
            return k if name == "stoch_k" else d

        if name in ("stoch_rsi_k", "stoch_rsi_d"):
            k, d = ind.stoch_rsi(s.closes)
            return k if name == "stoch_rsi_k" else d

        if name in ("supertrend", "supertrend_dir"):
            period = args[0] if args else 10
            line, direction = ind.supertrend(s, period)
            return line if name == "supertrend" else direction

        # Rolling extremes — the building blocks of breakout rules.
        # Deliberately EXCLUDES the current bar: `highest(20)` at bar i is the
        # highest high of bars i-20..i-1. Including bar i would make
        # "close > highest(20)" impossible to satisfy and, worse, would leak
        # the current bar's own high into the condition.
        if name in ("highest", "lowest"):
            period = args[0] if args else 20
            source = s.highs if name == "highest" else s.lows
            out: list = [None] * len(source)
            for i in range(period, len(source)):
                window = source[i - period : i]
                out[i] = max(window) if name == "highest" else min(window)
            return out

        if name == "volume_sma":
            return ind.sma(s.volumes, args[0] if args else 20)

        if name == "volume_ratio":
            base = ind.sma(s.volumes, args[0] if args else 20)
            return [
                (v / b) if (b not in (None, 0)) else None
                for v, b in zip(s.volumes, base)
            ]

        # Body/range descriptors used by price-action rules.
        if name == "body":
            return [c.body for c in s.candles]
        if name == "range":
            return [c.range for c in s.candles]
        if name == "body_ratio":
            return [(c.body / c.range) if c.range > 0 else 0.0 for c in s.candles]

        raise UnknownFeature(
            f"Unknown feature {key!r}. Supported: close/open/high/low/volume, ema(n), sma(n), "
            f"rsi(n), atr(n), macd, macd_signal, macd_hist, adx, plus_di, minus_di, "
            f"bb_upper/middle/lower(n), stoch_k, stoch_d, supertrend, supertrend_dir, "
            f"highest(n), lowest(n), vwap, volume_sma(n), volume_ratio(n), cci(n), mfi(n), obv, "
            f"body, range, body_ratio, hl2, hlc3."
        )

    @property
    def warmup_bars(self) -> int:
        """Bars needed before every cached feature has a value.

        The backtester skips these so no trade is ever taken on a half-computed
        indicator.
        """
        longest = 0
        for values in self._cache.values():
            first = next((i for i, v in enumerate(values) if v is not None), len(values))
            longest = max(longest, first)
        return longest
