"""Options pricing: Black-Scholes, the CRR binomial tree, implied volatility,
historical volatility, and multi-leg payoff diagrams.

Reference values for Black-Scholes come from Hull's "Options, Futures, and
Other Derivatives" (S=42, K=40, T=0.5, r=10%, sigma=20%, no dividend):
call ~= 4.76, put ~= 0.81. Everything else is checked against known
mathematical properties (put-call parity, American >= European, monotonic
IV, breakeven formulas for standard spreads) rather than a second guess at
the same arithmetic.
"""
import math

import pytest

from trading.models import AssetClass, Candle, Series, Timeframe
from trading.options import (
    ExerciseStyle,
    Greeks,
    Leg,
    OptionsError,
    OptionType,
    binomial_crr,
    black_scholes,
    bull_call_spread,
    call_butterfly,
    bear_put_spread,
    historical_volatility,
    implied_volatility,
    iron_condor,
    long_straddle,
    long_strangle,
    payoff_diagram,
)


# --- Black-Scholes: known reference values ------------------------------------

def test_call_matches_the_textbook_reference_value():
    result = black_scholes(42, 40, 0.5, 0.20, OptionType.CALL, risk_free_rate=0.10)
    assert result.fair_value == pytest.approx(4.76, abs=0.01)


def test_put_matches_the_textbook_reference_value():
    result = black_scholes(42, 40, 0.5, 0.20, OptionType.PUT, risk_free_rate=0.10)
    assert result.fair_value == pytest.approx(0.81, abs=0.01)


def test_put_call_parity_holds():
    call = black_scholes(42, 40, 0.5, 0.20, OptionType.CALL, risk_free_rate=0.10)
    put = black_scholes(42, 40, 0.5, 0.20, OptionType.PUT, risk_free_rate=0.10)
    lhs = call.fair_value - put.fair_value
    rhs = 42 - 40 * math.exp(-0.10 * 0.5)
    assert lhs == pytest.approx(rhs, abs=1e-6)


def test_call_delta_is_between_zero_and_one():
    result = black_scholes(42, 40, 0.5, 0.20, OptionType.CALL, risk_free_rate=0.10)
    assert 0.0 < result.greeks.delta < 1.0


def test_put_delta_is_between_negative_one_and_zero():
    result = black_scholes(42, 40, 0.5, 0.20, OptionType.PUT, risk_free_rate=0.10)
    assert -1.0 < result.greeks.delta < 0.0


def test_call_and_put_gamma_are_identical():
    """Gamma is the same for a call and a put at the same strike — a
    Black-Scholes identity worth pinning down."""
    call = black_scholes(100, 100, 0.25, 0.30, OptionType.CALL)
    put = black_scholes(100, 100, 0.25, 0.30, OptionType.PUT)
    assert call.greeks.gamma == pytest.approx(put.greeks.gamma, rel=1e-9)


def test_call_and_put_vega_are_identical():
    call = black_scholes(100, 100, 0.25, 0.30, OptionType.CALL)
    put = black_scholes(100, 100, 0.25, 0.30, OptionType.PUT)
    assert call.greeks.vega == pytest.approx(put.greeks.vega, rel=1e-9)


def test_deep_itm_call_delta_approaches_one():
    result = black_scholes(200, 50, 0.5, 0.20, OptionType.CALL, risk_free_rate=0.05)
    assert result.greeks.delta > 0.99


def test_deep_otm_call_delta_approaches_zero():
    result = black_scholes(50, 200, 0.5, 0.20, OptionType.CALL, risk_free_rate=0.05)
    assert result.greeks.delta < 0.01


def test_intrinsic_and_time_value_sum_to_fair_value():
    result = black_scholes(105, 100, 0.25, 0.25, OptionType.CALL)
    assert result.intrinsic_value + result.time_value == pytest.approx(result.fair_value, abs=1e-9)


def test_itm_call_has_positive_intrinsic_value():
    result = black_scholes(120, 100, 0.25, 0.25, OptionType.CALL)
    assert result.intrinsic_value == pytest.approx(20.0)


def test_otm_call_has_zero_intrinsic_value():
    result = black_scholes(80, 100, 0.25, 0.25, OptionType.CALL)
    assert result.intrinsic_value == 0.0


# --- input validation ----------------------------------------------------------

@pytest.mark.parametrize("spot,strike,t,vol", [
    (0, 100, 0.5, 0.2), (-10, 100, 0.5, 0.2),
    (100, 0, 0.5, 0.2), (100, -10, 0.5, 0.2),
    (100, 100, 0, 0.2), (100, 100, -0.1, 0.2),
    (100, 100, 0.5, 0), (100, 100, 0.5, -0.2),
])
def test_black_scholes_rejects_invalid_inputs(spot, strike, t, vol):
    with pytest.raises(OptionsError):
        black_scholes(spot, strike, t, vol, OptionType.CALL)


