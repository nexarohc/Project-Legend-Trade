"""Analysis API: the full 20-section report, narration, and research mode."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.dependencies import current_user
from app.ratelimit import compute_limiter
from database.db import get_session
from database.models import PaperPosition, User
from trading import learning, narrative
from trading.analysis import analyze_symbol
from trading.models import MarketDataError, Timeframe

logger = logging.getLogger("legend.analysis")
router = APIRouter(prefix="/analysis", tags=["analysis"])


class AnalyzeRequest(BaseModel):
    symbol: str = Field(..., min_length=1)
    timeframe: str = "1h"
    provider: str | None = None
    bars: int = Field(500, ge=100, le=5000)
    include_mtf: bool = True
    include_chart_data: bool = True
    account_balance: float = Field(10_000.0, gt=0)
    risk_percent: float = Field(1.0, gt=0, le=100)
    save: bool = True


class NarrateRequest(AnalyzeRequest):
    audience: str = "intermediate"   # beginner | intermediate | professional
    model: str | None = None


class ResearchRequest(BaseModel):
    question: str = Field(..., min_length=3)
    symbol: str | None = None
    timeframe: str = "1h"
    provider: str | None = None
    audience: str = "intermediate"
    model: str | None = None


class FeedbackRequest(BaseModel):
    analysis_id: int | None = None
    rating: str = Field(..., pattern="^(useful|wrong|unclear)$")
    comment: str = ""


# Cap on how many other open positions get pulled into the correlation check.
# Each one is a separate market-data fetch, so this bounds the request's
# latency and provider load rather than sizing to the largest book someone
# might have open.
MAX_CORRELATION_POSITIONS = 5


def _open_position_symbols(session: Session, user: User, exclude: str) -> list[str]:
    rows = (
        session.query(PaperPosition.symbol)
        .filter(PaperPosition.user_id == user.id, PaperPosition.status == "open")
        .filter(PaperPosition.symbol != exclude)
        .order_by(PaperPosition.opened_at.desc())
        .distinct()
        .limit(MAX_CORRELATION_POSITIONS)
        .all()
    )
    return [symbol for (symbol,) in rows]


def _run(request: AnalyzeRequest, session: Session | None = None, user: User | None = None):
    try:
        timeframe = Timeframe.parse(request.timeframe)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    open_positions = None
    if session is not None and user is not None:
        try:
            open_positions = _open_position_symbols(session, user, request.symbol)
        except Exception as exc:  # noqa: BLE001 - correlation is an enhancement, not a requirement
            logger.warning("could not load open positions for correlation: %s", exc)

    try:
        return analyze_symbol(
            symbol=request.symbol,
            timeframe=timeframe,
            provider=request.provider,
            bars=request.bars,
            include_mtf=request.include_mtf,
            account_balance=request.account_balance,
            risk_percent=request.risk_percent,
            include_chart_data=request.include_chart_data,
            open_position_symbols=open_positions,
        )
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("")
def run_analysis(request: AnalyzeRequest, http_request: Request,
                 session: Session = Depends(get_session),
                 user: User = Depends(current_user)):
    """Run the complete analysis pipeline for a symbol.

    Returns all 20 sections. Context, structure and evidence are computed
    before any probability is produced — that ordering is enforced by the
    pipeline, not by the caller.
    """
    compute_limiter.enforce(http_request, key=str(user.id))
    result = _run(request, session, user)
    payload = result.to_dict()

    if request.save:
        try:
            payload["analysis_id"] = learning.record_analysis(session, payload, user.id)
        except Exception as exc:  # noqa: BLE001 - never fail the analysis over logging
            logger.warning("could not store analysis: %s", exc)
            session.rollback()

    return payload


@router.post("/narrate")
def narrate(request: NarrateRequest, http_request: Request,
            session: Session = Depends(get_session),
            user: User = Depends(current_user)):
    """Analysis plus a prose walkthrough at the requested expertise level."""
    compute_limiter.enforce(http_request, key=str(user.id))
    result = _run(request, session, user)
    payload = result.to_dict()

    if request.save:
        try:
            payload["analysis_id"] = learning.record_analysis(session, payload, user.id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not store analysis: %s", exc)
            session.rollback()

    payload["narrative"] = narrative.narrate_analysis(payload, request.audience, request.model)
    return payload


@router.post("/research")
def research(request: ResearchRequest, http_request: Request,
             user: User = Depends(current_user)):
    """Answer a trading question, grounded in a live analysis when a symbol is given.

    Concept questions are served from the built-in glossary and work with no
    API key configured.
    """
    analysis_payload = None
    if request.symbol:
        analysis_request = AnalyzeRequest(
            symbol=request.symbol,
            timeframe=request.timeframe,
            provider=request.provider,
            include_chart_data=False,
            save=False,
        )
        analysis_payload = _run(analysis_request).to_dict()

    return narrative.research(
        request.question, analysis_payload, request.audience, request.model
    )


@router.get("/concepts")
def concepts():
    """Every concept the built-in glossary covers."""
    return {
        "concepts": [
            {"key": key, "title": entry["title"], "short": entry["short"]}
            for key, entry in sorted(narrative.CONCEPTS.items())
        ]
    }


@router.get("/concepts/{concept}")
def concept(concept: str, audience: str = Query("intermediate")):
    result = narrative.explain_concept(concept, audience)
    if not result.get("found"):
        raise HTTPException(status_code=404, detail=result["message"])
    return result


@router.post("/feedback")
def feedback(request: FeedbackRequest, session: Session = Depends(get_session),
             user: User = Depends(current_user)):
    """Record your own verdict on an analysis; feeds the learning engine."""
    feedback_id = learning.record_feedback(
        session, request.analysis_id, request.rating, request.comment, user.id
    )
    return {"id": feedback_id, "recorded": True}


@router.get("/calibration")
def calibration(symbol: str | None = Query(None), session: Session = Depends(get_session),
                user: User = Depends(current_user)):
    """How accurate past calls have actually been, once graded against price."""
    return learning.calibration_report(session, symbol, user.id)


@router.post("/score-pending")
def score_pending(limit: int = Query(50, ge=1, le=200), session: Session = Depends(get_session),
                  user: User = Depends(current_user)):
    """Grade stored analyses whose forward horizon has now elapsed."""
    return learning.score_pending_analyses(session, limit=limit, user_id=user.id)


@router.get("/lessons")
def lessons(session: Session = Depends(get_session), user: User = Depends(current_user)):
    """Patterns the platform has measured across stored analyses and backtests."""
    return learning.lessons_learned(session, user.id)
