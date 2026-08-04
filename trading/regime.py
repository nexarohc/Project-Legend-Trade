"""Market context: momentum, volatility, liquidity and regime classification.

This is Step 1 of the analysis pipeline and it runs before anything predictive.
The platform's rule is that it describes the market it is looking at before it
says anything about where the market might go, and this module produces that
description.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from trading.indicators import adx, atr, ema, last_valid, rsi
from trading.models import Series, Timeframe
from trading.structure import Trend, analyze_structure


@dataclass
class MarketContext:
    trend: str = "ranging"
    trend_strength: float = 0.0
    trend_strength_label: str = "weak"
    momentum: str = "neutral"
    momentum_score: float = 0.0
    volatility: str = "moderate"
    volatility_percentile: float = 50.0
    atr_value: float | None = None
    atr_percent: float | None = None
    liquidity: str = "unknown"
    regime: str = "undefined"
    condition: str = "undefined"
    htf_bias: str = "unknown"
    ltf_bias: str = "unknown"
    institutional_bias: str = "neutral"
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "trend": self.trend,
            "trend_strength": round(self.trend_strength, 1),
            "trend_strength_label": self.trend_strength_label,
            "momentum": self.momentum,
            "momentum_score": round(self.momentum_score, 1),
            "volatility": self.volatility,
            "volatility_percentile": round(self.volatility_percentile, 1),
            "atr": self.atr_value,
            "atr_percent": self.atr_percent,
            "liquidity": self.liquidity,
            "regime": self.regime,
            "condition": self.condition,
            "htf_bias": self.htf_bias,
            "ltf_bias": self.ltf_bias,
            "institutional_bias": self.institutional_bias,
            "evidence": self.evidence,
        }


def _percentile(values: list[float], target: float) -> float:
    """Share of `values` at or below `target`, as a percentage."""
    clean = [v for v in values if v is not None]
    if not clean:
        return 50.0
    return 100 * sum(1 for v in clean if v <= target) / len(clean)


def assess_momentum(series: Series) -> tuple[str, float, list[str]]:
    """Combine RSI level, RSI slope, and rate of change into one momentum read."""
    evidence: list[str] = []
    closes = series.closes
    if len(closes) < 30:
        return "neutral", 0.0, ["Not enough bars to judge momentum."]

    rsi_vals = rsi(closes)
    current_rsi = last_valid(rsi_vals)
    if current_rsi is None:
        return "neutral", 0.0, ["RSI has not warmed up yet."]

    # RSI distance from 50, scaled to -100..100.
    level_score = (current_rsi - 50) * 2

    # RSI slope over the last five bars catches momentum turning before level does.
    warm = [v for v in rsi_vals if v is not None]
    slope_score = 0.0
    if len(warm) >= 6:
        slope = warm[-1] - warm[-6]
        slope_score = max(-100, min(100, slope * 5))
        evidence.append(
            f"RSI is {current_rsi:.1f} and has moved {slope:+.1f} points over the last 5 bars."
        )
    else:
        evidence.append(f"RSI is {current_rsi:.1f}.")

    # Rate of change over 10 bars, normalised by ATR so it is comparable across assets.
    atr_now = last_valid(atr(series)) or 0.0
    roc_score = 0.0
    if len(closes) > 10 and atr_now > 0:
        move = closes[-1] - closes[-11]
        roc_score = max(-100, min(100, (move / atr_now) * 25))
        evidence.append(
            f"Price has moved {move:+.6g} over 10 bars — {move / atr_now:+.1f} ATR of displacement."
        )

    score = level_score * 0.4 + slope_score * 0.3 + roc_score * 0.3

    if score > 45:
        label = "strong bullish"
    elif score > 15:
        label = "bullish"
    elif score < -45:
        label = "strong bearish"
    elif score < -15:
        label = "bearish"
    else:
        label = "neutral"

    if current_rsi > 70:
        evidence.append("RSI above 70 — overbought; in a strong trend this signals strength, not a reversal.")
    elif current_rsi < 30:
        evidence.append("RSI below 30 — oversold; in a downtrend this signals strength, not a bottom.")

    return label, score, evidence


def assess_volatility(series: Series) -> tuple[str, float, float | None, float | None, list[str]]:
    """Rank current ATR against its own history rather than an absolute threshold."""
    evidence: list[str] = []
    atr_vals = atr(series, 14)
    current = last_valid(atr_vals)
    if current is None or not series.last:
        return "unknown", 50.0, None, None, ["ATR has not warmed up yet."]

    history = [v for v in atr_vals if v is not None]
    pct = _percentile(history, current)
    atr_percent = 100 * current / series.last.close if series.last.close else None

    if pct > 80:
        label = "high"
    elif pct > 55:
        label = "elevated"
    elif pct < 20:
        label = "low"
    else:
        label = "moderate"

    evidence.append(
        f"ATR(14) is {current:.6g}"
        + (f" ({atr_percent:.2f}% of price)" if atr_percent else "")
        + f", in the {pct:.0f}th percentile of its own {len(history)}-bar history — {label} volatility."
    )
    if label == "low":
        evidence.append("Compressed volatility often precedes expansion; size positions expecting a range break.")
    elif label == "high":
        evidence.append("Elevated volatility widens stops and reduces safe position size.")

    return label, pct, current, atr_percent, evidence


def assess_liquidity(series: Series) -> tuple[str, list[str]]:
    """Judge participation from volume consistency and wick behaviour.

    Genuinely thin markets show erratic volume and long wicks (price gapping
    through empty book), so those are the two things measured.
    """
    evidence: list[str] = []
    volumes = [v for v in series.volumes if v > 0]
    if len(volumes) < 20:
        return "unknown", ["This feed reports no usable volume for the symbol."]

    recent = volumes[-20:]
    mean = sum(recent) / len(recent)
    if mean <= 0:
        return "unknown", ["Volume is reported as zero."]

    variance = sum((v - mean) ** 2 for v in recent) / len(recent)
    cv = (variance ** 0.5) / mean  # coefficient of variation

    candles = series.candles[-20:]
    wick_share = sum(
        (c.upper_wick + c.lower_wick) / c.range for c in candles if c.range > 0
    ) / max(1, sum(1 for c in candles if c.range > 0))

    if cv < 0.6 and wick_share < 0.55:
        label = "high"
        evidence.append(
            f"Volume is consistent (variation {cv:.2f}) and bodies dominate wicks "
            f"({wick_share:.0%} wick share) — orderly, liquid trade."
        )
    elif cv > 1.2 or wick_share > 0.75:
        label = "low"
        evidence.append(
            f"Volume is erratic (variation {cv:.2f}) with {wick_share:.0%} of bar range in wicks — "
            f"thin book, expect slippage and false moves."
        )
    else:
        label = "moderate"
        evidence.append(
            f"Volume variation {cv:.2f} and {wick_share:.0%} wick share — normal participation."
        )
    return label, evidence


def assess_bias(series: Series) -> tuple[str, list[str]]:
    """Directional bias for one timeframe from EMA stack plus structure."""
    evidence: list[str] = []
    closes = series.closes
    if len(closes) < 60:
        return "unknown", ["Not enough history on this timeframe."]

    ema20 = last_valid(ema(closes, 20))
    ema50 = last_valid(ema(closes, 50))
    ema200 = last_valid(ema(closes, 200)) if len(closes) >= 200 else None
    price = closes[-1]

    score = 0
    if ema20 and ema50:
        if ema20 > ema50:
            score += 1
            evidence.append(f"EMA20 ({ema20:.6g}) is above EMA50 ({ema50:.6g}).")
        else:
            score -= 1
            evidence.append(f"EMA20 ({ema20:.6g}) is below EMA50 ({ema50:.6g}).")
    if ema200:
        if price > ema200:
            score += 1
            evidence.append(f"Price {price:.6g} is above the 200 EMA ({ema200:.6g}).")
        else:
            score -= 1
            evidence.append(f"Price {price:.6g} is below the 200 EMA ({ema200:.6g}).")

    structure = analyze_structure(series)
    if structure.trend is Trend.BULLISH:
        score += 1
        evidence.append(f"Swing structure is bullish ({structure.trend_strength:.0f}% of recent swings agree).")
    elif structure.trend is Trend.BEARISH:
        score -= 1
        evidence.append(f"Swing structure is bearish ({structure.trend_strength:.0f}% of recent swings agree).")

    if score >= 2:
        return "bullish", evidence
    if score <= -2:
        return "bearish", evidence
    return "neutral", evidence


def classify_regime(trend: str, adx_value: float | None, volatility: str) -> tuple[str, str]:
    """Map trend + ADX + volatility onto a regime and a one-word condition."""
    trending = adx_value is not None and adx_value >= 25

    if trending and trend == "bullish":
        return "trending bull", "trending"
    if trending and trend == "bearish":
        return "trending bear", "trending"
    if volatility in ("high", "elevated") and not trending:
        return "volatile range", "choppy"
    if volatility == "low":
        return "compressed range", "consolidating"
    return "balanced range", "ranging"


def build_context(
    series: Series,
    htf_series: Series | None = None,
    ltf_series: Series | None = None,
) -> MarketContext:
    """Produce the full market context — Step 1 of every analysis."""
    ctx = MarketContext()
    if len(series) < 30:
        ctx.evidence.append("Not enough bars to build market context.")
        return ctx

    structure = analyze_structure(series)
    ctx.trend = structure.trend.value
    ctx.trend_strength = structure.trend_strength

    adx_vals, plus_di, minus_di = adx(series)
    adx_now = last_valid(adx_vals)
    if adx_now is not None:
        if adx_now >= 40:
            ctx.trend_strength_label = "very strong"
        elif adx_now >= 25:
            ctx.trend_strength_label = "strong"
        elif adx_now >= 20:
            ctx.trend_strength_label = "developing"
        else:
            ctx.trend_strength_label = "weak"
        ctx.evidence.append(
            f"ADX(14) is {adx_now:.1f} — {ctx.trend_strength_label} directional movement "
            f"(+DI {last_valid(plus_di):.1f} vs -DI {last_valid(minus_di):.1f})."
            if last_valid(plus_di) is not None and last_valid(minus_di) is not None
            else f"ADX(14) is {adx_now:.1f} — {ctx.trend_strength_label} directional movement."
        )

    ctx.evidence.append(
        f"Swing structure is {structure.trend.value} with {structure.trend_strength:.0f}% of the "
        f"last six labelled swings agreeing; current phase reads as {structure.phase.value.replace('_', ' ')}."
    )

    ctx.momentum, ctx.momentum_score, momentum_evidence = assess_momentum(series)
    ctx.evidence.extend(momentum_evidence)

    ctx.volatility, ctx.volatility_percentile, ctx.atr_value, ctx.atr_percent, vol_evidence = (
        assess_volatility(series)
    )
    ctx.evidence.extend(vol_evidence)

    ctx.liquidity, liq_evidence = assess_liquidity(series)
    ctx.evidence.extend(liq_evidence)

    ctx.regime, ctx.condition = classify_regime(ctx.trend, adx_now, ctx.volatility)

    if htf_series is not None and len(htf_series) >= 60:
        ctx.htf_bias, htf_evidence = assess_bias(htf_series)
        ctx.evidence.append(
            f"Higher timeframe ({htf_series.timeframe.value}) bias is {ctx.htf_bias}: "
            + " ".join(htf_evidence)
        )
    if ltf_series is not None and len(ltf_series) >= 60:
        ctx.ltf_bias, ltf_evidence = assess_bias(ltf_series)
        ctx.evidence.append(
            f"Lower timeframe ({ltf_series.timeframe.value}) bias is {ctx.ltf_bias}: "
            + " ".join(ltf_evidence)
        )

    ctx.institutional_bias = _institutional_bias(ctx, structure)
    return ctx


def _institutional_bias(ctx: MarketContext, structure) -> str:
    """Net directional read weighting HTF above the working timeframe.

    Higher timeframe gets the largest weight because a setup that fights the
    daily rarely gets paid, however clean it looks on the 15-minute.
    """
    score = 0.0
    weights = {"bullish": 1.0, "bearish": -1.0, "neutral": 0.0, "unknown": 0.0, "ranging": 0.0}

    score += weights.get(ctx.htf_bias, 0.0) * 3
    score += weights.get(ctx.trend, 0.0) * 2
    score += weights.get(ctx.ltf_bias, 0.0) * 1
    if ctx.momentum.endswith("bullish"):
        score += 1.5 if ctx.momentum.startswith("strong") else 1.0
    elif ctx.momentum.endswith("bearish"):
        score -= 1.5 if ctx.momentum.startswith("strong") else 1.0

    if score >= 2.5:
        return "accumulating (net long)"
    if score <= -2.5:
        return "distributing (net short)"
    return "neutral / two-sided"