# --- binomial tree: American exercise -------------------------------------------

def test_american_call_matches_european_with_no_dividend():
    """No early-exercise benefit for a call with no dividend — American and
    European should converge to (almost) the same price."""
    european = black_scholes(42, 40, 0.5, 0.20, OptionType.CALL, risk_free_rate=0.10)
    american = binomial_crr(42, 40, 0.5, 0.20, OptionType.CALL, ExerciseStyle.AMERICAN,
                             risk_free_rate=0.10, steps=300)
    assert american.fair_value == pytest.approx(european.fair_value, abs=0.05)


def test_american_put_is_worth_at_least_as_much_as_european():
    """Early exercise can only add value for a put — American >= European,
    always."""
    european = black_scholes(40, 45, 1.0, 0.30, OptionType.PUT, risk_free_rate=0.08)
    american = binomial_crr(40, 45, 1.0, 0.30, OptionType.PUT, ExerciseStyle.AMERICAN,
                             risk_free_rate=0.08, steps=300)
    assert american.fair_value >= european.fair_value - 1e-6


def test_european_style_binomial_converges_to_black_scholes():
    bs = black_scholes(50, 50, 1.0, 0.25, OptionType.CALL, risk_free_rate=0.05)
    crr = binomial_crr(50, 50, 1.0, 0.25, OptionType.CALL, ExerciseStyle.EUROPEAN,
                        risk_free_rate=0.05, steps=500)
    assert crr.fair_value == pytest.approx(bs.fair_value, abs=0.02)


def test_binomial_rejects_too_few_steps():
    with pytest.raises(OptionsError):
        binomial_crr(100, 100, 0.5, 0.2, OptionType.CALL, steps=1)


# --- implied volatility ---------------------------------------------------------

def test_implied_volatility_recovers_the_input_volatility():
    priced = black_scholes(100, 100, 0.5, 0.28, OptionType.CALL, risk_free_rate=0.04)
    iv = implied_volatility(priced.fair_value, 100, 100, 0.5, OptionType.CALL, risk_free_rate=0.04)
    assert iv == pytest.approx(0.28, abs=1e-4)


def test_implied_volatility_works_for_puts_too():
    priced = black_scholes(100, 110, 0.5, 0.35, OptionType.PUT, risk_free_rate=0.03)
    iv = implied_volatility(priced.fair_value, 100, 110, 0.5, OptionType.PUT, risk_free_rate=0.03)
    assert iv == pytest.approx(0.35, abs=1e-4)


def test_implied_volatility_rejects_a_price_below_intrinsic():
    # A call struck at 50 with spot at 100 has intrinsic value 50 — quoting
    # it at 10 is not a real option price.
    with pytest.raises(OptionsError):
        implied_volatility(10, 100, 50, 0.5, OptionType.CALL)


def test_implied_volatility_rejects_nonpositive_price():
    with pytest.raises(OptionsError):
        implied_volatility(0, 100, 100, 0.5, OptionType.CALL)


# --- historical volatility -------------------------------------------------------

def _series(closes, timeframe=Timeframe.D1):
    candles = [Candle(timestamp=i * 86400, open=c, high=c, low=c, close=c, volume=1000)
               for i, c in enumerate(closes)]
    return Series(symbol="TEST", timeframe=timeframe, candles=candles, asset_class=AssetClass.STOCK)


def test_historical_volatility_is_zero_for_a_flat_series():
    series = _series([100.0] * 30)
    assert historical_volatility(series) == pytest.approx(0.0, abs=1e-9)


def test_historical_volatility_is_positive_for_a_moving_series():
    closes = [100 + (5 if i % 2 == 0 else -5) for i in range(30)]
    series = _series(closes)
    assert historical_volatility(series) > 0


def test_historical_volatility_requires_at_least_three_bars():
    with pytest.raises(OptionsError):
        historical_volatility(_series([100.0, 101.0]))


def test_historical_volatility_scales_with_timeframe():
    """The same day-to-day percentage move annualizes to a larger number on
    a daily series than a monthly one, purely from more periods per year."""
    closes = [100 * (1.01 ** i) for i in range(10)]
    daily = historical_volatility(_series(closes, Timeframe.D1))
    monthly = historical_volatility(_series(closes, Timeframe.MN1))
    assert daily > monthly


# --- payoff diagrams: strategy properties -----------------------------------------

