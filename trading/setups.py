"""Trade setup construction.

A setup is only emitted when the analysis actually supports one. If the
dominant scenario is "range" or the evidence is contradictory, this module
returns an explicit no-trade result with the reason — a platform that always
produces a setup is a platform that produces bad setups.

Levels are placed on structure, not on round multiples: the stop sits beyond
the swing that would invalidate the idea, and targets sit at the liquidity the
market is actually likely to reach for.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from trading.indicators import atr, last_valid
from trading.models import Series, Timeframe
from trading.structure import SwingType


@dataclass
class TradeSetup:
    valid: bool = False
    direction: str = "none"          # long | short | none
    entry: float = 0.0
    entry_type: str = "market"       # market | limit | stop
    stop_loss: float = 0.0
    take_profits: list[float] = field(default_factory=list)
    risk_reward: list[float] = field(default_factory=list)
    primary_rr: float = 0.0
    confidence: float = 0.0          # 0-100
    invalidation: str = ""
    holding_time: str = ""
    expected_move: float | None = None
    expected_move_percent: float | None = None
    rationale: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    no_trade_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "direction": self.direction,
            "entry": self.entry,
            "entry_type": self.entry_type,
            "stop_loss": self.stop_loss,
            "take_profits": self.take_profits,
            "risk_reward": [round(r, 2) for r in self.risk_reward],
            "primary_rr": round(self.primary_rr, 2),
            "confidence": round(self.confidence, 1),
            "invalidation": self.invalidation,
            "holding_time": self.holding_time,
            "expected_move": self.expected_move,
            "expected_move_percent": self.expected_move_percent,
            "rationale": self.rationale,
            "warnings": self.warnings,
            "no_trade_reason": self.no_trade_reason,
        }


# Minimum reward:risk we will publish. Below this, the setup is not worth the
# spread and slippage, however good the chart looks.
MIN_ACCEPTABLE_RR = 1.5


def build_setup(
    series: Series,
    context,        # regime.MarketContext
    structure,      # structure.StructureReport
    smc,            # smc.SMCReport
    levels,         # levels.LevelReport
    probabilities,  # probability.ProbabilityReport
    volume: dict,
) -> TradeSetup:
    """Derive a concrete trade plan from the completed analysis."""
    setup = TradeSetup()
    if not series.last or len(series) < 60:
        setup.no_trade_reason = "Not enough price history to plan a trade."
        return setup

    price = series.last.close
    atr_now = last_valid(atr(series, 14))
    if not atr_now or atr_now <= 0:
        setup.no_trade_reason = "ATR is unavailable, so risk cannot be sized."
        return setup

    dominant, dominant_prob = probabilities.dominant

    # --- Decide whether there is a trade at all -----------------------------
    if dominant in ("range", "false breakout"):
        setup.no_trade_reason = (
            f"The dominant scenario is '{dominant}' at {dominant_prob:.0f}%. "
            f"Rotational and failed-break conditions are where directional setups bleed out; "
            f"the analysis does not support taking a side here."
        )
        return setup

    if probabilities.confidence < 35:
        setup.no_trade_reason = (
            f"Scenario confidence is only {probabilities.confidence:.0f}%. The evidence is too "
            f"evenly split between outcomes to justify a position."
        )
        return setup

    long_score = probabilities.bullish_continuation
    short_score = probabilities.bearish_continuation
    if dominant == "reversal":
        # A reversal trades against the prevailing trend.
        direction = "short" if context.trend == "bullish" else "long"
    elif dominant == "breakout":
        direction = "long" if long_score >= short_score else "short"
    else:
        direction = "long" if long_score > short_score else "short"

    if abs(long_score - short_score) < 5 and dominant != "reversal":
        setup.no_trade_reason = (
            f"Bullish ({long_score:.0f}%) and bearish ({short_score:.0f}%) continuation odds are "
            f"within 5 points of each other — there is no directional edge to trade."
        )
        return setup

    setup.direction = direction
    rationale: list[str] = [
        f"Dominant scenario is {dominant} at {dominant_prob:.0f}% with "
        f"{probabilities.confidence:.0f}% confidence.",
    ]
    rationale.extend(f.explanation for f in probabilities.factors[:4])

    # --- Entry --------------------------------------------------------------
    entry, entry_type, entry_note = _choose_entry(series, smc, levels, direction, price, atr_now)
    setup.entry = entry
    setup.entry_type = entry_type
    rationale.append(entry_note)

    # --- Stop ---------------------------------------------------------------
    stop, stop_note = _choose_stop(series, structure, direction, entry, atr_now)
    setup.stop_loss = stop
    rationale.append(stop_note)

    risk = abs(entry - stop)
    if risk <= 0:
        setup.no_trade_reason = "Stop and entry resolved to the same price; no valid risk."
        return setup

    # --- Targets ------------------------------------------------------------
    targets, target_notes = _choose_targets(smc, levels, direction, entry, risk, atr_now)
    setup.take_profits = targets
    rationale.extend(target_notes)

    setup.risk_reward = [abs(t - entry) / risk for t in targets]
    setup.primary_rr = setup.risk_reward[0] if setup.risk_reward else 0.0

    if setup.primary_rr < MIN_ACCEPTABLE_RR:
        setup.no_trade_reason = (
            f"The nearest sensible target is only {setup.primary_rr:.2f}R away. Below "
            f"{MIN_ACCEPTABLE_RR}R the setup does not clear costs and a normal win rate."
        )
        setup.rationale = rationale
        return setup

    # --- Confidence, invalidation, timing ----------------------------------
    setup.confidence = _confidence(probabilities, context, volume, setup.primary_rr, direction)
    setup.invalidation = _invalidation(structure, direction, stop)
    setup.holding_time = _holding_time(series.timeframe, probabilities.base_rates.get("horizon", 10))

    furthest = targets[-1] if targets else entry
    setup.expected_move = abs(furthest - entry)
    setup.expected_move_percent = 100 * setup.expected_move / entry if entry else None

    # --- Warnings -----------------------------------------------------------
    if context.htf_bias in ("bullish", "bearish"):
        aligned = (context.htf_bias == "bullish") == (direction == "long")
        if not aligned:
            setup.warnings.append(
                f"This is a counter-trend trade: the higher timeframe bias is {context.htf_bias} "
                f"while the setup is {direction}. Reduce size accordingly."
            )
    if context.volatility == "high":
        setup.warnings.append(
            "Volatility is elevated — the stop is wide in cash terms, so size must come down "
            "to keep risk constant."
        )
    if context.liquidity == "low":
        setup.warnings.append(
            "Liquidity is thin; expect worse fills than the plan assumes on both entry and exit."
        )
    if volume.get("available") and not volume.get("confirms_move"):
        setup.warnings.append(
            "Volume is not currently confirming the move — consider waiting for a participation "
            "spike before committing."
        )

    setup.rationale = rationale
    setup.valid = True
    return setup


def _choose_entry(
    series: Series, smc, levels, direction: str, price: float, atr_now: float
) -> tuple[float, str, str]:
    """Prefer a limit at an unmitigated zone; fall back to market at the close.

    Entering at a zone rather than chasing the close is what produces the tight
    stop that makes the reward:risk work.
    """
    want = "bullish" if direction == "long" else "bearish"

    candidates = [z for z in smc.order_blocks if z.direction == want and not z.mitigated]
    candidates += [z for z in smc.fair_value_gaps if z.direction == want and not z.mitigated]

    # A usable zone is one price hasn't passed yet, within 2 ATR.
    usable = []
    for zone in candidates:
        if direction == "long" and zone.top < price and (price - zone.top) < atr_now * 2:
            usable.append((price - zone.top, zone))
        elif direction == "short" and zone.bottom > price and (zone.bottom - price) < atr_now * 2:
            usable.append((zone.bottom - price, zone))

    if usable:
        _, zone = min(usable, key=lambda pair: pair[0])
        entry = zone.top if direction == "long" else zone.bottom
        return entry, "limit", (
            f"Entry set at {entry:.6g}, the edge of an unmitigated {zone.direction} "
            f"{zone.kind.replace('_', ' ')} — a pullback into this zone gives a far tighter "
            f"invalidation than entering at {price:.6g}."
        )

    if smc.ote.get("available") and smc.ote.get("direction") == want:
        entry = smc.ote["top"] if direction == "long" else smc.ote["bottom"]
        if (direction == "long" and entry < price) or (direction == "short" and entry > price):
            return entry, "limit", (
                f"Entry set at {entry:.6g} inside the optimal trade entry zone. {smc.ote['evidence']}"
            )

    return price, "market", (
        f"No unmitigated zone sits within reach, so entry is at market ({price:.6g}). "
        f"This is a worse fill than a pullback entry and the reward:risk reflects that."
    )


def _choose_stop(series: Series, structure, direction: str, entry: float, atr_now: float) -> tuple[float, str]:
    """Place the stop beyond the structural level that would prove the idea wrong.

    An ATR buffer is added so ordinary noise around the swing does not take the
    trade out before the thesis has actually failed.
    """
    buffer = atr_now * 0.35

    if direction == "long":
        lows = [s for s in structure.swings if s.type is SwingType.LOW and s.price < entry]
        if lows:
            swing = max(lows, key=lambda s: s.price)
            stop = swing.price - buffer
            return stop, (
                f"Stop at {stop:.6g}, {buffer:.6g} below the swing low at {swing.price:.6g}. "
                f"A close beneath that low breaks the higher-low sequence this trade depends on."
            )
        stop = entry - atr_now * 1.5
        return stop, (
            f"No protective swing low is available, so the stop falls back to 1.5 ATR "
            f"({stop:.6g}) — a volatility-based invalidation rather than a structural one."
        )

    highs = [s for s in structure.swings if s.type is SwingType.HIGH and s.price > entry]
    if highs:
        swing = min(highs, key=lambda s: s.price)
        stop = swing.price + buffer
        return stop, (
            f"Stop at {stop:.6g}, {buffer:.6g} above the swing high at {swing.price:.6g}. "
            f"A close above that high breaks the lower-high sequence this trade depends on."
        )
    stop = entry + atr_now * 1.5
    return stop, (
        f"No protective swing high is available, so the stop falls back to 1.5 ATR "
        f"({stop:.6g}) — a volatility-based invalidation rather than a structural one."
    )


def _choose_targets(
    smc, levels, direction: str, entry: float, risk: float, atr_now: float
) -> tuple[list[float], list[str]]:
    """Target the liquidity and levels price is actually likely to reach for."""
    notes: list[str] = []
    candidates: list[tuple[float, str]] = []

    for pool in smc.liquidity_pools:
        if direction == "long" and pool.direction == "buyside" and pool.price > entry:
            candidates.append((pool.price, f"buy-side liquidity at {pool.price:.6g} ({pool.taps} taps)"))
        elif direction == "short" and pool.direction == "sellside" and pool.price < entry:
            candidates.append((pool.price, f"sell-side liquidity at {pool.price:.6g} ({pool.taps} taps)"))

    level_pool = levels.resistance if direction == "long" else levels.support
    level_pool = level_pool + levels.period_levels
    for level in level_pool:
        if direction == "long" and level.price > entry:
            candidates.append((level.price, f"{level.label} at {level.price:.6g}"))
        elif direction == "short" and level.price < entry:
            candidates.append((level.price, f"{level.label} at {level.price:.6g}"))

    candidates.sort(key=lambda pair: abs(pair[0] - entry))

    targets: list[float] = []
    for price_level, description in candidates:
        distance = abs(price_level - entry)
        if distance < risk * 1.0:
            continue  # too close to be worth a target
        if any(abs(price_level - t) < risk * 0.4 for t in targets):
            continue  # effectively the same level as one we already took
        targets.append(price_level)
        notes.append(
            f"TP{len(targets)} at {price_level:.6g} ({distance / risk:.2f}R) — {description}."
        )
        if len(targets) == 3:
            break

    # Backfill with R-multiples when the chart doesn't offer three levels.
    fallbacks = [1.5, 2.5, 4.0]
    while len(targets) < 3:
        multiple = fallbacks[len(targets)]
        price_level = entry + risk * multiple if direction == "long" else entry - risk * multiple
        if any(abs(price_level - t) < risk * 0.4 for t in targets):
            fallbacks[len(targets)] += 1.0
            continue
        targets.append(price_level)
        notes.append(
            f"TP{len(targets)} at {price_level:.6g} ({multiple:.1f}R) — no further structural level "
            f"in range, so this is a fixed R-multiple target."
        )

    targets.sort(reverse=(direction == "short"))
    return targets, notes


def _confidence(probabilities, context, volume: dict, rr: float, direction: str) -> float:
    """Blend scenario confidence, confluence and reward:risk into a 0-100 score."""
    score = probabilities.confidence * 0.45

    # Reward:risk contribution, saturating at 4R.
    score += min(rr / 4, 1.0) * 100 * 0.2

    # HTF alignment is the single most valuable confluence.
    if context.htf_bias in ("bullish", "bearish"):
        aligned = (context.htf_bias == "bullish") == (direction == "long")
        score += 15 if aligned else -10

    if context.trend_strength_label in ("strong", "very strong"):
        aligned = (context.trend == "bullish") == (direction == "long")
        score += 10 if aligned else -5

    if volume.get("available") and volume.get("confirms_move"):
        score += 8
    if context.liquidity == "low":
        score -= 8

    return max(0.0, min(100.0, score))


def _invalidation(structure, direction: str, stop: float) -> str:
    event = structure.last_event
    base = (
        f"A close beyond {stop:.6g} invalidates the idea outright. "
    )
    if event:
        opposite = "bearish" if direction == "long" else "bullish"
        base += (
            f"Ahead of that, an opposing {opposite} CHOCH would remove the structural premise "
            f"(the current read rests on the {event.direction} {event.kind} at {event.price:.6g})."
        )
    return base


def _holding_time(timeframe: Timeframe, horizon: int) -> str:
    """Express the expected holding period in human units."""
    total_minutes = timeframe.minutes * horizon
    if total_minutes < 60:
        return f"about {total_minutes} minutes ({horizon} bars on {timeframe.value})"
    if total_minutes < 1440:
        return f"about {total_minutes / 60:.1f} hours ({horizon} bars on {timeframe.value})"
    return f"about {total_minutes / 1440:.1f} days ({horizon} bars on {timeframe.value})"
