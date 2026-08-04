"""Options pricing: Black-Scholes, a CRR binomial tree for American exercise,
implied volatility, historical volatility, and multi-leg payoff diagrams.

No new data dependency. Every function here is closed-form or numerical math
taking spot/strike/time/rate/volatility as inputs — volatility can be passed
explicitly, or estimated from the underlying's own real historical price
series via `historical_volatility`. That matters because the market-data
vendors this platform can reach for free (Polygon, Twelve Data, Finnhub) do
not serve live option data on a free tier (see
`docs/TRADING_TERMINAL.md`'s "Honest limitations" — the same lesson learned
the hard way while verifying equity streaming applies here too, so it was
checked before, not after, promising a live option chain).

This module deliberately does not fetch or display a live option chain
(strikes, bid/ask, open interest, real IV). Faking that with stale or
synthetic numbers would misrepresent real market data as this platform
consistently refuses to do elsewhere (see decision 27 in
`docs/PROJECT_STATE.md`, about the `LIVE`/`DELAYED` stream badge). What is
here — pricing a contract you specify, and its Greeks — needs no chain.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from trading.models import Series, Timeframe


class OptionType(str, Enum):
    CALL = "call"
    PUT = "put"


class ExerciseStyle(str, Enum):
    EUROPEAN = "european"
    AMERICAN = "american"


class OptionsError(Exception):
    """Bad inputs to the pricing engine. Never silently guessed at."""


# Trading periods per year, by bar timeframe, for annualizing historical
# volatility. Equity-market-hours convention (252 trading days/year, a 6.5h
# session) — a documented approximation for 24/7 markets like crypto, not a
# silent one.
_PERIODS_PER_YEAR: dict[Timeframe, float] = {
    Timeframe.M1: 252 * 6.5 * 60,
    Timeframe.M5: 252 * 6.5 * 12,
    Timeframe.M15: 252 * 6.5 * 4,
    Timeframe.M30: 252 * 6.5 * 2,
    Timeframe.H1: 252 * 6.5,
    Timeframe.H4: 252 * 6.5 / 4,
    Timeframe.D1: 252,
    Timeframe.W1: 52,
    Timeframe.MN1: 12,
}


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _validate_inputs(spot: float, strike: float, time_to_expiry_years: float, volatility: float) -> None:
    if spot <= 0:
        raise OptionsError("Spot price must be positive.")
    if strike <= 0:
        raise OptionsError("Strike price must be positive.")
    if time_to_expiry_years <= 0:
        raise OptionsError("Time to expiry must be positive — this option has already expired.")
    if volatility <= 0:
        raise OptionsError("Volatility must be positive.")


@dataclass
class Greeks:
    delta: float
    gamma: float
    theta: float  # per calendar day
    vega: float   # per 1 percentage point of volatility (e.g. 0.20 -> 0.21)
    rho: float    # per 1 percentage point of the risk-free rate

    def to_dict(self) -> dict:
        return {
            "delta": round(self.delta, 6), "gamma": round(self.gamma, 6),
            "theta": round(self.theta, 6), "vega": round(self.vega, 6),
            "rho": round(self.rho, 6),
        }


@dataclass
class PricingResult:
    fair_value: float
    intrinsic_value: float
    time_value: float
    greeks: Greeks
    inputs: dict

    def to_dict(self) -> dict:
        return {
            "fair_value": round(self.fair_value, 4),
            "intrinsic_value": round(self.intrinsic_value, 4),
            "time_value": round(self.time_value, 4),
            "greeks": self.greeks.to_dict(),
            "inputs": self.inputs,
        }


def black_scholes(
    spot: float,
    strike: float,
    time_to_expiry_years: float,
    volatility: float,
    option_type: OptionType,
    risk_free_rate: float = 0.05,
    dividend_yield: float = 0.0,
) -> PricingResult:
    """European option fair value and exact closed-form Greeks."""
    _validate_inputs(spot, strike, time_to_expiry_years, volatility)

    S, K, T, sigma, r, q = spot, strike, time_to_expiry_years, volatility, risk_free_rate, dividend_yield
    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    disc_r = math.exp(-r * T)
    disc_q = math.exp(-q * T)

    if option_type is OptionType.CALL:
        price = S * disc_q * _norm_cdf(d1) - K * disc_r * _norm_cdf(d2)
        delta = disc_q * _norm_cdf(d1)
        theta_year = (
            -S * disc_q * _norm_pdf(d1) * sigma / (2 * sqrt_T)
            - r * K * disc_r * _norm_cdf(d2)
            + q * S * disc_q * _norm_cdf(d1)
        )
        rho = K * T * disc_r * _norm_cdf(d2) / 100.0
        intrinsic = max(S - K, 0.0)
    else:
        price = K * disc_r * _norm_cdf(-d2) - S * disc_q * _norm_cdf(-d1)
        delta = disc_q * (_norm_cdf(d1) - 1.0)
        theta_year = (
            -S * disc_q * _norm_pdf(d1) * sigma / (2 * sqrt_T)
            + r * K * disc_r * _norm_cdf(-d2)
            - q * S * disc_q * _norm_cdf(-d1)
        )
        rho = -K * T * disc_r * _norm_cdf(-d2) / 100.0
        intrinsic = max(K - S, 0.0)

    gamma = disc_q * _norm_pdf(d1) / (S * sigma * sqrt_T)
    vega = S * disc_q * _norm_pdf(d1) * sqrt_T / 100.0  # per 1 vol point

    return PricingResult(
        fair_value=price,
        intrinsic_value=intrinsic,
        time_value=price - intrinsic,
        greeks=Greeks(delta=delta, gamma=gamma, theta=theta_year / 365.0, vega=vega, rho=rho),
        inputs={
            "spot": S, "strike": K, "time_to_expiry_years": T, "volatility": sigma,
            "risk_free_rate": r, "dividend_yield": q, "option_type": option_type.value,
            "exercise_style": ExerciseStyle.EUROPEAN.value, "model": "black_scholes",
        },
    )


def _crr_price(
    S: float, K: float, T: float, sigma: float, option_type: OptionType,
    exercise_style: ExerciseStyle, r: float, q: float, steps: int,
) -> float:
    dt = T / steps
    u = math.exp(sigma * math.sqrt(dt))
    d = 1.0 / u
    disc = math.exp(-r * dt)
    p = (math.exp((r - q) * dt) - d) / (u - d)
    if not (0.0 < p < 1.0):
        raise OptionsError(
            "Binomial tree parameters produce an invalid up/down probability — "
            "volatility is out of a sane range for this time step."
        )

    values = []
    for i in range(steps + 1):
        s_final = S * (u ** (steps - i)) * (d ** i)
        payoff = max(s_final - K, 0.0) if option_type is OptionType.CALL else max(K - s_final, 0.0)
        values.append(payoff)

    for step in range(steps - 1, -1, -1):
        next_values = []
        for i in range(step + 1):
            continuation = disc * (p * values[i] + (1 - p) * values[i + 1])
            if exercise_style is ExerciseStyle.AMERICAN:
                s_node = S * (u ** (step - i)) * (d ** i)
                intrinsic = max(s_node - K, 0.0) if option_type is OptionType.CALL else max(K - s_node, 0.0)
                continuation = max(continuation, intrinsic)
            next_values.append(continuation)
        values = next_values

    return values[0]


def binomial_crr(
    spot: float,
    strike: float,
    time_to_expiry_years: float,
    volatility: float,
    option_type: OptionType,
    exercise_style: ExerciseStyle = ExerciseStyle.AMERICAN,
    risk_free_rate: float = 0.05,
    dividend_yield: float = 0.0,
    steps: int = 200,
) -> PricingResult:
    """Cox-Ross-Rubinstein binomial tree. Handles early exercise, so this is
    the model to use for American options; Greeks come from finite-difference
    bumps since early exercise has no closed form."""
    _validate_inputs(spot, strike, time_to_expiry_years, volatility)
    if steps < 10:
        raise OptionsError("steps must be at least 10 for a meaningful tree.")

    def price_at(S=spot, sigma=volatility, T=time_to_expiry_years, r=risk_free_rate):
        return _crr_price(S, strike, T, sigma, option_type, exercise_style, r, dividend_yield, steps)

    fair_value = price_at()
    intrinsic = (max(spot - strike, 0.0) if option_type is OptionType.CALL
                 else max(strike - spot, 0.0))

    h_s = spot * 0.01
    delta = (price_at(S=spot + h_s) - price_at(S=spot - h_s)) / (2 * h_s)
    gamma = (price_at(S=spot + h_s) - 2 * fair_value + price_at(S=spot - h_s)) / (h_s * h_s)
    vega = price_at(sigma=volatility + 0.01) - fair_value  # per 1 vol point

    one_day = 1.0 / 365.0
    if time_to_expiry_years > one_day:
        theta = price_at(T=time_to_expiry_years - one_day) - fair_value  # negative = decay
    else:
        theta = -fair_value  # collapses to intrinsic at expiry

    rho = price_at(r=risk_free_rate + 0.01) - fair_value  # per 1 rate point

    return PricingResult(
        fair_value=fair_value,
        intrinsic_value=intrinsic,
        time_value=fair_value - intrinsic,
        greeks=Greeks(delta=delta, gamma=gamma, theta=theta, vega=vega, rho=rho),
        inputs={
            "spot": spot, "strike": strike, "time_to_expiry_years": time_to_expiry_years,
            "volatility": volatility, "risk_free_rate": risk_free_rate,
            "dividend_yield": dividend_yield, "option_type": option_type.value,
            "exercise_style": exercise_style.value, "model": "binomial_crr", "steps": steps,
        },
    )


def implied_volatility(
    market_price: float,
    spot: float,
    strike: float,
    time_to_expiry_years: float,
    option_type: OptionType,
    risk_free_rate: float = 0.05,
    dividend_yield: float = 0.0,
    tolerance: float = 1e-6,
    max_iterations: int = 100,
) -> float:
    """Solve for the volatility that makes Black-Scholes match an observed
    market price, via bisection. BS price is monotonic increasing in sigma,
    so bisection always converges without needing a derivative (vega can be
    ~0 deep out-of-the-money, which breaks Newton's method there)."""
    if market_price <= 0:
        raise OptionsError("Market price must be positive.")
    intrinsic = (max(spot - strike, 0.0) if option_type is OptionType.CALL
                 else max(strike - spot, 0.0))
    if market_price < intrinsic - 1e-9:
        raise OptionsError(
            f"Market price {market_price:g} is below intrinsic value {intrinsic:g} — "
            "not a valid option price, cannot solve for volatility."
        )

    low, high = 1e-4, 5.0
    price_low = black_scholes(spot, strike, time_to_expiry_years, low, option_type,
                               risk_free_rate, dividend_yield).fair_value
    price_high = black_scholes(spot, strike, time_to_expiry_years, high, option_type,
                                risk_free_rate, dividend_yield).fair_value
    if not (price_low <= market_price <= price_high):
        raise OptionsError(
            "Market price is outside what any volatility between "
            f"{low:.2%} and {high:.0%} would produce — check the inputs, this usually "
            "means a stale or mistyped market price."
        )

    for _ in range(max_iterations):
        mid = (low + high) / 2
        price_mid = black_scholes(spot, strike, time_to_expiry_years, mid, option_type,
                                   risk_free_rate, dividend_yield).fair_value
        if abs(price_mid - market_price) < tolerance:
            return mid
        if price_mid < market_price:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def historical_volatility(series: Series) -> float:
    """Annualized volatility from the underlying's own real close prices —
    the free, honest stand-in for implied volatility when no live option
    chain is available. Log returns, sample stdev, annualized by the
    timeframe's approximate trading-periods-per-year."""
    closes = [c.close for c in series.candles if c.close > 0]
    if len(closes) < 3:
        raise OptionsError("Need at least 3 closed bars to estimate historical volatility.")

    returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    periods_per_year = _PERIODS_PER_YEAR.get(series.timeframe, 252)
    return math.sqrt(variance * periods_per_year)


@dataclass
class Leg:
    option_type: OptionType
    strike: float
    premium: float  # price per contract: positive = cost to enter this leg
    quantity: int    # positive = long (bought), negative = short (sold)

    def payoff_at(self, spot_at_expiry: float) -> float:
        intrinsic = (max(spot_at_expiry - self.strike, 0.0) if self.option_type is OptionType.CALL
                     else max(self.strike - spot_at_expiry, 0.0))
        return self.quantity * (intrinsic - self.premium)


def payoff_diagram(
    legs: list[Leg],
    spot_high: float | None = None,
    points: int = 61,
    current_spot: float | None = None,
) -> dict:
    """P/L at expiration across a range of underlying prices, from 0 up to
    `spot_high`. Detects an unbounded profit or loss by checking the slope at
    the top of the range — the payoff of any option combination is exactly
    linear beyond the highest strike, so as long as `spot_high` extends past
    every leg's strike, that slope tells you the true asymptotic direction
    rather than just what's visible in the sampled window. There is no
    equivalent unbounded case on the downside: the underlying is floored at
    0, so every combination's worst case at spot=0 is finite and is always
    included as the first sampled point.
    """
    if not legs:
        raise OptionsError("At least one leg is required.")
    if points < 3:
        raise OptionsError("points must be at least 3.")

    strikes = [leg.strike for leg in legs]
    if spot_high is None:
        center = current_spot or (sum(strikes) / len(strikes))
        spot_high = max(max(strikes) * 1.5, center * 1.5, 1.0)
    if spot_high <= max(strikes):
        raise OptionsError(
            "spot_high must extend past every leg's strike, or the unbounded "
            "profit/loss detection at the edge of the range is unreliable."
        )

    step = spot_high / (points - 1)
    curve = []
    for i in range(points):
        spot = step * i
        pnl = sum(leg.payoff_at(spot) for leg in legs)
        curve.append({"spot": round(spot, 4), "pnl": round(pnl, 4)})

    pnls = [pt["pnl"] for pt in curve]
    unbounded_profit = curve[-1]["pnl"] > curve[-2]["pnl"] + 1e-6
    unbounded_loss = curve[-1]["pnl"] < curve[-2]["pnl"] - 1e-6

    breakevens: list[float] = []
    for i in range(1, len(curve)):
        p0, p1 = curve[i - 1]["pnl"], curve[i]["pnl"]
        s0, s1 = curve[i - 1]["spot"], curve[i]["spot"]
        if p0 == 0 and (not breakevens or breakevens[-1] != s0):
            breakevens.append(s0)
        elif (p0 < 0 < p1) or (p0 > 0 > p1):
            frac = -p0 / (p1 - p0)
            breakevens.append(round(s0 + frac * (s1 - s0), 4))

    return {
        "curve": curve,
        "max_profit": None if unbounded_profit else round(max(pnls), 4),
        "max_loss": None if unbounded_loss else round(min(pnls), 4),
        "breakevens": breakevens,
        "net_premium": round(sum(leg.quantity * leg.premium for leg in legs), 4),
    }


# --- named multi-leg strategies -----------------------------------------------
#
# Each builder prices its own legs via Black-Scholes given a spot/volatility/
# rate/expiry, rather than requiring the caller to already know each leg's
# market price — real math, seeded with either a caller-supplied volatility
# or the underlying's own historical volatility (see `historical_volatility`).

def _leg_price(spot, strike, t, vol, option_type, r, q) -> float:
    return black_scholes(spot, strike, t, vol, option_type, r, q).fair_value


def bull_call_spread(spot, lower_strike, upper_strike, t, vol, r=0.05, q=0.0) -> list[Leg]:
    if lower_strike >= upper_strike:
        raise OptionsError("lower_strike must be below upper_strike.")
    return [
        Leg(OptionType.CALL, lower_strike, _leg_price(spot, lower_strike, t, vol, OptionType.CALL, r, q), 1),
        Leg(OptionType.CALL, upper_strike, _leg_price(spot, upper_strike, t, vol, OptionType.CALL, r, q), -1),
    ]


def bear_put_spread(spot, lower_strike, upper_strike, t, vol, r=0.05, q=0.0) -> list[Leg]:
    if lower_strike >= upper_strike:
        raise OptionsError("lower_strike must be below upper_strike.")
    return [
        Leg(OptionType.PUT, upper_strike, _leg_price(spot, upper_strike, t, vol, OptionType.PUT, r, q), 1),
        Leg(OptionType.PUT, lower_strike, _leg_price(spot, lower_strike, t, vol, OptionType.PUT, r, q), -1),
    ]


def long_straddle(spot, strike, t, vol, r=0.05, q=0.0) -> list[Leg]:
    return [
        Leg(OptionType.CALL, strike, _leg_price(spot, strike, t, vol, OptionType.CALL, r, q), 1),
        Leg(OptionType.PUT, strike, _leg_price(spot, strike, t, vol, OptionType.PUT, r, q), 1),
    ]


def long_strangle(spot, put_strike, call_strike, t, vol, r=0.05, q=0.0) -> list[Leg]:
    if put_strike >= call_strike:
        raise OptionsError("put_strike must be below call_strike.")
    return [
        Leg(OptionType.PUT, put_strike, _leg_price(spot, put_strike, t, vol, OptionType.PUT, r, q), 1),
        Leg(OptionType.CALL, call_strike, _leg_price(spot, call_strike, t, vol, OptionType.CALL, r, q), 1),
    ]


def iron_condor(spot, put_long, put_short, call_short, call_long, t, vol, r=0.05, q=0.0) -> list[Leg]:
    if not (put_long < put_short < call_short < call_long):
        raise OptionsError("Strikes must satisfy put_long < put_short < call_short < call_long.")
    return [
        Leg(OptionType.PUT, put_long, _leg_price(spot, put_long, t, vol, OptionType.PUT, r, q), 1),
        Leg(OptionType.PUT, put_short, _leg_price(spot, put_short, t, vol, OptionType.PUT, r, q), -1),
        Leg(OptionType.CALL, call_short, _leg_price(spot, call_short, t, vol, OptionType.CALL, r, q), -1),
        Leg(OptionType.CALL, call_long, _leg_price(spot, call_long, t, vol, OptionType.CALL, r, q), 1),
    ]


def call_butterfly(spot, lower_strike, middle_strike, upper_strike, t, vol, r=0.05, q=0.0) -> list[Leg]:
    if not (lower_strike < middle_strike < upper_strike):
        raise OptionsError("Strikes must satisfy lower_strike < middle_strike < upper_strike.")
    if abs((middle_strike - lower_strike) - (upper_strike - middle_strike)) > 1e-6:
        raise OptionsError("A butterfly's wings must be equal width around the middle strike.")
    return [
        Leg(OptionType.CALL, lower_strike, _leg_price(spot, lower_strike, t, vol, OptionType.CALL, r, q), 1),
        Leg(OptionType.CALL, middle_strike, _leg_price(spot, middle_strike, t, vol, OptionType.CALL, r, q), -2),
        Leg(OptionType.CALL, upper_strike, _leg_price(spot, upper_strike, t, vol, OptionType.CALL, r, q), 1),
    ]


STRATEGIES = {
    "bull_call_spread": bull_call_spread,
    "bear_put_spread": bear_put_spread,
    "long_straddle": long_straddle,
    "long_strangle": long_strangle,
    "iron_condor": iron_condor,
    "call_butterfly": call_butterfly,
}