def test_bull_call_spread_has_capped_profit_and_loss():
    legs = bull_call_spread(spot=100, lower_strike=95, upper_strike=105,
                             t=0.25, vol=0.25)
    result = payoff_diagram(legs, current_spot=100)
    width = 105 - 95
    assert result["max_profit"] == pytest.approx(width - result["net_premium"], abs=1e-6)
    assert result["max_loss"] == pytest.approx(-result["net_premium"], abs=1e-6)
    assert result["net_premium"] > 0  # a bull call spread is a net debit


def test_bear_put_spread_has_capped_profit_and_loss():
    legs = bear_put_spread(spot=100, lower_strike=90, upper_strike=100,
                            t=0.25, vol=0.25)
    result = payoff_diagram(legs, current_spot=100)
    assert result["max_profit"] is not None
    assert result["max_loss"] is not None
    assert result["net_premium"] > 0


def test_long_straddle_has_unbounded_profit():
    legs = long_straddle(spot=100, strike=100, t=0.25, vol=0.30)
    result = payoff_diagram(legs, current_spot=100)
    assert result["max_profit"] is None
    assert result["max_loss"] == pytest.approx(-result["net_premium"], abs=1e-6)


def test_long_straddle_breakevens_bracket_the_strike():
    legs = long_straddle(spot=100, strike=100, t=0.25, vol=0.30)
    result = payoff_diagram(legs, current_spot=100)
    assert len(result["breakevens"]) == 2
    lower, upper = sorted(result["breakevens"])
    assert lower < 100 < upper
    premium = result["net_premium"]
    assert lower == pytest.approx(100 - premium, abs=0.01)
    assert upper == pytest.approx(100 + premium, abs=0.01)


def test_long_strangle_requires_put_strike_below_call_strike():
    with pytest.raises(OptionsError):
        long_strangle(spot=100, put_strike=110, call_strike=90, t=0.25, vol=0.25)


def test_iron_condor_max_profit_equals_net_credit():
    legs = iron_condor(spot=100, put_long=85, put_short=90, call_short=110,
                        call_long=115, t=0.25, vol=0.25)
    result = payoff_diagram(legs, current_spot=100)
    assert result["net_premium"] < 0  # a net credit
    assert result["max_profit"] == pytest.approx(-result["net_premium"], abs=1e-6)


def test_iron_condor_requires_ordered_strikes():
    with pytest.raises(OptionsError):
        iron_condor(spot=100, put_long=90, put_short=85, call_short=110,
                    call_long=115, t=0.25, vol=0.25)


def test_call_butterfly_requires_equal_wing_widths():
    with pytest.raises(OptionsError):
        call_butterfly(spot=100, lower_strike=90, middle_strike=100,
                       upper_strike=115, t=0.25, vol=0.25)


def test_call_butterfly_max_loss_is_the_net_debit():
    legs = call_butterfly(spot=100, lower_strike=90, middle_strike=100,
                          upper_strike=110, t=0.25, vol=0.25)
    result = payoff_diagram(legs, current_spot=100)
    assert result["max_loss"] == pytest.approx(-result["net_premium"], abs=1e-6)
    assert result["max_profit"] is not None


# --- payoff_diagram: generic behavior ------------------------------------------

def test_payoff_diagram_requires_at_least_one_leg():
    with pytest.raises(OptionsError):
        payoff_diagram([])


def test_payoff_diagram_requires_spot_high_past_every_strike():
    legs = [Leg(OptionType.CALL, 100, 5.0, 1)]
    with pytest.raises(OptionsError):
        payoff_diagram(legs, spot_high=100)  # not strictly past the strike


def test_payoff_diagram_includes_spot_zero():
    legs = [Leg(OptionType.PUT, 100, 5.0, 1)]
    result = payoff_diagram(legs, current_spot=100)
    assert result["curve"][0]["spot"] == 0.0
    # A long put at spot=0 is worth strike minus the premium paid.
    assert result["curve"][0]["pnl"] == pytest.approx(100 - 5.0, abs=1e-6)


def test_short_call_has_unbounded_loss():
    legs = [Leg(OptionType.CALL, 100, 5.0, -1)]  # sold naked
    result = payoff_diagram(legs, current_spot=100)
    assert result["max_loss"] is None
    assert result["max_profit"] == pytest.approx(5.0, abs=1e-6)


# --- Greeks dataclass ------------------------------------------------------------

def test_greeks_to_dict_rounds_values():
    g = Greeks(delta=0.123456789, gamma=0.01, theta=-0.05, vega=0.2, rho=0.1)
    d = g.to_dict()
    assert d["delta"] == round(0.123456789, 6)
