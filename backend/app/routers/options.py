"""Options pricing API: Black-Scholes / binomial fair value and Greeks,
implied volatility, historical volatility, and multi-leg payoff diagrams.

There is deliberately no endpoint here for a live option chain (strikes,
bid/ask, open interest). None of the market-data vendors this platform can
reach for free serve that — see `docs/TRADING_TERMINAL.md`'s "Honest
limitations" — and faking it would misrepresent real market data, which
this project consistently refuses to do (see decision 27 in
`docs/PROJECT_STATE.md`). Every endpoint here prices a contract you specify;
volatility can be supplied explicitly or estimated from the underlying's own
real historical prices via `symbol`.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.dependencies import current_user
from database.models import User
from trading import options
from trading.market import market_service
from trading.models import MarketDataError, Timeframe

logger = logging.getLogger("legend.options")
router = APIRouter(prefix="/options", tags=["options"])


def _timeframe(raw: str) -> Timeframe:
    try:
        return Timeframe.parse(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _option_type(raw: str) -> options.OptionType:
    try:
        return options.OptionType(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="option_type must be 'call' or 'put'.") from None


def _exercise_style(raw: str) -> options.ExerciseStyle:
    try:
        return options.ExerciseStyle(raw)
    except ValueError:
        raise HTTPException(
            status_code=400, detail="exercise_style must be 'european' or 'american'."
        ) from None


def _resolve_spot(spot: float | None, symbol: str | None) -> float:
    if spot is not None:
        return spot
    if not symbol:
        raise HTTPException(status_code=400, detail="Provide either spot or symbol.")
    try:
        return market_service.quote(symbol).price
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _resolve_volatility(
    volatility: float | None, symbol: str | None, timeframe: str, window: int,
) -> tuple[float, str]:
    if volatility is not None:
        return volatility, "explicit"
    if not symbol:
        raise HTTPException(
            status_code=400,
            detail="volatility is required when no symbol is given to estimate it from.",
        )
    try:
        series = market_service.candles(symbol, _timeframe(timeframe), window)
        vol = options.historical_volatility(series)
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except options.OptionsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return vol, f"historical ({window} bars, {timeframe})"


class PriceRequest(BaseModel):
    spot: float | None = Field(None, gt=0)
    symbol: str | None = None
    strike: float = Field(..., gt=0)
    days_to_expiry: float = Field(..., gt=0)
    option_type: str = Field(..., pattern="^(call|put)$")
    exercise_style: str = Field("european", pattern="^(european|american)$")
    volatility: float | None = Field(None, gt=0)
    volatility_timeframe: str = "1d"
    volatility_window: int = Field(252, ge=3, le=5000)
    risk_free_rate: float = Field(0.05, ge=-0.05, le=1.0)
    dividend_yield: float = Field(0.0, ge=0.0, le=1.0)
    steps: int = Field(200, ge=10, le=2000)


@router.post("/price")
def price(request: PriceRequest, user: User = Depends(current_user)):
    """Fair value and Greeks for a single contract. European contracts use
    exact closed-form Black-Scholes; American contracts use a CRR binomial
    tree (needed for early exercise) with finite-difference Greeks."""
    option_type = _option_type(request.option_type)
    style = _exercise_style(request.exercise_style)
    spot = _resolve_spot(request.spot, request.symbol)
    volatility, vol_source = _resolve_volatility(
        request.volatility, request.symbol, request.volatility_timeframe, request.volatility_window
    )
    t = request.days_to_expiry / 365.0

    try:
        if style is options.ExerciseStyle.AMERICAN:
            result = options.binomial_crr(
                spot, request.strike, t, volatility, option_type, style,
                request.risk_free_rate, request.dividend_yield, request.steps,
            )
        else:
            result = options.black_scholes(
                spot, request.strike, t, volatility, option_type,
                request.risk_free_rate, request.dividend_yield,
            )
    except options.OptionsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    payload = result.to_dict()
    payload["volatility_source"] = vol_source
    return payload


class ImpliedVolatilityRequest(BaseModel):
    market_price: float = Field(..., gt=0)
    spot: float | None = Field(None, gt=0)
    symbol: str | None = None
    strike: float = Field(..., gt=0)
    days_to_expiry: float = Field(..., gt=0)
    option_type: str = Field(..., pattern="^(call|put)$")
    risk_free_rate: float = Field(0.05, ge=-0.05, le=1.0)
    dividend_yield: float = Field(0.0, ge=0.0, le=1.0)


@router.post("/implied-volatility")
def implied_volatility_endpoint(request: ImpliedVolatilityRequest, user: User = Depends(current_user)):
    """Solve for the volatility a real, observed option price implies —
    useful for comparing against the underlying's historical volatility."""
    option_type = _option_type(request.option_type)
    spot = _resolve_spot(request.spot, request.symbol)
    t = request.days_to_expiry / 365.0

    try:
        iv = options.implied_volatility(
            request.market_price, spot, request.strike, t, option_type,
            request.risk_free_rate, request.dividend_yield,
        )
    except options.OptionsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"implied_volatility": round(iv, 6), "spot": spot}


