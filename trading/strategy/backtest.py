"""Event-driven backtester.

The execution model is deliberately pessimistic, because a backtest's only
value is as a lower bound on disappointment. Specifically:

* **Signals are evaluated on a closed bar and filled at the next bar's open.**
  Nothing is ever filled at the price that generated the signal. This single
  rule eliminates the most common form of look-ahead bias.
* **When a bar's range covers both the stop and a target, the stop is assumed
  to fill first.** Without intrabar data it is impossible to know the order, and
  assuming the favourable one inflates results.
* **Costs are charged on every fill**, including each partial take-profit.
* **Position size is derived from the stop distance**, so risk per trade is
  constant in percentage terms rather than drifting with volatility.
* **Gaps are honoured.** If a bar opens beyond the stop, the fill happens at
  the open, not at the stop price — that is what actually happens to a
  real order.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from trading.indicators import atr
from trading.models import Series
from trading.strategy.engine import evaluate_all, evaluate_filters
from trading.strategy.features import FeatureSet
from trading.strategy.spec import StopType, StrategySpec, TargetType

logger = logging.getLogger("legend.trading.backtest")


@dataclass
class Fill:
    """One execution — an entry, a partial take-profit, or a final exit."""

    index: int
    timestamp: int
    price: float
    quantity: float
    kind: str          # entry | take_profit | stop | signal_exit | time_stop | end_of_data
    cost: float        # commission + slippage + spread paid on this fill

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "time": self.timestamp,
            "price": self.price,
            "quantity": self.quantity,
            "kind": self.kind,
            "cost": round(self.cost, 6),
        }


@dataclass
class Trade:
    direction: str            # long | short
    entry_index: int
    entry_time: int
    entry_price: float
    quantity: float
    stop_price: float
    targets: list[float]
    exit_index: int | None = None
    exit_time: int | None = None
    exit_price: float | None = None
    exit_reason: str = ""
    gross_pnl: float = 0.0
    costs: float = 0.0
    net_pnl: float = 0.0
    r_multiple: float = 0.0
    bars_held: int = 0
    max_favourable: float = 0.0     # best unrealised profit, in R
    max_adverse: float = 0.0        # worst unrealised loss, in R
    fills: list[Fill] = field(default_factory=list)
    entry_reason: str = ""

    @property
    def won(self) -> bool:
        return self.net_pnl > 0

    def to_dict(self) -> dict:
        return {
            "direction": self.direction,
            "entry_index": self.entry_index,
            "entry_time": self.entry_time,
            "entry_price": self.entry_price,
            "quantity": self.quantity,
            "stop_price": self.stop_price,
            "targets": self.targets,
            "exit_index": self.exit_index,
            "exit_time": self.exit_time,
            "exit_price": self.exit_price,
            "exit_reason": self.exit_reason,
            "gross_pnl": round(self.gross_pnl, 6),
            "costs": round(self.costs, 6),
            "net_pnl": round(self.net_pnl, 6),
            "r_multiple": round(self.r_multiple, 3),
            "bars_held": self.bars_held,
            "max_favourable_r": round(self.max_favourable, 2),
            "max_adverse_r": round(self.max_adverse, 2),
            "entry_reason": self.entry_reason,
            "fills": [f.to_dict() for f in self.fills],
        }


@dataclass
class BacktestResult:
    spec_name: str
    symbol: str
    timeframe: str
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[dict] = field(default_factory=list)
    initial_capital: float = 10_000.0
    final_equity: float = 10_000.0
    bars_tested: int = 0
    start_time: int = 0
    end_time: int = 0
    warnings: list[str] = field(default_factory=list)
    execution_notes: list[str] = field(default_factory=list)

    def to_dict(self, include_trades: bool = True) -> dict:
        return {
            "spec_name": self.spec_name,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "trades": [t.to_dict() for t in self.trades] if include_trades else [],
            "trade_count": len(self.trades),
            "equity_curve": self.equity_curve,
            "initial_capital": self.initial_capital,
            "final_equity": round(self.final_equity, 2),
            "bars_tested": self.bars_tested,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "warnings": self.warnings,
            "execution_notes": self.execution_notes,
        }


EXECUTION_NOTES = [
    "Signals are evaluated on closed bars and filled at the next bar's open — never at the "
    "signal bar's close.",
    "When one bar's range contains both the stop and a target, the stop is assumed to fill "
    "first, because intrabar sequence is unknowable from OHLCV.",
    "Gaps through the stop fill at the bar's open, not at the stop price.",
    "Commission, slippage and spread are charged on every fill, including partial exits.",
    "Position size is derived from the stop distance so each trade risks the same percentage "
    "of equity.",
    "Only one position is held at a time; a new signal while in a trade is ignored.",
]


def run_backtest(spec: StrategySpec, series: Series) -> BacktestResult:
    """Run `spec` over `series` and return the trade log and equity curve."""
    result = BacktestResult(
        spec_name=spec.name,
        symbol=series.symbol,
        timeframe=series.timeframe.value,
        initial_capital=spec.initial_capital,
        final_equity=spec.initial_capital,
        bars_tested=len(series),
        execution_notes=list(EXECUTION_NOTES),
    )

    problems = spec.validate()
    if problems:
        result.warnings.extend(problems)
        return result

    if len(series) < 100:
        result.warnings.append(
            f"Only {len(series)} bars supplied. A backtest this short cannot produce a "
            f"statistically meaningful result."
        )
    if not series.candles:
        return result

    candles = series.candles
    result.start_time = candles[0].timestamp
    result.end_time = candles[-1].timestamp

    features = FeatureSet(series)
    atr_values = atr(series, 14)

    # Touch every feature the spec references so warm-up can be measured up front.
    for group in (spec.entry_long, spec.entry_short, spec.exit_long, spec.exit_short, spec.filters):
        for condition in group:
            try:
                features.get(condition.left)
                if isinstance(condition.right, str):
                    features.get(condition.right)
            except Exception as exc:  # noqa: BLE001 - report and abort rather than half-run
                result.warnings.append(f"Cannot evaluate rule '{condition.describe()}': {exc}")
                return result

    warmup = max(features.warmup_bars, 20)
    if warmup >= len(candles) - 5:
        result.warnings.append(
            f"Indicator warm-up needs {warmup} bars but only {len(candles)} were supplied — "
            f"there is no testable period left."
        )
        return result

    equity = spec.initial_capital
    open_trade: Trade | None = None
    pending: dict | None = None      # signal awaiting next-bar execution
    remaining_targets: list[tuple[float, float]] = []   # (price, quantity)
    stop_price = 0.0
    initial_risk_per_unit = 0.0
    breakeven_moved = False

    for i in range(warmup, len(candles)):
        candle = candles[i]

        # ---- 1. Execute any signal generated on the previous bar --------------
        if pending is not None and open_trade is None:
            open_trade, stop_price, remaining_targets, initial_risk_per_unit, equity = _open_position(
                pending, candle, i, spec, equity, atr_values, series
            )
            breakeven_moved = False
            pending = None
            if open_trade is None:
                # Sizing failed (zero stop distance, no equity); note and move on.
                continue

        # ---- 2. Manage an open position on this bar ---------------------------
        if open_trade is not None:
            equity, closed = _manage_position(
                open_trade, candle, i, spec, equity, stop_price, remaining_targets,
                initial_risk_per_unit, atr_values, features, breakeven_moved,
            )
            stop_price = open_trade.stop_price
            breakeven_moved = breakeven_moved or _at_breakeven(open_trade, stop_price)

            if closed:
                result.trades.append(open_trade)
                open_trade = None
                remaining_targets = []

        # ---- 3. Look for a new signal on this closed bar ----------------------
        if open_trade is None and pending is None:
            if _session_allows(spec, candle.timestamp) and evaluate_filters(spec.filters, features, i):
                if spec.management.allow_long and evaluate_all(spec.entry_long, features, i):
                    pending = {"direction": "long", "signal_index": i}
                elif spec.management.allow_short and evaluate_all(spec.entry_short, features, i):
                    pending = {"direction": "short", "signal_index": i}

        # ---- 4. Record equity -------------------------------------------------
        mark = equity
        if open_trade is not None:
            unrealised = _unrealised(open_trade, candle.close)
            mark = equity + unrealised
        result.equity_curve.append({"time": candle.timestamp, "equity": round(mark, 2)})

    # Close anything still open at the last price, so the equity curve is honest.
    if open_trade is not None:
        last = candles[-1]
        equity = _close_remaining(
            open_trade, last, len(candles) - 1, spec, equity, "end_of_data"
        )
        result.trades.append(open_trade)
        if result.equity_curve:
            result.equity_curve[-1]["equity"] = round(equity, 2)
        result.warnings.append(
            "The final trade was still open at the end of the data and was closed at the last "
            "price; its result is not a real exit."
        )

    result.final_equity = equity
    if not result.trades:
        result.warnings.append(
            "The strategy produced no trades over this period. Either the rules are too "
            "restrictive or the market never met them."
        )
    return result


def _open_position(
    pending: dict, candle, index: int, spec: StrategySpec,
    equity: float, atr_values: list, series: Series,
) -> tuple[Trade | None, float, list, float, float]:
    """Fill the pending signal at this bar's open and size the position."""
    direction = pending["direction"]
    entry_price = candle.open

    atr_now = atr_values[index] or atr_values[pending["signal_index"]]
    if not atr_now or atr_now <= 0:
        return None, 0.0, [], 0.0, equity

    stop_price = _stop_price(spec, direction, entry_price, atr_now, series, index)
    risk_per_unit = abs(entry_price - stop_price)
    if risk_per_unit <= 0:
        return None, 0.0, [], 0.0, equity

    risk_amount = equity * (spec.risk_percent / 100)
    quantity = risk_amount / risk_per_unit
    if quantity <= 0:
        return None, 0.0, [], 0.0, equity

    # Cap notional at equity so an unlevered backtest cannot silently use leverage.
    max_quantity = equity / entry_price if entry_price > 0 else 0
    if quantity > max_quantity:
        quantity = max_quantity

    cost = _fill_cost(entry_price, quantity, spec)
    equity -= cost

    targets = _target_prices(spec, direction, entry_price, risk_per_unit, atr_now)
    allocations = spec.targets.normalized_allocations()
    remaining_targets = [
        (price, quantity * allocation) for price, allocation in zip(targets, allocations)
    ]

    trade = Trade(
        direction=direction,
        entry_index=index,
        entry_time=candle.timestamp,
        entry_price=entry_price,
        quantity=quantity,
        stop_price=stop_price,
        targets=targets,
        costs=cost,
        entry_reason=(
            f"{direction} signal on bar {pending['signal_index']} filled at the next bar's open "
            f"({entry_price:.6g}); stop {stop_price:.6g} ({risk_per_unit:.6g} away)."
        ),
    )
    trade.fills.append(Fill(index, candle.timestamp, entry_price, quantity, "entry", cost))
    return trade, stop_price, remaining_targets, risk_per_unit, equity


