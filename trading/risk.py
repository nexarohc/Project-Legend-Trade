"""Risk engine: position sizing, exposure, and the risks a chart cannot show.

Position size is derived from the stop distance, never guessed — the account
risk percentage and the invalidation level together determine the size, which
is the only arrangement that keeps risk constant across instruments and
volatility regimes.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from trading.indicators import atr, last_valid
from trading.models import AssetClass, Series


@dataclass
class RiskAssessment:
    volatility_risk: str = "unknown"
    liquidity_risk: str = "unknown"
    gap_risk: str = "unknown"
    news_risk: str = "unknown"
    correlation_risk: str = "unknown"
    max_drawdown_estimate: float | None = None
    position_size: float | None = None
    position_notional: float | None = None
    risk_amount: float | None = None
    portfolio_exposure_percent: float | None = None
    # Per-symbol Pearson correlations against the caller's other open
    # positions, when any were supplied. None when there was nothing to
    # compare against — that is a different fact from "measured and low".
    correlation_detail: list[dict] | None = None
    # High-importance economic releases landing within the lookahead window,
    # when a calendar was supplied. None (not []) means no calendar was
    # connected — the same "unmodelled vs. measured-and-clear" distinction
    # correlation_detail draws.
    news_event_detail: list[dict] | None = None
    warnings: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "volatility_risk": self.volatility_risk,
            "liquidity_risk": self.liquidity_risk,
            "gap_risk": self.gap_risk,
            "news_risk": self.news_risk,
            "news_event_detail": self.news_event_detail,
            "correlation_risk": self.correlation_risk,
            "max_drawdown_estimate": self.max_drawdown_estimate,
            "position_size": self.position_size,
            "position_notional": self.position_notional,
            "risk_amount": self.risk_amount,
            "portfolio_exposure_percent": self.portfolio_exposure_percent,
            "correlation_detail": self.correlation_detail,
            "warnings": self.warnings,
            "evidence": self.evidence,
        }


def position_size(
    account_balance: float,
    risk_percent: float,
    entry: float,
    stop: float,
    contract_size: float = 1.0,
) -> dict:
    """Size a position so that being stopped out costs exactly `risk_percent`.

    Returns the unit quantity, the notional it represents, and the cash at
    risk. A zero stop distance is rejected rather than silently producing an
    infinite position.
    """
    stop_distance = abs(entry - stop)
    if stop_distance <= 0:
        return {
            "valid": False,
            "reason": "Entry and stop are identical — no risk can be computed.",
        }
    if account_balance <= 0 or risk_percent <= 0:
        return {"valid": False, "reason": "Account balance and risk percent must both be positive."}

    risk_amount = account_balance * (risk_percent / 100)
    units = risk_amount / (stop_distance * contract_size)
    notional = units * entry * contract_size

    return {
        "valid": True,
        "units": units,
        "notional": notional,
        "risk_amount": risk_amount,
        "stop_distance": stop_distance,
        "stop_distance_percent": 100 * stop_distance / entry if entry else None,
        "leverage_required": notional / account_balance if account_balance else None,
        "explanation": (
            f"Risking {risk_percent:.2f}% of {account_balance:,.2f} = {risk_amount:,.2f}. "
            f"With a stop {stop_distance:.6g} away, that funds {units:.6g} units "
            f"({notional:,.2f} notional)."
        ),
    }


def assess_gap_risk(series: Series, asset_class: AssetClass) -> tuple[str, str]:
    """Measure how often this instrument gaps between bars.

    Crypto trades continuously so gap risk is structurally low; equities gap
    over every overnight session, which is a real exposure a chart-only view
    hides.
    """
    candles = series.candles
    if len(candles) < 30:
        return "unknown", "Not enough history to measure gap behaviour."

    atr_now = last_valid(atr(series)) or 0.0
    if atr_now <= 0:
        return "unknown", "ATR unavailable."

    gaps = 0
    largest = 0.0
    for i in range(1, len(candles)):
        gap = abs(candles[i].open - candles[i - 1].close)
        if gap > atr_now * 0.5:
            gaps += 1
            largest = max(largest, gap)

    rate = gaps / (len(candles) - 1)
    if asset_class is AssetClass.CRYPTO and rate < 0.02:
        return "low", (
            "This market trades continuously and has gapped on under 2% of bars — "
            "stops should fill close to their level."
        )
    if rate > 0.08:
        return "high", (
            f"{rate:.0%} of bars opened more than half an ATR away from the prior close "
            f"(largest gap {largest:.6g}). Stops can and will slip through their level."
        )
    if rate > 0.03:
        return "moderate", (
            f"{rate:.0%} of bars gapped meaningfully — expect occasional slippage on stops."
        )
    return "low", f"Only {rate:.0%} of bars gapped materially; stop fills should be reliable."


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    """Pearson correlation coefficient, stdlib only. None if either side has no variance."""
    n = len(xs)
    if n < 2:
        return None
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x <= 0 or var_y <= 0:
        return None
    return cov / (var_x * var_y) ** 0.5


def compute_position_correlation(
    series: Series, related: dict[str, Series], min_overlap: int = 30,
) -> dict:
    """Correlate this symbol's returns against a caller's other open positions.

    Pure and network-free by design — `related` is candles the caller already
    fetched, not a lookup this function performs. That keeps it testable
    without a market connection and keeps the trading/ layer's boundary
    (Series in, findings out) intact even though the *reason* to call it is
    inherently multi-symbol.

    Correlated on bar-to-bar percentage returns, not price levels: two
    instruments that both happened to drift upward over the window would
    otherwise show a high correlation that has nothing to do with how they
    actually move together. Pairs are matched by timestamp so instruments on
    different sessions or with gaps still compare the bars that genuinely
    coincide, and a pair with too little overlap is reported as such rather
    than given a number that isn't meaningful.
    """
    if not related:
        return {"available": False, "reason": "No other open positions to compare against."}

    def returns_by_time(s: Series) -> dict[int, float]:
        out: dict[int, float] = {}
        candles = s.candles
        for i in range(1, len(candles)):
            prev = candles[i - 1].close
            if prev:
                out[candles[i].timestamp] = (candles[i].close - prev) / prev
        return out

    base_returns = returns_by_time(series)
    pairs: list[dict] = []
    for symbol, other in related.items():
        other_returns = returns_by_time(other)
        shared = sorted(set(base_returns) & set(other_returns))
        if len(shared) < min_overlap:
            pairs.append({
                "symbol": symbol,
                "correlation": None,
                "sample_size": len(shared),
                "evidence": (
                    f"Only {len(shared)} overlapping bars with {symbol} — "
                    f"need at least {min_overlap} to measure correlation."
                ),
            })
            continue

        r = _pearson([base_returns[t] for t in shared], [other_returns[t] for t in shared])
        if r is None:
            pairs.append({
                "symbol": symbol,
                "correlation": None,
                "sample_size": len(shared),
                "evidence": f"{symbol} showed no return variance over the overlap — correlation undefined.",
            })
            continue

        strength = "strongly" if abs(r) > 0.7 else "moderately" if abs(r) > 0.4 else "weakly"
        direction = "with" if r > 0 else "against"
        pairs.append({
            "symbol": symbol,
            "correlation": round(r, 3),
            "sample_size": len(shared),
            "evidence": (
                f"{series.symbol} moved {strength} {direction} {symbol} "
                f"(r={r:+.2f} over {len(shared)} overlapping bars) — "
                f"{'treat these as one bet, not two' if abs(r) > 0.7 else 'a real but partial overlap in risk'}"
                if abs(r) > 0.4 else
                f"{series.symbol} and {symbol} moved largely independently "
                f"(r={r:+.2f} over {len(shared)} overlapping bars)."
            ),
        })

    measured = [p for p in pairs if p["correlation"] is not None]
    max_abs = max((abs(p["correlation"]) for p in measured), default=None)
    if max_abs is None:
        level = "unknown"
    elif max_abs > 0.7:
        level = "high"
    elif max_abs > 0.4:
        level = "moderate"
    else:
        level = "low"

    return {"available": True, "level": level, "pairs": pairs}


def assess_risk(
    series: Series,
    context,                       # regime.MarketContext
    setup=None,                    # setups.TradeSetup | None
    account_balance: float = 10_000.0,
    risk_percent: float = 1.0,
    open_exposure_percent: float = 0.0,
    related_series: dict[str, Series] | None = None,
    upcoming_high_impact_events: list | None = None,  # econcalendar.EconomicEvent
) -> RiskAssessment:
    """Full risk review for the current market and (optionally) a proposed setup."""
    assessment = RiskAssessment()

    # Volatility risk from the context module's percentile ranking.
    if context.volatility in ("high",):
        assessment.volatility_risk = "high"
        assessment.warnings.append(
            "Volatility is in the top quintile of its own history — a normal stop distance "
            "here is much wider in cash terms than usual, so position size must shrink."
        )
    elif context.volatility == "elevated":
        assessment.volatility_risk = "moderate"
    elif context.volatility == "low":
        assessment.volatility_risk = "low"
        assessment.warnings.append(
            "Volatility is compressed. Stops placed at normal ATR multiples are unusually tight "
            "and an expansion move can blow through them."
        )
    else:
        assessment.volatility_risk = "moderate"
    assessment.evidence.append(
        f"ATR sits at the {context.volatility_percentile:.0f}th percentile of its history "
        f"({context.atr_percent:.2f}% of price)."
        if context.atr_percent else
        f"ATR sits at the {context.volatility_percentile:.0f}th percentile of its history."
    )

    # Liquidity risk.
    assessment.liquidity_risk = {
        "high": "low", "moderate": "moderate", "low": "high", "unknown": "unknown",
    }[context.liquidity]
    if assessment.liquidity_risk == "high":
        assessment.warnings.append(
            "Thin participation detected — slippage on entry and exit will exceed the "
            "backtested assumption."
        )

    # Gap risk.
    assessment.gap_risk, gap_note = assess_gap_risk(series, series.asset_class)
    assessment.evidence.append(gap_note)

    # News risk: honestly unmodelled with no calendar connected, otherwise
    # driven by whether a high-importance release falls inside the lookahead
    # window the caller already filtered (see backend/app/routers/analysis.py).
    if upcoming_high_impact_events is None:
        assessment.news_risk = "unmodelled"
        assessment.warnings.append(
            "No economic calendar is connected, so scheduled-event risk is not modelled. "
            "Check for high-impact releases before taking any setup."
        )
    elif upcoming_high_impact_events:
        assessment.news_risk = "elevated"
        assessment.news_event_detail = [e.to_dict() for e in upcoming_high_impact_events]
        names = ", ".join(f"{e.name} ({e.date})" for e in upcoming_high_impact_events[:3])
        assessment.warnings.append(
            f"High-impact release(s) ahead: {names}. Estimated importance, not an official "
            f"rating — see the calendar for the full list."
        )
    else:
        assessment.news_risk = "low"
        assessment.evidence.append(
            "No high-importance economic releases found in the lookahead window."
        )

    # Correlation risk: measured against the caller's other open positions
    # when supplied, and honestly labelled unmodelled when there is nothing
    # to compare against — single-symbol analysis genuinely cannot see the
    # book on its own.
    if related_series:
        correlation = compute_position_correlation(series, related_series)
        assessment.correlation_detail = correlation.get("pairs")
        assessment.correlation_risk = correlation.get("level", "unknown")
        for pair in correlation.get("pairs", []):
            assessment.evidence.append(pair["evidence"])
        if assessment.correlation_risk == "high":
            assessment.warnings.append(
                "This position is strongly correlated with an existing open position — "
                "sizing both independently understates the combined risk."
            )
    else:
        assessment.correlation_risk = "unmodelled"
        if series.asset_class is AssetClass.CRYPTO:
            assessment.evidence.append(
                "Crypto majors correlate heavily with BTC; treat several alt positions as one bet "
                "unless you have measured otherwise."
            )
        elif series.asset_class in (AssetClass.STOCK, AssetClass.ETF):
            assessment.evidence.append(
                "Equity positions carry shared index and sector beta; concurrent longs are rarely "
                "independent risks."
            )

    # Estimated drawdown from recent behaviour.
    assessment.max_drawdown_estimate = _recent_drawdown(series)
    if assessment.max_drawdown_estimate is not None:
        assessment.evidence.append(
            f"Largest peak-to-trough decline over the visible history is "
            f"{assessment.max_drawdown_estimate:.1f}% — a realistic scale for adverse excursion."
        )

    # Sizing, when a setup is supplied.
    if setup is not None and setup.valid:
        sizing = position_size(account_balance, risk_percent, setup.entry, setup.stop_loss)
        if sizing.get("valid"):
            assessment.position_size = sizing["units"]
            assessment.position_notional = sizing["notional"]
            assessment.risk_amount = sizing["risk_amount"]
            assessment.evidence.append(sizing["explanation"])

            leverage = sizing.get("leverage_required") or 0
            if leverage > 3:
                assessment.warnings.append(
                    f"This size needs {leverage:.1f}x leverage. The stop is tight relative to the "
                    f"account; either widen the stop or accept a smaller position."
                )
            assessment.portfolio_exposure_percent = open_exposure_percent + risk_percent
            if assessment.portfolio_exposure_percent > 6:
                assessment.warnings.append(
                    f"Total portfolio risk would reach {assessment.portfolio_exposure_percent:.1f}%. "
                    f"Beyond roughly 6% aggregate risk, a normal losing streak becomes an account event."
                )
        else:
            assessment.warnings.append(sizing.get("reason", "Position size could not be computed."))

    return assessment


def _recent_drawdown(series: Series) -> float | None:
    """Largest peak-to-trough decline in the visible history, as a percentage."""
    if len(series) < 20:
        return None
    peak = series.candles[0].high
    worst = 0.0
    for c in series.candles:
        peak = max(peak, c.high)
        if peak > 0:
            worst = max(worst, (peak - c.low) / peak)
    return worst * 100
