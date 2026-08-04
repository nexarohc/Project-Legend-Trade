"""Strategy API: natural-language build, backtest, validation, audit, Pine export."""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.dependencies import current_user
from app.ratelimit import compute_limiter
from database.db import get_session
from database.models import StrategyRecord, User
from trading import learning, narrative
from trading.market import market_service
from trading.models import MarketDataError, Timeframe
from trading.pine import PineGenerationError, generate
from trading.strategy import StrategySpec, parse, run_full_study
from trading.strategy.features import UnknownFeature

logger = logging.getLogger("legend.strategy")
router = APIRouter(prefix="/strategy", tags=["strategy"])


class BuildRequest(BaseModel):
    description: str = Field(..., min_length=3)
    symbol: str = "BTCUSDT"
    timeframe: str | None = None
    use_model_fallback: bool = Field(
        True,
        description="If the built-in parser recognises nothing, ask the LLM to draft the spec.",
    )


class StudyRequest(BaseModel):
    spec: dict | None = None
    description: str | None = None
    symbol: str | None = None
    timeframe: str | None = None
    provider: str | None = None
    bars: int = Field(2000, ge=200, le=5000)
    monte_carlo_runs: int = Field(2000, ge=100, le=20_000)
    include_trades: bool = True
    run_validation: bool = True
    save: bool = True


class PineRequest(BaseModel):
    spec: dict | None = None
    description: str | None = None
    symbol: str = "BTCUSDT"
    timeframe: str | None = None
    kind: str = Field("strategy", pattern="^(strategy|indicator)$")


class SaveRequest(BaseModel):
    spec: dict
    name: str | None = None


def _spec_from(spec_dict: dict | None, description: str | None,
               symbol: str | None, timeframe: str | None) -> StrategySpec:
    """Build a spec from an explicit dict or from a description."""
    if spec_dict:
        try:
            return StrategySpec.from_dict(spec_dict)
        except Exception as exc:  # noqa: BLE001 - report the shape problem plainly
            raise HTTPException(status_code=400, detail=f"Invalid strategy spec: {exc}") from exc

    if not description:
        raise HTTPException(
            status_code=400,
            detail="Provide either a strategy 'spec' or a natural-language 'description'.",
        )

    tf = None
    if timeframe:
        try:
            tf = Timeframe.parse(timeframe)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return parse(description, symbol or "BTCUSDT", tf)


@router.post("/build")
def build(request: BuildRequest):
    """Turn a plain-English description into a full strategy specification.

    The response includes exactly what the parser understood and what it
    ignored, so nothing silently makes it into (or out of) the strategy.
    """
    timeframe = None
    if request.timeframe:
        try:
            timeframe = Timeframe.parse(request.timeframe)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    spec = parse(request.description, request.symbol, timeframe)

    # If the deterministic parser fell back to its default, a model may do better.
    used_model = False
    fell_back = any("No recognised setup keyword" in note for note in spec.parse_notes)
    if fell_back and request.use_model_fallback:
        proposed = narrative.propose_strategy(request.description)
        if proposed:
            try:
                proposed.setdefault("symbol", request.symbol)
                proposed.setdefault("timeframe", (timeframe or spec.timeframe).value)
                proposed.setdefault("source_text", request.description)
                model_spec = StrategySpec.from_dict(proposed)
                if not model_spec.validate():
                    model_spec.parse_notes = [
                        "The built-in parser did not recognise this description, so a language "
                        "model drafted the rules. Review them carefully before trusting the "
                        "backtest — they are the model's interpretation of your words."
                    ]
                    model_spec.description = request.description
                    spec = model_spec
                    used_model = True
            except Exception as exc:  # noqa: BLE001 - keep the deterministic spec on failure
                logger.info("model strategy proposal rejected: %s", exc)

    problems = spec.validate()
    return {
        "spec": spec.to_dict(),
        "specification": spec.describe(),
        "valid": not problems,
        "problems": problems,
        "parsed_by": "model" if used_model else "rule_parser",
    }