def _manage_position(
    trade: Trade, candle, index: int, spec: StrategySpec, equity: float,
    stop_price: float, remaining_targets: list, initial_risk: float,
    atr_values: list, features: FeatureSet, breakeven_moved: bool,
) -> tuple[float, bool]:
    """Process one bar for an open trade. Returns (equity, closed)."""
    long = trade.direction == "long"
    trade.bars_held = index - trade.entry_index

    # Track excursion in R for the trade log.
    if initial_risk > 0:
        favourable = (candle.high - trade.entry_price) if long else (trade.entry_price - candle.low)
        adverse = (trade.entry_price - candle.low) if long else (candle.high - trade.entry_price)
        trade.max_favourable = max(trade.max_favourable, favourable / initial_risk)
        trade.max_adverse = max(trade.max_adverse, adverse / initial_risk)

    # --- Gap through the stop: fill at the open, which is what really happens.
    gapped = (long and candle.open <= trade.stop_price) or (not long and candle.open >= trade.stop_price)
    if gapped:
        equity = _close_remaining(trade, candle, index, spec, equity, "stop", price=candle.open)
        return equity, True

    # --- Stop hit intrabar. Checked BEFORE targets: when a bar spans both, we
    # assume the loss, because OHLCV cannot tell us which came first.
    stop_hit = (long and candle.low <= trade.stop_price) or (not long and candle.high >= trade.stop_price)
    if stop_hit:
        equity = _close_remaining(trade, candle, index, spec, equity, "stop", price=trade.stop_price)
        return equity, True

    # --- Take-profits, in order.
    filled_any = False
    still_open: list[tuple[float, float]] = []
    for target_price, quantity in remaining_targets:
        reached = (long and candle.high >= target_price) or (not long and candle.low <= target_price)
        if reached and quantity > 0:
            cost = _fill_cost(target_price, quantity, spec)
            pnl = (target_price - trade.entry_price) * quantity if long else \
                  (trade.entry_price - target_price) * quantity
            equity += pnl - cost
            trade.gross_pnl += pnl
            trade.costs += cost
            trade.quantity -= quantity
            trade.fills.append(
                Fill(index, candle.timestamp, target_price, quantity, "take_profit", cost)
            )
            filled_any = True
        else:
            still_open.append((target_price, quantity))

    remaining_targets[:] = still_open

    if trade.quantity <= 1e-12:
        _finalize(trade, index, candle.timestamp, remaining_targets, initial_risk, "take_profit")
        return equity, True

    # --- Breakeven and trailing stop management.
    if initial_risk > 0 and spec.management.move_stop_to_breakeven_at_rr and not breakeven_moved:
        threshold = spec.management.move_stop_to_breakeven_at_rr
        progress = (candle.high - trade.entry_price) / initial_risk if long else \
                   (trade.entry_price - candle.low) / initial_risk
        if progress >= threshold:
            trade.stop_price = trade.entry_price

    if spec.management.trailing_atr_multiple:
        atr_now = atr_values[index]
        if atr_now:
            distance = spec.management.trailing_atr_multiple * atr_now
            trail = candle.close - distance if long else candle.close + distance
            # A trailing stop only ever moves in the favourable direction.
            trade.stop_price = max(trade.stop_price, trail) if long else min(trade.stop_price, trail)

    # --- Time stop.
    if spec.management.max_bars_in_trade and trade.bars_held >= spec.management.max_bars_in_trade:
        equity = _close_remaining(trade, candle, index, spec, equity, "time_stop", price=candle.close)
        return equity, True

    # --- Rule-based exit.
    exit_rules = spec.exit_long if long else spec.exit_short
    if exit_rules and evaluate_all(exit_rules, features, index):
        equity = _close_remaining(trade, candle, index, spec, equity, "signal_exit", price=candle.close)
        return equity, True

    if filled_any:
        # Partial fills leave the trade open; record progress on the log.
        trade.exit_reason = "partial take-profit filled"
    return equity, False


