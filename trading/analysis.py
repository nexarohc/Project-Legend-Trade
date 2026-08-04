"""The analysis pipeline — context first, prediction last.

Order matters here and is enforced by the code, not by convention. Market
context, structure, price action, volume, indicators, SMC and levels are all
computed and explained *before* the probability engine is allowed to run, and
the trade setup is derived from that probability output rather than the other
way round. That ordering is the platform's core rule: describe, then explain,
then quantify, then propose.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from trading import indicators as ind
from trading import levels as levels_mod
from trading import patterns, probability, regime, risk, setups, smc, structure, volume, wyckoff
from trading.models import Series, Timeframe

logger = logging.getLogger("legend.trading.analysis")


@dataclass
class AnalysisResult:
    """The complete 20-section analysis for one symbol/timeframe."""

    symbol: str
    timeframe: str
    provider: str
    generated_at: int
    price: float
    bars_analyzed: int

    executive_summary: str = ""
    market_context: dict = field(default_factory=dict)
    trend_analysis: dict = field(default_factory=dict)
    market_structure: dict = field(default_factory=dict)
    smart_money: dict = field(default_factory=dict)
    volume_analysis: dict = field(default_factory=dict)
    indicator_analysis: dict = field(default_factory=dict)
    key_levels: dict = field(default_factory=dict)
    price_action: dict = field(default_factory=dict)
    probability_analysis: dict = field(default_factory=dict)
    trade_setup: dict = field(default_factory=dict)
    risk_review: dict = field(default_factory=dict)
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    improvements: list[str] = field(default_factory=list)
    verdict: dict = field(default_factory=dict)
    chart_data: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "provider": self.provider,
            "generated_at": self.generated_at,
            "price": self.price,
            "bars_analyzed": self.bars_analyzed,
            "executive_summary": self.executive_summary,
            "market_context": self.market_context,
            "trend_analysis": self.trend_analysis,
            "market_structure": self.market_structure,
            "smart_money": self.smart_money,
            "volume_analysis": self.volume_analysis,
            "indicator_analysis": self.indicator_analysis,
            "key_levels": self.key_levels,
            "price_action": self.price_action,
            "probability_analysis": self.probability_analysis,
            "trade_setup": self.trade_setup,
            "risk_review": self.risk_review,
            "strengths": self.strengths,
            "weaknesses": self.weaknesses,
            "improvements": self.improvements,
            "verdict": self.verdict,
            "chart_data": self.chart_data,
            "warnings": self.warnings,
        }


def analyze(
    series: Series,
    htf_series: Series | None = None,
    ltf_series: Series | None = None,
    account_balance: float = 10_000.0,
    risk_percent: float = 1.0,
    include_chart_data: bool = True,
    horizon: int = 10,
    related_series: dict[str, Series] | None = None,
    upcoming_high_impact_events: list | None = None,
) -> AnalysisResult:
    """Run the full pipeline over a series.

    `htf_series` / `ltf_series` are optional but strongly recommended: without
    them the higher- and lower-timeframe bias sections report "unknown" rather
    than guessing from a single chart.

    `related_series` is candles for the caller's other open positions, keyed
    by symbol, when they have any — enables the correlation-risk measurement
    in `risk.assess_risk`. Optional; without it correlation is honestly
    reported as unmodelled rather than guessed at.

    `upcoming_high_impact_events` is the caller's pre-filtered list of
    high-importance `econcalendar.EconomicEvent`s within whatever lookahead
    window it chose. Optional; without it, news risk is honestly reported as
    unmodelled. `analyze_symbol` below fetches this itself via
    `econcalendar.calendar_service`, the same way it fetches `related_series`
    via `market_service` — this pure `analyze()` function never reaches out
    for either.
    """
    started = time.time()
    last = series.last
    if last is None:
        raise ValueError("Cannot analyze an empty series")

    result = AnalysisResult(
        symbol=series.symbol,
        timeframe=series.timeframe.value,
        provider=series.provider,
        generated_at=int(time.time()),
        price=last.close,
        bars_analyzed=len(series),
    )

    # Warning thresholds reflect what the engines actually consume: the 200 EMA
    # needs 200 bars, and the probability engine's empirical base rates need 120
    # before it will measure anything at all. Staying silent between those
    # points would let a user trade a report whose headline numbers are
    # structurally unavailable.
    if len(series) < 60:
        result.warnings.append(
            f"Only {len(series)} bars available. Structure, probability and setup output "
            f"degrade sharply below 60 bars and should not be traded from."
        )
    elif len(series) < 120:
        result.warnings.append(
            f"Only {len(series)} bars available. The probability engine needs 120 bars to "
            f"measure historical base rates, so the odds below rest on weighted evidence alone."
        )
    elif len(series) < 200:
        result.warnings.append(
            f"Only {len(series)} bars available. The 200-period EMA and long-horizon levels "
            f"cannot be computed, so regime and trend readings are less reliable than usual."
        )

    # --- Step 1: market context (never skipped, always first) ---------------
    context = regime.build_context(series, htf_series, ltf_series)
    result.market_context = context.to_dict()

    # --- Step 2: market structure -------------------------------------------
    struct = structure.analyze_structure(series)
    result.market_structure = struct.to_dict()

    # Wyckoff event labelling folds into the structure section rather than
    # standing alone: it is a refinement of the same range/phase read, using
    # the same swings and sweeps, not an independent analysis.
    wyckoff_report = wyckoff.analyze_wyckoff(series, struct)
    result.market_structure["wyckoff_events"] = wyckoff_report.to_dict()

    result.trend_analysis = {
        "trend": context.trend,
        "strength": round(context.trend_strength, 1),
        "strength_label": context.trend_strength_label,
        "internal": struct.internal_trend.value,
        "external": struct.external_trend.value,
        "phase": struct.phase.value,
        "htf_bias": context.htf_bias,
        "ltf_bias": context.ltf_bias,
        "regime": context.regime,
        "condition": context.condition,
        "evidence": struct.notes,
    }

    # --- Step 3: price action ------------------------------------------------
    result.price_action = patterns.analyze_price_action(series)

    # --- Step 4: volume ------------------------------------------------------
    vol = volume.analyze_volume(series)
    result.volume_analysis = vol

    # --- Step 5: indicators --------------------------------------------------
    computed = ind.compute_all(series)
    result.indicator_analysis = _explain_indicators(series, computed)

    # --- Step 6: smart money concepts ----------------------------------------
    smc_report = smc.analyze_smc(series, struct)
    result.smart_money = smc_report.to_dict()

    # --- Step 7: key levels ---------------------------------------------------
    level_report = levels_mod.analyze_levels(series, struct.swings, vol if vol.get("available") else None)
    result.key_levels = level_report.to_dict()

    # --- Step 9: probabilities (only now that everything is described) -------
    probs = probability.compute_probabilities(
        series, context, struct, smc_report, vol, result.price_action, level_report, horizon
    )
    result.probability_analysis = probs.to_dict()

    # --- Step 10: trade setup -------------------------------------------------
    setup = setups.build_setup(series, context, struct, smc_report, level_report, probs, vol)
    result.trade_setup = setup.to_dict()

    # --- Step 11: risk --------------------------------------------------------
    risk_report = risk.assess_risk(
        series, context, setup if setup.valid else None, account_balance, risk_percent,
        related_series=related_series,
        upcoming_high_impact_events=upcoming_high_impact_events,
    )
    result.risk_review = risk_report.to_dict()

    # --- Step 8 + 16-19: summary, strengths, weaknesses, verdict --------------
    result.strengths, result.weaknesses, result.improvements = _grade(
        context, struct, vol, probs, setup, result.price_action
    )
    result.executive_summary = _summarize(series, context, struct, probs, setup)
    result.verdict = _verdict(probs, setup, context, result.weaknesses)

    if include_chart_data:
        result.chart_data = _chart_payload(series, computed, struct, smc_report, level_report)

    logger.info(
        "analysis complete: %s %s in %.0fms",
        series.symbol, series.timeframe.value, (time.time() - started) * 1000,
    )
    return result


def _explain_indicators(series: Series, computed: dict) -> dict:
    """Turn raw indicator values into per-indicator readings with explanations.

    Every indicator gets a sentence saying what its value means *for this
    chart* — a bare "RSI 63" tells a user nothing they couldn't read off an axis.
    """
    latest = computed["latest"]
    price = series.last.close
    readings: list[dict] = []

    def add(name: str, value, reading: str, explanation: str, bias: str = "neutral") -> None:
        readings.append({
            "name": name,
            "value": round(value, 6) if isinstance(value, (int, float)) else value,
            "reading": reading,
            "explanation": explanation,
            "bias": bias,
        })

    ema20, ema50, ema200 = latest["ema20"], latest["ema50"], latest["ema200"]
    if ema20 and ema50:
        bullish = ema20 > ema50
        add("EMA 20/50", ema20,
            "bullish stack" if bullish else "bearish stack",
            f"EMA20 at {ema20:.6g} is {'above' if bullish else 'below'} EMA50 at {ema50:.6g}. "
            f"The short-term average leading the medium-term one is the basic definition of "
            f"{'an up' if bullish else 'a down'}trend on this timeframe.",
            "bullish" if bullish else "bearish")
    if ema200:
        above = price > ema200
        add("EMA 200", ema200,
            "price above" if above else "price below",
            f"Price {price:.6g} sits {'above' if above else 'below'} the 200 EMA at {ema200:.6g} — "
            f"the conventional dividing line between a bull and bear regime.",
            "bullish" if above else "bearish")

    vwap_now = latest["vwap"]
    if vwap_now:
        above = price > vwap_now
        add("VWAP", vwap_now,
            "trading above VWAP" if above else "trading below VWAP",
            f"Session VWAP is {vwap_now:.6g}. Price {'above' if above else 'below'} it means the "
            f"average participant in this session is {'in profit on longs' if above else 'underwater on longs'}, "
            f"which shapes where they defend.",
            "bullish" if above else "bearish")

    rsi_now = latest["rsi"]
    if rsi_now:
        if rsi_now > 70:
            reading, bias = "overbought", "bearish"
            note = "Above 70. In a strong trend this reflects strength rather than an immediate top."
        elif rsi_now < 30:
            reading, bias = "oversold", "bullish"
            note = "Below 30. In a strong downtrend this reflects weakness rather than a bottom."
        elif rsi_now > 55:
            reading, bias = "bullish", "bullish"
            note = "Above the 55 midline, momentum favours buyers."
        elif rsi_now < 45:
            reading, bias = "bearish", "bearish"
            note = "Below the 45 midline, momentum favours sellers."
        else:
            reading, bias, note = "neutral", "neutral", "Sitting on the midline — no momentum edge."
        add("RSI (14)", rsi_now, reading, f"RSI is {rsi_now:.1f}. {note}", bias)

    macd_line, macd_signal, macd_hist = latest["macd"], latest["macd_signal"], latest["macd_hist"]
    if macd_line is not None and macd_signal is not None:
        bullish = macd_line > macd_signal
        add("MACD", macd_line,
            "bullish cross" if bullish else "bearish cross",
            f"MACD line {macd_line:.6g} is {'above' if bullish else 'below'} its signal "
            f"{macd_signal:.6g} (histogram {macd_hist:.6g}). "
            f"{'Momentum is building' if bullish else 'Momentum is fading'} on this timeframe.",
            "bullish" if bullish else "bearish")

    adx_now, plus_di, minus_di = latest["adx"], latest["plus_di"], latest["minus_di"]
    if adx_now:
        if adx_now >= 25:
            reading = "trending"
            note = "Above 25 confirms a real directional move rather than chop."
        elif adx_now >= 20:
            reading = "developing"
            note = "Between 20 and 25 the trend is forming but not confirmed."
        else:
            reading = "ranging"
            note = "Below 20 means there is no directional edge; trend-following systems bleed here."
        directional = ""
        if plus_di is not None and minus_di is not None:
            directional = f" +DI {plus_di:.1f} vs -DI {minus_di:.1f} puts {'buyers' if plus_di > minus_di else 'sellers'} in control."
        add("ADX (14)", adx_now, reading, f"ADX is {adx_now:.1f}. {note}{directional}",
            "bullish" if (plus_di or 0) > (minus_di or 0) and adx_now >= 20 else
            "bearish" if adx_now >= 20 else "neutral")

    atr_now = latest["atr"]
    if atr_now:
        add("ATR (14)", atr_now, "volatility unit",
            f"Average true range is {atr_now:.6g} ({100 * atr_now / price:.2f}% of price) — "
            f"the size of a normal bar, and the unit stops and targets should be measured in.")

    bb_u, bb_m, bb_l = latest["bb_upper"], latest["bb_middle"], latest["bb_lower"]
    if bb_u and bb_l and bb_m:
        width = (bb_u - bb_l) / bb_m if bb_m else 0
        if price > bb_u:
            reading, bias = "above upper band", "bullish"
            note = "Closing outside the upper band is an expansion signal, not automatically an overshoot."
        elif price < bb_l:
            reading, bias = "below lower band", "bearish"
            note = "Closing outside the lower band signals downside expansion."
        else:
            position = (price - bb_l) / (bb_u - bb_l) if bb_u != bb_l else 0.5
            reading, bias = f"{position:.0%} through the bands", "neutral"
            note = "Price is inside the bands — normal distribution of the last 20 closes."
        add("Bollinger Bands", bb_m, reading,
            f"Bands span {bb_l:.6g}-{bb_u:.6g}, width {width:.2%} of the basis. {note}", bias)

    st_line, st_dir = latest["supertrend"], latest["supertrend_dir"]
    if st_line and st_dir:
        up = st_dir > 0
        add("SuperTrend", st_line, "bullish" if up else "bearish",
            f"SuperTrend flipped {'long' if up else 'short'} and now tracks at {st_line:.6g}, "
            f"which doubles as a trailing invalidation level.",
            "bullish" if up else "bearish")

    for key, label, hi, lo in (
        ("cci", "CCI (20)", 100, -100),
        ("mfi", "MFI (14)", 80, 20),
        ("stoch_rsi_k", "Stoch RSI %K", 80, 20),
    ):
        value = latest[key]
        if value is None:
            continue
        if value > hi:
            reading, bias = "overbought", "bearish"
        elif value < lo:
            reading, bias = "oversold", "bullish"
        else:
            reading, bias = "neutral", "neutral"
        add(label, value, reading,
            f"{label} reads {value:.1f}; {reading} against its {lo}/{hi} thresholds.", bias)

    obv_series = computed["arrays"]["obv"]
    if len(obv_series) > 20:
        recent_change = obv_series[-1] - obv_series[-20]
        price_change = price - series.closes[-20]
        agreeing = (recent_change > 0) == (price_change > 0)
        add("OBV", obv_series[-1], "confirming" if agreeing else "diverging",
            f"On-balance volume moved {recent_change:+,.0f} over 20 bars while price moved "
            f"{price_change:+.6g}. Volume flow {'confirms' if agreeing else 'contradicts'} the price move.",
            "neutral" if agreeing else "bearish" if price_change > 0 else "bullish")

    tenkan, kijun = latest["ichimoku_tenkan"], latest["ichimoku_kijun"]
    span_a, span_b = latest["ichimoku_senkou_a"], latest["ichimoku_senkou_b"]
    if tenkan and kijun and span_a and span_b:
        cloud_top, cloud_bottom = max(span_a, span_b), min(span_a, span_b)
        if price > cloud_top:
            reading, bias = "above the cloud", "bullish"
        elif price < cloud_bottom:
            reading, bias = "below the cloud", "bearish"
        else:
            reading, bias = "inside the cloud", "neutral"
        add("Ichimoku", kijun, reading,
            f"Tenkan {tenkan:.6g}, Kijun {kijun:.6g}, cloud {cloud_bottom:.6g}-{cloud_top:.6g}. "
            f"Price {reading} — {'trend intact' if bias != 'neutral' else 'unresolved, the cloud is a no-trade zone for Ichimoku systems'}.",
            bias)

    bullish_count = sum(1 for r in readings if r["bias"] == "bullish")
    bearish_count = sum(1 for r in readings if r["bias"] == "bearish")
    total = bullish_count + bearish_count

    return {
        "readings": readings,
        "bullish_count": bullish_count,
        "bearish_count": bearish_count,
        "consensus": (
            "bullish" if bullish_count > bearish_count * 1.5
            else "bearish" if bearish_count > bullish_count * 1.5
            else "mixed"
        ),
        "agreement_percent": round(100 * max(bullish_count, bearish_count) / total, 1) if total else 0.0,
        "summary": (
            f"{bullish_count} of {len(readings)} indicators read bullish and {bearish_count} bearish. "
            + ("Broad agreement raises the weight of the directional read."
               if total and max(bullish_count, bearish_count) / total > 0.7
               else "Indicators disagree, which is itself evidence the market is unresolved.")
        ),
    }


def _grade(context, struct, vol, probs, setup, price_action) -> tuple[list[str], list[str], list[str]]:
    """Honest strengths/weaknesses of the current read, plus what would improve it."""
    strengths: list[str] = []
    weaknesses: list[str] = []
    improvements: list[str] = []

    if context.trend_strength_label in ("strong", "very strong"):
        strengths.append(
            f"Directional movement is {context.trend_strength_label} — trend-following logic has an edge here."
        )
    else:
        weaknesses.append(
            f"Directional movement is {context.trend_strength_label}; trend setups have a poor "
            f"expectancy in this regime."
        )

    if context.htf_bias in ("bullish", "bearish") and context.htf_bias == context.trend:
        strengths.append("Higher timeframe and working timeframe agree — the strongest confluence available.")
    elif context.htf_bias in ("bullish", "bearish"):
        weaknesses.append(
            f"Higher timeframe bias ({context.htf_bias}) conflicts with the working timeframe "
            f"({context.trend})."
        )
    else:
        improvements.append("Supply higher-timeframe data so HTF bias stops reading as unknown.")

    if struct.trend_strength >= 75:
        strengths.append(f"Swing structure is clean ({struct.trend_strength:.0f}% agreement).")
    elif struct.trend_strength:
        weaknesses.append(f"Swing structure is mixed ({struct.trend_strength:.0f}% agreement).")

    if vol.get("available"):
        if vol.get("confirms_move"):
            strengths.append("Volume confirms the current move.")
        if vol.get("divergence", {}).get("detected"):
            weaknesses.append(f"Volume divergence present: {vol['divergence']['evidence']}")
    else:
        weaknesses.append(vol.get("reason", "No volume data available."))
        improvements.append("Connect a feed that reports real volume to unlock delta and profile analysis.")

    if probs.confidence >= 60:
        strengths.append(f"Scenario confidence is {probs.confidence:.0f}% with clear separation between outcomes.")
    else:
        weaknesses.append(f"Scenario confidence is only {probs.confidence:.0f}% — outcomes are closely matched.")

    if probs.sample_size < 50:
        weaknesses.append(
            f"Only {probs.sample_size} historical analogues were found, so the empirical component "
            f"of the probabilities is weak."
        )
        improvements.append("Load more history (2000+ bars) so base rates are measured on a larger sample.")

    if context.liquidity == "low":
        weaknesses.append("Liquidity is thin; execution will differ from the plan.")

    if setup.valid:
        strengths.append(f"A setup with {setup.primary_rr:.2f}R to first target is available.")
        if setup.entry_type == "market":
            improvements.append(
                "Wait for a pullback into a zone rather than entering at market — it would "
                "materially improve the reward:risk."
            )
    else:
        improvements.append(
            "No trade is warranted right now. " + (setup.no_trade_reason or "")
        )

    if price_action.get("confidence", 0) < 0.55:
        improvements.append("Wait for a decisive candlestick signal; current price action is ambiguous.")

    return strengths, weaknesses, improvements


def _summarize(series, context, struct, probs, setup) -> str:
    """Two or three sentences a trader can read before anything else."""
    dominant, value = probs.dominant
    price = series.last.close

    parts = [
        f"{series.symbol} on the {series.timeframe.value} is in a {context.regime} "
        f"({context.condition}) at {price:.6g}, with {context.trend_strength_label} directional "
        f"movement and {context.volatility} volatility.",
        f"Structure reads {struct.trend.value} in a {struct.phase.value.replace('_', ' ')} phase"
        + (f", most recently a {struct.last_event.direction} {struct.last_event.kind}."
           if struct.last_event else "."),
        f"The highest-probability path is {dominant} at {value:.0f}% "
        f"(confidence {probs.confidence:.0f}%).",
    ]
    if setup.valid:
        parts.append(
            f"That supports a {setup.direction} from {setup.entry:.6g}, stop {setup.stop_loss:.6g}, "
            f"first target {setup.take_profits[0]:.6g} at {setup.primary_rr:.2f}R."
        )
    else:
        parts.append(f"No trade is justified: {setup.no_trade_reason}")
    return " ".join(parts)


def _verdict(probs, setup, context, weaknesses: list[str]) -> dict:
    """The final call, with the reasoning that produced it."""
    if not setup.valid:
        return {
            "decision": "NO TRADE",
            "confidence": round(probs.confidence, 1),
            "reasoning": setup.no_trade_reason,
            "action": "Stand aside and re-evaluate when conditions change.",
        }

    if setup.confidence >= 65 and setup.primary_rr >= 2 and len(weaknesses) <= 2:
        decision = "TAKE"
        action = (
            f"Enter {setup.direction} at {setup.entry:.6g} ({setup.entry_type}), "
            f"stop {setup.stop_loss:.6g}, scale out at "
            + ", ".join(f"{t:.6g}" for t in setup.take_profits) + "."
        )
    elif setup.confidence >= 45:
        decision = "WATCH"
        action = (
            f"The {setup.direction} idea is valid but not yet compelling. Wait for price to reach "
            f"{setup.entry:.6g} and for confirmation before committing."
        )
    else:
        decision = "STAND ASIDE"
        action = "The evidence is too thin to justify risk. Wait for a cleaner setup."

    return {
        "decision": decision,
        "confidence": round(setup.confidence, 1),
        "reasoning": (
            f"Setup confidence {setup.confidence:.0f}% with {setup.primary_rr:.2f}R to first target. "
            + (f"Concerns: {'; '.join(weaknesses[:3])}." if weaknesses else "No material concerns.")
        ),
        "action": action,
    }


def _chart_payload(series, computed, struct, smc_report, level_report) -> dict:
    """Everything the chart needs to draw overlays, aligned to bar timestamps."""
    times = [c.timestamp for c in series.candles]

    def line(name: str) -> list[dict]:
        values = computed["arrays"].get(name, [])
        return [
            {"time": t, "value": v}
            for t, v in zip(times, values)
            if v is not None
        ]

    return {
        "candles": [c.to_dict() for c in series.candles],
        "overlays": {
            "ema20": line("ema20"),
            "ema50": line("ema50"),
            "ema200": line("ema200"),
            "vwap": line("vwap"),
            "bb_upper": line("bb_upper"),
            "bb_lower": line("bb_lower"),
            "supertrend": line("supertrend"),
        },
        "oscillators": {
            "rsi": line("rsi"),
            "macd": line("macd"),
            "macd_signal": line("macd_signal"),
            "macd_hist": line("macd_hist"),
            "adx": line("adx"),
            "volume": [
                {"time": c.timestamp, "value": c.volume, "color": "up" if c.bullish else "down"}
                for c in series.candles
            ],
        },
        "markers": {
            "swings": [s.to_dict() for s in struct.swings[-30:]],
            "events": [e.to_dict() for e in struct.events[-15:]],
            "sweeps": [s.to_dict() for s in struct.sweeps[-10:]],
        },
        "zones": {
            "order_blocks": [z.to_dict() for z in smc_report.order_blocks if not z.mitigated],
            "fair_value_gaps": [z.to_dict() for z in smc_report.fair_value_gaps],
            "breakers": [z.to_dict() for z in smc_report.breaker_blocks],
        },
        "levels": {
            "support": [l.to_dict() for l in level_report.support],
            "resistance": [l.to_dict() for l in level_report.resistance],
            "period": [l.to_dict() for l in level_report.period_levels],
            "fibonacci": [l.to_dict() for l in level_report.fibonacci],
        },
        "trendlines": [t.to_dict() for t in level_report.trendlines],
    }


def analyze_symbol(
    symbol: str,
    timeframe: Timeframe,
    provider: str | None = None,
    bars: int = 500,
    include_mtf: bool = True,
    account_balance: float = 10_000.0,
    risk_percent: float = 1.0,
    include_chart_data: bool = True,
    open_position_symbols: list[str] | None = None,
) -> AnalysisResult:
    """Fetch live data for a symbol and run the full analysis over it.

    `open_position_symbols`, when given, are the caller's other open
    positions. Each is fetched best-effort at the same timeframe so
    `risk.assess_risk` can measure correlation against them; a fetch failure
    for one drops that symbol from the comparison rather than failing the
    whole analysis, since correlation is an enhancement to risk, not a
    requirement for it.
    """
    from trading.market import market_service

    series = market_service.candles(symbol, timeframe, bars, provider)
    htf = ltf = None
    if include_mtf:
        try:
            htf = market_service.candles(symbol, timeframe.higher(), 300, provider)
        except Exception as exc:  # noqa: BLE001 - MTF is an enhancement, not a requirement
            logger.info("HTF fetch failed for %s: %s", symbol, exc)
        try:
            ltf = market_service.candles(symbol, timeframe.lower(), 300, provider)
        except Exception as exc:  # noqa: BLE001
            logger.info("LTF fetch failed for %s: %s", symbol, exc)

    related: dict[str, Series] = {}
    for other_symbol in dict.fromkeys(open_position_symbols or []):  # dedupe, keep order
        if other_symbol.upper() == symbol.upper():
            continue  # correlating a symbol with itself is not a finding
        try:
            related[other_symbol] = market_service.candles(other_symbol, timeframe, 200, provider)
        except Exception as exc:  # noqa: BLE001 - one bad symbol should not sink the analysis
            logger.info("correlation fetch failed for %s: %s", other_symbol, exc)

    # Same treatment as HTF/LTF and correlation above: best-effort, and a
    # failure (no FRED_API_KEY, FRED unreachable) degrades to the honest
    # "unmodelled" in risk.assess_risk rather than failing the analysis.
    upcoming_high_impact = None
    try:
        from trading.econcalendar import calendar_service

        events = calendar_service.upcoming(days=2)
        upcoming_high_impact = [e for e in events if e.importance == "high"]
    except Exception as exc:  # noqa: BLE001 - the calendar is an enhancement, not a requirement
        logger.info("economic calendar fetch failed: %s", exc)

    return analyze(
        series, htf, ltf,
        account_balance=account_balance,
        risk_percent=risk_percent,
        include_chart_data=include_chart_data,
        related_series=related or None,
        upcoming_high_impact_events=upcoming_high_impact,
    )
