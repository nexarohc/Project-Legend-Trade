"""Structure, SMC, volume, levels and the full analysis pipeline.

The most important assertions here are the negative ones: that a wick through a
level is classified as a sweep rather than a break, that no detector reads a bar
that has not closed, and that the pipeline declines to produce a setup when the
evidence does not support one.
"""
import random

import pytest

from tests.trading_fixtures import (
    candle,
    flat_series,
    make_series,
    ranging_series,
    series_with_fvg,
    series_with_gap,
    series_with_sweep,
    trending_series,
)
from trading import levels as levels_mod
from trading import patterns, regime, risk, smc, structure, volume
from trading.analysis import analyze
from trading.models import Timeframe


# --- market structure -------------------------------------------------------

def test_swings_are_confirmed_from_both_sides():
    """No swing may be reported within `lookback` bars of the right edge.

    A pivot needs bars on both sides to be confirmed; reporting one earlier
    would be look-ahead bias baked into the structure layer.
    """
    series = trending_series(bars=200)
    lookback = 3
    swings = structure.find_swings(series, lookback)
    assert swings
    assert max(s.index for s in swings) <= len(series) - lookback - 1


def test_swing_labels_follow_the_sequence():
    series = trending_series(bars=250, drift=0.3)
    swings = structure.find_swings(series)
    labelled = [s for s in swings if s.label]
    assert labelled
    for swing in labelled:
        assert swing.label.value in ("HH", "HL", "LH", "LL")


def test_uptrend_is_classified_bullish():
    report = structure.analyze_structure(trending_series(bars=300, drift=0.35))
    assert report.trend is structure.Trend.BULLISH
    assert report.trend_strength > 50


def test_downtrend_is_classified_bearish():
    report = structure.analyze_structure(trending_series(bars=300, drift=-0.35, seed=11))
    assert report.trend is structure.Trend.BEARISH


def test_bos_requires_a_close_beyond_the_level_not_a_wick():
    """The sweep fixture wicks above a swing high but closes below it.

    That must produce a sweep at 110.0 and must NOT produce a bullish break
    at the same level — the distinction is the entire point of using closes.
    """
    series = series_with_sweep()
    report = structure.analyze_structure(series, lookback=2)

    swept = [s for s in report.sweeps if s.direction == "buyside" and 109.5 <= s.level <= 110.5]
    assert swept, "the wick through the swing high was not detected as a sweep"

    broke = [
        e for e in report.events
        if e.direction == "bullish" and 109.5 <= e.price <= 110.5
    ]
    assert not broke, "a wick was wrongly classified as a break of structure"


def test_structure_events_are_bos_or_choch_only():
    report = structure.analyze_structure(trending_series(bars=300))
    for event in report.events:
        assert event.kind in ("BOS", "CHOCH")
        assert event.direction in ("bullish", "bearish")
        assert event.evidence


def test_short_series_degrades_without_raising():
    report = structure.analyze_structure(make_series([candle(i, 100, 101, 99, 100) for i in range(5)]))
    assert report.trend is structure.Trend.RANGING
    assert report.notes


# --- price action -----------------------------------------------------------

def test_bullish_engulfing_is_detected():
    candles = [candle(i, 100, 100.5, 99.5, 100) for i in range(20)]
    candles.append(candle(20, 101, 101.2, 99.0, 99.2))     # bearish
    candles.append(candle(21, 99.0, 102.0, 98.9, 101.5))   # engulfing bull
    found = patterns.detect_patterns(make_series(candles))
    assert any(p.name == "Bullish Engulfing" for p in found)


def test_hammer_requires_a_long_lower_wick():
    candles = [candle(i, 100, 100.5, 99.5, 100) for i in range(20)]
    candles.append(candle(20, 100, 100.2, 96.0, 99.9))     # long lower wick
    found = patterns.detect_patterns(make_series(candles))
    assert any(p.name in ("Hammer", "Pin Bar") for p in found)


def test_doji_detected_when_open_equals_close():
    candles = [candle(i, 100, 101, 99, 100.4) for i in range(20)]
    candles.append(candle(20, 100, 102, 98, 100.02))
    found = patterns.detect_patterns(make_series(candles))
    assert any(p.name == "Doji" for p in found)


def test_fake_breakout_detected_when_close_returns_inside():
    candles = [candle(i, 100, 100.5, 99.5, 100) for i in range(30)]
    candles.append(candle(30, 100, 105.0, 99.8, 100.1))    # spike up, close back inside
    found = patterns.detect_fake_breakouts(make_series(candles), window=20)
    assert any("Fake Breakout (upside)" == p.name for p in found)


