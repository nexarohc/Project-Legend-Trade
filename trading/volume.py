"""Volume analysis: profile, pressure, divergence.

A note on delta. True buy/sell delta needs tick or order-book data that free
OHLCV feeds do not carry. Rather than invent it, this module derives a
*proxy* from where each bar closes within its own range, and labels it as such
everywhere it surfaces. Presenting an estimate as if it were measured order
flow would be exactly the kind of unexplained number this platform is meant to
avoid.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from trading.indicators import sma
from trading.models import Series


@dataclass
class VolumeNode:
    price: float
    volume: float
    percent_of_total: float

    def to_dict(self) -> dict:
        return {
            "price": self.price,
            "volume": self.volume,
            "percent": round(self.percent_of_total, 2),
        }


@dataclass
class VolumeProfile:
    """Volume distributed across price buckets over a window of bars."""

    poc: float          # Point of Control — the highest-volume price
    vah: float          # Value Area High
    val: float          # Value Area Low
    nodes: list[VolumeNode] = field(default_factory=list)
    high_volume_nodes: list[VolumeNode] = field(default_factory=list)
    low_volume_nodes: list[VolumeNode] = field(default_factory=list)
    total_volume: float = 0.0
    bars: int = 0

    def to_dict(self) -> dict:
        return {
            "poc": self.poc,
            "vah": self.vah,
            "val": self.val,
            "nodes": [n.to_dict() for n in self.nodes],
            "high_volume_nodes": [n.to_dict() for n in self.high_volume_nodes],
            "low_volume_nodes": [n.to_dict() for n in self.low_volume_nodes],
            "total_volume": self.total_volume,
            "bars": self.bars,
        }


def build_profile(series: Series, bins: int = 48, value_area: float = 0.70) -> VolumeProfile | None:
    """Distribute each bar's volume across the price buckets its range covers.

    Spreading volume across a bar's whole range (rather than dumping it all at
    the close) is what makes the profile resemble a real traded-volume
    distribution from OHLCV data.
    """
    candles = series.candles
    if not candles:
        return None

    top = max(c.high for c in candles)
    bottom = min(c.low for c in candles)
    if top <= bottom:
        return None

    step = (top - bottom) / bins
    buckets = [0.0] * bins

    for c in candles:
        lo_bin = min(bins - 1, max(0, int((c.low - bottom) / step)))
        hi_bin = min(bins - 1, max(0, int((c.high - bottom) / step)))
        touched = hi_bin - lo_bin + 1
        share = c.volume / touched
        for b in range(lo_bin, hi_bin + 1):
            buckets[b] += share

    total = sum(buckets)
    if total <= 0:
        return None

    nodes = [
        VolumeNode(price=bottom + (i + 0.5) * step, volume=v, percent_of_total=100 * v / total)
        for i, v in enumerate(buckets)
    ]

    poc_index = max(range(bins), key=lambda i: buckets[i])
    poc_price = nodes[poc_index].price

    # Grow the value area outward from the POC until it holds `value_area` of volume.
    covered = buckets[poc_index]
    low_i = high_i = poc_index
    target = total * value_area
    while covered < target and (low_i > 0 or high_i < bins - 1):
        below = buckets[low_i - 1] if low_i > 0 else -1
        above = buckets[high_i + 1] if high_i < bins - 1 else -1
        if above >= below:
            high_i += 1
            covered += buckets[high_i]
        else:
            low_i -= 1
            covered += buckets[low_i]

    average = total / bins
    return VolumeProfile(
        poc=poc_price,
        vah=nodes[high_i].price,
        val=nodes[low_i].price,
        nodes=nodes,
        high_volume_nodes=[n for n in nodes if n.volume > average * 1.5],
        low_volume_nodes=[n for n in nodes if n.volume < average * 0.4],
        total_volume=total,
        bars=len(candles),
    )


def delta_proxy(series: Series) -> list[float]:
    """Per-bar signed volume estimate from close position within the bar's range.

    A bar closing on its high implies buyers absorbed the whole range; closing
    mid-range implies balance. This is an approximation of order-flow delta,
    not a measurement of it.
    """
    out = []
    for c in series.candles:
        if c.range <= 0:
            out.append(0.0)
            continue
        # Maps close position to -1 (closed on low) .. +1 (closed on high).
        position = (c.close - c.low) / c.range
        out.append(c.volume * (2 * position - 1))
    return out


def analyze_volume(series: Series, profile_bars: int = 200) -> dict:
    """Full volume pass: trend, spikes, pressure, divergence, and the profile."""
    candles = series.candles
    n = len(candles)
    evidence: list[str] = []

    if n < 20:
        return {"available": False, "reason": "Need at least 20 bars for volume analysis."}

    volumes = series.volumes
    if sum(volumes) <= 0:
        # Some FX feeds ship zero volume; say so instead of reporting noise.
        return {
            "available": False,
            "reason": "This data feed reports no volume for this symbol (common on spot FX).",
        }

    vol_ma = sma(volumes, 20)
    latest_vol = volumes[-1]
    baseline = vol_ma[-1] or (sum(volumes[-20:]) / 20)
    ratio = latest_vol / baseline if baseline else 1.0

    # Volume trend: recent 10 bars vs the 40 before them.
    recent = sum(volumes[-10:]) / 10
    older = sum(volumes[-50:-10]) / 40 if n >= 50 else recent
    trend_ratio = recent / older if older else 1.0
    if trend_ratio > 1.2:
        vol_trend = "rising"
        evidence.append(f"10-bar average volume is {trend_ratio:.2f}x the prior 40-bar average.")
    elif trend_ratio < 0.8:
        vol_trend = "falling"
        evidence.append(f"10-bar average volume is only {trend_ratio:.2f}x the prior 40-bar average.")
    else:
        vol_trend = "steady"
        evidence.append(f"Volume is stable at {trend_ratio:.2f}x its recent average.")

    spike = ratio >= 2.0
    if spike:
        evidence.append(f"Latest bar traded {ratio:.1f}x the 20-bar average volume — a genuine spike.")

    # Buying vs selling pressure over the last 20 bars, via the delta proxy.
    deltas = delta_proxy(series)
    window_delta = deltas[-20:]
    buy_pressure = sum(d for d in window_delta if d > 0)
    sell_pressure = -sum(d for d in window_delta if d < 0)
    pressure_total = buy_pressure + sell_pressure
    buy_share = (buy_pressure / pressure_total * 100) if pressure_total else 50.0

    if buy_share > 60:
        pressure = "buyers in control"
    elif buy_share < 40:
        pressure = "sellers in control"
    else:
        pressure = "balanced"
    evidence.append(
        f"Estimated delta over the last 20 bars is {buy_share:.0f}% buy-side ({pressure}); "
        f"derived from close-within-range, not tick data."
    )

    # Divergence: price makes a new extreme, volume/OBV does not confirm.
    divergence = _volume_divergence(series)
    if divergence["detected"]:
        evidence.append(divergence["evidence"])

    # Confirmation: is the latest directional move backed by volume?
    last = candles[-1]
    confirmed = ratio > 1.2 and last.body > 0.5 * last.range
    if confirmed:
        evidence.append(
            f"The latest {'up' if last.bullish else 'down'} bar has a strong body and "
            f"{ratio:.1f}x volume — the move is participation-backed."
        )
    elif ratio < 0.7 and last.body > 0.6 * last.range:
        evidence.append(
            f"The latest directional bar came on only {ratio:.1f}x volume — thin participation, "
            f"treat the move with caution."
        )

    profile = build_profile(series.tail(min(profile_bars, n)))
    profile_dict = profile.to_dict() if profile else None
    if profile:
        price = last.close
        location = (
            "above the value area (premium)" if price > profile.vah
            else "below the value area (discount)" if price < profile.val
            else "inside the value area (fair value)"
        )
        evidence.append(
            f"Volume profile over {profile.bars} bars: POC {profile.poc:.6g}, "
            f"value area {profile.val:.6g}-{profile.vah:.6g}. Price is {location}."
        )

    return {
        "available": True,
        "volume_trend": vol_trend,
        "relative_volume": round(ratio, 2),
        "spike": spike,
        "pressure": pressure,
        "buy_share_percent": round(buy_share, 1),
        "confirms_move": confirmed,
        "divergence": divergence,
        "profile": profile_dict,
        "poc": profile.poc if profile else None,
        "vah": profile.vah if profile else None,
        "val": profile.val if profile else None,
        "evidence": evidence,
    }


def _volume_divergence(series: Series, window: int = 30) -> dict:
    """Price/volume divergence over the last `window` bars.

    Bearish: a higher price high on lower volume — the new high is unconvinced.
    Bullish: a lower price low on lower volume — sellers are exhausted.
    """
    candles = series.candles
    if len(candles) < window * 2:
        return {"detected": False, "type": None, "evidence": ""}

    recent = candles[-window:]
    prior = candles[-window * 2 : -window]

    recent_high = max(c.high for c in recent)
    prior_high = max(c.high for c in prior)
    recent_low = min(c.low for c in recent)
    prior_low = min(c.low for c in prior)

    recent_vol = sum(c.volume for c in recent) / window
    prior_vol = sum(c.volume for c in prior) / window
    if prior_vol <= 0:
        return {"detected": False, "type": None, "evidence": ""}

    vol_change = recent_vol / prior_vol

    if recent_high > prior_high and vol_change < 0.8:
        return {
            "detected": True,
            "type": "bearish",
            "evidence": (
                f"Price made a higher high ({prior_high:.6g} -> {recent_high:.6g}) while average "
                f"volume fell to {vol_change:.2f}x — the new high is not being confirmed by participation."
            ),
        }
    if recent_low < prior_low and vol_change < 0.8:
        return {
            "detected": True,
            "type": "bullish",
            "evidence": (
                f"Price made a lower low ({prior_low:.6g} -> {recent_low:.6g}) while average "
                f"volume fell to {vol_change:.2f}x — selling pressure is drying up."
            ),
        }
    return {"detected": False, "type": None, "evidence": ""}
