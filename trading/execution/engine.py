"""The execution engine: the one path orders travel through.

Order of operations is fixed and non-negotiable:

    assemble context -> run guardrails -> record the attempt ->
    (only if allowed) transmit to the broker -> record the result

The attempt is persisted *before* anything is sent, so a crash between the
guardrail pass and the broker call still leaves a record that an order was
about to go out. And a refused order is recorded too — the guardrail firing is
itself an event worth keeping.

Reconciliation always resolves toward the broker. Our `OrderRecord` rows are a
log of intent; `get_positions()` and `get_orders()` from the adapter are the
truth. Nothing here ever infers a fill from having sent an order.
"""
from __future__ import annotations

import datetime as dt
import json
import logging

from sqlalchemy.orm import Session

from database.models import ExecutionState, OrderRecord, StrategyRecord
from trading.execution import guardrails
from trading.execution.base import (
    BrokerAdapter,
    BrokerError,
    ExecutionMode,
    Order,
    OrderRequest,
    OrderStatus,
)
from trading.execution.guardrails import (
    ExecutionContext,
    GuardrailDecision,
    RiskLimits,
)

logger = logging.getLogger("legend.execution.engine")


class ExecutionEngine:
    """Owns the submit path, the kill switch, and reconciliation for one user."""

    def __init__(self, session: Session, user_id: int, adapter_factory=None):
        self.session = session
        self.user_id = user_id
        # Injectable so tests can supply a fake broker; defaults to the real one.
        self._adapter_factory = adapter_factory or _default_adapter_factory

    # --- state ----------------------------------------------------------------

    def state(self) -> ExecutionState:
        """The user's execution state row, created on first use."""
        row = (
            self.session.query(ExecutionState)
            .filter(ExecutionState.user_id == self.user_id)
            .first()
        )
        if row is None:
            # Everyone starts in internal paper mode. Reaching a broker, let
            # alone live money, is always an explicit later step.
            row = ExecutionState(user_id=self.user_id, mode=ExecutionMode.PAPER.value)
            self.session.add(row)
            self.session.commit()
            self.session.refresh(row)
        return row

    def limits(self) -> RiskLimits:
        row = self.state()
        overrides = json.loads(row.limits_json or "{}")
        base = RiskLimits()
        for key, value in overrides.items():
            if hasattr(base, key):
                setattr(base, key, value)
        return base

    def set_limits(self, overrides: dict) -> dict:
        """Merge new overrides into the user's existing ones and persist them,
        provided the result only tightens the platform defaults (see
        `guardrails.validate_limit_overrides`). Returns the resulting
        effective limits, or an `error` key if any field would loosen a
        default — nothing is written in that case."""
        row = self.state()
        existing = json.loads(row.limits_json or "{}")
        merged_overrides = {**existing, **overrides}

        effective = RiskLimits()
        for key, value in merged_overrides.items():
            if not hasattr(effective, key):
                return {"error": f"Unknown limit field: {key!r}"}
            setattr(effective, key, value)

        error = guardrails.validate_limit_overrides(effective.to_dict())
        if error:
            return {"error": error}

        row.limits_json = json.dumps(merged_overrides)
        self.session.commit()
        return effective.to_dict()

    def reset_limits(self) -> dict:
        """Clear all per-user overrides, reverting to the platform defaults."""
        row = self.state()
        row.limits_json = "{}"
        self.session.commit()
        return RiskLimits().to_dict()

    def adapter(self, mode: ExecutionMode | None = None) -> BrokerAdapter | None:
        """The broker adapter for the current (or given) mode.

        Returns None in internal paper mode, which touches no broker.
        """
        effective = mode or ExecutionMode(self.state().mode)
        if effective is ExecutionMode.PAPER:
            return None
        return self._adapter_factory(self.state().broker, effective)

    # --- the submit path ------------------------------------------------------

    def submit(self, request: OrderRequest) -> dict:
        """Run an order through the guardrails and, if permitted, to the broker.

        Always returns a result describing what happened — including the full
        guardrail decision when the order was refused.
        """
        state = self.state()
        mode = ExecutionMode(state.mode)
        context = self._build_context(request, state, mode)
        decision = guardrails.evaluate(request, context)

        record = self._record_attempt(request, mode, state.broker, decision)

        if not decision.allowed:
            self.session.commit()
            return {
                "submitted": False,
                "order_id": record.id,
                "guardrails": decision.to_dict(),
                "order": None,
            }

        # Internal paper mode: fill nothing to a broker, hand off to the
        # existing paper-position machinery instead.
        if mode is ExecutionMode.PAPER:
            record.status = "accepted_paper"
            record.allowed = True
            self.session.commit()
            return {
                "submitted": True,
                "order_id": record.id,
                "guardrails": decision.to_dict(),
                "order": {"mode": "paper", "note": (
                    "Recorded in internal paper mode. Use the paper-trading endpoints to "
                    "manage the simulated position; no broker was contacted."
                )},
            }

        # Broker paper or live: transmit.
        adapter = self.adapter(mode)
        if adapter is None or not adapter.configured:
            record.status = "rejected_no_broker"
            record.reason = "No configured broker for this mode."
            self.session.commit()
            return {
                "submitted": False,
                "order_id": record.id,
                "guardrails": decision.to_dict(),
                "error": (
                    f"Mode is {mode.value} but no broker credentials are configured. "
                    f"Set the Alpaca keys for this mode."
                ),
            }

        try:
            order = adapter.submit_order(request)
        except BrokerError as exc:
            record.status = "rejected_by_broker"
            record.reason = str(exc)
            self.session.commit()
            logger.warning("broker rejected order for user %s: %s", self.user_id, exc)
            return {
                "submitted": False,
                "order_id": record.id,
                "guardrails": decision.to_dict(),
                "error": str(exc),
            }

        self._apply_broker_order(record, order)
        self.session.commit()

        return {
            "submitted": True,
            "order_id": record.id,
            "guardrails": decision.to_dict(),
            "order": order.to_dict(),
        }

    # --- kill switch ----------------------------------------------------------

    def engage_kill_switch(self, reason: str = "manual") -> dict:
        """Flatten everything, cancel everything, block new orders.

        The blocking flag is set first and persisted, so even if flattening
        partially fails, no new order can slip through while it is retried.
        """
        state = self.state()
        state.kill_switch_active = True
        state.kill_switch_reason = reason
        state.kill_switch_engaged_at = dt.datetime.utcnow()
        # Disarm live confirmation too, so re-enabling trading later requires
        # re-confirming rather than inheriting a stale arming.
        state.live_confirmed_until = None
        self.session.commit()

        cancelled = 0
        closed: list[dict] = []
        errors: list[str] = []

        adapter = self.adapter()
        if adapter is not None and adapter.configured:
            try:
                cancelled = adapter.cancel_all_orders()
            except BrokerError as exc:
                errors.append(f"cancel orders: {exc}")
            try:
                closed = [o.to_dict() for o in adapter.close_all_positions()]
            except BrokerError as exc:
                errors.append(f"close positions: {exc}")

        logger.warning(
            "kill switch engaged for user %s (%s): cancelled %d, closed %d, errors %d",
            self.user_id, reason, cancelled, len(closed), len(errors),
        )
        return {
            "kill_switch": "engaged",
            "reason": reason,
            "orders_cancelled": cancelled,
            "positions_closed": closed,
            "errors": errors,
            "note": (
                "New orders are blocked until the kill switch is disengaged. "
                + ("Some broker calls failed; verify your account directly and retry."
                   if errors else "All positions flattened and orders cancelled.")
            ),
        }

    def disengage_kill_switch(self) -> dict:
        state = self.state()
        state.kill_switch_active = False
        state.kill_switch_reason = ""
        state.kill_switch_engaged_at = None
        self.session.commit()
        return {"kill_switch": "disengaged", "note": "New orders are permitted again."}

    # --- live confirmation ----------------------------------------------------

    def confirm_live(self, phrase: str, minutes: int = 30) -> dict:
        """Arm live trading for a bounded window if the phrase matches exactly."""
        if phrase != guardrails.LIVE_CONFIRMATION_PHRASE:
            return {
                "confirmed": False,
                "error": f'The confirmation phrase must be exactly "{guardrails.LIVE_CONFIRMATION_PHRASE}".',
            }
        if not guardrails.live_trading_enabled_on_instance():
            return {
                "confirmed": False,
                "error": (
                    f"Live trading is not enabled on this instance. Set "
                    f"{guardrails.LIVE_ENABLED_ENV}=true server-side first."
                ),
            }

        state = self.state()
        state.live_confirmed_until = dt.datetime.utcnow() + dt.timedelta(minutes=minutes)
        self.session.commit()
        return {
            "confirmed": True,
            "expires_at": state.live_confirmed_until.isoformat(),
            "note": f"Live execution is armed for {minutes} minutes.",
        }

    def set_mode(self, mode: str) -> dict:
        """Change execution mode. Switching to live is heavily gated downstream."""
        try:
            new_mode = ExecutionMode(mode)
        except ValueError:
            return {"error": f"Unknown mode {mode!r}. Use paper, broker_paper or live."}

        if new_mode.is_live and not guardrails.live_trading_enabled_on_instance():
            return {
                "error": (
                    f"Live mode is unavailable: {guardrails.LIVE_ENABLED_ENV} is not set on "
                    f"this instance."
                ),
            }

        state = self.state()
        state.mode = new_mode.value
        self.session.commit()
        return {"mode": new_mode.value, "note": _mode_note(new_mode)}

    def set_broker(self, broker: str) -> dict:
        """Change which broker `broker_paper`/`live` mode routes to.

        Clears any live confirmation window on change — arming live orders is
        a decision about a specific broker's credentials, and letting it
        silently carry over to a newly selected one would be exactly the kind
        of thing `confirm_live`'s friction exists to prevent.
        """
        if broker not in KNOWN_BROKERS:
            return {"error": f"Unknown broker {broker!r}. Known brokers: {', '.join(KNOWN_BROKERS)}."}

        state = self.state()
        state.broker = broker
        state.live_confirmed_until = None
        self.session.commit()
        return {"broker": broker}

    # --- reconciliation -------------------------------------------------------

    def reconcile(self) -> dict:
        """Refresh our order records and positions from the broker.

        The broker is authoritative. Where our record disagrees with the
        broker's order state, the broker wins and our row is corrected.
        """
        adapter = self.adapter()
        if adapter is None:
            return {"reconciled": False, "reason": "Internal paper mode has no broker to reconcile against."}
        if not adapter.configured:
            return {"reconciled": False, "reason": "No broker credentials for the current mode."}

        try:
            broker_orders = {o.broker_order_id: o for o in adapter.get_orders(open_only=False)}
            positions = adapter.get_positions()
            account = adapter.get_account()
        except BrokerError as exc:
            return {"reconciled": False, "reason": str(exc)}

        updated = 0
        our_open = (
            self.session.query(OrderRecord)
            .filter(
                OrderRecord.user_id == self.user_id,
                OrderRecord.broker_order_id.isnot(None),
                OrderRecord.status.notin_(["filled", "cancelled", "rejected_by_broker",
                                           "rejected_by_guardrail", "expired"]),
            )
            .all()
        )
        for record in our_open:
            broker_order = broker_orders.get(record.broker_order_id)
            if broker_order is None:
                continue
            before = record.status
            self._apply_broker_order(record, broker_order)
            if record.status != before:
                updated += 1

        self.session.commit()
        return {
            "reconciled": True,
            "orders_updated": updated,
            "open_positions": [p.to_dict() for p in positions],
            "account": account.to_dict(),
        }

    # --- helpers --------------------------------------------------------------

    def _build_context(self, request: OrderRequest, state: ExecutionState,
                       mode: ExecutionMode) -> ExecutionContext:
        context = ExecutionContext(
            mode=mode,
            user_id=self.user_id,
            kill_switch_active=state.kill_switch_active,
            live_confirmed=self._live_confirmed(state),
            limits=self.limits(),
            orders_last_hour=self._orders_last_hour(),
        )

        # Current price for stop/size validation. A failure to price the symbol
        # leaves the fields None, and the guardrails fail closed on that.
        try:
            from trading.market import market_service

            context.current_price = market_service.quote(request.symbol).price
        except Exception as exc:  # noqa: BLE001 - fail closed, don't crash the submit
            logger.info("could not price %s for guardrails: %s", request.symbol, exc)

        # Broker-derived account state, when a broker is in play.
        adapter = self.adapter(mode)
        if adapter is not None and adapter.configured:
            try:
                account = adapter.get_account()
                context.account_equity = account.equity
                positions = adapter.get_positions()
                context.open_position_count = len(positions)
                context.existing_exposure = sum(abs(p.market_value or 0.0) for p in positions)
                if account.trading_blocked:
                    context.limits.max_open_positions = 0  # broker halt -> nothing new
            except BrokerError as exc:
                logger.info("could not read broker account for guardrails: %s", exc)

        # Strategy provenance.
        if request.strategy_id is not None:
            strategy = self.session.get(StrategyRecord, request.strategy_id)
            if strategy and strategy.user_id == self.user_id:
                context.strategy_audit_verdict = strategy.last_verdict
                context.strategy_paper_trade_count = self._paper_trades_for(request.symbol)

        return context

    def _live_confirmed(self, state: ExecutionState) -> bool:
        return bool(
            state.live_confirmed_until
            and state.live_confirmed_until > dt.datetime.utcnow()
        )

    def _orders_last_hour(self) -> int:
        cutoff = dt.datetime.utcnow() - dt.timedelta(hours=1)
        return (
            self.session.query(OrderRecord)
            .filter(
                OrderRecord.user_id == self.user_id,
                OrderRecord.created_at >= cutoff,
                OrderRecord.allowed.is_(True),
            )
            .count()
        )

    def _paper_trades_for(self, symbol: str) -> int:
        from database.models import PaperPosition

        return (
            self.session.query(PaperPosition)
            .filter(
                PaperPosition.user_id == self.user_id,
                PaperPosition.status == "closed",
            )
            .count()
        )

    def _record_attempt(self, request: OrderRequest, mode: ExecutionMode,
                        broker: str, decision: GuardrailDecision) -> OrderRecord:
        record = OrderRecord(
            user_id=self.user_id,
            mode=mode.value,
            broker=broker,
            client_order_id=request.client_order_id,
            symbol=request.symbol.upper(),
            side=request.side.value,
            quantity=request.quantity,
            order_type=request.order_type.value,
            limit_price=request.limit_price,
            stop_price=request.stop_price,
            take_profit_price=request.take_profit_price,
            status="rejected_by_guardrail" if not decision.allowed else "pending",
            allowed=decision.allowed,
            guardrail_json=json.dumps(decision.to_dict())[:20_000],
            reason="" if decision.allowed else "; ".join(decision.blocking_reasons),
            strategy_id=request.strategy_id,
            analysis_id=request.analysis_id,
            raw_json=json.dumps(request.to_dict()),
        )
        self.session.add(record)
        self.session.flush()   # assign an id before we might commit-and-return
        return record

    def _apply_broker_order(self, record: OrderRecord, order: Order) -> None:
        record.broker_order_id = order.broker_order_id
        record.client_order_id = order.client_order_id or record.client_order_id
        record.status = order.status.value
        record.filled_quantity = order.filled_quantity
        record.average_fill_price = order.average_fill_price
        record.raw_json = json.dumps(order.raw)[:20_000] if order.raw else record.raw_json
        record.reconciled_at = dt.datetime.utcnow()

    # --- read models ----------------------------------------------------------

    def status(self) -> dict:
        state = self.state()
        mode = ExecutionMode(state.mode)
        adapter = self.adapter(mode)

        health = None
        if adapter is not None:
            health = adapter.health()

        return {
            "mode": state.mode,
            "broker": state.broker,
            "kill_switch_active": state.kill_switch_active,
            "kill_switch_reason": state.kill_switch_reason,
            "live_confirmed": self._live_confirmed(state),
            "live_confirmed_until": (
                state.live_confirmed_until.isoformat() if state.live_confirmed_until else None
            ),
            "live_enabled_on_instance": guardrails.live_trading_enabled_on_instance(),
            "limits": self.limits().to_dict(),
            "broker_health": health,
            "guardrail_rules": guardrails.describe_rules(self.limits()),
        }

    def orders(self, limit: int = 50) -> list[dict]:
        records = (
            self.session.query(OrderRecord)
            .filter(OrderRecord.user_id == self.user_id)
            .order_by(OrderRecord.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": r.id,
                "mode": r.mode,
                "symbol": r.symbol,
                "side": r.side,
                "quantity": r.quantity,
                "order_type": r.order_type,
                "stop_price": r.stop_price,
                "status": r.status,
                "allowed": r.allowed,
                "reason": r.reason,
                "filled_quantity": r.filled_quantity,
                "average_fill_price": r.average_fill_price,
                "broker_order_id": r.broker_order_id,
                "strategy_id": r.strategy_id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in records
        ]


KNOWN_BROKERS = ("alpaca", "tradier", "ibkr")


def _default_adapter_factory(broker: str, mode: ExecutionMode) -> BrokerAdapter:
    if broker == "alpaca":
        from trading.execution.alpaca import AlpacaAdapter

        return AlpacaAdapter(mode)
    if broker == "tradier":
        from trading.execution.tradier import TradierAdapter

        return TradierAdapter(mode)
    if broker == "ibkr":
        from trading.execution.ibkr import IBKRAdapter

        return IBKRAdapter(mode)
    raise BrokerError(f"Unknown broker {broker!r}. Known brokers: {', '.join(KNOWN_BROKERS)}.")


def _mode_note(mode: ExecutionMode) -> str:
    return {
        ExecutionMode.PAPER: "Internal simulation. No broker is contacted.",
        ExecutionMode.BROKER_PAPER: "Broker sandbox — real API, fake money. Requires paper keys.",
        ExecutionMode.LIVE: (
            "LIVE — real money. Requires the instance flag, live keys, a typed confirmation, "
            "an APPROVED strategy audit, and a paper-trading record."
        ),
    }[mode]
