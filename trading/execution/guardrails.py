"""The layer that decides whether an order may be placed at all.

Everything here exists to say **no**. Adapters transmit, the engine records,
and this module is the only place allowed to refuse — which means every reason
an order can be rejected is visible in one file rather than scattered across
call sites.

Design principles, in order of importance:

1. **Fail closed.** Any check that cannot be evaluated refuses. A guardrail
   that silently passes when its input is missing is not a guardrail.
2. **Live mode is opt-in three times over.** An instance-level environment
   flag, an explicit mode selection, and a typed confirmation phrase. No single
   mistake reaches real money.
3. **Refusals explain themselves.** A blocked order returns the specific rule
   and the number that broke it, because "rejected" with no reason trains
   people to disable the check.
4. **Protection is mandatory.** No entry order without a stop, ever. This is
   the check most likely to be resented and the one most worth keeping.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum

from trading.execution.base import ExecutionMode, OrderRequest, OrderSide

logger = logging.getLogger("legend.execution.guardrails")

# The phrase a user must type to arm live trading. Deliberately awkward: a
# checkbox gets clicked reflexively, this does not.
LIVE_CONFIRMATION_PHRASE = "TRADE LIVE MONEY"

# Instance-level switch. Without this set, live mode is unreachable no matter
# what any request or database row says.
#
# Every other setting in this codebase also answers to its old DEX_-prefixed
# name, because an operator's existing .env being silently ignored after the
# rename is a bad failure. This one deliberately does not. It is the switch that
# arms real money, and honouring a leftover variable from a differently-named
# product would arm it without anyone deciding to — the one direction where
# ignoring the old name is the safe outcome rather than the unsafe one. Setting
# it again is a five-second, fully-deliberate act, which is the point.
LIVE_ENABLED_ENV = "LEGEND_ENABLE_LIVE_TRADING"


class Severity(str, Enum):
    BLOCK = "block"      # the order will not be sent
    WARN = "warn"        # sent, but the caller is told


@dataclass
class Refusal:
    rule: str
    severity: Severity
    reason: str
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "rule": self.rule,
            "severity": self.severity.value,
            "reason": self.reason,
            "detail": self.detail,
        }


@dataclass
class GuardrailDecision:
    allowed: bool
    refusals: list[Refusal] = field(default_factory=list)
    warnings: list[Refusal] = field(default_factory=list)
    checks_run: list[str] = field(default_factory=list)

    @property
    def blocking_reasons(self) -> list[str]:
        return [r.reason for r in self.refusals]

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "refusals": [r.to_dict() for r in self.refusals],
            "warnings": [w.to_dict() for w in self.warnings],
            "checks_run": self.checks_run,
            "summary": (
                "Order permitted." if self.allowed
                else "Order refused: " + "; ".join(self.blocking_reasons)
            ),
        }


@dataclass
class RiskLimits:
    """Per-instance caps. Conservative by default — raising them is a decision."""

    max_position_percent: float = 10.0      # of account equity, single position
    max_total_exposure_percent: float = 30.0
    max_order_notional: float = 25_000.0
    max_open_positions: int = 10
    max_orders_per_hour: int = 20
    min_stop_distance_percent: float = 0.1  # a stop 0.01% away is not a stop
    max_stop_distance_percent: float = 25.0

    def to_dict(self) -> dict:
        return {
            "max_position_percent": self.max_position_percent,
            "max_total_exposure_percent": self.max_total_exposure_percent,
            "max_order_notional": self.max_order_notional,
            "max_open_positions": self.max_open_positions,
            "max_orders_per_hour": self.max_orders_per_hour,
            "min_stop_distance_percent": self.min_stop_distance_percent,
            "max_stop_distance_percent": self.max_stop_distance_percent,
        }


# Which direction counts as "stricter than the platform default" for each
# field. Per-user overrides may only move a value in this direction — never
# the other way — so a user can tighten their own guardrails but nobody can
# use this path to loosen them, keeping "the only place allowed to refuse"
# (this module's job) unaffected by who calls the override endpoint.
_STRICTER_DIRECTION = {
    "max_position_percent": "at_most",
    "max_total_exposure_percent": "at_most",
    "max_order_notional": "at_most",
    "max_open_positions": "at_most",
    "max_orders_per_hour": "at_most",
    "min_stop_distance_percent": "at_least",
    "max_stop_distance_percent": "at_most",
}


def validate_limit_overrides(merged: dict) -> str | None:
    """Check a fully-merged (defaults + overrides) limits dict against the
    platform defaults. Returns an error string, or None if valid.

    `merged` must contain every RiskLimits field, already coerced to the
    right type by the caller (the Pydantic request model does this).
    """
    defaults = RiskLimits()
    for key, direction in _STRICTER_DIRECTION.items():
        value = merged[key]
        if value <= 0:
            return f"{key} must be greater than zero (got {value})."
        default_value = getattr(defaults, key)
        if direction == "at_most" and value > default_value:
            return (
                f"{key} may not exceed the platform default of {default_value} "
                f"(got {value}) — per-user overrides can only tighten limits, not loosen them."
            )
        if direction == "at_least" and value < default_value:
            return (
                f"{key} may not go below the platform default of {default_value} "
                f"(got {value}) — per-user overrides can only tighten limits, not loosen them."
            )
    if merged["min_stop_distance_percent"] >= merged["max_stop_distance_percent"]:
        return "min_stop_distance_percent must be less than max_stop_distance_percent."
    return None


@dataclass
class ExecutionContext:
    """Everything the guardrails need in order to decide.

    Assembled by the engine from the broker and the database. Fields that are
    `None` mean "could not be determined", and checks that depend on them
    refuse rather than assume.
    """

    mode: ExecutionMode
    user_id: int

    kill_switch_active: bool = False
    live_confirmed: bool = False           # the user typed the phrase
    account_equity: float | None = None
    current_price: float | None = None
    open_position_count: int | None = None
    existing_exposure: float | None = None
    orders_last_hour: int = 0

    # Provenance of the order, when it came from a strategy.
    strategy_audit_verdict: str | None = None
    strategy_paper_trade_count: int = 0

    limits: RiskLimits = field(default_factory=RiskLimits)


def live_trading_enabled_on_instance() -> bool:
    """Whether this deployment permits live trading at all."""
    return os.environ.get(LIVE_ENABLED_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def evaluate(request: OrderRequest, context: ExecutionContext) -> GuardrailDecision:
    """Run every guardrail. Returns whether the order may be sent, and why not."""
    decision = GuardrailDecision(allowed=True)

    def block(rule: str, reason: str, detail: str = "") -> None:
        decision.refusals.append(Refusal(rule, Severity.BLOCK, reason, detail))
        decision.allowed = False

    def warn(rule: str, reason: str, detail: str = "") -> None:
        decision.warnings.append(Refusal(rule, Severity.WARN, reason, detail))

    def ran(rule: str) -> None:
        decision.checks_run.append(rule)

    # --- 1. Kill switch -------------------------------------------------------
    ran("kill_switch")
    if context.kill_switch_active:
        block(
            "kill_switch",
            "The kill switch is engaged; no new orders will be sent.",
            "Disengage it explicitly before trading again.",
        )
        # Nothing else matters once the kill switch is on.
        return decision

    # --- 2. Live-mode gating --------------------------------------------------
    ran("live_mode_gating")
    if context.mode.is_live:
        if not live_trading_enabled_on_instance():
            block(
                "live_mode_gating",
                "Live trading is not enabled on this instance.",
                f"Set {LIVE_ENABLED_ENV}=true in the server environment and restart. "
                f"This is deliberately a server-side setting, so no API request can turn it on.",
            )
        if not context.live_confirmed:
            block(
                "live_confirmation",
                "Live trading has not been confirmed for this session.",
                f'Type "{LIVE_CONFIRMATION_PHRASE}" to arm live execution.',
            )

    # --- 3. Basic order sanity ------------------------------------------------
    ran("order_sanity")
    if request.quantity <= 0:
        block("order_sanity", f"Quantity must be positive, got {request.quantity}.")
    if not request.symbol or not request.symbol.strip():
        block("order_sanity", "No symbol supplied.")

    # --- 4. Mandatory protective stop ----------------------------------------
    # The single most important rule here. An entry without a stop has unbounded
    # downside, and "I'll watch it" is not a risk control.
    ran("mandatory_stop")
    if request.stop_price is None:
        block(
            "mandatory_stop",
            "Entry orders must carry a protective stop.",
            "The platform will not place an unprotected entry. Supply stop_price, or "
            "close a position through the flatten endpoint if you meant to exit.",
        )
    elif context.current_price:
        distance = abs(context.current_price - request.stop_price)
        distance_percent = 100 * distance / context.current_price

        # A stop on the wrong side would trigger instantly and is almost always
        # a sign the caller has the direction confused.
        wrong_side = (
            (request.side is OrderSide.BUY and request.stop_price >= context.current_price)
            or (request.side is OrderSide.SELL and request.stop_price <= context.current_price)
        )
        if wrong_side:
            block(
                "stop_placement",
                f"A {request.side.value} order's stop must sit on the protective side of price.",
                f"Stop {request.stop_price:g} against a price of {context.current_price:g}.",
            )
        elif distance_percent < context.limits.min_stop_distance_percent:
            block(
                "stop_placement",
                f"The stop is only {distance_percent:.3f}% away, which is inside normal noise.",
                f"Minimum is {context.limits.min_stop_distance_percent}%.",
            )
        elif distance_percent > context.limits.max_stop_distance_percent:
            block(
                "stop_placement",
                f"The stop is {distance_percent:.1f}% away, which risks far more than intended.",
                f"Maximum is {context.limits.max_stop_distance_percent}%.",
            )

    # --- 5. Strategy provenance ----------------------------------------------
    # An order attributed to a strategy inherits that strategy's audit verdict.
    # Routing capital into a strategy the audit rejected would make the audit
    # decorative.
    ran("strategy_audit")
    if request.strategy_id is not None:
        verdict = (context.strategy_audit_verdict or "").upper()
        if not verdict:
            block(
                "strategy_audit",
                "This strategy has never been audited.",
                "Run a full study on it before routing orders.",
            )
        elif verdict != "APPROVED":
            block(
                "strategy_audit",
                f"The strategy's audit verdict is {verdict}, not APPROVED.",
                "Fix the issues the audit raised and re-run it.",
            )
        elif context.mode.is_live and context.strategy_paper_trade_count < 20:
            block(
                "paper_trading_record",
                f"This strategy has only {context.strategy_paper_trade_count} closed paper "
                f"trades; 20 are required before it may trade live.",
                "Paper trade it first. An approved backtest is not a track record.",
            )

    # --- 6. Position sizing ---------------------------------------------------
    ran("position_size")
    if context.current_price and request.quantity:
        notional = context.current_price * request.quantity

        if notional > context.limits.max_order_notional:
            block(
                "position_size",
                f"Order notional {notional:,.2f} exceeds the per-order cap of "
                f"{context.limits.max_order_notional:,.2f}.",
            )

        if context.account_equity:
            position_percent = 100 * notional / context.account_equity
            if position_percent > context.limits.max_position_percent:
                block(
                    "position_size",
                    f"This position would be {position_percent:.1f}% of equity, above the "
                    f"{context.limits.max_position_percent}% cap.",
                )

            if context.existing_exposure is not None:
                total_percent = 100 * (context.existing_exposure + notional) / context.account_equity
                if total_percent > context.limits.max_total_exposure_percent:
                    block(
                        "total_exposure",
                        f"Total exposure would reach {total_percent:.1f}% of equity, above the "
                        f"{context.limits.max_total_exposure_percent}% cap.",
                    )
        elif context.mode.touches_broker:
            # Cannot size against unknown equity — refuse rather than guess.
            block(
                "position_size",
                "Account equity could not be read from the broker, so position size "
                "cannot be checked.",
                "This is a fail-closed refusal: the platform will not size blind.",
            )

    # --- 7. Concentration and pacing -----------------------------------------
    ran("open_positions")
    if context.open_position_count is not None:
        if context.open_position_count >= context.limits.max_open_positions:
            block(
                "open_positions",
                f"Already holding {context.open_position_count} positions, at the limit of "
                f"{context.limits.max_open_positions}.",
            )

    ran("order_rate")
    if context.orders_last_hour >= context.limits.max_orders_per_hour:
        block(
            "order_rate",
            f"{context.orders_last_hour} orders in the last hour, at the limit of "
            f"{context.limits.max_orders_per_hour}.",
            "This is a runaway-loop guard as much as a discipline one.",
        )

    # --- 8. Advisory warnings -------------------------------------------------
    ran("advisories")
    if request.take_profit_price is None:
        warn(
            "no_target",
            "This order has no take-profit attached.",
            "Not blocking — discretionary exits are legitimate — but the position has no "
            "planned exit on the upside.",
        )
    if context.mode.is_live:
        warn(
            "live_mode",
            "This order will be sent with real money.",
            f"Broker: live endpoint. Instance flag {LIVE_ENABLED_ENV} is enabled.",
        )

    if not decision.allowed:
        logger.warning(
            "order refused for user %s: %s",
            context.user_id, "; ".join(decision.blocking_reasons),
        )
    return decision


def describe_rules(limits: RiskLimits | None = None) -> dict:
    """Every rule and its current threshold, for the UI and the docs."""
    limits = limits or RiskLimits()
    return {
        "always_blocking": [
            "The kill switch is engaged.",
            "Live mode without the server-side instance flag.",
            "Live mode without a typed confirmation.",
            "An entry order with no protective stop.",
            "A stop placed on the wrong side of price.",
            "An order attributed to a strategy that is not APPROVED by the audit.",
            "A live order from a strategy with fewer than 20 closed paper trades.",
            "Any size check that cannot be evaluated (fail-closed).",
        ],
        "limits": limits.to_dict(),
        "live_requirements": [
            f"Server environment: {LIVE_ENABLED_ENV}=true",
            "Execution mode explicitly set to live",
            f'Typed confirmation: "{LIVE_CONFIRMATION_PHRASE}"',
            "Strategy audit verdict APPROVED",
            "At least 20 closed paper trades for that strategy",
        ],
        "notes": [
            "Guardrails fail closed: a check whose inputs are unavailable refuses the order.",
            "Broker credentials are read from the environment only; they are never stored "
            "in the database.",
            "The kill switch flattens all positions, cancels all orders, and blocks new ones "
            "until explicitly disengaged.",
        ],
    }