@router.get("/historical-volatility")
def historical_volatility_endpoint(
    symbol: str = Query(..., min_length=1),
    timeframe: str = Query("1d"),
    window: int = Query(252, ge=3, le=5000),
):
    """Annualized volatility from the symbol's own real price history — the
    free, honest stand-in for implied volatility when no option chain is
    available. See `trading/options.py`'s module docstring for why there is
    no live-chain endpoint here."""
    try:
        series = market_service.candles(symbol, _timeframe(timeframe), window)
        vol = options.historical_volatility(series)
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except options.OptionsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "symbol": series.symbol, "timeframe": timeframe, "bars_used": len(series.candles),
        "annualized_volatility": round(vol, 6),
    }


class LegInput(BaseModel):
    option_type: str = Field(..., pattern="^(call|put)$")
    strike: float = Field(..., gt=0)
    premium: float = Field(..., ge=0)
    quantity: int = Field(..., description="Positive = long (bought), negative = short (sold)")


class PayoffRequest(BaseModel):
    legs: list[LegInput] | None = None
    strategy: str | None = None
    strategy_params: dict | None = None
    spot: float | None = Field(None, gt=0)
    symbol: str | None = None
    spot_high: float | None = Field(None, gt=0)
    points: int = Field(61, ge=3, le=500)


@router.get("/strategies")
def strategies():
    """Named multi-leg strategies `/options/payoff` can build and price for
    you, plus the parameters each one needs."""
    return {
        "strategies": {
            "bull_call_spread": ["lower_strike", "upper_strike", "days_to_expiry", "volatility (optional)"],
            "bear_put_spread": ["lower_strike", "upper_strike", "days_to_expiry", "volatility (optional)"],
            "long_straddle": ["strike", "days_to_expiry", "volatility (optional)"],
            "long_strangle": ["put_strike", "call_strike", "days_to_expiry", "volatility (optional)"],
            "iron_condor": ["put_long", "put_short", "call_short", "call_long", "days_to_expiry", "volatility (optional)"],
            "call_butterfly": ["lower_strike", "middle_strike", "upper_strike", "days_to_expiry", "volatility (optional)"],
        }
    }


@router.post("/payoff")
def payoff(request: PayoffRequest, user: User = Depends(current_user)):
    """P/L at expiration across a range of underlying prices, for either
    explicit legs or a named strategy (auto-priced via Black-Scholes)."""
    if bool(request.legs) == bool(request.strategy):
        raise HTTPException(status_code=400, detail="Provide exactly one of legs or strategy.")

    if request.legs:
        legs = [
            options.Leg(_option_type(leg.option_type), leg.strike, leg.premium, leg.quantity)
            for leg in request.legs
        ]
        current_spot = request.spot
        if current_spot is None and request.symbol:
            current_spot = _resolve_spot(None, request.symbol)
    else:
        builder = options.STRATEGIES.get(request.strategy)
        if builder is None:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown strategy {request.strategy!r}. See GET /options/strategies.",
            )
        params = dict(request.strategy_params or {})
        days_to_expiry = params.pop("days_to_expiry", None)
        if days_to_expiry is None:
            raise HTTPException(status_code=400, detail="strategy_params.days_to_expiry is required.")
        volatility_param = params.pop("volatility", None)

        current_spot = _resolve_spot(request.spot, request.symbol)
        volatility, _ = _resolve_volatility(volatility_param, request.symbol, "1d", 252)
        t = days_to_expiry / 365.0

        try:
            legs = builder(spot=current_spot, t=t, vol=volatility, **params)
        except TypeError as exc:
            raise HTTPException(status_code=400, detail=f"Bad strategy_params: {exc}") from exc
        except options.OptionsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        result = options.payoff_diagram(legs, spot_high=request.spot_high, points=request.points,
                                         current_spot=current_spot)
    except options.OptionsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    result["legs"] = [
        {"option_type": leg.option_type.value, "strike": leg.strike,
         "premium": round(leg.premium, 4), "quantity": leg.quantity}
        for leg in legs
    ]
    return result
