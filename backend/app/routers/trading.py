"""Trading API: watchlist, alerts, paper positions, and the TradingView webhook.

Paper trading is deliberately the only execution path in the platform. Nothing
here can place a live order — going live is a decision that belongs with a
broker integration the user configures themselves, after a strategy has passed
the audit and been paper traded.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.dependencies import current_user
from app.ratelimit import webhook_limiter
from database.db import get_session
from database.models import PaperPosition, PriceAlert, TradingWebhook, User, WatchlistItem
from trading.market import market_service
from trading.models import MarketDataError, Timeframe
from trading.risk import position_size

logger = logging.getLogger("legend.trading")
router = APIRouter(prefix="/trading", tags=["trading"])


# ---------------------------------------------------------------------------
# Watchlist
# ---------------------------------------------------------------------------

class WatchlistRequest(BaseModel):
    symbol: str = Field(..., min_length=1)
    provider: str | None = None
    asset_class: str = "crypto"


@router.get("/watchlist")
def watchlist(session: Session = Depends(get_session),
    user: User = Depends(current_user),
    with_quotes: bool = Query(True)):
    """The watchlist, with a live quote per symbol when requested."""
    items = (
        session.query(WatchlistItem)
        .filter(WatchlistItem.user_id == user.id)
        .order_by(WatchlistItem.sort_order, WatchlistItem.id)
        .all()
    )
    out = []
    for item in items:
        entry = {
            "id": item.id,
            "symbol": item.symbol,
            "provider": item.provider or None,
            "asset_class": item.asset_class,
        }
        if with_quotes:
            try:
                entry["quote"] = market_service.quote(item.symbol, item.provider or None).to_dict()
            except MarketDataError as exc:
                # One unavailable symbol must not blank the whole panel.
                entry["error"] = str(exc)
        out.append(entry)
    return {"items": out}


@router.post("/watchlist")
def add_to_watchlist(request: WatchlistRequest, session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    symbol = request.symbol.upper().strip()
    existing = (
        session.query(WatchlistItem)
        .filter(WatchlistItem.user_id == user.id, WatchlistItem.symbol == symbol)
        .first()
    )
    if existing:
        return {"id": existing.id, "symbol": symbol, "already_present": True}

    highest = session.query(WatchlistItem).filter(WatchlistItem.user_id == user.id).count()
    item = WatchlistItem(
        user_id=user.id,
        symbol=symbol,
        provider=request.provider or "",
        asset_class=request.asset_class,
        sort_order=highest,
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return {"id": item.id, "symbol": item.symbol, "added": True}


@router.delete("/watchlist/{item_id}")
def remove_from_watchlist(item_id: int, session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    item = session.get(WatchlistItem, item_id)
    # Ownership is checked as part of the lookup: a row belonging to someone else
    # must be indistinguishable from one that does not exist.
    if not item or item.user_id != user.id:
        raise HTTPException(status_code=404, detail="Watchlist item not found.")
    session.delete(item)
    session.commit()
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

class AlertRequest(BaseModel):
    symbol: str = Field(..., min_length=1)
    timeframe: str = "1h"
    condition: str = Field(..., pattern="^(above|below|crosses)$")
    price: float = Field(..., gt=0)
    note: str = ""


@router.get("/alerts")
def alerts(session: Session = Depends(get_session),
    user: User = Depends(current_user),
    active_only: bool = Query(False)):
    query = session.query(PriceAlert).filter(PriceAlert.user_id == user.id)
    if active_only:
        query = query.filter(PriceAlert.active.is_(True), PriceAlert.triggered.is_(False))
    records = query.order_by(PriceAlert.created_at.desc()).all()
    return {
        "alerts": [
            {
                "id": a.id,
                "symbol": a.symbol,
                "timeframe": a.timeframe,
                "condition": a.condition,
                "price": a.price,
                "note": a.note,
                "active": a.active,
                "triggered": a.triggered,
                "triggered_at": a.triggered_at.isoformat() if a.triggered_at else None,
                "triggered_price": a.triggered_price,
            }
            for a in records
        ]
    }


@router.post("/alerts")
def create_alert(request: AlertRequest, session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    alert = PriceAlert(
        user_id=user.id,
        symbol=request.symbol.upper(),
        timeframe=request.timeframe,
        condition=request.condition,
        price=request.price,
        note=request.note,
    )
    session.add(alert)
    session.commit()
    session.refresh(alert)
    return {"id": alert.id, "created": True}


@router.delete("/alerts/{alert_id}")
def delete_alert(alert_id: int, session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    alert = session.get(PriceAlert, alert_id)
    if not alert or alert.user_id != user.id:
        raise HTTPException(status_code=404, detail="Alert not found.")
    session.delete(alert)
    session.commit()
    return {"deleted": True}


@router.post("/alerts/check")
def check_alerts(session: Session = Depends(get_session), user: User = Depends(current_user)):
    """Evaluate every active alert against the current price.

    Called by the UI on a timer. Kept as an explicit endpoint rather than a
    background task so the desktop app has no hidden polling loop running when
    the window is closed.
    """
    active = (
        session.query(PriceAlert)
        .filter(
            PriceAlert.user_id == user.id,
            PriceAlert.active.is_(True),
            PriceAlert.triggered.is_(False),
        )
        .all()
    )
    if not active:
        return {"checked": 0, "triggered": []}

    prices: dict[str, float] = {}
    triggered = []

    for alert in active:
        if alert.symbol not in prices:
            try:
                prices[alert.symbol] = market_service.quote(alert.symbol).price
            except MarketDataError as exc:
                logger.info("alert check failed for %s: %s", alert.symbol, exc)
                continue
        price = prices[alert.symbol]

        hit = (
            (alert.condition == "above" and price >= alert.price)
            or (alert.condition == "below" and price <= alert.price)
            # "crosses" fires from either side once price reaches the level.
            or (alert.condition == "crosses" and abs(price - alert.price) / alert.price < 0.001)
        )
        if hit:
            alert.triggered = True
            alert.triggered_at = dt.datetime.utcnow()
            alert.triggered_price = price
            triggered.append({
                "id": alert.id,
                "symbol": alert.symbol,
                "condition": alert.condition,
                "level": alert.price,
                "price": price,
                "note": alert.note,
            })

    session.commit()
    return {"checked": len(active), "triggered": triggered}


# ---------------------------------------------------------------------------
# Paper trading
# ---------------------------------------------------------------------------

class PaperOpenRequest(BaseModel):
    symbol: str = Field(..., min_length=1)
    direction: str = Field(..., pattern="^(long|short)$")
    entry_price: float | None = Field(None, gt=0, description="Defaults to the live price")
    stop_price: float = Field(..., gt=0)
    target_price: float | None = None
    timeframe: str = "1h"
    account_balance: float = Field(10_000.0, gt=0)
    risk_percent: float = Field(1.0, gt=0, le=100)
    quantity: float | None = Field(None, gt=0, description="Overrides risk-based sizing")
    source: str = "manual"
    notes: str = ""


class PaperCloseRequest(BaseModel):
    exit_price: float | None = Field(None, gt=0, description="Defaults to the live price")
    reason: str = "manual"


@router.get("/paper/positions")
def paper_positions(session: Session = Depends(get_session),
    user: User = Depends(current_user),
    status: str = Query("open")):
    """Paper positions, with live unrealised P&L on the open ones."""
    query = session.query(PaperPosition).filter(PaperPosition.user_id == user.id)
    if status in ("open", "closed"):
        query = query.filter(PaperPosition.status == status)
    records = query.order_by(PaperPosition.opened_at.desc()).all()

    prices: dict[str, float] = {}
    out = []
    for p in records:
        entry = {
            "id": p.id,
            "symbol": p.symbol,
            "timeframe": p.timeframe,
            "direction": p.direction,
            "quantity": p.quantity,
            "entry_price": p.entry_price,
            "stop_price": p.stop_price,
            "target_price": p.target_price,
            "status": p.status,
            "exit_price": p.exit_price,
            "exit_reason": p.exit_reason,
            "realized_pnl": p.realized_pnl,
            "r_multiple": p.r_multiple,
            "source": p.source,
            "notes": p.notes,
            "opened_at": p.opened_at.isoformat() if p.opened_at else None,
            "closed_at": p.closed_at.isoformat() if p.closed_at else None,
        }

        if p.status == "open":
            if p.symbol not in prices:
                try:
                    prices[p.symbol] = market_service.quote(p.symbol).price
                except MarketDataError:
                    prices[p.symbol] = 0.0
            price = prices[p.symbol]
            if price:
                unrealised = (
                    (price - p.entry_price) * p.quantity if p.direction == "long"
                    else (p.entry_price - price) * p.quantity
                )
                risk = abs(p.entry_price - p.stop_price) * p.quantity
                entry["current_price"] = price
                entry["unrealized_pnl"] = round(unrealised, 2)
                entry["unrealized_r"] = round(unrealised / risk, 2) if risk else None
        out.append(entry)

    return {"positions": out}


@router.post("/paper/open")
def paper_open(request: PaperOpenRequest, session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """Open a simulated position, sized from the stop distance."""
    try:
        entry_price = request.entry_price or market_service.quote(request.symbol).price
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if request.quantity:
        quantity = request.quantity
        explanation = f"Quantity set explicitly to {quantity:g}."
    else:
        sizing = position_size(
            request.account_balance, request.risk_percent, entry_price, request.stop_price
        )
        if not sizing.get("valid"):
            raise HTTPException(status_code=400, detail=sizing.get("reason", "Cannot size position."))
        quantity = sizing["units"]
        explanation = sizing["explanation"]

    # Reject a stop on the wrong side — it would make the position unlosable.
    if request.direction == "long" and request.stop_price >= entry_price:
        raise HTTPException(
            status_code=400,
            detail=f"A long's stop ({request.stop_price:g}) must sit below entry ({entry_price:g}).",
        )
    if request.direction == "short" and request.stop_price <= entry_price:
        raise HTTPException(
            status_code=400,
            detail=f"A short's stop ({request.stop_price:g}) must sit above entry ({entry_price:g}).",
        )

    position = PaperPosition(
        user_id=user.id,
        symbol=request.symbol.upper(),
        timeframe=request.timeframe,
        direction=request.direction,
        quantity=quantity,
        entry_price=entry_price,
        stop_price=request.stop_price,
        target_price=request.target_price,
        source=request.source,
        notes=request.notes,
    )
    session.add(position)
    session.commit()
    session.refresh(position)

    return {
        "id": position.id,
        "opened": True,
        "entry_price": entry_price,
        "quantity": quantity,
        "sizing_explanation": explanation,
    }


@router.post("/paper/close/{position_id}")
def paper_close(position_id: int, request: PaperCloseRequest,
                session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    position = session.get(PaperPosition, position_id)
    if not position or position.user_id != user.id:
        raise HTTPException(status_code=404, detail="Position not found.")
    if position.status == "closed":
        raise HTTPException(status_code=400, detail="Position is already closed.")

    try:
        exit_price = request.exit_price or market_service.quote(position.symbol).price
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    pnl = (
        (exit_price - position.entry_price) * position.quantity if position.direction == "long"
        else (position.entry_price - exit_price) * position.quantity
    )
    risk = abs(position.entry_price - position.stop_price) * position.quantity

    position.status = "closed"
    position.exit_price = exit_price
    position.exit_reason = request.reason
    position.realized_pnl = pnl
    position.r_multiple = pnl / risk if risk else None
    position.closed_at = dt.datetime.utcnow()
    session.commit()

    return {
        "id": position.id,
        "closed": True,
        "exit_price": exit_price,
        "realized_pnl": round(pnl, 2),
        "r_multiple": round(position.r_multiple, 2) if position.r_multiple else None,
    }


@router.post("/paper/sync")
def paper_sync(session: Session = Depends(get_session), user: User = Depends(current_user)):
    """Close any open paper position whose stop or target has been reached.

    Uses the bar high/low rather than the last price, so a level touched
    between polls is still detected. Stop is checked before target for the same
    reason the backtester does it: without intrabar data, assume the loss.
    """
    open_positions = (
        session.query(PaperPosition)
        .filter(PaperPosition.user_id == user.id, PaperPosition.status == "open")
        .all()
    )
    closed = []

    for position in open_positions:
        try:
            timeframe = Timeframe.parse(position.timeframe or "1h")
            series = market_service.candles(position.symbol, timeframe, 3, force=True)
        except (MarketDataError, ValueError) as exc:
            logger.info("paper sync skipped %s: %s", position.symbol, exc)
            continue

        if not series.candles:
            continue
        bar = series.candles[-1]
        long = position.direction == "long"

        hit_stop = bar.low <= position.stop_price if long else bar.high >= position.stop_price
        hit_target = (
            position.target_price is not None
            and (bar.high >= position.target_price if long else bar.low <= position.target_price)
        )

        exit_price = reason = None
        if hit_stop:
            exit_price, reason = position.stop_price, "stop"
        elif hit_target:
            exit_price, reason = position.target_price, "target"

        if exit_price is None:
            continue

        pnl = (
            (exit_price - position.entry_price) * position.quantity if long
            else (position.entry_price - exit_price) * position.quantity
        )
        risk = abs(position.entry_price - position.stop_price) * position.quantity

        position.status = "closed"
        position.exit_price = exit_price
        position.exit_reason = reason
        position.realized_pnl = pnl
        position.r_multiple = pnl / risk if risk else None
        position.closed_at = dt.datetime.utcnow()

        closed.append({
            "id": position.id,
            "symbol": position.symbol,
            "reason": reason,
            "exit_price": exit_price,
            "realized_pnl": round(pnl, 2),
        })

    session.commit()
    return {"checked": len(open_positions), "closed": closed}


@router.get("/paper/performance")
def paper_performance(session: Session = Depends(get_session), user: User = Depends(current_user)):
    """Aggregate results of paper trading — the record that justifies going live."""
    closed = (
        session.query(PaperPosition)
        .filter(PaperPosition.user_id == user.id, PaperPosition.status == "closed")
        .all()
    )
    if not closed:
        return {
            "available": False,
            "reason": "No paper trades have been closed yet.",
        }

    wins = [p for p in closed if (p.realized_pnl or 0) > 0]
    losses = [p for p in closed if (p.realized_pnl or 0) < 0]
    total_pnl = sum(p.realized_pnl or 0 for p in closed)
    r_values = [p.r_multiple for p in closed if p.r_multiple is not None]

    gross_win = sum(p.realized_pnl for p in wins) if wins else 0.0
    gross_loss = abs(sum(p.realized_pnl for p in losses)) if losses else 0.0

    return {
        "available": True,
        "total_trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(100 * len(wins) / len(closed), 1),
        "total_pnl": round(total_pnl, 2),
        "average_r": round(sum(r_values) / len(r_values), 3) if r_values else None,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
        "best_trade": round(max((p.realized_pnl or 0) for p in closed), 2),
        "worst_trade": round(min((p.realized_pnl or 0) for p in closed), 2),
        "note": (
            "Paper results exclude the psychological difference of real capital and assume your "
            "fills matched the recorded prices."
            if len(closed) >= 20 else
            f"Only {len(closed)} closed paper trades — far too few to judge an edge."
        ),
    }


# ---------------------------------------------------------------------------
# TradingView webhooks
# ---------------------------------------------------------------------------

@router.post("/webhook/tradingview/{webhook_token}")
async def tradingview_webhook(webhook_token: str, request: Request,
                              session: Session = Depends(get_session)):
    """Receive a TradingView alert for the account owning `webhook_token`.

    Accepts either JSON (`{"symbol": "...", "action": "buy", "price": 123}`) or
    the plain-text messages the generated Pine scripts emit
    (`LONG BTCUSD @ 63000 | stop 62000`).

    Authentication is by secret URL rather than a bearer token, because
    TradingView cannot attach custom headers to an alert. The token is compared
    in constant time and identifies which account the alert belongs to. Get the
    URL from `GET /auth/webhook-url`, treat it as a credential, and rotate it if
    it leaks.

    Alerts are recorded but never auto-executed. An inbound webhook is an
    untrusted endpoint whose URL ends up pasted into third-party configuration;
    wiring it straight to order placement is how a leaked URL drains an account.
    """
    webhook_limiter.enforce(request)

    # Look the token up in constant time across candidates, so response timing
    # cannot be used to brute-force a valid token character by character.
    user = None
    for candidate in session.query(User).filter(User.is_active.is_(True)).all():
        if candidate.webhook_token and secrets.compare_digest(
            candidate.webhook_token, webhook_token
        ):
            user = candidate
    if user is None:
        raise HTTPException(status_code=404, detail="Unknown webhook URL.")

    raw = (await request.body()).decode("utf-8", errors="replace")[:10_000]

    symbol = action = None
    price = None

    try:
        payload = json.loads(raw)
        if isinstance(payload, dict):
            symbol = (payload.get("symbol") or payload.get("ticker") or "").upper() or None
            action = (payload.get("action") or payload.get("side") or "").lower() or None
            raw_price = payload.get("price") or payload.get("close")
            price = float(raw_price) if raw_price is not None else None
    except (json.JSONDecodeError, TypeError, ValueError):
        # Fall back to the plain-text format the generated Pine emits.
        match = re.search(
            r"\b(LONG|SHORT|BUY|SELL|CLOSE)\b\s+([A-Z0-9:._-]+)?\s*(?:@\s*([0-9.]+))?",
            raw.upper(),
        )
        if match:
            verb, ticker, raw_price = match.groups()
            action = {"LONG": "buy", "BUY": "buy", "SHORT": "sell",
                      "SELL": "sell", "CLOSE": "close"}.get(verb)
            symbol = ticker
            try:
                price = float(raw_price) if raw_price else None
            except ValueError:
                price = None

    record = TradingWebhook(
        user_id=user.id,
        raw_body=raw,
        symbol=symbol,
        action=action,
        price=price,
        parsed=bool(symbol and action),
        note=(
            "Recorded only. This endpoint never places orders — review it and open a paper "
            "position manually if you want to act on it."
        ),
    )
    session.add(record)
    session.commit()
    session.refresh(record)

    return {
        "received": True,
        "id": record.id,
        "parsed": record.parsed,
        "symbol": symbol,
        "action": action,
        "price": price,
        "note": record.note,
    }


@router.get("/webhook/history")
def webhook_history(session: Session = Depends(get_session),
    user: User = Depends(current_user),
    limit: int = Query(50, ge=1, le=200),
):
    records = (
        session.query(TradingWebhook)
        .filter(TradingWebhook.user_id == user.id)
        .order_by(TradingWebhook.received_at.desc())
        .limit(limit)
        .all()
    )
    return {
        "webhooks": [
            {
                "id": r.id,
                "symbol": r.symbol,
                "action": r.action,
                "price": r.price,
                "parsed": r.parsed,
                "raw_body": r.raw_body[:500],
                "received_at": r.received_at.isoformat() if r.received_at else None,
            }
            for r in records
        ]
    }
