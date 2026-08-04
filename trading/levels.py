"""Key level detection: support/resistance, period extremes, round numbers, fibs, trendlines.

Levels are scored by how many times price actually reacted at them and how
recently, then de-duplicated so the chart shows a handful of meaningful lines
rather than a wall of them.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

from trading.indicators import atr
from trading.models import Series, Timeframe
from trading.structure import Swing, SwingType


@dataclass
class Level:
    price: float
    kind: str        # support | resistance | supply | demand | period | round | fib | poc
    label: str
    touches: int = 1
    strength: float = 0.5   # 0-1
    last_touch_index: int | None = None
    evidence: str = ""

    def to_dict(self) -> dict:
        return {
            "price": self.price,
            "kind": self.kind,
            "label": self.label,
            "touches": self.touches,
            "strength": round(self.strength, 2),
            "evidence": self.evidence,
        }


@dataclass
class Trendline:
    start_index: int
    end_index: int
    start_price: float
    end_price: float
    direction: str   # ascending | descending
    touches: int
    evidence: str

    def price_at(self, index: int) -> float:
        span = self.end_index - self.start_index
        if span == 0:
            return self.end_price
        slope = (self.end_price - self.start_price) / span
        return self.start_price + slope * (index - self.start_index)

    def to_dict(self) -> dict:
        return {
            "start_index": self.start_index,
            "end_index": self.end_index,
            "start_price": self.start_price,
            "end_price": self.end_price,
            "direction": self.direction,
            "touches": self.touches,
            "evidence": self.evidence,
        }


@dataclass
class LevelReport:
    support: list[Level] = field(default_factory=list)
    resistance: list[Level] = field(default_factory=list)
    period_levels: list[Level] = field(default_factory=list)
    round_numbers: list[Level] = field(default_factory=list)
    fibonacci: list[Level] = field(default_factory=list)
    trendlines: list[Trendline] = field(default_factory=list)
    nearest_support: Level | None = None
    nearest_resistance: Level | None = None
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "support": [l.to_dict() for l in self.support],
            "resistance": [l.to_dict() for l in self.resistance],
            "period_levels": [l.to_dict() for l in self.period_levels],
            "round_numbers": [l.to_dict() for l in self.round_numbers],
            "fibonacci": [l.to_dict() for l in self.fibonacci],
            "trendlines": [t.to_dict() for t in self.trendlines],
            "nearest_support": self.nearest_support.to_dict() if self.nearest_support else None,
            "nearest_resistance": self.nearest_resistance.to_dict() if self.nearest_resistance else None,
            "evidence": self.evidence,
        }


def cluster_swing_levels(series: Series, swings: list[Swing], tolerance_atr: float = 0.5) -> list[Level]:
    """Group nearby swing prices into single levels weighted by touch count.

    Three swings within half an ATR of each other are one level that has been
    respected three times, not three separate levels — and that distinction is
    the whole point of a support/resistance line.
    """
    if not swings or not series.last:
        return []

    atr_vals = atr(series, 14)
    reference = next((v for v in reversed(atr_vals) if v is not None), None)
    if not reference:
        return []
    band = reference * tolerance_atr

    levels: list[Level] = []
    for swing in swings:
        merged = False
        for level in levels:
            if abs(level.price - swing.price) <= band:
                # Weighted mean keeps the level anchored where most touches were.
                level.price = (level.price * level.touches + swing.price) / (level.touches + 1)
                level.touches += 1
                level.last_touch_index = max(level.last_touch_index or 0, swing.index)
                merged = True
                break
        if not merged:
            levels.append(Level(
                price=swing.price,
                kind="resistance" if swing.type is SwingType.HIGH else "support",
                label=f"Swing {'high' if swing.type is SwingType.HIGH else 'low'}",
                touches=1,
                last_touch_index=swing.index,
            ))

    n = len(series)
    for level in levels:
        recency = 1 - ((n - (level.last_touch_index or 0)) / n) if n else 0
        # Touches matter most; recency breaks ties between equally-tested levels.
        level.strength = min(1.0, (level.touches / 4) * 0.7 + recency * 0.3)
        level.evidence = (
            f"Price reacted at {level.price:.6g} {level.touches} time"
            f"{'s' if level.touches > 1 else ''}, most recently {n - (level.last_touch_index or 0)} bars ago."
        )

    price = series.last.close
    # A level below price acts as support regardless of how it originally formed.
    for level in levels:
        level.kind = "support" if level.price < price else "resistance"

    return sorted(levels, key=lambda l: l.strength, reverse=True)


def period_levels(series: Series) -> list[Level]:
    """Previous day/week/month highs and lows, plus the current session's extremes.

    These are the levels the whole market watches, which is what gives them
    their reaction rate — they are not derived from this chart's swings at all.
    """
    if not series.candles:
        return []

    out: list[Level] = []
    now = series.candles[-1]

    def bucket(ts: int, period: str) -> tuple:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        if period == "day":
            return (dt.year, dt.month, dt.day)
        if period == "week":
            iso = dt.isocalendar()
            return (iso[0], iso[1])
        return (dt.year, dt.month)

    for period, label in (("day", "Daily"), ("week", "Weekly"), ("month", "Monthly")):
        current_bucket = bucket(now.timestamp, period)
        current = [c for c in series.candles if bucket(c.timestamp, period) == current_bucket]
        previous_buckets = sorted({bucket(c.timestamp, period) for c in series.candles})
        if len(previous_buckets) < 2:
            continue
        prev_bucket = previous_buckets[-2]
        previous = [c for c in series.candles if bucket(c.timestamp, period) == prev_bucket]

        if previous:
            out.append(Level(
                price=max(c.high for c in previous), kind="period",
                label=f"Previous {label} High", touches=1, strength=0.75,
                evidence=f"High of the previous {period} — a standard liquidity reference.",
            ))
            out.append(Level(
                price=min(c.low for c in previous), kind="period",
                label=f"Previous {label} Low", touches=1, strength=0.75,
                evidence=f"Low of the previous {period} — a standard liquidity reference.",
            ))
        if current and period == "day":
            out.append(Level(
                price=max(c.high for c in current), kind="period",
                label="Current Session High", touches=1, strength=0.6,
                evidence="Highest print of the current session.",
            ))
            out.append(Level(
                price=min(c.low for c in current), kind="period",
                label="Current Session Low", touches=1, strength=0.6,
                evidence="Lowest print of the current session.",
            ))
    return out


def round_numbers(series: Series, count: int = 4) -> list[Level]:
    """Psychological round levels above and below price.

    The step is chosen from the price's own magnitude so this behaves sensibly
    whether the instrument trades at 0.65 or 65,000.
    """
    if not series.last:
        return []
    price = series.last.close
    if price <= 0:
        return []

    magnitude = math.floor(math.log10(price))
    step = 10 ** (magnitude - 1)
    if price / step > 50:
        step *= 5

    base = math.floor(price / step) * step
    out: list[Level] = []
    for i in range(-count, count + 1):
        level_price = base + i * step
        if level_price <= 0:
            continue
        # Levels ending in a whole multiple of the larger step are stronger.
        major = abs(level_price % (step * 10)) < step * 0.001
        out.append(Level(
            price=level_price, kind="round",
            label=f"Round number {level_price:.6g}",
            touches=1,
            strength=0.6 if major else 0.35,
            evidence=(
                f"{'Major' if major else 'Minor'} psychological level — order books cluster at "
                f"round figures like {level_price:.6g}."
            ),
        ))
    return out


def fibonacci_levels(series: Series, swings: list[Swing]) -> list[Level]:
    """Retracement levels across the most recent significant swing leg."""
    highs = [s for s in swings if s.type is SwingType.HIGH]
    lows = [s for s in swings if s.type is SwingType.LOW]
    if not highs or not lows:
        return []

    last_high, last_low = highs[-1], lows[-1]
    top, bottom = last_high.price, last_low.price
    if top <= bottom:
        return []

    span = top - bottom
    uptrend = last_low.index < last_high.index
    ratios = [0.236, 0.382, 0.5, 0.618, 0.705, 0.786]

    out: list[Level] = []
    for ratio in ratios:
        price = top - span * ratio if uptrend else bottom + span * ratio
        golden = ratio in (0.618, 0.705)
        out.append(Level(
            price=price, kind="fib",
            label=f"Fib {ratio:.3g}",
            touches=1,
            strength=0.7 if golden else 0.45,
            evidence=(
                f"{ratio:.1%} retracement of the {bottom:.6g}-{top:.6g} "
                f"{'up' if uptrend else 'down'} leg."
                + (" Inside the golden pocket where trend pullbacks most often end."
                   if golden else "")
            ),
        ))
    return out


def find_trendlines(series: Series, swings: list[Swing], min_touches: int = 3) -> list[Trendline]:
    """Fit lines through swing pivots and keep those price actually respected.

    Every pair of same-type pivots defines a candidate line; a line is kept only
    if at least `min_touches` other pivots sit within half an ATR of it, which
    filters out the arbitrary lines you can always draw through any two points.
    """
    if len(swings) < min_touches or not series.last:
        return []

    atr_vals = atr(series, 14)
    reference = next((v for v in reversed(atr_vals) if v is not None), None)
    if not reference:
        return []
    tolerance = reference * 0.5

    lines: list[Trendline] = []
    for kind, direction_name in ((SwingType.LOW, "ascending"), (SwingType.HIGH, "descending")):
        points = [s for s in swings if s.type is kind]
        for i in range(len(points) - 1):
            for j in range(i + 1, len(points)):
                a, b = points[i], points[j]
                if b.index - a.index < 5:
                    continue
                slope = (b.price - a.price) / (b.index - a.index)

                touches = 0
                for p in points:
                    projected = a.price + slope * (p.index - a.index)
                    if abs(p.price - projected) <= tolerance:
                        touches += 1
                if touches < min_touches:
                    continue

                ascending = slope > 0
                if (kind is SwingType.LOW) != ascending:
                    continue  # keep support lines rising and resistance lines falling

                lines.append(Trendline(
                    start_index=a.index, end_index=b.index,
                    start_price=a.price, end_price=b.price,
                    direction=direction_name,
                    touches=touches,
                    evidence=(
                        f"{direction_name.capitalize()} line through {touches} swing "
                        f"{'lows' if kind is SwingType.LOW else 'highs'} from bar {a.index} to {b.index}."
                    ),
                ))

    lines.sort(key=lambda t: (-t.touches, -(t.end_index - t.start_index)))
    # Keep at most one line per direction — the best-supported one.
    best: dict[str, Trendline] = {}
    for line in lines:
        best.setdefault(line.direction, line)
    return list(best.values())


def analyze_levels(series: Series, swings: list[Swing], volume_profile: dict | None = None) -> LevelReport:
    """Assemble every level type and pick out the nearest actionable ones."""
    report = LevelReport()
    if not series.last:
        return report

    price = series.last.close
    clustered = cluster_swing_levels(series, swings)
    report.support = [l for l in clustered if l.kind == "support"][:6]
    report.resistance = [l for l in clustered if l.kind == "resistance"][:6]
    report.period_levels = period_levels(series)
    report.round_numbers = round_numbers(series)
    report.fibonacci = fibonacci_levels(series, swings)
    report.trendlines = find_trendlines(series, swings)

    # The volume profile's POC is one of the strongest magnets on the chart.
    if volume_profile and volume_profile.get("poc"):
        poc = volume_profile["poc"]
        report.period_levels.append(Level(
            price=poc, kind="poc", label="Volume Point of Control", touches=1, strength=0.8,
            evidence="Price at which the most volume traded over the profile window — a magnet level.",
        ))

    everything = (
        report.support + report.resistance + report.period_levels
        + report.round_numbers + report.fibonacci
    )
    below = [l for l in everything if l.price < price]
    above = [l for l in everything if l.price > price]

    if below:
        report.nearest_support = max(below, key=lambda l: (l.price, l.strength))
        distance = (price - report.nearest_support.price) / price * 100
        report.evidence.append(
            f"Nearest support: {report.nearest_support.label} at "
            f"{report.nearest_support.price:.6g} ({distance:.2f}% below). "
            f"{report.nearest_support.evidence}"
        )
    if above:
        report.nearest_resistance = min(above, key=lambda l: (l.price, -l.strength))
        distance = (report.nearest_resistance.price - price) / price * 100
        report.evidence.append(
            f"Nearest resistance: {report.nearest_resistance.label} at "
            f"{report.nearest_resistance.price:.6g} ({distance:.2f}% above). "
            f"{report.nearest_resistance.evidence}"
        )
    for line in report.trendlines:
        projected = line.price_at(len(series) - 1)
        report.evidence.append(
            f"{line.evidence} Currently projects to {projected:.6g}."
        )

    return report