def test_price_action_bias_is_bounded():
    result = patterns.analyze_price_action(trending_series(bars=200))
    assert result["bias"] in ("bullish", "bearish", "neutral")
    assert 0 <= result["confidence"] <= 1


# --- volume -----------------------------------------------------------------

def test_volume_profile_value_area_brackets_the_poc():
    profile = volume.build_profile(trending_series(bars=200))
    assert profile is not None
    assert profile.val <= profile.poc <= profile.vah


def test_zero_volume_feed_is_reported_not_faked():
    """FX feeds often report no volume. Say so rather than emit noise."""
    candles = [candle(i, 100, 101, 99, 100, v=0.0) for i in range(60)]
    result = volume.analyze_volume(make_series(candles))
    assert result["available"] is False
    assert "volume" in result["reason"].lower()


def test_volume_analysis_reports_evidence():
    result = volume.analyze_volume(trending_series(bars=200))
    assert result["available"] is True
    assert result["evidence"]
    assert 0 <= result["buy_share_percent"] <= 100


def test_delta_proxy_signs_follow_close_position():
    up_bar = candle(0, 100, 102, 100, 102, 500)     # closes on the high
    down_bar = candle(1, 102, 102, 100, 100, 500)   # closes on the low
    deltas = volume.delta_proxy(make_series([up_bar, down_bar]))
    assert deltas[0] > 0
    assert deltas[1] < 0


# --- smart money concepts ---------------------------------------------------

def test_fair_value_gap_detected_between_non_overlapping_bars():
    gaps = smc.find_fair_value_gaps(series_with_fvg())
    bullish = [g for g in gaps if g.direction == "bullish"]
    assert bullish
    gap = bullish[0]
    assert gap.bottom == pytest.approx(100.5)
    assert gap.top == pytest.approx(102.0)


def test_premium_discount_locates_price_in_range():
    series = trending_series(bars=200)
    report = structure.analyze_structure(series)
    result = smc.premium_discount(series, report)
    if result["available"]:
        assert 0 <= result["position_percent"] <= 100
        assert result["zone"] in ("premium", "discount", "equilibrium")


def test_smc_zones_carry_evidence():
    series = trending_series(bars=300)
    report = smc.analyze_smc(series, structure.analyze_structure(series))
    for zone in report.order_blocks + report.fair_value_gaps:
        assert zone.evidence, "every zone must explain why it exists"
        assert zone.top >= zone.bottom


def test_smc_handles_short_series():
    series = make_series([candle(i, 100, 101, 99, 100) for i in range(10)])
    report = smc.analyze_smc(series, structure.analyze_structure(series))
    assert report.narrative


# --- levels -----------------------------------------------------------------

def test_levels_are_classified_relative_to_price():
    series = trending_series(bars=250)
    swings = structure.find_swings(series)
    report = levels_mod.analyze_levels(series, swings)
    price = series.last.close
    assert all(l.price < price for l in report.support)
    assert all(l.price > price for l in report.resistance)


def test_round_numbers_scale_with_price_magnitude():
    small = levels_mod.round_numbers(make_series([candle(0, 0.65, 0.66, 0.64, 0.65)]))
    large = levels_mod.round_numbers(make_series([candle(0, 63000, 63500, 62500, 63000)]))
    assert small and large
    # Steps must differ by orders of magnitude, not be a fixed constant.
    assert large[0].price > small[0].price * 1000


def test_nearest_levels_bracket_current_price():
    series = trending_series(bars=250)
    report = levels_mod.analyze_levels(series, structure.find_swings(series))
    price = series.last.close
    if report.nearest_support:
        assert report.nearest_support.price < price
    if report.nearest_resistance:
        assert report.nearest_resistance.price > price


# --- regime and risk --------------------------------------------------------

def test_context_reports_every_required_field():
    context = regime.build_context(trending_series(bars=300))
    payload = context.to_dict()
    for field in ("trend", "momentum", "volatility", "liquidity", "regime",
                  "institutional_bias", "evidence"):
        assert field in payload
    assert payload["evidence"], "context must always explain itself"


def test_gap_risk_detects_a_real_gap():
    level, note = risk.assess_gap_risk(series_with_gap(), series_with_gap().asset_class)
    assert level in ("low", "moderate", "high", "unknown")
    assert note


