"""Wyckoff event labelling: spring, upthrust, sign of strength/weakness, test, LPS/LPSY.

`structure.py` already classifies the current *phase* (accumulation,
distribution, ...) from volatility contraction and range position — but that
reading is a single snapshot at the end of the series. A Wyckoff campaign is
the opposite of a snapshot: a spring happens while the market is ranging, and
by the time its sign-of-strength and last-point-of-support confirm days later,
the *current* phase has already flipped to an uptrend. Gating every event off
today's phase would silently discard the very sequence this module exists to
find.

So detection here is anchored locally in time rather than globally: for each
liquidity sweep `structure.detect_sweeps` already found, the range it sweeps is
recomputed from the bars immediately preceding it, not from the tail of the
whole series. That is what makes a spring found on bar 60 still recognisable
as one after fifty more bars of uptrend have printed.

Everything below builds on primitives structure.py already computed —
`structure.sweeps` for spring/upthrust, `structure.swings` for the pullback
that becomes a last point of support/supply — rather than re-deriving pivots.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from trading.indicators import atr
from trading.models import Series
from trading.structure import LiquiditySweep, StructureReport, SwingType

# How far a sweep's level may sit from its own local range edge and still
# count as an edge event rather than a mid-range wick.
EDGE_TOLERANCE_ATR = 1.0

# A local window whose (high - low) / midpoint exceeds this is judged to be
# trending rather than ranging, so a sweep inside it is not a spring/upthrust.
# Trends travel; ranges contain. 40 bars of genuine range rarely exceeds this.
MAX_CONTAINMENT_PERCENT = 20.0

# Span alone is not enough: a slow, steady drift can still look "contained"
# over any one 40-bar slice even while trending hard zoomed out. Directional
# efficiency is |net change| / (high - low) over the same window — near 0 for
# a real range that oscillates back near where it started, near 1 for a move
# that mostly travelled one way. A window above this is a slow trend, not a
# range, whatever its span looks like in isolation.
MAX_DIRECTIONAL_EFFICIENCY = 0.40

# How many bars after a spring/upthrust the confirming breakout must arrive.
# Wyckoff's own writing describes this as prompt; an unbounded search would
# eventually attribute an unrelated, much later breakout to an old sweep.
CONFIRMATION_WINDOW = 40

# A breakout needs at least this much volume relative to the trailing average
# to count as a sign of strength/weakness rather than a routine push through
# the level.
BREAKOUT_VOLUME_MULTIPLE = 1.2

# A test must trade on less volume than the original spring/upthrust to count
# as a legitimate low-supply/low-demand retest rather than a second attack.
TEST_VOLUME_MULTIPLE = 0.85


class WyckoffEventType(str, Enum):
    SPRING = "spring"
    UPTHRUST = "upthrust"
    SIGN_OF_STRENGTH = "sign_of_strength"
    SIGN_OF_WEAKNESS = "sign_of_weakness"
    TEST = "test"
    LAST_POINT_OF_SUPPORT = "last_point_of_support"
    LAST_POINT_OF_SUPPLY = "last_point_of_supply"


@dataclass
class WyckoffEvent:
    type: WyckoffEventType
    index: int
    timestamp: int
    price: float
    evidence: str
    # index of the spring/upthrust this event confirms or extends, if any —
    # lets the UI draw a line from cause to consequence rather than a loose dot.
    related_index: int | None = None
    # For SIGN_OF_STRENGTH/SIGN_OF_WEAKNESS only: the local range edge this
    # breakout broke, carried forward so the later last-point-of-support/supply
    # search checks the pullback against the level that was actually broken,
    # not the breakout bar's own close.
    boundary: float | None = None

    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "index": self.index,
            "time": self.timestamp,
            "price": self.price,
            "evidence": self.evidence,
            "related_index": self.related_index,
        }


@dataclass
class WyckoffReport:
    events: list[WyckoffEvent] = field(default_factory=list)
    # "accumulating"/"distributing" once a spring/upthrust exists with no
    # breakout yet; "*_confirmed" once the full sequence completes; "none"
    # when no sweep qualified as a range-edge event at all.
    campaign: str = "none"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "events": [e.to_dict() for e in self.events],
            "campaign": self.campaign,
            "notes": self.notes,
        }


def _local_range(candles: list, index: int, window: int = 40) -> tuple[float | None, float | None]:
    """The high/low of the `window` bars strictly before `index`.

    Excludes the bar at `index` itself deliberately: for a spring, that bar is
    the wick that undercuts the range, so including it would pull the local
    low down to the wick and mask the very level being tested.
    """
    start = max(0, index - window)
    segment = candles[start:index]
    if not segment:
        return None, None
    return max(c.high for c in segment), min(c.low for c in segment)


def _average_volume(candles: list, index: int, window: int = 20) -> float | None:
    start = max(0, index - window)
    values = [c.volume for c in candles[start:index] if c.volume]
    if not values:
        return None
    return sum(values) / len(values)


def _directional_efficiency(candles: list, index: int, high: float, low: float, window: int = 40) -> float | None:
    start = max(0, index - window)
    segment = candles[start:index]
    if len(segment) < 5 or high <= low:
        return None
    net_change = abs(segment[-1].close - segment[0].close)
    return net_change / (high - low)


def analyze_wyckoff(series: Series, structure: StructureReport) -> WyckoffReport:
    """Label spring/upthrust/SOS/SOW/test/LPS/LPSY across the whole series.

    Each sweep `structure.detect_sweeps` already found is tested against the
    range that existed immediately before it, so a sweep from early in a long
    series is judged by the range it actually broke, not by wherever price has
    since ended up.
    """
    report = WyckoffReport()
    candles = series.candles
    if not structure.sweeps:
        report.notes.append("No liquidity sweep detected — nothing for a spring or upthrust to anchor to.")
        return report

    atr_vals = atr(series, 14)
    found_any_edge_sweep = False

    for sweep in structure.sweeps:
        is_bullish = sweep.direction == "sellside"  # a spring; upthrust otherwise
        local_high, local_low = _local_range(candles, sweep.index)
        if local_high is None or local_high <= local_low:
            continue

        span_percent = 100 * (local_high - local_low) / ((local_high + local_low) / 2)
        if span_percent > MAX_CONTAINMENT_PERCENT:
            continue  # the level sat inside a trend, not a range — not a spring/upthrust

        efficiency = _directional_efficiency(candles, sweep.index, local_high, local_low)
        if efficiency is not None and efficiency > MAX_DIRECTIONAL_EFFICIENCY:
            continue  # net travel dominates the window — a slow trend, not a range

        edge = local_low if is_bullish else local_high
        band = (atr_vals[sweep.index] or 0.0) * EDGE_TOLERANCE_ATR
        if abs(sweep.level - edge) > band:
            continue  # swept a level that wasn't actually the edge of its local range

        found_any_edge_sweep = True
        boundary = local_high if is_bullish else local_low
        report.events.append(_spring_or_upthrust_event(sweep, is_bullish))
        _find_test_and_breakout(candles, atr_vals, sweep, boundary, is_bullish, report)

    for event in list(report.events):
        if event.type is WyckoffEventType.SIGN_OF_STRENGTH:
            _find_last_point(structure, event, is_bullish=True, report=report)
        elif event.type is WyckoffEventType.SIGN_OF_WEAKNESS:
            _find_last_point(structure, event, is_bullish=False, report=report)

    report.events.sort(key=lambda e: e.index)
    report.campaign = _campaign_label(report.events)
    if not found_any_edge_sweep:
        report.notes.append(
            "Sweeps exist but none sat at the edge of a contained local range — "
            "no spring or upthrust confirmed."
        )
    return report


def _spring_or_upthrust_event(sweep: LiquiditySweep, is_bullish: bool) -> WyckoffEvent:
    event_type = WyckoffEventType.SPRING if is_bullish else WyckoffEventType.UPTHRUST
    return WyckoffEvent(
        type=event_type,
        index=sweep.index,
        timestamp=sweep.timestamp,
        price=sweep.level,
        evidence=(
            f"{'Sellside' if is_bullish else 'Buyside'} sweep at {sweep.level:.6g}, at the edge of "
            f"its local {'low' if is_bullish else 'high'} with the 40 bars before it contained — "
            f"{'a spring: a shakeout below support' if is_bullish else 'an upthrust: a false breakout above resistance'} "
            f"that failed to hold."
        ),
    )


def _find_test_and_breakout(
    candles: list, atr_vals: list, sweep: LiquiditySweep, boundary: float, is_bullish: bool,
    report: WyckoffReport,
) -> None:
    """After a spring/upthrust, look for a low-volume test and a breakout on volume."""
    window_end = min(len(candles), sweep.index + 1 + CONFIRMATION_WINDOW)
    sweep_volume = candles[sweep.index].volume

    test_found = False
    for i in range(sweep.index + 1, window_end):
        candle = candles[i]
        band = (atr_vals[i] or 0.0) * 0.3

        if not test_found:
            touch = candle.low if is_bullish else candle.high
            near_level = abs(touch - sweep.level) <= band
            holds = (candle.low >= sweep.level - band) if is_bullish else (candle.high <= sweep.level + band)
            quiet = sweep_volume and candle.volume < sweep_volume * TEST_VOLUME_MULTIPLE
            if near_level and holds and quiet:
                report.events.append(WyckoffEvent(
                    type=WyckoffEventType.TEST,
                    index=i,
                    timestamp=candle.timestamp,
                    price=touch,
                    related_index=sweep.index,
                    evidence=(
                        f"Bar {i} returned to {sweep.level:.6g} on volume {candle.volume:.0f} "
                        f"({candle.volume / sweep_volume:.0%} of the sweep's) and held — "
                        f"a test of the {'spring' if is_bullish else 'upthrust'} at bar {sweep.index}."
                    ),
                ))
                test_found = True

        avg_vol = _average_volume(candles, i)
        if avg_vol:
            broke = candle.close > boundary if is_bullish else candle.close < boundary
            strong = candle.volume >= avg_vol * BREAKOUT_VOLUME_MULTIPLE
            if broke and strong:
                event_type = (
                    WyckoffEventType.SIGN_OF_STRENGTH if is_bullish
                    else WyckoffEventType.SIGN_OF_WEAKNESS
                )
                report.events.append(WyckoffEvent(
                    type=event_type,
                    index=i,
                    timestamp=candle.timestamp,
                    price=candle.close,
                    related_index=sweep.index,
                    boundary=boundary,
                    evidence=(
                        f"Bar {i} closed {'above' if is_bullish else 'below'} the prior range "
                        f"{'high' if is_bullish else 'low'} of {boundary:.6g} at {candle.close:.6g} "
                        f"on {candle.volume / avg_vol:.1f}x average volume — "
                        f"{'sign of strength' if is_bullish else 'sign of weakness'} following the "
                        f"{'spring' if is_bullish else 'upthrust'} at bar {sweep.index}."
                    ),
                ))
                return


def _find_last_point(
    structure: StructureReport, breakout_event: WyckoffEvent, is_bullish: bool, report: WyckoffReport,
) -> None:
    """After a confirmed breakout, the first pullback swing that holds the old
    range boundary as new support (LPS) or new resistance (LPSY)."""
    swing_type = SwingType.LOW if is_bullish else SwingType.HIGH
    boundary = breakout_event.boundary
    if boundary is None:
        return
    for swing in structure.swings:
        if swing.index <= breakout_event.index or swing.type is not swing_type:
            continue
        holds = swing.price >= boundary if is_bullish else swing.price <= boundary
        if holds:
            event_type = (
                WyckoffEventType.LAST_POINT_OF_SUPPORT if is_bullish
                else WyckoffEventType.LAST_POINT_OF_SUPPLY
            )
            report.events.append(WyckoffEvent(
                type=event_type,
                index=swing.index,
                timestamp=swing.timestamp,
                price=swing.price,
                related_index=breakout_event.index,
                evidence=(
                    f"Pullback to {swing.price:.6g} after bar {breakout_event.index}'s breakout held the "
                    f"old range {'high' if is_bullish else 'low'} as new "
                    f"{'support' if is_bullish else 'resistance'} — "
                    f"{'last point of support' if is_bullish else 'last point of supply'}."
                ),
            ))
        return  # only the first swing after the breakout is eligible either way


def _campaign_label(events: list[WyckoffEvent]) -> str:
    types = {e.type for e in events}
    if WyckoffEventType.LAST_POINT_OF_SUPPORT in types:
        return "markup_confirmed"
    if WyckoffEventType.LAST_POINT_OF_SUPPLY in types:
        return "markdown_confirmed"
    if WyckoffEventType.SIGN_OF_STRENGTH in types:
        return "accumulation_breaking_out"
    if WyckoffEventType.SIGN_OF_WEAKNESS in types:
        return "distribution_breaking_down"
    if WyckoffEventType.SPRING in types:
        return "accumulating"
    if WyckoffEventType.UPTHRUST in types:
        return "distributing"
    return "none"
