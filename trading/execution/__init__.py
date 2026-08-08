"""Live execution: broker adapters, guardrails, and the engine that routes orders.

The layering is deliberate and one-directional:

    adapters      translate and transmit; never decide
    guardrails    the only place allowed to refuse an order
    engine        the single path an order travels, and the audit log

Nothing above the engine talks to an adapter directly, so every order — paper
or live — passes the same guardrails and lands in the same record.
"""
from trading.execution.base import (
    BrokerAccount,
    BrokerAdapter,
    BrokerError,
    BrokerPosition,
    ExecutionMode,
    Order,
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)
from trading.execution.engine import ExecutionEngine
from trading.execution.guardrails import (
    ExecutionContext,
    GuardrailDecision,
    RiskLimits,
    evaluate,
    live_trading_enabled_on_instance,
)

__all__ = [
    "BrokerAdapter", "BrokerAccount", "BrokerPosition", "BrokerError",
    "Order", "OrderRequest", "OrderSide", "OrderType", "OrderStatus",
    "TimeInForce", "ExecutionMode",
    "ExecutionEngine",
    "ExecutionContext", "GuardrailDecision", "RiskLimits", "evaluate",
    "live_trading_enabled_on_instance",
]
