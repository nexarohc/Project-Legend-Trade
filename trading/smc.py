"""Smart Money Concepts / ICT detection.

Order blocks, fair value gaps, breakers, liquidity pools, premium-discount and
the optimal trade entry zone. These concepts have no single canonical
definition, so each detector documents the rule it actually applies — the point
is that every zone the platform draws can be traced back to specific bars, not
to a black box.

Zones carry a `mitigated` flag: once price has traded back through a zone its
predictive value is largely spent, and hiding that would make old levels look
more meaningful than they are.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from trading.indicators import atr
from trading.models import Series
from trading.structure import StructureReport, SwingType


@dataclass
class Zone:
    """A price band with an origin bar and a reason for existing."""

    kind: str            # order_block | breaker | mitigation | fvg | liquidity
    direction: str       # bullish | bearish
    top: float
    bottom: float
    index: int
    timestamp: int
    mitigated: bool = False
    strength: float = 0.5
    evidence: str = ""

    @property
    def mid(self) -> float:
        return (self.top + self.bottom) / 2

    def contains(self, price: float) -> bool:
        return self.bottom <= price <= self.top

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "direction": self.direction,
            "top": self.top,
            "bottom": self.bottom,
            "mid": self.mid,
            "index": self.index,
            "time": self.timestamp,
            "mitigated": self.mitigated,
            "strength": round(self.strength, 2),
            "evidence": self.evidence,
        }


@dataclass
class LiquidityPool:
    price: float
    direction: str  # buyside | sellside
    scope: str      # internal | external
    taps: int
    evidence: str

    def to_dict(self) -> dict:
        return {
            "price": self.price,
            "direction": self.direction,
            "scope": self.scope,
            "taps": self.taps,
            "evidence": self.evidence,
        }


@dataclass
class SMCReport:
    order_blocks: list[Zone] = field(default_factory=list)
    breaker_blocks: list[Zone] = field(default_factory=list)
    mitigation_blocks: list[Zone] = field(default_factory=list)
    fair_value_gaps: list[Zone] = field(default_factory=list)
    liquidity_pools: list[LiquidityPool] = field(default_factory=list)
    premium_discount: dict = field(default_factory=dict)
    ote: dict = field(default_factory=dict)
    inducement: dict | None = None
    narrative: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "order_blocks": [z.to_dict() for z in self.order_blocks],
            "breaker_blocks": [z.to_dict() for z in self.breaker_blocks],
            "mitigation_blocks": [z.to_dict() for z in self.mitigation_blocks],
            "fair_value_gaps": [z.to_dict() for z in self.fair_value_gaps],
            "liquidity_pools": [p.to_dict() for p in self.liquidity_pools],
            "premium_discount": self.premium_discount,
            "ote": self.ote,
            "inducement": self.inducement,
            "narrative": self.narrative,
        }


def find_fair_value_gaps(series: Series, min_atr_fraction: float = 0.1) -> list[Zone]:
    """Three-bar imbalances where bar 1 and bar 3 do not overlap.

    The gap between candle[i-1].high and candle[i+1].low (bullish) is a price
    range that traded in one direction so fast it left no two-sided auction —
    the classic ICT fair value gap. Tiny gaps are filtered by ATR so noise on
    illiquid symbols doesn't flood the chart.
    """
    gaps: list[Zone] = []
    candles = series.candles
    atr_vals = atr(series, 14)

    for i in range(1, len(candles) - 1):
        before, after = candles[i - 1], candles[i + 1]
        threshold = (atr_vals[i] or 0.0) * min_atr_fraction

        if after.low > before.high and (after.low - before.high) > threshold:
            gaps.append(Zone(
                kind="fvg", direction="bullish",
                top=after.low, bottom=before.high,
                index=i, timestamp=candles[i].timestamp,
                strength=min(1.0, (after.low - before.high) / max(atr_vals[i] or 1e-9, 1e-9)),
                evidence=(
                    f"Bar {i - 1} high {before.high:.6g} sits below bar {i + 1} low {after.low:.6g} — "
                    f"an unfilled {after.low - before.high:.6g} imbalance left by the displacement bar."
                ),
            ))
        elif after.high < before.low and (before.low - after.high) > threshold:
            gaps.append(Zone(
                kind="fvg", direction="bearish",
                top=before.low, bottom=after.high,
                index=i, timestamp=candles[i].timestamp,
                strength=min(1.0, (before.low - after.high) / max(atr_vals[i] or 1e-9, 1e-9)),
                evidence=(
                    f"Bar {i - 1} low {before.low:.6g} sits above bar {i + 1} high {after.high:.6g} — "
                    f"an unfilled {before.low - after.high:.6g} imbalance left by the displacement bar."
                ),
            ))

    _mark_mitigated(gaps, series)
    return gaps


def find_order_blocks(series: Series, structure: StructureReport, min_displacement: float = 1.5) -> list[Zone]:
    """The last opposing candle before a displacement move that breaks structure.

    Rule applied here: for each BOS/CHOCH event, walk back up to 12 bars from
    the breaking bar to find the last candle of the opposite colour, and require
    the move that followed to be at least `min_displacement` x ATR. The
    displacement filter is what separates an institutional order block from any
    random red candle in an uptrend.
    """
    blocks: list[Zone] = []
    candles = series.candles
    atr_vals = atr(series, 14)

    for event in structure.events:
        idx = event.index
        if idx >= len(candles):
            continue
        reference_atr = atr_vals[idx] or 0.0
        if reference_atr <= 0:
            continue

        bullish_break = event.direction == "bullish"
        # Displacement = how far price ran from the breaking bar.
        forward = candles[idx : idx + 5]
        if bullish_break:
            travel = max(c.high for c in forward) - candles[idx].open
        else:
            travel = candles[idx].open - min(c.low for c in forward)
        if travel < min_displacement * reference_atr:
            continue

        for back in range(idx, max(idx - 12, 0) - 1, -1):
            candidate = candles[back]
            is_opposing = candidate.bearish if bullish_break else candidate.bullish
            if not is_opposing:
                continue

            blocks.append(Zone(
                kind="order_block",
                direction="bullish" if bullish_break else "bearish",
                top=candidate.high,
                bottom=candidate.low,
                index=back,
                timestamp=candidate.timestamp,
                strength=min(1.0, travel / (min_displacement * reference_atr) / 2),
                evidence=(
                    f"Last {'down' if bullish_break else 'up'} candle at "
                    f"{candidate.low:.6g}-{candidate.high:.6g} before a {travel / reference_atr:.1f}x ATR "
                    f"displacement that produced a {event.direction} {event.kind}."
                ),
            ))
            break

    _mark_mitigated(blocks, series)
    # Newest first, capped — the chart is unreadable with fifty zones on it.
    return sorted(blocks, key=lambda z: z.index, reverse=True)[:12]


def find_breaker_and_mitigation(series: Series, order_blocks: list[Zone]) -> tuple[list[Zone], list[Zone]]:
    """Split failed order blocks into breakers and mitigation blocks.

    A breaker is an order block that price closed decisively through — it flips
    polarity and is expected to hold as support/resistance from the other side.
    A mitigation block is one price returned into and respected without a
    decisive break, i.e. the position was mitigated rather than invalidated.
    """
    breakers: list[Zone] = []
    mitigations: list[Zone] = []
    candles = series.candles

    for block in order_blocks:
        after = candles[block.index + 1 :]
        if not after:
            continue

        if block.direction == "bullish":
            broke = any(c.close < block.bottom for c in after)
            touched = any(c.low <= block.top and c.close > block.bottom for c in after)
        else:
            broke = any(c.close > block.top for c in after)
            touched = any(c.high >= block.bottom and c.close < block.top for c in after)

        if broke:
            flipped = "bearish" if block.direction == "bullish" else "bullish"
            breakers.append(Zone(
                kind="breaker", direction=flipped,
                top=block.top, bottom=block.bottom,
                index=block.index, timestamp=block.timestamp,
                strength=block.strength,
                evidence=(
                    f"{block.direction.capitalize()} order block at {block.bottom:.6g}-{block.top:.6g} "
                    f"was closed through, flipping it into a {flipped} breaker."
                ),
            ))
        elif touched:
            mitigations.append(Zone(
                kind="mitigation", direction=block.direction,
                top=block.top, bottom=block.bottom,
                index=block.index, timestamp=block.timestamp,
                strength=block.strength * 0.8,
                evidence=(
                    f"Price returned into the {block.direction} block at "
                    f"{block.bottom:.6g}-{block.top:.6g} and respected it — orders mitigated, level still live."
                ),
            ))

    return breakers, mitigations


def find_liquidity_pools(series: Series, structure: StructureReport) -> list[LiquidityPool]:
    """Where resting stops most likely sit: equal highs/lows and untapped swings."""
    pools: list[LiquidityPool] = []
    price = series.last.close if series.last else 0.0

    for eq in structure.equal_highs:
        pools.append(LiquidityPool(
            price=eq.price,
            direction="buyside",
            scope="external" if eq.price > price else "internal",
            taps=len(eq.indices),
            evidence=(
                f"{len(eq.indices)} swing highs cluster at {eq.price:.6g} — buy-side liquidity "
                f"(stops of short positions) rests just above."
            ),
        ))
    for eq in structure.equal_lows:
        pools.append(LiquidityPool(
            price=eq.price,
            direction="sellside",
            scope="external" if eq.price < price else "internal",
            taps=len(eq.indices),
            evidence=(
                f"{len(eq.indices)} swing lows cluster at {eq.price:.6g} — sell-side liquidity "
                f"(stops of long positions) rests just below."
            ),
        ))

    # Untapped recent swings also hold liquidity, even without an equal partner.
    candles = series.candles
    for swing in structure.swings[-8:]:
        later = candles[swing.index + 1 :]
        if not later:
            continue
        if swing.type is SwingType.HIGH and not any(c.high > swing.price for c in later):
            pools.append(LiquidityPool(
                price=swing.price, direction="buyside",
                scope="external" if swing.price > price else "internal",
                taps=1,
                evidence=f"Swing high {swing.price:.6g} has not been traded through since it formed.",
            ))
        elif swing.type is SwingType.LOW and not any(c.low < swing.price for c in later):
            pools.append(LiquidityPool(
                price=swing.price, direction="sellside",
                scope="external" if swing.price < price else "internal",
                taps=1,
                evidence=f"Swing low {swing.price:.6g} has not been traded through since it formed.",
            ))

    # De-duplicate near-identical levels, keeping the best-evidenced one.
    pools.sort(key=lambda p: (-p.taps, abs(p.price - price)))
    unique: list[LiquidityPool] = []
    for pool in pools:
        if not any(abs(pool.price - u.price) / max(price, 1e-9) < 0.0015 for u in unique):
            unique.append(pool)
    return unique[:12]


def premium_discount(series: Series, structure: StructureReport) -> dict:
    """Where price sits inside the current dealing range.

    Above the 50% equilibrium is premium (expensive — favour selling); below is
    discount (cheap — favour buying). This is the frame ICT setups are graded in.
    """
    high = structure.range_high
    low = structure.range_low
    if high is None or low is None or high <= low or not series.last:
        return {"available": False}

    price = series.last.close
    equilibrium = (high + low) / 2
    position = (price - low) / (high - low)

    if position > 0.7:
        zone, bias = "premium", "favours selling — price is expensive within the range"
    elif position < 0.3:
        zone, bias = "discount", "favours buying — price is cheap within the range"
    else:
        zone, bias = "equilibrium", "no edge from location alone — price sits near fair value"

    return {
        "available": True,
        "range_high": high,
        "range_low": low,
        "equilibrium": equilibrium,
        "price": price,
        "position_percent": round(position * 100, 1),
        "zone": zone,
        "evidence": (
            f"Price {price:.6g} is {position:.0%} of the way through the {low:.6g}-{high:.6g} "
            f"dealing range (equilibrium {equilibrium:.6g}) — {zone}, which {bias}."
        ),
    }


def optimal_trade_entry(series: Series, structure: StructureReport) -> dict:
    """The 62-79% retracement band of the most recent impulse leg.

    Direction follows the last structural event, because OTE is only meaningful
    as a pullback entry in the direction of the break.
    """
    if not structure.events or not structure.swings or not series.last:
        return {"available": False}

    event = structure.events[-1]
    swings_before = [s for s in structure.swings if s.index <= event.index]
    if len(swings_before) < 2:
        return {"available": False}

    if event.direction == "bullish":
        leg_low = min(s.price for s in swings_before[-3:] if s.type is SwingType.LOW) \
            if any(s.type is SwingType.LOW for s in swings_before[-3:]) else None
        leg_high = max(c.high for c in series.candles[event.index :])
        if leg_low is None or leg_high <= leg_low:
            return {"available": False}
        span = leg_high - leg_low
        zone_top, zone_bottom = leg_high - 0.62 * span, leg_high - 0.79 * span
    else:
        leg_high = max(s.price for s in swings_before[-3:] if s.type is SwingType.HIGH) \
            if any(s.type is SwingType.HIGH for s in swings_before[-3:]) else None
        leg_low = min(c.low for c in series.candles[event.index :])
        if leg_high is None or leg_high <= leg_low:
            return {"available": False}
        span = leg_high - leg_low
        zone_bottom, zone_top = leg_low + 0.62 * span, leg_low + 0.79 * span

    price = series.last.close
    return {
        "available": True,
        "direction": event.direction,
        "top": max(zone_top, zone_bottom),
        "bottom": min(zone_top, zone_bottom),
        "price_in_zone": min(zone_top, zone_bottom) <= price <= max(zone_top, zone_bottom),
        "evidence": (
            f"62-79% retracement of the {event.direction} leg from {leg_low:.6g} to {leg_high:.6g} "
            f"sits at {min(zone_top, zone_bottom):.6g}-{max(zone_top, zone_bottom):.6g}."
        ),
    }


def find_inducement(series: Series, structure: StructureReport) -> dict | None:
    """Inducement: the minor liquidity grab that precedes the real move.

    Detected as a sweep that happened shortly before the most recent structural
    event, in the opposite direction — the classic "take the obvious stops
    first" sequence.
    """
    if not structure.events or not structure.sweeps:
        return None

    event = structure.events[-1]
    for sweep in reversed(structure.sweeps):
        if not (0 < event.index - sweep.index <= 15):
            continue
        opposite = (
            (event.direction == "bullish" and sweep.direction == "sellside")
            or (event.direction == "bearish" and sweep.direction == "buyside")
        )
        if opposite:
            return {
                "index": sweep.index,
                "time": sweep.timestamp,
                "level": sweep.level,
                "direction": sweep.direction,
                "evidence": (
                    f"{sweep.direction} liquidity at {sweep.level:.6g} was swept "
                    f"{event.index - sweep.index} bars before the {event.direction} {event.kind} — "
                    f"textbook inducement ahead of the real move."
                ),
            }
    return None


def _mark_mitigated(zones: list[Zone], series: Series) -> None:
    """Flag zones price has already traded back through."""
    candles = series.candles
    for zone in zones:
        for c in candles[zone.index + 2 :]:
            if zone.direction == "bullish" and c.low <= zone.bottom:
                zone.mitigated = True
                break
            if zone.direction == "bearish" and c.high >= zone.top:
                zone.mitigated = True
                break


def analyze_smc(series: Series, structure: StructureReport) -> SMCReport:
    """Run every SMC detector and assemble the narrative."""
    report = SMCReport()
    if len(series) < 30:
        report.narrative.append("Not enough bars for Smart Money Concepts analysis.")
        return report

    report.order_blocks = find_order_blocks(series, structure)
    report.breaker_blocks, report.mitigation_blocks = find_breaker_and_mitigation(
        series, report.order_blocks
    )
    report.fair_value_gaps = [z for z in find_fair_value_gaps(series) if not z.mitigated][-10:]
    report.liquidity_pools = find_liquidity_pools(series, structure)
    report.premium_discount = premium_discount(series, structure)
    report.ote = optimal_trade_entry(series, structure)
    report.inducement = find_inducement(series, structure)

    live_blocks = [b for b in report.order_blocks if not b.mitigated]
    if live_blocks:
        nearest = min(live_blocks, key=lambda b: abs(b.mid - series.last.close))
        report.narrative.append(
            f"Nearest unmitigated {nearest.direction} order block: "
            f"{nearest.bottom:.6g}-{nearest.top:.6g}. {nearest.evidence}"
        )
    if report.fair_value_gaps:
        gap = report.fair_value_gaps[-1]
        report.narrative.append(
            f"Most recent unfilled {gap.direction} FVG at {gap.bottom:.6g}-{gap.top:.6g}. {gap.evidence}"
        )
    if report.premium_discount.get("available"):
        report.narrative.append(report.premium_discount["evidence"])
    if report.ote.get("available"):
        report.narrative.append(report.ote["evidence"])
    if report.inducement:
        report.narrative.append(report.inducement["evidence"])

    buyside = [p for p in report.liquidity_pools if p.direction == "buyside"]
    sellside = [p for p in report.liquidity_pools if p.direction == "sellside"]
    if buyside and sellside:
        price = series.last.close
        nearest_buy = min(buyside, key=lambda p: abs(p.price - price))
        nearest_sell = min(sellside, key=lambda p: abs(p.price - price))
        report.narrative.append(
            f"Liquidity is stacked at {nearest_sell.price:.6g} below and "
            f"{nearest_buy.price:.6g} above — the most likely draw is whichever side "
            f"the current structure is pointing toward."
        )

    return report
