"""Execution API: broker status, mode, orders, kill switch.

Every endpoint is scoped to the authenticated user and goes through
`ExecutionEngine`, so the guardrails and the audit record cannot be bypassed by
hitting a lower-level route — there isn't one.

Live trading has an intentionally steep approach: you must set an instance-level
environment flag, switch mode, and type a confirmation phrase, and even then an
order is refused unless it carries a stop and comes from an APPROVED, paper-
traded strategy. That friction is the feature.

**Broker-connected modes (`broker_paper`, `live`) are admin-only**, because
broker credentials are instance-level, not per-user (see
`trading/execution/alpaca.py`'s module docstring) — every account on this
instance would otherwise route through the *same* broker connection. On a
single-operator desktop instance this changes nothing: the implicit local
account is always admin. It only matters once real strangers can register,
which is exactly when it needs to matter.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.dependencies import current_user
from app.ratelimit import compute_limiter
from database.db import get_session
from database.models import User
from trading.execution import ExecutionEngine, RiskLimits
from trading.execution.base import OrderRequest, OrderSide, OrderType, TimeInForce
from trading.execution.engine import KNOWN_BROKERS

logger = logging.getLogger("legend.execution")
router = APIRouter(prefix="/execution", tags=["execution"])


def engine_for(user: User, session: Session) -> ExecutionEngine:
    return ExecutionEngine(session, user.id)


def _require_admin_for_shared_broker(user: User) -> None:
    """Broker-connected modes share one instance-level connection across every
    account, so only the instance administrator may touch them. On a
    single-operator desktop instance the implicit local account is always
    admin, so this changes nothing there — it only bites once real strangers
    can register, which is the point."""
    if not user.is_admin:
        raise HTTPException(
            status_code=403,
            detail=(
                "Broker-connected modes are limited to the instance administrator. "
                "Broker credentials are shared across the whole instance, not per-user."
            ),
        )


class SubmitOrderRequest(BaseModel):
    symbol: str = Field(..., min_length=1)
    side: str = Field(..., pattern="^(buy|sell)$")
    quantity: float = Field(..., gt=0)
    order_type: str = Field("market", pattern="^(market|limit|stop|stop_limit)$")
    limit_price: float | None = Field(None, gt=0)
    # Not Optional in spirit — the guardrails refuse an entry without it — but
    # nullable so the schema can express an exit. The refusal, with its reason,
    # is clearer than a 422 here.
    stop_price: float | None = Field(None, gt=0)
    take_profit_price: float | None = Field(None, gt=0)
    time_in_force: str = Field("day", pattern="^(day|gtc|ioc|fok)$")
    strategy_id: int | None = None
    analysis_id: int | None = None
    reason: str = ""


class ModeRequest(BaseModel):
    mode: str = Field(..., pattern="^(paper|broker_paper|live)$")


class BrokerRequest(BaseModel):
    broker: str = Field(..., pattern=f"^({'|'.join(KNOWN_BROKERS)})$")


class ConfirmLiveRequest(BaseModel):
    phrase: str
    minutes: int = Field(30, ge=1, le=240)


class KillSwitchRequest(BaseModel):
    reason: str = "manual"


class LimitsUpdateRequest(BaseModel):
    """Every field is optional — send only the ones you want to change.
    Each may only tighten the platform default, never loosen it; see
    `guardrails.validate_limit_overrides`."""
    max_position_percent: float | None = Field(None, gt=0)
    max_total_exposure_percent: float | None = Field(None, gt=0)
    max_order_notional: float | None = Field(None, gt=0)
    max_open_positions: int | None = Field(None, ge=1)
    max_orders_per_hour: int | None = Field(None, ge=1)
    min_stop_distance_percent: float | None = Field(None, gt=0)
    max_stop_distance_percent: float | None = Field(None, gt=0)


@router.get("/status")
def status(user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Current mode, kill-switch state, live-arming state, limits and broker health."""
    result = engine_for(user, session).status()
    # Broker-connected modes are admin-only (see module docstring); the UI
    # uses this to disable those controls up front instead of only failing on
    # submit.
    result["user_is_admin"] = user.is_admin
    return result


@router.post("/mode")
def set_mode(request: ModeRequest, user: User = Depends(current_user),
             session: Session = Depends(get_session)):
    if request.mode != "paper":
        _require_admin_for_shared_broker(user)
    result = engine_for(user, session).set_mode(request.mode)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("/brokers")
