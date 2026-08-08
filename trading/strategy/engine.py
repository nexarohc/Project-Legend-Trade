"""Rule evaluation.

Conditions are evaluated bar by bar against a `FeatureSet`. Two rules govern
everything here:

* An unwarmed feature (`None`) makes a condition **false**, never zero. A
  strategy must not trade on an indicator that has not finished warming up.
* A cross is evaluated using bar `i` and bar `i-1` only. Nothing ever reads
  index `i+1`, which is what keeps the backtester free of look-ahead bias.
"""
from __future__ import annotations

from trading.strategy.features import FeatureSet
from trading.strategy.spec import Condition, Op


def resolve(feature_set: FeatureSet, operand: str | float, index: int) -> float | None:
    """Resolve an operand to a number: a constant passes through, a name is looked up."""
    if isinstance(operand, (int, float)):
        return float(operand)
    return feature_set.value(operand, index)


def evaluate(condition: Condition, feature_set: FeatureSet, index: int) -> bool:
    """True when `condition` holds on bar `index`."""
    left_now = feature_set.value(condition.left, index)
    if left_now is None:
        return False

    if condition.op in (Op.RISING, Op.FALLING):
        lookback = int(condition.right) if isinstance(condition.right, (int, float)) else 1
        prior = feature_set.value(condition.left, index - lookback)
        if prior is None:
            return False
        return left_now > prior if condition.op is Op.RISING else left_now < prior

    right_now = resolve(feature_set, condition.right, index)
    if right_now is None:
        return False

    if condition.op is Op.GT:
        return left_now > right_now
    if condition.op is Op.LT:
        return left_now < right_now
    if condition.op is Op.GTE:
        return left_now >= right_now
    if condition.op is Op.LTE:
        return left_now <= right_now

    # Crosses need the previous bar on both sides.
    if index == 0:
        return False
    left_prev = feature_set.value(condition.left, index - 1)
    right_prev = resolve(feature_set, condition.right, index - 1)
    if left_prev is None or right_prev is None:
        return False

    if condition.op is Op.CROSS_ABOVE:
        return left_prev <= right_prev and left_now > right_now
    if condition.op is Op.CROSS_BELOW:
        return left_prev >= right_prev and left_now < right_now

    return False


def evaluate_all(conditions: list[Condition], feature_set: FeatureSet, index: int) -> bool:
    """True when every condition holds (empty list is False — no rule means no trade)."""
    if not conditions:
        return False
    return all(evaluate(c, feature_set, index) for c in conditions)


def evaluate_filters(conditions: list[Condition], feature_set: FeatureSet, index: int) -> bool:
    """True when every filter passes. An empty filter list permits trading."""
    if not conditions:
        return True
    return all(evaluate(c, feature_set, index) for c in conditions)


def explain(condition: Condition, feature_set: FeatureSet, index: int) -> str:
    """Render a condition with its actual values — used in trade-by-trade audit output."""
    left = feature_set.value(condition.left, index)
    right = resolve(feature_set, condition.right, index)
    left_text = f"{left:.6g}" if left is not None else "n/a"
    right_text = f"{right:.6g}" if right is not None else "n/a"
    holds = evaluate(condition, feature_set, index)
    return (
        f"{condition.left}={left_text} {condition.op.description} "
        f"{condition.right}={right_text} -> {'true' if holds else 'false'}"
    )
