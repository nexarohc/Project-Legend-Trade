"""Scenario probability engine.

Two things produce the numbers here, and both are inspectable:

1. **An empirical base rate** measured on the symbol's own history. We find
   every past bar whose regime resembled the present one and count what
   actually happened over the next N bars. This anchors the output in what this
   instrument has really done rather than in a hand-tuned constant.

2. **Weighted evidence** from structure, momentum, volume, SMC and levels,
   which tilts the base rate. Every factor carries its own weight and a
   sentence explaining it, and all of them are returned to the caller.

The engine reports the sample size behind every base rate and downgrades its
own confidence when that sample is thin. A probability with no stated evidence
is exactly what this platform is built to avoid emitting.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from trading.indicators import adx, atr, last_valid, rsi
from trading.models import Series


@dataclass
class Factor:
    """One piece of evidence pushing a scenario up or down."""

    name: str
    scenario: str
    weight: float          # signed: positive supports the scenario
    explanation: str

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "scenario": self.scenario,
            "weight": round(self.weight, 2),
            "explanation": self.explanation,
        }


@dataclass
class ProbabilityReport:
    bullish_continuation: float = 0.0
    bearish_continuation: float = 0.0
    breakout: float = 0.0
    reversal: float = 0.0
    range_bound: float = 0.0
    false_breakout: float = 0.0
    confidence: float = 0.0          # 0-100, how much to trust the split
    sample_size: int = 0
    base_rates: dict = field(default_factory=dict)
    factors: list[Factor] = field(default_factory=list)
    reasoning: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "bullish_continuation": round(self.bullish_continuation, 1),
            "bearish_continuation": round(self.bearish_continuation, 1),
            "breakout": round(self.breakout, 1),
            "reversal": round(self.reversal, 1),
            "range_bound": round(self.range_bound, 1),
            "false_breakout": round(self.false_breakout, 1),
            "confidence": round(self.confidence, 1),
            "sample_size": self.sample_size,
            "base_rates": self.base_rates,
            "factors": [f.to_dict() for f in self.factors],
            "reasoning": self.reasoning,
            "caveats": self.caveats,
        }

    @property
    def dominant(self) -> tuple[str, float]:
        options = {
            "bullish continuation": self.bullish_continuation,
            "bearish continuation": self.bearish_continuation,
            "breakout": self.breakout,
            "reversal": self.reversal,
            "range": self.range_bound,
            "false breakout": self.false_breakout,
        }
        name = max(options, key=options.get)
        return name, options[name]


def measure_base_rates(series: Series, horizon: int = 10) -> dict:
    """Measure what actually happened next, historically, in comparable conditions.

    "Comparable" means the same trend direction (price vs its own 50-bar mean)
    and the same ADX bucket. For each matching past bar we look `horizon` bars
    forward and classify the outcome. This is a genuine empirical frequency for
    this symbol and timeframe — not a universal constant — and the sample size
    is returned so a caller can see how much weight it deserves.
    """
    n = len(series)
    empty = {
        "continuation": 0.0, "reversal": 0.0, "range": 0.0,
        "sample": 0, "horizon": horizon,
        "note": "Not enough history to measure base rates.",
    }
    if n < 120:
        return empty

    closes = series.closes
    atr_vals = atr(series, 14)
    adx_vals, _, _ = adx(series, 14)

    # Characterise the present bar.
    current_adx = last_valid(adx_vals)
    if current_adx is None:
        return empty
    current_bucket = _adx_bucket(current_adx)
    current_dir = _direction_at(closes, n - 1)

    matches = 0
    continuation = reversal = ranged = 0

    # Stop `horizon` bars early so every sampled bar has a real forward outcome.
    for i in range(60, n - horizon):
        a = adx_vals[i]
        atr_now = atr_vals[i]
        if a is None or atr_now is None or atr_now <= 0:
            continue
        if _adx_bucket(a) != current_bucket:
            continue
        direction = _direction_at(closes, i)
        if direction != current_dir or direction == 0:
            continue

        matches += 1
        move = closes[i + horizon] - closes[i]
        normalized = move / atr_now  # in ATR units, so it is comparable across time

        if abs(normalized) < 0.75:
            ranged += 1
        elif (normalized > 0) == (direction > 0):
            continuation += 1
        else:
            reversal += 1

    if matches < 20:
        return {
            **empty,
            "sample": matches,
            "note": (
                f"Only {matches} historical analogues found — too few to be reliable, "
                f"so evidence weighting dominates the result."
            ),
        }

    return {
        "continuation": 100 * continuation / matches,
        "reversal": 100 * reversal / matches,
        "range": 100 * ranged / matches,
        "sample": matches,
        "horizon": horizon,
        "direction": "bullish" if current_dir > 0 else "bearish",
        "note": (
            f"Across {matches} past bars on this symbol with the same trend direction and "
            f"ADX regime, price continued {100 * continuation / matches:.0f}% of the time, "
            f"reversed {100 * reversal / matches:.0f}%, and went nowhere "
            f"{100 * ranged / matches:.0f}% over the next {horizon} bars."
        ),
    }


def _adx_bucket(value: float) -> str:
    if value >= 40:
        return "very_strong"
    if value >= 25:
        return "strong"
    if value >= 20:
        return "developing"
    return "weak"


def _direction_at(closes: list[float], index: int, period: int = 50) -> int:
    """+1 when price is above its own N-bar mean, -1 below, 0 when flat."""
    start = max(0, index - period + 1)
    window = closes[start : index + 1]
    if len(window) < 10:
        return 0
    mean = sum(window) / len(window)
    if closes[index] > mean * 1.001:
        return 1
    if closes[index] < mean * 0.999:
        return -1
    return 0


def compute_probabilities(
    series: Series,
    context,          # regime.MarketContext
    structure,        # structure.StructureReport
    smc,              # smc.SMCReport
    volume: dict,
    price_action: dict,
    levels,           # levels.LevelReport
    horizon: int = 10,
) -> ProbabilityReport:
    """Blend empirical base rates with weighted evidence into scenario odds."""
    report = ProbabilityReport()
    if len(series) < 60:
        report.caveats.append("Fewer than 60 bars available — probabilities are not meaningful.")
        return report

    base = measure_base_rates(series, horizon)
    report.base_rates = base
    report.sample_size = base.get("sample", 0)
    if base.get("note"):
        report.reasoning.append(base["note"])

    # Start from the base rates, split into directional buckets by trend.
    bull_dir = context.trend == "bullish" or context.institutional_bias.startswith("accumulating")
    continuation_seed = base.get("continuation", 40.0) or 40.0
    reversal_seed = base.get("reversal", 25.0) or 25.0
    range_seed = base.get("range", 35.0) or 35.0

    scores = {
        "bullish_continuation": continuation_seed if bull_dir else reversal_seed * 0.6,
        "bearish_continuation": continuation_seed if not bull_dir else reversal_seed * 0.6,
        "breakout": 15.0,
        "reversal": reversal_seed,
        "range_bound": range_seed,
        "false_breakout": 10.0,
    }

    factors: list[Factor] = []

    def add(name: str, scenario: str, weight: float, explanation: str) -> None:
        factors.append(Factor(name, scenario, weight, explanation))
        scores[scenario] = max(0.0, scores[scenario] + weight)

    # --- Structure evidence -------------------------------------------------
    event = structure.last_event
    if event:
        target = "bullish_continuation" if event.direction == "bullish" else "bearish_continuation"
        if event.kind == "BOS":
            add("Break of structure", target, 12,
                f"A {event.direction} BOS confirms trend continuation. {event.evidence}")
        else:
            add("Change of character", "reversal", 14,
                f"A {event.direction} CHOCH is the first evidence of a trend change. {event.evidence}")
            add("Change of character", target, 8,
                f"A CHOCH also opens the door to a new {event.direction} leg developing.")

    if structure.trend_strength >= 75:
        target = "bullish_continuation" if structure.trend == "bullish" else "bearish_continuation"
        add("Clean swing structure", target, 10,
            f"{structure.trend_strength:.0f}% of recent swings agree with the {structure.trend} trend — "
            f"a clean staircase rarely breaks without warning.")
    elif structure.trend_strength and structure.trend_strength < 55:
        add("Mixed swing structure", "range_bound", 10,
            f"Only {structure.trend_strength:.0f}% of recent swings agree on direction — "
            f"choppy structure favours rotation over trend.")

    if structure.phase.value in ("compression", "range"):
        add("Compression phase", "breakout", 12,
            "Volatility is compressing; compressed ranges resolve into expansion, "
            "which raises the odds of a breakout over the coming bars.")
        add("Compression phase", "range_bound", 6,
            "Until it resolves, compression itself means continued range trade.")
    elif structure.phase.value == "expansion":
        target = "bullish_continuation" if structure.trend == "bullish" else "bearish_continuation"
        add("Expansion phase", target, 8,
            "Volatility is already expanding, which favours the move continuing rather than "
            "a fresh breakout from a base.")
        add("Expansion phase", "range_bound", -10,
            "An expanding market is by definition not ranging.")

    if structure.sweeps:
        recent_sweeps = [s for s in structure.sweeps if s.index >= len(series) - 10]
        for sweep in recent_sweeps[-2:]:
            target = "bullish_continuation" if sweep.direction == "sellside" else "bearish_continuation"
            add("Recent liquidity sweep", target, 9,
                f"{sweep.evidence} Stop runs that reverse typically precede a move the other way.")
            add("Recent liquidity sweep", "false_breakout", 6,
                "A failed break at this level is itself the false-breakout scenario playing out.")

    # --- Momentum & context -------------------------------------------------
    if context.momentum.startswith("strong"):
        target = "bullish_continuation" if context.momentum.endswith("bullish") else "bearish_continuation"
        add("Strong momentum", target, 10,
            f"Momentum reads {context.momentum} (score {context.momentum_score:+.0f}) — "
            f"strong momentum tends to persist over a {horizon}-bar horizon.")
    elif context.momentum == "neutral":
        add("Flat momentum", "range_bound", 8,
            "Momentum is neutral, which is characteristic of rotation rather than trend.")

    if context.htf_bias in ("bullish", "bearish"):
        target = "bullish_continuation" if context.htf_bias == "bullish" else "bearish_continuation"
        aligned = (context.htf_bias == "bullish") == bull_dir
        if aligned:
            add("HTF alignment", target, 12,
                f"The higher timeframe is {context.htf_bias} and agrees with the working timeframe — "
                f"the strongest single confluence available.")
        else:
            add("HTF conflict", "reversal", 10,
                f"The higher timeframe is {context.htf_bias} while this timeframe points the other way — "
                f"counter-trend setups fail more often than they pay.")

    if context.volatility == "low":
        add("Low volatility", "breakout", 8,
            f"ATR sits in the {context.volatility_percentile:.0f}th percentile — "
            f"quiet markets are where breakouts originate.")
    elif context.volatility == "high":
        add("High volatility", "false_breakout", 8,
            f"ATR in the {context.volatility_percentile:.0f}th percentile means wide, noisy bars "
            f"that overshoot levels and snap back.")

    if context.liquidity == "low":
        add("Thin liquidity", "false_breakout", 7,
            "Participation is thin; breaks on low liquidity are the ones that most often fail.")

    # --- Volume -------------------------------------------------------------
    if volume.get("available"):
        if volume.get("confirms_move"):
            target = "bullish_continuation" if series.last.bullish else "bearish_continuation"
            add("Volume confirmation", target, 9,
                f"The latest directional bar printed on {volume['relative_volume']}x average volume — "
                f"real participation behind the move.")
        divergence = volume.get("divergence", {})
        if divergence.get("detected"):
            add("Volume divergence", "reversal", 11, divergence["evidence"])
        if volume.get("spike"):
            add("Volume spike", "breakout", 7,
                f"A {volume['relative_volume']}x volume spike marks the kind of participation "
                f"that accompanies genuine breaks.")
        if volume.get("volume_trend") == "falling":
            target = "bullish_continuation" if bull_dir else "bearish_continuation"
            add("Fading volume", target, -8,
                "Volume is falling into the current move — trends that lose participation stall.")

    # --- Price action -------------------------------------------------------
    pa_bias = price_action.get("bias")
    pa_conf = price_action.get("confidence", 0)
    if pa_bias in ("bullish", "bearish") and pa_conf > 0.6:
        target = f"{pa_bias}_continuation"
        add("Price action bias", target, 8 * pa_conf,
            f"Recent candlestick patterns lean {pa_bias} with {pa_conf:.0%} agreement "
            f"({price_action.get('bullish_weight')} bull vs {price_action.get('bearish_weight')} bear weight).")
    fakeouts = [p for p in price_action.get("recent", []) if "Fake Breakout" in p["name"]]
    if fakeouts:
        add("Recent failed break", "false_breakout", 10,
            f"{fakeouts[-1]['evidence']}")

    # --- SMC & levels -------------------------------------------------------
    if smc.premium_discount.get("available"):
        zone = smc.premium_discount["zone"]
        if zone == "premium":
            add("Premium pricing", "bearish_continuation", 7,
                f"Price is at {smc.premium_discount['position_percent']:.0f}% of the dealing range — "
                f"a premium location where institutional selling is expected.")
            add("Premium pricing", "reversal", 5,
                "Buying into premium is where late longs get trapped.")
        elif zone == "discount":
            add("Discount pricing", "bullish_continuation", 7,
                f"Price is at {smc.premium_discount['position_percent']:.0f}% of the dealing range — "
                f"a discount location where institutional buying is expected.")

    live_blocks = [b for b in smc.order_blocks if not b.mitigated]
    if live_blocks and series.last:
        price = series.last.close
        nearest = min(live_blocks, key=lambda b: abs(b.mid - price))
        atr_now = last_valid(atr(series)) or 0.0
        if atr_now and abs(nearest.mid - price) < atr_now * 1.5:
            target = "bullish_continuation" if nearest.direction == "bullish" else "bearish_continuation"
            add("At an order block", target, 9,
                f"Price is within 1.5 ATR of an unmitigated {nearest.direction} order block at "
                f"{nearest.bottom:.6g}-{nearest.top:.6g}. {nearest.evidence}")

    if levels.nearest_resistance and levels.nearest_support and series.last:
        price = series.last.close
        to_res = levels.nearest_resistance.price - price
        to_sup = price - levels.nearest_support.price
        atr_now = last_valid(atr(series)) or 0.0
        if atr_now > 0:
            if to_res < atr_now * 0.5:
                add("Pressed into resistance", "breakout", 7,
                    f"Price sits within half an ATR of {levels.nearest_resistance.label} at "
                    f"{levels.nearest_resistance.price:.6g} — either it breaks or it rejects.")
                add("Pressed into resistance", "reversal", 6,
                    f"{levels.nearest_resistance.evidence}")
            if to_sup < atr_now * 0.5:
                add("Sitting on support", "reversal", 6,
                    f"Price sits within half an ATR of {levels.nearest_support.label} at "
                    f"{levels.nearest_support.price:.6g}. {levels.nearest_support.evidence}")
            room = (to_res + to_sup) / atr_now
            if room < 2:
                add("Tight level box", "range_bound", 9,
                    f"Support and resistance are only {room:.1f} ATR apart — little room to trend.")

    # --- Normalise ----------------------------------------------------------
    total = sum(scores.values())
    if total <= 0:
        report.caveats.append("No usable evidence; probabilities withheld.")
        return report

    report.bullish_continuation = 100 * scores["bullish_continuation"] / total
    report.bearish_continuation = 100 * scores["bearish_continuation"] / total
    report.breakout = 100 * scores["breakout"] / total
    report.reversal = 100 * scores["reversal"] / total
    report.range_bound = 100 * scores["range_bound"] / total
    report.false_breakout = 100 * scores["false_breakout"] / total
    report.factors = sorted(factors, key=lambda f: abs(f.weight), reverse=True)

    report.confidence = _confidence(report, base, factors)

    dominant, value = report.dominant
    report.reasoning.append(
        f"Most likely scenario over the next {horizon} bars: {dominant} at {value:.0f}%, "
        f"driven mainly by "
        + ", ".join(f.name.lower() for f in report.factors[:3])
        + "."
    )
    report.caveats.append(
        "These are conditional odds over a fixed forward horizon, not a guarantee. "
        "They ignore scheduled news and assume the current regime persists."
    )
    if report.sample_size < 50:
        report.caveats.append(
            f"The historical analogue sample is only {report.sample_size} bars — "
            f"treat the empirical component as weak."
        )
    return report


def _confidence(report: ProbabilityReport, base: dict, factors: list[Factor]) -> float:
    """How much the split should be trusted.

    High when many independent factors agree and the historical sample is
    large; low when the top two scenarios are nearly tied, because a 26/25
    split is not a call.
    """
    values = sorted(
        [report.bullish_continuation, report.bearish_continuation, report.breakout,
         report.reversal, report.range_bound, report.false_breakout],
        reverse=True,
    )
    separation = values[0] - values[1]          # 0-100
    sample = min(1.0, base.get("sample", 0) / 100)
    breadth = min(1.0, len(factors) / 12)

    score = separation * 2.0 * 0.5 + sample * 100 * 0.25 + breadth * 100 * 0.25
    return max(0.0, min(100.0, score))
