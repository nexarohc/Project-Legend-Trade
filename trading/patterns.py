"""Candlestick and price-action pattern detection.

Patterns are scored, not just flagged. A pin bar with a 4x wick at a swing low
is not the same signal as a marginal one in the middle of a range, so each
detection carries a 0-1 `strength` that the probability engine weights by.

Sizes are measured against a rolling average range rather than absolute price,
so the same thresholds work on BTC at $60,000 and EURUSD at 1.08.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from trading.models import Candle, Series


class PatternBias(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


@dataclass
class Pattern:
    name: str
    bias: PatternBias
    index: int
    timestamp: int
    strength: float  # 0-1
    evidence: str

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "bias": self.bias.value,
            "index": self.index,
            "time": self.timestamp,
            "strength": round(self.strength, 2),
            "evidence": self.evidence,
        }


def _avg_range(candles: list[Candle], end: int, period: int = 14) -> float:
    window = candles[max(0, end - period) : end]
    if not window:
        return candles[end].range or 1e-9
    avg = sum(c.range for c in window) / len(window)
    return avg or 1e-9


def _is_doji(c: Candle, avg: float) -> bool:
    return c.range > 0 and c.body <= 0.1 * c.range and c.range >= 0.3 * avg


def detect_patterns(series: Series, lookback: int = 60) -> list[Pattern]:
    """Scan the most recent `lookback` bars for every supported pattern."""
    candles = series.candles
    n = len(candles)
    if n < 5:
        return []

    found: list[Pattern] = []
    start = max(3, n - lookback)

    for i in range(start, n):
        c = candles[i]
        prev = candles[i - 1]
        avg = _avg_range(candles, i)
        if c.range <= 0:
            continue

        found.extend(_single_bar(c, i, avg))
        found.extend(_two_bar(c, prev, i, avg))
        if i >= 2:
            found.extend(_three_bar(candles, i, avg))

    return found


def _single_bar(c: Candle, i: int, avg: float) -> list[Pattern]:
    out: list[Pattern] = []
    body_ratio = c.body / c.range

    # Doji — indecision.
    if _is_doji(c, avg):
        out.append(Pattern(
            "Doji", PatternBias.NEUTRAL, i, c.timestamp,
            strength=min(1.0, c.range / avg * 0.5),
            evidence=f"Body is {body_ratio:.1%} of the {c.range:.6g} range — open and close nearly equal.",
        ))

    # Hammer / Shooting star: one long wick, small body at the opposite end.
    if c.lower_wick >= 2 * c.body and c.upper_wick <= 0.35 * c.body and c.body > 0:
        out.append(Pattern(
            "Hammer", PatternBias.BULLISH, i, c.timestamp,
            strength=min(1.0, (c.lower_wick / c.body) / 4),
            evidence=(
                f"Lower wick {c.lower_wick:.6g} is {c.lower_wick / c.body:.1f}x the body "
                f"with almost no upper wick — sellers pushed down and were absorbed."
            ),
        ))
    if c.upper_wick >= 2 * c.body and c.lower_wick <= 0.35 * c.body and c.body > 0:
        out.append(Pattern(
            "Shooting Star", PatternBias.BEARISH, i, c.timestamp,
            strength=min(1.0, (c.upper_wick / c.body) / 4),
            evidence=(
                f"Upper wick {c.upper_wick:.6g} is {c.upper_wick / c.body:.1f}x the body — "
                f"buyers pushed up and were rejected."
            ),
        ))

    # Pin bar — either-side rejection candle, judged on wick share of range.
    dominant_wick = max(c.upper_wick, c.lower_wick)
    if dominant_wick >= 0.66 * c.range and c.range >= 0.8 * avg:
        bullish = c.lower_wick > c.upper_wick
        out.append(Pattern(
            "Pin Bar", PatternBias.BULLISH if bullish else PatternBias.BEARISH, i, c.timestamp,
            strength=min(1.0, dominant_wick / c.range),
            evidence=(
                f"{'Lower' if bullish else 'Upper'} wick is {dominant_wick / c.range:.0%} of the bar's "
                f"range on an above-average {c.range / avg:.1f}x bar."
            ),
        ))

    # Marubozu — full-body conviction candle.
    if body_ratio >= 0.9 and c.range >= 1.2 * avg:
        out.append(Pattern(
            "Marubozu", PatternBias.BULLISH if c.bullish else PatternBias.BEARISH, i, c.timestamp,
            strength=min(1.0, c.range / avg / 2),
            evidence=(
                f"Body is {body_ratio:.0%} of a {c.range / avg:.1f}x-average range with negligible "
                f"wicks — one-sided control for the whole bar."
            ),
        ))
    return out


def _two_bar(c: Candle, prev: Candle, i: int, avg: float) -> list[Pattern]:
    out: list[Pattern] = []

    # Engulfing — current body fully covers the previous body, opposite colour.
    if c.bullish and prev.bearish and c.close >= prev.open and c.open <= prev.close and prev.body > 0:
        out.append(Pattern(
            "Bullish Engulfing", PatternBias.BULLISH, i, c.timestamp,
            strength=min(1.0, c.body / max(prev.body, 1e-9) / 2),
            evidence=(
                f"Bull body {c.body:.6g} fully engulfs the prior bear body {prev.body:.6g} "
                f"({c.body / prev.body:.1f}x)."
            ),
        ))
    if c.bearish and prev.bullish and c.close <= prev.open and c.open >= prev.close and prev.body > 0:
        out.append(Pattern(
            "Bearish Engulfing", PatternBias.BEARISH, i, c.timestamp,
            strength=min(1.0, c.body / max(prev.body, 1e-9) / 2),
            evidence=(
                f"Bear body {c.body:.6g} fully engulfs the prior bull body {prev.body:.6g} "
                f"({c.body / prev.body:.1f}x)."
            ),
        ))

    # Inside / outside bars — compression and expansion in two-bar form.
    if c.high <= prev.high and c.low >= prev.low:
        out.append(Pattern(
            "Inside Bar", PatternBias.NEUTRAL, i, c.timestamp,
            strength=min(1.0, 1 - (c.range / max(prev.range, 1e-9))),
            evidence=f"Whole bar contained inside the prior bar's {prev.low:.6g}-{prev.high:.6g} range.",
        ))
    if c.high > prev.high and c.low < prev.low:
        out.append(Pattern(
            "Outside Bar", PatternBias.BULLISH if c.bullish else PatternBias.BEARISH, i, c.timestamp,
            strength=min(1.0, c.range / max(prev.range, 1e-9) / 2),
            evidence=(
                f"Bar takes out both sides of the prior range and closes "
                f"{'up' if c.bullish else 'down'}."
            ),
        ))
    return out


def _three_bar(candles: list[Candle], i: int, avg: float) -> list[Pattern]:
    out: list[Pattern] = []
    a, b, c = candles[i - 2], candles[i - 1], candles[i]

    # Morning / evening star — reversal with an indecision bar in the middle.
    if a.bearish and c.bullish and b.body < 0.5 * a.body and c.close > (a.open + a.close) / 2:
        out.append(Pattern(
            "Morning Star", PatternBias.BULLISH, i, c.timestamp,
            strength=min(1.0, (c.close - (a.open + a.close) / 2) / max(a.body, 1e-9)),
            evidence=(
                "Down bar, small-bodied indecision bar, then a bull bar closing back above the "
                "midpoint of the first — sellers lost control across three bars."
            ),
        ))
    if a.bullish and c.bearish and b.body < 0.5 * a.body and c.close < (a.open + a.close) / 2:
        out.append(Pattern(
            "Evening Star", PatternBias.BEARISH, i, c.timestamp,
            strength=min(1.0, ((a.open + a.close) / 2 - c.close) / max(a.body, 1e-9)),
            evidence=(
                "Up bar, small-bodied indecision bar, then a bear bar closing back below the "
                "midpoint of the first — buyers lost control across three bars."
            ),
        ))

    # Three soldiers / crows — sustained one-way pressure.
    trio = (a, b, c)
    if all(x.bullish for x in trio) and a.close < b.close < c.close:
        if all(x.body > 0.5 * x.range for x in trio):
            out.append(Pattern(
                "Three White Soldiers", PatternBias.BULLISH, i, c.timestamp,
                strength=min(1.0, sum(x.body for x in trio) / (3 * avg)),
                evidence="Three consecutive strong-bodied bull bars each closing higher.",
            ))
    if all(x.bearish for x in trio) and a.close > b.close > c.close:
        if all(x.body > 0.5 * x.range for x in trio):
            out.append(Pattern(
                "Three Black Crows", PatternBias.BEARISH, i, c.timestamp,
                strength=min(1.0, sum(x.body for x in trio) / (3 * avg)),
                evidence="Three consecutive strong-bodied bear bars each closing lower.",
            ))
    return out


def detect_fake_breakouts(series: Series, lookback: int = 60, window: int = 20) -> list[Pattern]:
    """Bars that break a recent extreme then close back inside it.

    Distinct from a liquidity sweep in `structure.py`: that one is anchored to
    confirmed swing pivots, this one to the rolling N-bar high/low, so it also
    catches failed breaks of consolidation edges that never formed a pivot.
    """
    candles = series.candles
    n = len(candles)
    out: list[Pattern] = []
    start = max(window, n - lookback)

    for i in range(start, n):
        c = candles[i]
        prior = candles[i - window : i]
        if not prior:
            continue
        prior_high = max(x.high for x in prior)
        prior_low = min(x.low for x in prior)

        if c.high > prior_high and c.close < prior_high:
            out.append(Pattern(
                "Fake Breakout (upside)", PatternBias.BEARISH, i, c.timestamp,
                strength=min(1.0, (c.high - prior_high) / max(c.range, 1e-9)),
                evidence=(
                    f"Broke the {window}-bar high {prior_high:.6g} intrabar to {c.high:.6g} "
                    f"but closed back beneath it at {c.close:.6g}."
                ),
            ))
        if c.low < prior_low and c.close > prior_low:
            out.append(Pattern(
                "Fake Breakout (downside)", PatternBias.BULLISH, i, c.timestamp,
                strength=min(1.0, (prior_low - c.low) / max(c.range, 1e-9)),
                evidence=(
                    f"Broke the {window}-bar low {prior_low:.6g} intrabar to {c.low:.6g} "
                    f"but closed back above it at {c.close:.6g}."
                ),
            ))
    return out


def analyze_price_action(series: Series, lookback: int = 60) -> dict:
    """Full price-action pass: patterns, fakeouts, and the resulting net bias."""
    patterns = detect_patterns(series, lookback)
    fakeouts = detect_fake_breakouts(series, lookback)
    everything = sorted(patterns + fakeouts, key=lambda p: p.index)

    # Recent patterns dominate: weight by how close they are to the last bar.
    n = len(series)
    bull = bear = 0.0
    for p in everything:
        recency = max(0.0, 1 - (n - 1 - p.index) / 20)  # fades to 0 over 20 bars
        weight = p.strength * recency
        if p.bias is PatternBias.BULLISH:
            bull += weight
        elif p.bias is PatternBias.BEARISH:
            bear += weight

    total = bull + bear
    if total < 0.1:
        bias, confidence = PatternBias.NEUTRAL, 0.0
    elif bull > bear:
        bias, confidence = PatternBias.BULLISH, bull / total
    else:
        bias, confidence = PatternBias.BEARISH, bear / total

    return {
        "patterns": [p.to_dict() for p in everything],
        "recent": [p.to_dict() for p in everything if p.index >= n - 10],
        "bias": bias.value,
        "confidence": round(confidence, 2),
        "bullish_weight": round(bull, 2),
        "bearish_weight": round(bear, 2),
    }