def brokers():
    """Which broker adapters exist. Whether one is actually usable — has
    credentials configured — shows up in /execution/status's broker_health."""
    return {"brokers": list(KNOWN_BROKERS)}


@router.post("/broker")
def set_broker(request: BrokerRequest, user: User = Depends(current_user),
               session: Session = Depends(get_session)):
    """Switch which broker `broker_paper`/`live` mode routes to. Clears any
    active live-confirmation window, since that was a decision about a
    specific broker's credentials."""
    _require_admin_for_shared_broker(user)
    result = engine_for(user, session).set_broker(request.broker)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/confirm-live")
def confirm_live(request: ConfirmLiveRequest, user: User = Depends(current_user),
                 session: Session = Depends(get_session)):
    """Arm live execution for a bounded window. Requires the exact phrase."""
    _require_admin_for_shared_broker(user)
    result = engine_for(user, session).confirm_live(request.phrase, request.minutes)
    if not result.get("confirmed"):
        raise HTTPException(status_code=400, detail=result.get("error", "Confirmation failed."))
    return result


@router.post("/orders")
def submit_order(request: SubmitOrderRequest, http_request: Request,
                 user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Submit an order through the guardrails.

    A refused order returns HTTP 200 with `submitted: false` and the full
    guardrail decision — a refusal is a normal, expected outcome, not an error,
    and the caller needs the reasons.
    """
    compute_limiter.enforce(http_request, key=f"exec-{user.id}")

    order_request = OrderRequest(
        symbol=request.symbol,
        side=OrderSide(request.side),
        quantity=request.quantity,
        order_type=OrderType(request.order_type),
        limit_price=request.limit_price,
        stop_price=request.stop_price,
        take_profit_price=request.take_profit_price,
        time_in_force=TimeInForce(request.time_in_force),
        strategy_id=request.strategy_id,
        analysis_id=request.analysis_id,
        reason=request.reason,
    )
    return engine_for(user, session).submit(order_request)


@router.get("/orders")
def orders(user: User = Depends(current_user), session: Session = Depends(get_session),
           limit: int = Query(50, ge=1, le=200)):
    """Order history, including orders the guardrails refused."""
    return {"orders": engine_for(user, session).orders(limit)}


@router.post("/reconcile")
def reconcile(user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Refresh order and position state from the broker, which is authoritative."""
    return engine_for(user, session).reconcile()


@router.post("/kill-switch")
def kill_switch(request: KillSwitchRequest, user: User = Depends(current_user),
                session: Session = Depends(get_session)):
    """Flatten all positions, cancel all orders, and block new ones."""
    return engine_for(user, session).engage_kill_switch(request.reason)


@router.delete("/kill-switch")
def disengage_kill_switch(user: User = Depends(current_user),
                          session: Session = Depends(get_session)):
    """Re-enable trading after a kill-switch stop."""
    return engine_for(user, session).disengage_kill_switch()


@router.get("/rules")
def rules(user: User = Depends(current_user), session: Session = Depends(get_session)):
    """The full guardrail rule set and current thresholds, for display."""
    from trading.execution import guardrails

    return guardrails.describe_rules(engine_for(user, session).limits())


@router.get("/limits")
def get_limits(user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Your effective risk limits (platform defaults, tightened by any
    per-user overrides you've set) alongside the platform defaults for
    reference."""
    return {
        "effective": engine_for(user, session).limits().to_dict(),
        "platform_defaults": RiskLimits().to_dict(),
    }


@router.post("/limits")
def set_limits(request: LimitsUpdateRequest, user: User = Depends(current_user),
               session: Session = Depends(get_session)):
    """Tighten your own risk limits below the platform default. This can only
    make limits stricter, never looser — every account, including the admin's,
    is capped at the platform defaults; this endpoint just lets you self-impose
    something more conservative."""
    overrides = {k: v for k, v in request.model_dump().items() if v is not None}
    if not overrides:
        raise HTTPException(status_code=400, detail="No fields provided.")
    result = engine_for(user, session).set_limits(overrides)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.delete("/limits")
def reset_limits(user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Clear all of your per-user limit overrides, reverting to the platform
    defaults."""
    return engine_for(user, session).reset_limits()
