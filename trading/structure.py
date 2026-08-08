"""Market structure detection.

Everything here derives from confirmed swing pivots, and a pivot is only
confirmed once `lookback` bars have printed on *both* sides of it. That delay is
deliberate: it is what makes the output honest. A pivot judged from bars that
had not closed yet would be look-ahead bias, and would make every backtest
built on this module worthless.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from trading.indicators import atr
from trading.models import Series


class SwingType(str, Enum):
    HIGH = "high"
    LOW = "low"


class StructureLabel(str, Enum):
    HH = "HH"  # higher high
    HL = "HL"  # higher low
    LH = "LH"  # lower high
    LL = "LL"  # lower low


class Trend(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    RANGING = "ranging"


class MarketPhase(str, Enum):
    ACCUMULATION = "accumulation"
    DISTRIBUTION = "distribution"
    EXPANSION = "expansion"
    COMPRESSION = "compression"
    RANGE = "range"
    TREND_CONTINUATION = "trend_continuation"
    TREND_REVERSAL = "trend_reversal"


@dataclass
class Swing:
    index: int
    timestamp: int
    price: float
    type: SwingType
    label: StructureLabel | None = None

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "time": self.timestamp,
            "price": self.price,
            "type": self.type.value,
            "label": self.label.value if self.label else None,
        }


@dataclass
class StructureEvent:
    """A break of structure or change of character."""

    kind: str  # "BOS" | "CHOCH"
    direction: str  # "bullish" | "bearish"
    index: int
    timestamp: int
    price: float  # the swing level that was broken
    broke_swing_index: int
    evidence: str

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "direction": self.direction,
            "index": self.index,
            "time": self.timestamp,
            "price": self.price,
            "evidence": self.evidence,
        }


@dataclass
class LiquiditySweep:
    """Price took out a prior swing then closed back inside — a stop run."""

    index: int
    timestamp: int
    level: float
    direction: str  # "buyside" (swept highs) | "sellside" (swept lows)
    reclaimed: bool
    evidence: str

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "time": self.timestamp,
            "level": self.level,
            "direction": self.direction,
            "reclaimed": self.reclaimed,
            "evidence": self.evidence,
        }


@dataclass
class EqualLevel:
    price: float
    indices: list[int]
    timestamps: list[int]
    direction: str  # "highs" | "lows"

    def to_dict(self) -> dict:
        return {
            "price": self.price,
            "count": len(self.indices),
            "times": self.timestamps,
            "direction": self.direction,
        }


@dataclass
class StructureReport:
    swings: list[Swing] = field(default_factory=list)
    events: list[StructureEvent] = field(default_factory=list)
    sweeps: list[LiquiditySweep] = field(default_factory=list)
    equal_highs: list[EqualLevel] = field(default_factory=list)
    equal_lows: list[EqualLevel] = field(default_factory=list)
    trend: Trend = Trend.RANGING
    trend_strength: float = 0.0  # 0-100
    phase: MarketPhase = MarketPhase.RANGE
    internal_trend: Trend = Trend.RANGING
    external_trend: Trend = Trend.RANGING
    range_high: float | None = None
    range_low: float | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "swings": [s.to_dict() for s in self.swings],
            "events": [e.to_dict() for e in self.events],
            "sweeps": [s.to_dict() for s in self.sweeps],
            "equal_highs": [e.to_dict() for e in self.equal_highs],
            "equal_lows": [e.to_dict() for e in self.equal_lows],
            "trend": self.trend.value,
            "trend_strength": round(self.trend_strength, 1),
            "phase": self.phase.value,
            "internal_trend": self.internal_trend.value,
            "external_trend": self.external_trend.value,
            "range_high": self.range_high,
            "range_low": self.range_low,
            "notes": self.notes,
        }

    @property
    def last_event(self) -> StructureEvent | None:
        return self.events[-1] if self.events else None


def find_swings(series: Series, lookback: int = 3) -> list[Swing]:
    """Fractal pivot detection.

    A bar is a swing high when its high is the highest of the `lookback` bars
    either side of it. Bars within `lookback` of the right edge are skipped
    because they are not confirmed yet.
    """
    swings: list[Swing] = []
    candles = series.candles
    n = len(candles)

    for i in range(lookback, n - lookback):
        window = candles[i - lookback : i + lookback + 1]
        pivot = candles[i]

        if pivot.high == max(c.high for c in window) and pivot.high > candles[i - 1].high:
            swings.append(Swing(i, pivot.timestamp, pivot.high, SwingType.HIGH))
        elif pivot.low == min(c.low for c in window) and pivot.low < candles[i - 1].low:
            swings.append(Swing(i, pivot.timestamp, pivot.low, SwingType.LOW))

    return _label_swings(swings)


def _label_swings(swings: list[Swing]) -> list[Swing]:
    """Tag each swing HH/HL/LH/LL relative to the previous swing of the same type."""
    last_high: float | None = None
    last_low: float | None = None

    for swing in swings:
        if swing.type is SwingType.HIGH:
            if last_high is not None:
                swing.label = StructureLabel.HH if swing.price > last_high else StructureLabel.LH
            last_high = swing.price
        else:
            if last_low is not None:
                swing.label = StructureLabel.HL if swing.price > last_low else StructureLabel.LL
            last_low = swing.price
    return swings


def detect_structure_events(series: Series, swings: list[Swing]) -> list[StructureEvent]:
    """Find BOS and CHOCH from confirmed closes through prior swings.

    BOS  = a close beyond the last swing in the *same* direction as trend
           (continuation).
    CHOCH = the first close beyond a swing *against* the prevailing trend
           (the character of the market changed).

    We require a candle *close* beyond the level, not just a wick, so that a
    stop-hunt wick is classified as a sweep instead of a break.
    """
    events: list[StructureEvent] = []
    if len(swings) < 2:
        return events

    trend: Trend = Trend.RANGING
    candles = series.candles

    for i, swing in enumerate(swings):
        # Only consider swings that already have a following swing to break them.
        prior_highs = [s for s in swings[:i] if s.type is SwingType.HIGH]
        prior_lows = [s for s in swings[:i] if s.type is SwingType.LOW]
        if not prior_highs or not prior_lows:
            continue

        last_high = prior_highs[-1]
        last_low = prior_lows[-1]

        # Scan the bars between this swing and the next for a decisive close.
        end = swings[i + 1].index if i + 1 < len(swings) else len(candles)
        for bar_idx in range(swing.index, min(end + 1, len(candles))):
            close = candles[bar_idx].close

            if close > last_high.price:
                kind = "BOS" if trend is Trend.BULLISH else "CHOCH"
                if not _already_recorded(events, kind, "bullish", last_high.index):
                    events.append(StructureEvent(
                        kind=kind,
                        direction="bullish",
                        index=bar_idx,
                        timestamp=candles[bar_idx].timestamp,
                        price=last_high.price,
                        broke_swing_index=last_high.index,
                        evidence=(
                            f"Close {close:.6g} above swing high {last_high.price:.6g} "
                            f"({'continuation of' if kind == 'BOS' else 'reversal against'} "
                            f"{trend.value} structure)"
                        ),
                    ))
                    trend = Trend.BULLISH
                break

            if close < last_low.price:
                kind = "BOS" if trend is Trend.BEARISH else "CHOCH"
                if not _already_recorded(events, kind, "bearish", last_low.index):
                    events.append(StructureEvent(
                        kind=kind,
                        direction="bearish",
                        index=bar_idx,
                        timestamp=candles[bar_idx].timestamp,
                        price=last_low.price,
                        broke_swing_index=last_low.index,
                        evidence=(
                            f"Close {close:.6g} below swing low {last_low.price:.6g} "
                            f"({'continuation of' if kind == 'BOS' else 'reversal against'} "
                            f"{trend.value} structure)"
                        ),
                    ))
                    trend = Trend.BEARISH
                break

    return events


def _already_recorded(events: list[StructureEvent], kind: str, direction: str, swing_index: int) -> bool:
    return any(
        e.broke_swing_index == swing_index and e.direction == direction and e.kind == kind
        for e in events
    )


def detect_sweeps(series: Series, swings: list[Swing], tolerance_atr: float = 0.1) -> list[LiquiditySweep]:
    """Liquidity sweeps: a wick through a swing that the body fails to hold.

    This is the mirror of a BOS — same level, but price rejects instead of
    accepting, which is why the two are detected together and reported
    separately.
    """
    sweeps: list[LiquiditySweep] = []
    candles = series.candles
    atr_vals = atr(series, 14)

    for i, candle in enumerate(candles):
        band = (atr_vals[i] or 0.0) * tolerance_atr

        for swing in swings:
            if swing.index >= i or i - swing.index > 100:
                continue  # only sweep levels that already exist and are still relevant

            if swing.type is SwingType.HIGH and candle.high > swing.price + band:
                if candle.close < swing.price:
                    sweeps.append(LiquiditySweep(
                        index=i,
                        timestamp=candle.timestamp,
                        level=swing.price,
                        direction="buyside",
                        reclaimed=True,
                        evidence=(
                            f"Wick to {candle.high:.6g} took out swing high {swing.price:.6g} "
                            f"but closed back below at {candle.close:.6g}"
                        ),
                    ))
            elif swing.type is SwingType.LOW and candle.low < swing.price - band:
                if candle.close > swing.price:
                    sweeps.append(LiquiditySweep(
                        index=i,
                        timestamp=candle.timestamp,
                        level=swing.price,
                        direction="sellside",
                        reclaimed=True,
                        evidence=(
                            f"Wick to {candle.low:.6g} took out swing low {swing.price:.6g} "
                            f"but closed back above at {candle.close:.6g}"
                        ),
                    ))

    # Keep only the most recent sweep per level so the report isn't spammed.
    seen: dict[tuple[float, str], LiquiditySweep] = {}
    for sweep in sweeps:
        seen[(round(sweep.level, 8), sweep.direction)] = sweep
    return sorted(seen.values(), key=lambda s: s.index)


def detect_equal_levels(
    series: Series, swings: list[Swing], tolerance_atr: float = 0.15
) -> tuple[list[EqualLevel], list[EqualLevel]]:
    """Cluster swings that sit at effectively the same price (EQH / EQL).

    Equal highs and lows are where resting stop orders pool, which is why they
    matter more than an ordinary swing.
    """
    atr_vals = atr(series, 14)
    reference_atr = next((v for v in reversed(atr_vals) if v is not None), None)
    if reference_atr is None:
        return [], []
    band = reference_atr * tolerance_atr

    def cluster(kind: SwingType, direction: str) -> list[EqualLevel]:
        points = [s for s in swings if s.type is kind]
        used: set[int] = set()
        groups: list[EqualLevel] = []

        for i, swing in enumerate(points):
            if i in used:
                continue
            members = [swing]
            member_ids = {i}
            for j in range(i + 1, len(points)):
                if j in used:
                    continue
                if abs(points[j].price - swing.price) <= band:
                    members.append(points[j])
                    member_ids.add(j)
            if len(members) >= 2:
                used |= member_ids
                groups.append(EqualLevel(
                    price=sum(m.price for m in members) / len(members),
                    indices=[m.index for m in members],
                    timestamps=[m.timestamp for m in members],
                    direction=direction,
                ))
        return groups

    return cluster(SwingType.HIGH, "highs"), cluster(SwingType.LOW, "lows")


def classify_trend(swings: list[Swing]) -> tuple[Trend, float]:
    """Score the trend from the sequence of swing labels.

    Strength is the share of the last six labelled swings that agree with the
    dominant direction, so a clean HH/HL staircase scores near 100 and a choppy
    mix scores near 50 (reported as ranging).
    """
    labelled = [s for s in swings if s.label][-6:]
    if len(labelled) < 2:
        return Trend.RANGING, 0.0

    bull = sum(1 for s in labelled if s.label in (StructureLabel.HH, StructureLabel.HL))
    bear = sum(1 for s in labelled if s.label in (StructureLabel.LH, StructureLabel.LL))
    total = bull + bear
    if total == 0:
        return Trend.RANGING, 0.0

    if bull > bear:
        return Trend.BULLISH, 100 * bull / total
    if bear > bull:
        return Trend.BEARISH, 100 * bear / total
    return Trend.RANGING, 50.0


def classify_phase(series: Series, swings: list[Swing], trend: Trend) -> tuple[MarketPhase, list[str]]:
    """Label the current phase from range width and volatility behaviour."""
    notes: list[str] = []
    if len(series) < 40:
        return MarketPhase.RANGE, ["Not enough history to classify the market phase."]

    atr_vals = atr(series, 14)
    recent = [v for v in atr_vals[-20:] if v is not None]
    older = [v for v in atr_vals[-60:-20] if v is not None]
    if not recent or not older:
        return MarketPhase.RANGE, notes

    atr_now = sum(recent) / len(recent)
    atr_before = sum(older) / len(older)
    ratio = atr_now / atr_before if atr_before else 1.0

    window = series.candles[-40:]
    high = max(c.high for c in window)
    low = min(c.low for c in window)
    span = (high - low) / series.last.close if series.last.close else 0.0

    if ratio > 1.3:
        notes.append(f"Volatility expanding: 20-bar ATR is {ratio:.2f}x the prior 40-bar average.")
        phase = MarketPhase.EXPANSION
    elif ratio < 0.75:
        notes.append(f"Volatility compressing: 20-bar ATR is {ratio:.2f}x the prior 40-bar average.")
        phase = MarketPhase.COMPRESSION
    elif span < 0.03:
        notes.append(f"Price confined to a {span:.2%} band over 40 bars.")
        phase = MarketPhase.RANGE
    elif trend is Trend.BULLISH:
        phase = MarketPhase.TREND_CONTINUATION
    elif trend is Trend.BEARISH:
        phase = MarketPhase.TREND_CONTINUATION
    else:
        phase = MarketPhase.RANGE

    # Wyckoff-flavoured refinement: a compression after a sustained move is
    # accumulation at lows and distribution at highs.
    if phase is MarketPhase.COMPRESSION:
        position = (series.last.close - low) / (high - low) if high != low else 0.5
        if position < 0.35:
            phase = MarketPhase.ACCUMULATION
            notes.append("Compression in the lower third of the range — accumulation characteristics.")
        elif position > 0.65:
            phase = MarketPhase.DISTRIBUTION
            notes.append("Compression in the upper third of the range — distribution characteristics.")

    return phase, notes


def analyze_structure(series: Series, lookback: int = 3) -> StructureReport:
    """Run the full structure pass over a series."""
    report = StructureReport()
    if len(series) < lookback * 2 + 5:
        report.notes.append("Not enough bars for structure analysis.")
        return report

    report.swings = find_swings(series, lookback)
    report.events = detect_structure_events(series, report.swings)
    report.sweeps = detect_sweeps(series, report.swings)
    report.equal_highs, report.equal_lows = detect_equal_levels(series, report.swings)

    report.trend, report.trend_strength = classify_trend(report.swings)
    report.phase, notes = classify_phase(series, report.swings, report.trend)
    report.notes.extend(notes)

    # External structure = major swings (wide lookback); internal = minor ones.
    external_swings = find_swings(series, max(lookback * 2, 5))
    report.external_trend, _ = classify_trend(external_swings)
    internal_swings = find_swings(series, max(lookback - 1, 2))
    report.internal_trend, _ = classify_trend(internal_swings)

    if report.internal_trend is not report.external_trend and report.external_trend is not Trend.RANGING:
        report.notes.append(
            f"Internal structure ({report.internal_trend.value}) disagrees with external "
            f"structure ({report.external_trend.value}) — likely a pullback inside the larger trend."
        )

    window = series.candles[-50:]
    report.range_high = max(c.high for c in window)
    report.range_low = min(c.low for c in window)

    if report.last_event:
        e = report.last_event
        report.notes.append(f"Most recent structural event: {e.direction} {e.kind}. {e.evidence}")

    return report