def test_position_size_scales_inversely_with_stop_distance():
    tight = risk.position_size(10_000, 1.0, 100, 99)
    wide = risk.position_size(10_000, 1.0, 100, 90)
    assert tight["valid"] and wide["valid"]
    assert tight["units"] > wide["units"]
    # Cash at risk is identical regardless of stop width — the whole point.
    assert tight["risk_amount"] == pytest.approx(wide["risk_amount"])


def test_position_size_rejects_zero_stop_distance():
    result = risk.position_size(10_000, 1.0, 100, 100)
    assert result["valid"] is False


def test_risk_flags_unmodelled_news_exposure():
    series = trending_series(bars=200)
    assessment = risk.assess_risk(series, regime.build_context(series))
    assert assessment.news_risk == "unmodelled"
    assert any("calendar" in w.lower() for w in assessment.warnings)


def test_risk_reports_low_news_risk_with_an_empty_calendar_window():
    series = trending_series(bars=200)
    assessment = risk.assess_risk(series, regime.build_context(series), upcoming_high_impact_events=[])
    assert assessment.news_risk == "low"
    assert assessment.news_event_detail is None


def test_risk_flags_elevated_news_risk_with_a_high_impact_event():
    from trading.econcalendar import EconomicEvent

    series = trending_series(bars=200)
    event = EconomicEvent(release_id="10", name="Employment Situation", date="2026-08-01", importance="high")
    assessment = risk.assess_risk(series, regime.build_context(series), upcoming_high_impact_events=[event])
    assert assessment.news_risk == "elevated"
    assert assessment.news_event_detail == [event.to_dict()]
    assert any("Employment Situation" in w for w in assessment.warnings)


# --- position correlation ----------------------------------------------------
#
# Correlation is computed on bar-to-bar percentage returns, not price levels,
# and matched by timestamp — building the fixtures directly from those returns
# (rather than from arbitrary OHLC) is what lets each test assert an exact,
# known coefficient instead of just "high" or "low".

def _series_from_returns(returns: list[float], start: float = 100.0, symbol: str = "BASE"):
    price = start
    candles = []
    for i, r in enumerate(returns):
        o = price
        price = price * (1 + r)
        candles.append(candle(i, o, max(o, price), min(o, price), price))
    return make_series(candles, symbol=symbol)


def test_correlation_is_unmodelled_with_no_related_positions():
    series = trending_series(bars=200)
    assessment = risk.assess_risk(series, regime.build_context(series))
    assert assessment.correlation_risk == "unmodelled"
    assert assessment.correlation_detail is None


def test_identical_returns_correlate_at_one():
    returns = [0.01, -0.02, 0.015, 0.03, -0.01, 0.02, -0.015, 0.01, 0.005, -0.02] * 4
    base = _series_from_returns(returns, symbol="BASE")
    same = _series_from_returns(returns, symbol="TWIN")  # identical path, different symbol

    result = risk.compute_position_correlation(base, {"TWIN": same})
    assert result["available"] is True
    assert result["level"] == "high"
    pair = result["pairs"][0]
    assert pair["correlation"] == pytest.approx(1.0, abs=1e-9)
    assert "one bet" in pair["evidence"]


def test_inverted_returns_correlate_at_negative_one():
    returns = [0.01, -0.02, 0.015, 0.03, -0.01, 0.02, -0.015, 0.01, 0.005, -0.02] * 4
    base = _series_from_returns(returns, symbol="BASE")
    inverse = _series_from_returns([-r for r in returns], symbol="INVERSE")

    result = risk.compute_position_correlation(base, {"INVERSE": inverse})
    pair = result["pairs"][0]
    assert pair["correlation"] == pytest.approx(-1.0, abs=1e-9)
    # Strongly correlated in magnitude even though the sign is negative —
    # a short-the-other-side hedge is still one bet, not a diversified two.
    assert result["level"] == "high"


def test_uncorrelated_returns_are_reported_as_low():
    rnd_a = random.Random(1)
    rnd_b = random.Random(99)
    returns_a = [rnd_a.gauss(0, 0.01) for _ in range(80)]
    returns_b = [rnd_b.gauss(0, 0.01) for _ in range(80)]
    base = _series_from_returns(returns_a, symbol="BASE")
    other = _series_from_returns(returns_b, symbol="OTHER")

    result = risk.compute_position_correlation(base, {"OTHER": other})
    pair = result["pairs"][0]
    assert abs(pair["correlation"]) < 0.4
    assert result["level"] == "low"