def _close_remaining(
    trade: Trade, candle, index: int, spec: StrategySpec,
    equity: float, reason: str, price: float | None = None,
) -> float:
    """Close whatever quantity is left and finalise the trade record."""
    exit_price = price if price is not None else candle.close
    quantity = trade.quantity
    if quantity > 0:
        cost = _fill_cost(exit_price, quantity, spec)
        pnl = (exit_price - trade.entry_price) * quantity if trade.direction == "long" else \
              (trade.entry_price - exit_price) * quantity
        equity += pnl - cost
        trade.gross_pnl += pnl
        trade.costs += cost
        trade.quantity = 0.0
        trade.fills.append(Fill(index, candle.timestamp, exit_price, quantity, reason, cost))

    initial_risk = abs(trade.entry_price - trade.fills[0].price) if trade.fills else 0
    _finalize(trade, index, candle.timestamp, [], initial_risk, reason, exit_price)
    return equity


def _finalize(
    trade: Trade, index: int, timestamp: int, remaining: list,
    initial_risk: float, reason: str, exit_price: float | None = None,
) -> None:
    trade.exit_index = index
    trade.exit_time = timestamp
    trade.exit_reason = reason
    trade.bars_held = index - trade.entry_index
    trade.net_pnl = trade.gross_pnl - trade.costs

    # Weighted average exit across all non-entry fills.
    exits = [f for f in trade.fills if f.kind != "entry"]
    if exits:
        total_quantity = sum(f.quantity for f in exits)
        trade.exit_price = (
            sum(f.price * f.quantity for f in exits) / total_quantity if total_quantity else exit_price
        )
    else:
        trade.exit_price = exit_price

    entry_quantity = trade.fills[0].quantity if trade.fills else 0
    risk_amount = abs(trade.entry_price - trade.stop_price) * entry_quantity
    # R is measured against the ORIGINAL risk, not a trailed stop, so a 2R
    # winner means the same thing across every trade in the log.
    if risk_amount > 0:
        trade.r_multiple = trade.net_pnl / risk_amount