@router.post("/study")
def study(request: StudyRequest, http_request: Request,
          session: Session = Depends(get_session),
          user: User = Depends(current_user)):
    """Backtest, validate and audit a strategy in one call.

    Returns the full picture: specification, backtest, metrics, Monte Carlo,
    walk-forward, regime test, parameter stability and the audit verdict.
    """
    compute_limiter.enforce(http_request, key=str(user.id))
    spec = _spec_from(request.spec, request.description, request.symbol, request.timeframe)

    symbol = request.symbol or spec.symbol
    timeframe = spec.timeframe
    if request.timeframe:
        try:
            timeframe = Timeframe.parse(request.timeframe)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        series = market_service.candles(symbol, timeframe, request.bars, request.provider)
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    try:
        result = run_full_study(
            spec, series,
            monte_carlo_runs=request.monte_carlo_runs,
            include_trades=request.include_trades,
            run_validation=request.run_validation,
        )
    except UnknownFeature as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if request.save:
        try:
            result["backtest_id"] = learning.record_backtest(session, result, user_id=user.id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not store backtest: %s", exc)
            session.rollback()

    return result


@router.post("/pine")
def pine(request: PineRequest):
    """Generate Pine Script v6 from a specification or description."""
    spec = _spec_from(request.spec, request.description, request.symbol, request.timeframe)
    try:
        return generate(spec, request.kind)
    except PineGenerationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/save")
def save(request: SaveRequest, session: Session = Depends(get_session),
         user: User = Depends(current_user)):
    """Persist a strategy specification."""
    try:
        spec = StrategySpec.from_dict(request.spec)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Invalid strategy spec: {exc}") from exc

    record = StrategyRecord(
        user_id=user.id,
        name=request.name or spec.name,
        symbol=spec.symbol,
        timeframe=spec.timeframe.value,
        source_text=spec.source_text,
        spec_json=json.dumps(spec.to_dict()),
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return {"id": record.id, "name": record.name, "saved": True}


@router.get("/saved")
def saved(session: Session = Depends(get_session), user: User = Depends(current_user),
          limit: int = Query(50, ge=1, le=200)):
    records = (
        session.query(StrategyRecord)
        .filter(StrategyRecord.user_id == user.id)
        .order_by(StrategyRecord.created_at.desc())
        .limit(limit)
        .all()
    )
    return {
        "strategies": [
            {
                "id": r.id,
                "name": r.name,
                "symbol": r.symbol,
                "timeframe": r.timeframe,
                "source_text": r.source_text,
                "last_verdict": r.last_verdict,
                "last_score": r.last_score,
                "last_net_profit_percent": r.last_net_profit_percent,
                "last_trade_count": r.last_trade_count,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in records
        ]
    }


@router.get("/saved/{strategy_id}")
def saved_detail(strategy_id: int, session: Session = Depends(get_session),
                 user: User = Depends(current_user)):
    record = session.get(StrategyRecord, strategy_id)
    if not record or record.user_id != user.id:
        raise HTTPException(status_code=404, detail="Strategy not found.")
    return {"id": record.id, "name": record.name, "spec": json.loads(record.spec_json)}


@router.delete("/saved/{strategy_id}")
def delete_saved(strategy_id: int, session: Session = Depends(get_session),
                 user: User = Depends(current_user)):
    record = session.get(StrategyRecord, strategy_id)
    if not record or record.user_id != user.id:
        raise HTTPException(status_code=404, detail="Strategy not found.")
    session.delete(record)
    session.commit()
    return {"deleted": True}


@router.get("/leaderboard")
def leaderboard(session: Session = Depends(get_session), user: User = Depends(current_user),
                limit: int = Query(20, ge=1, le=100)):
    """Best-audited strategies tested so far."""
    return {"leaderboard": learning.strategy_leaderboard(session, limit, user.id)}


@router.get("/features")
def features():
    """Every feature name usable in a strategy rule, for the rule builder UI."""
    return {
        "price": ["close", "open", "high", "low", "volume", "hl2", "hlc3"],
        "moving_averages": ["ema(n)", "sma(n)", "rma(n)", "vwap"],
        "oscillators": [
            "rsi(n)", "cci(n)", "mfi(n)", "stoch_k", "stoch_d",
            "stoch_rsi_k", "stoch_rsi_d", "macd", "macd_signal", "macd_hist",
        ],
        "trend": ["adx(n)", "plus_di", "minus_di", "supertrend", "supertrend_dir"],
        "volatility": ["atr(n)", "bb_upper(n)", "bb_middle(n)", "bb_lower(n)"],
        "breakout": ["highest(n)", "lowest(n)"],
        "volume": ["volume_sma(n)", "volume_ratio(n)", "obv"],
        "candle": ["body", "range", "body_ratio"],
        "operators": [">", "<", ">=", "<=", "crosses_above", "crosses_below", "rising", "falling"],
        "notes": [
            "highest(n) and lowest(n) exclude the current bar, so 'close > highest(20)' is a "
            "genuine breakout condition.",
            "An indicator that has not finished warming up evaluates as false, never as zero.",
        ],
    }