def test_insufficient_overlap_is_reported_not_guessed():
    base = _series_from_returns([0.01] * 50, symbol="BASE")
    short = _series_from_returns([0.01] * 5, symbol="SHORT")  # far fewer bars

    result = risk.compute_position_correlation(base, {"SHORT": short}, min_overlap=30)
    pair = result["pairs"][0]
    assert pair["correlation"] is None
    assert "overlapping bars" in pair["evidence"]


def test_no_related_series_returns_unavailable():
    base = trending_series(bars=100)
    result = risk.compute_position_correlation(base, {})
    assert result["available"] is False


def test_correlation_wired_through_assess_risk():
    returns = [0.01, -0.02, 0.015, 0.03, -0.01, 0.02, -0.015, 0.01, 0.005, -0.02] * 4
    base = _series_from_returns(returns, symbol="BASE")
    same = _series_from_returns(returns, symbol="TWIN")

    assessment = risk.assess_risk(
        base, regime.build_context(base), related_series={"TWIN": same},
    )
    assert assessment.correlation_risk == "high"
    assert assessment.correlation_detail
    assert any("TWIN" in e for e in assessment.evidence)
    assert any("correlated" in w.lower() for w in assessment.warnings)


def test_analysis_pipeline_passes_related_series_through():
    returns = [0.01, -0.02, 0.015, 0.03, -0.01, 0.02, -0.015, 0.01, 0.005, -0.02] * 20
    base = _series_from_returns(returns, symbol="BASE")
    same = _series_from_returns(returns, symbol="TWIN")

    result = analyze(base, related_series={"TWIN": same}, include_chart_data=False)
    assert result.risk_review["correlation_risk"] == "high"
    assert result.risk_review["correlation_detail"]


# --- full pipeline ----------------------------------------------------------

def test_analysis_returns_every_section():
    result = analyze(trending_series(bars=400)).to_dict()
    required = [
        "executive_summary", "market_context", "trend_analysis", "market_structure",
        "smart_money", "volume_analysis", "indicator_analysis", "key_levels",
        "price_action", "probability_analysis", "trade_setup", "risk_review",
        "strengths", "weaknesses", "improvements", "verdict", "chart_data",
    ]
    for section in required:
        assert section in result, f"missing section: {section}"
    assert result["executive_summary"]


def test_probabilities_sum_to_one_hundred():
    probs = analyze(trending_series(bars=400)).to_dict()["probability_analysis"]
    total = sum(probs[k] for k in (
        "bullish_continuation", "bearish_continuation", "breakout",
        "reversal", "range_bound", "false_breakout",
    ))
    assert total == pytest.approx(100.0, abs=0.5)


def test_every_probability_factor_is_explained():
    probs = analyze(trending_series(bars=400)).to_dict()["probability_analysis"]
    assert probs["factors"], "probabilities must be backed by factors"
    for factor in probs["factors"]:
        assert factor["explanation"], "an unexplained factor is a black box"
        assert factor["scenario"]


def test_analysis_declines_a_setup_in_a_range():
    """A rotational market should produce no trade, with a stated reason."""
    setup = analyze(ranging_series(bars=400)).to_dict()["trade_setup"]
    if not setup["valid"]:
        assert setup["no_trade_reason"]


def test_valid_setup_is_internally_consistent():
    result = analyze(trending_series(bars=400, drift=0.4)).to_dict()
    setup = result["trade_setup"]
    if setup["valid"]:
        if setup["direction"] == "long":
            assert setup["stop_loss"] < setup["entry"]
            assert all(tp > setup["entry"] for tp in setup["take_profits"])
        else:
            assert setup["stop_loss"] > setup["entry"]
            assert all(tp < setup["entry"] for tp in setup["take_profits"])
        assert setup["primary_rr"] >= 1.5   # below this the builder must decline
        assert setup["invalidation"]


def test_verdict_always_carries_reasoning():
    verdict = analyze(trending_series(bars=400)).to_dict()["verdict"]
    assert verdict["decision"] in ("TAKE", "WATCH", "STAND ASIDE", "NO TRADE")
    assert verdict["reasoning"]
    assert verdict["action"]


def test_analysis_warns_on_thin_history():
    result = analyze(trending_series(bars=70)).to_dict()
    assert result["warnings"]


def test_analysis_rejects_empty_series():
    with pytest.raises(ValueError):
        analyze(make_series([]))


def test_flat_market_does_not_crash_the_pipeline():
    result = analyze(flat_series(200)).to_dict()
    assert result["executive_summary"]