def _unrealised(trade: Trade, price: float) -> float:
    if trade.direction == "long":
        return (price - trade.entry_price) * trade.quantity
    return (trade.entry_price - price) * trade.quantity


def _at_breakeven(trade: Trade, stop_price: float) -> bool:
    return abs(stop_price - trade.entry_price) < 1e-9


def _fill_cost(price: float, quantity: float, spec: StrategySpec) -> float:
    """Commission + slippage + spread on one fill, in account currency."""
    notional = abs(price * quantity)
    bps = spec.costs.commission_bps + spec.costs.slippage_bps + spec.costs.spread_bps
    return notional * bps / 10_000


def _stop_price(
    spec: StrategySpec, direction: str, entry: float, atr_now: float, series: Series, index: int
) -> float:
    long = direction == "long"

    if spec.stop.type is StopType.ATR:
        distance = spec.stop.value * atr_now
    elif spec.stop.type is StopType.PERCENT:
        distance = entry * spec.stop.value / 100
    elif spec.stop.type is StopType.FIXED:
        distance = spec.stop.value
    else:  # STRUCTURE — beyond the recent swing, with an ATR buffer
        window = series.candles[max(0, index - 20) : index]
        if window:
            level = min(c.low for c in window) if long else max(c.high for c in window)
            buffer = spec.stop.value * atr_now
            return level - buffer if long else level + buffer
        distance = 1.5 * atr_now

    return entry - distance if long else entry + distance


def _target_prices(
    spec: StrategySpec, direction: str, entry: float, risk_per_unit: float, atr_now: float
) -> list[float]:
    long = direction == "long"
    prices: list[float] = []
    for value in spec.targets.values:
        if spec.targets.type is TargetType.RR:
            distance = risk_per_unit * value
        elif spec.targets.type is TargetType.PERCENT:
            distance = entry * value / 100
        else:  # ATR
            distance = atr_now * value
        prices.append(entry + distance if long else entry - distance)
    return prices


def _session_allows(spec: StrategySpec, timestamp: int) -> bool:
    """Apply the session/day filter to a bar's open time (UTC)."""
    session = spec.session
    if not session.active:
        return True

    when = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    if session.days and when.weekday() not in session.days:
        return False

    if session.start_hour is None or session.end_hour is None:
        return True

    hour = when.hour
    if session.start_hour <= session.end_hour:
        return session.start_hour <= hour < session.end_hour
    # Window wraps midnight (e.g. Sydney 21:00-06:00).
    return hour >= session.start_hour or hour < session.end_hour
