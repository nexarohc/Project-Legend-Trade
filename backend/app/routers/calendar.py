"""Economic calendar API: scheduled data releases from FRED.

No auth required — this is reference data, same as `/market/*`. Importance is
a hand-maintained heuristic, not something FRED provides; see the module
docstring in `trading/econcalendar.py` before trusting it as authoritative.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, HTTPException, Query, Request

from app.ratelimit import compute_limiter
from trading.econcalendar import calendar_service
from trading.models import MarketDataError

router = APIRouter(prefix="/calendar", tags=["calendar"])


@router.get("")
def events(
    http_request: Request,
    start: str = Query(..., description="YYYY-MM-DD, inclusive"),
    end: str = Query(..., description="YYYY-MM-DD, inclusive"),
    importance: str | None = Query(None, pattern="^(high|medium|low)$"),
):
    """Scheduled economic releases between `start` and `end`."""
    compute_limiter.enforce(http_request)

    try:
        dt.date.fromisoformat(start)
        dt.date.fromisoformat(end)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid date: {exc}") from exc
    if end < start:
        raise HTTPException(status_code=400, detail="'end' must not be before 'start'.")

    try:
        found = calendar_service.events(start, end)
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if importance:
        found = [e for e in found if e.importance == importance]

    return {"events": [e.to_dict() for e in found], "count": len(found)}


@router.get("/upcoming")
def upcoming(http_request: Request, days: int = Query(14, ge=1, le=90)):
    """Convenience endpoint: scheduled releases from today through `days` ahead."""
    compute_limiter.enforce(http_request)

    try:
        found = calendar_service.upcoming(days=days)
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {"events": [e.to_dict() for e in found], "count": len(found)}
