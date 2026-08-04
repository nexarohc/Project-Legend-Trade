"""Pine Script v6 generation from a StrategySpec.

The generated script is meant to reproduce the backtester's behaviour on
TradingView, so the translation is deliberate about the places the two engines
differ by default:

* `highest(n)` in this platform excludes the current bar, so it compiles to
  `ta.highest(high, n)[1]`. Without the `[1]` a breakout rule can never fire,
  because the current bar's own high is inside the window it must exceed.
* `calc_on_every_tick=false` keeps TradingView evaluating on bar close, which
  is what the backtester does.
* `process_orders_on_close=false` leaves fills on the next bar's open, matching
  the backtester's execution model.
* Commission and slippage are declared in the `strategy()` call from the spec's
  own cost settings, so the TradingView Strategy Tester charges the same
  friction the local backtest did.

Every generated block carries a comment explaining what it does and why.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from trading.strategy.spec import (
    Condition,
    Op,
    StopType,
    StrategySpec,
    TargetType,
)


class PineGenerationError(ValueError):
    """Raised when a spec references something with no Pine equivalent."""


@dataclass
class _Builder:
    """Collects deduplicated indicator declarations and the variable names for them."""

    declarations: list[str] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)
    _names: dict[str, str] = field(default_factory=dict)
    _seen_inputs: set[str] = field(default_factory=set)

    def declare(self, key: str, name: str, code: str) -> str:
        """Register a calculation once and return its variable name."""
        if key not in self._names:
            self._names[key] = name
            self.declarations.append(code)
        return self._names[key]

    def add_input(self, key: str, code: str) -> None:
        if key not in self._seen_inputs:
            self._seen_inputs.add(key)
            self.inputs.append(code)


def _feature_to_pine(feature: str, builder: _Builder) -> str:
    """Translate one feature name into a Pine expression or variable."""
    key = feature.strip().lower()

    # Bare price/volume series need no declaration.
    direct = {
        "close": "close", "price": "close", "open": "open", "high": "high",
        "low": "low", "volume": "volume", "hl2": "hl2", "median": "hl2",
        "hlc3": "hlc3", "typical": "hlc3",
        "body": "math.abs(close - open)",
        "range": "(high - low)",
        "body_ratio": "(high - low > 0 ? math.abs(close - open) / (high - low) : 0)",
    }
    if key in direct:
        return direct[key]

    name, args = _split(key)

    if name in ("ema", "sma", "rma"):
        period = args[0] if args else 20
        fn = {"ema": "ta.ema", "sma": "ta.sma", "rma": "ta.rma"}[name]
        var = f"{name}{period}"
        builder.add_input(var, f'{var}Len = input.int({period}, "{name.upper()} Length", minval=1, group="Indicators")')
        return builder.declare(key, var, f"{var} = {fn}(close, {var}Len)")

    if name == "rsi":
        period = args[0] if args else 14
        builder.add_input("rsi", f'rsiLen = input.int({period}, "RSI Length", minval=1, group="Indicators")')
        return builder.declare(key, "rsiValue", "rsiValue = ta.rsi(close, rsiLen)")

    if name == "atr":
        period = args[0] if args else 14
        builder.add_input("atr", f'atrLen = input.int({period}, "ATR Length", minval=1, group="Risk")')
        return builder.declare(key, "atrValue", "atrValue = ta.atr(atrLen)")

    if name == "cci":
        period = args[0] if args else 20
        return builder.declare(key, "cciValue", f"cciValue = ta.cci(close, {period})")

    if name == "mfi":
        period = args[0] if args else 14
        return builder.declare(key, "mfiValue", f"mfiValue = ta.mfi(hlc3, {period})")

    if name == "obv":
        return builder.declare(key, "obvValue", "obvValue = ta.obv")

    if name == "vwap":
        return builder.declare(key, "vwapValue", "vwapValue = ta.vwap")

    if name in ("macd", "macd_signal", "macd_hist"):
        builder.declare(
            "macd_tuple", "macdLine",
            "[macdLine, macdSignal, macdHist] = ta.macd(close, 12, 26, 9)",
        )
        return {"macd": "macdLine", "macd_signal": "macdSignal", "macd_hist": "macdHist"}[name]

    if name in ("adx", "plus_di", "minus_di"):
        period = args[0] if args else 14
        builder.add_input("adx", f'adxLen = input.int({period}, "ADX/DI Length", minval=1, group="Indicators")')
        builder.declare(
            "dmi_tuple", "diPlus",
            "[diPlus, diMinus, adxValue] = ta.dmi(adxLen, adxLen)",
        )
        return {"adx": "adxValue", "plus_di": "diPlus", "minus_di": "diMinus"}[name]

    if name in ("bb_upper", "bb_middle", "bb_lower"):
        period = args[0] if args else 20
        builder.add_input("bb", f'bbLen = input.int({period}, "Bollinger Length", minval=1, group="Indicators")')
        builder.declare(
            "bb_tuple", "bbMiddle",
            "[bbMiddle, bbUpper, bbLower] = ta.bb(close, bbLen, 2.0)",
        )
        return {"bb_upper": "bbUpper", "bb_middle": "bbMiddle", "bb_lower": "bbLower"}[name]

    if name in ("stoch_k", "stoch_d"):
        period = args[0] if args else 14
        builder.declare(
            "stoch_tuple", "stochK",
            f"stochK = ta.stoch(close, high, low, {period})\nstochD = ta.sma(stochK, 3)",
        )
        return "stochK" if name == "stoch_k" else "stochD"

    if name in ("stoch_rsi_k", "stoch_rsi_d"):
        builder.declare(
            "stochrsi_tuple", "stochRsiK",
            "srcRsi = ta.rsi(close, 14)\n"
            "stochRsiK = ta.sma(ta.stoch(srcRsi, srcRsi, srcRsi, 14), 3)\n"
            "stochRsiD = ta.sma(stochRsiK, 3)",
        )
        return "stochRsiK" if name == "stoch_rsi_k" else "stochRsiD"

    if name in ("supertrend", "supertrend_dir"):
        period = args[0] if args else 10
        builder.add_input(
            "supertrend",
            f'stAtrLen = input.int({period}, "SuperTrend ATR Length", minval=1, group="Indicators")\n'
            f'stFactor = input.float(3.0, "SuperTrend Factor", minval=0.1, step=0.1, group="Indicators")',
        )
        builder.declare(
            "supertrend_tuple", "stLine",
            "[stLine, stDirection] = ta.supertrend(stFactor, stAtrLen)",
        )
        if name == "supertrend":
            return "stLine"
        # Pine reports -1 when bullish, the inverse of this platform's
        # convention, so normalise it here rather than inverting every rule.
        return builder.declare(
            "supertrend_dir_norm", "stDirNormalised",
            "stDirNormalised = stDirection < 0 ? 1 : -1",
        )

    if name in ("highest", "lowest"):
        period = args[0] if args else 20
        source = "high" if name == "highest" else "low"
        var = f"{name}{period}"
        builder.add_input(
            f"{name}_len",
            f'{var}Len = input.int({period}, "{name.capitalize()} Lookback", minval=1, group="Entry")',
        )
        # [1] excludes the current bar, matching this platform's definition.
        return builder.declare(
            key, var, f"{var} = ta.{name}({source}, {var}Len)[1]"
        )

    if name == "volume_sma":
        period = args[0] if args else 20
        return builder.declare(key, f"volSma{period}", f"volSma{period} = ta.sma(volume, {period})")

    if name == "volume_ratio":
        period = args[0] if args else 20
        builder.declare(f"volume_sma({period})", f"volSma{period}", f"volSma{period} = ta.sma(volume, {period})")
        return builder.declare(
            key, f"volRatio{period}",
            f"volRatio{period} = volSma{period} > 0 ? volume / volSma{period} : 0.0",
        )

    raise PineGenerationError(
        f"No Pine Script equivalent is defined for the feature '{feature}'."
    )


def _split(key: str) -> tuple[str, list[int]]:
    if "(" not in key:
        return key, []
    name, _, rest = key.partition("(")
    args = [int(a.strip()) for a in rest.rstrip(")").split(",") if a.strip().isdigit()]
    return name, args


def _condition_to_pine(condition: Condition, builder: _Builder) -> str:
    left = _feature_to_pine(condition.left, builder)
    if isinstance(condition.right, (int, float)):
        right = f"{float(condition.right):g}"
    else:
        right = _feature_to_pine(str(condition.right), builder)

    if condition.op is Op.CROSS_ABOVE:
        return f"ta.crossover({left}, {right})"
    if condition.op is Op.CROSS_BELOW:
        return f"ta.crossunder({left}, {right})"
    if condition.op is Op.RISING:
        lookback = int(condition.right) if isinstance(condition.right, (int, float)) else 1
        return f"{left} > {left}[{lookback}]"
    if condition.op is Op.FALLING:
        lookback = int(condition.right) if isinstance(condition.right, (int, float)) else 1
        return f"{left} < {left}[{lookback}]"
    return f"{left} {condition.op.value} {right}"


def _conditions_to_pine(conditions: list[Condition], builder: _Builder) -> str:
    if not conditions:
        return "false"
    return " and ".join(f"({_condition_to_pine(c, builder)})" for c in conditions)


def generate_strategy(spec: StrategySpec) -> str:
    """Emit a complete, compilable Pine v6 strategy script."""
    builder = _Builder()

    long_expr = _conditions_to_pine(spec.entry_long, builder) if spec.entry_long else "false"
    short_expr = _conditions_to_pine(spec.entry_short, builder) if spec.entry_short else "false"
    filter_expr = _conditions_to_pine(spec.filters, builder) if spec.filters else "true"
    exit_long_expr = _conditions_to_pine(spec.exit_long, builder) if spec.exit_long else "false"
    exit_short_expr = _conditions_to_pine(spec.exit_short, builder) if spec.exit_short else "false"

    # ATR is needed for stops/targets even if no rule referenced it.
    atr_var = _feature_to_pine("atr(14)", builder)

    lines: list[str] = []
    add = lines.append

    # --- header --------------------------------------------------------------
    add("// " + "=" * 76)
    add(f"// {spec.name}")
    add("// " + "-" * 76)
    for chunk in _wrap(spec.description or "Generated strategy.", 74):
        add(f"// {chunk}")
    add("//")
    add("// Generated by Dex Trading Terminal from a validated strategy specification.")
    add("//")
    add("// Execution model (kept aligned with the platform's own backtester):")
    add("//   - Signals evaluate on bar close; fills happen on the next bar's open.")
    add("//   - Commission and slippage below mirror the spec's cost assumptions.")
    add("//   - Breakout lookbacks use [1] so the current bar is excluded from its")
    add("//     own high/low window.")
    add("//")
    add("// Backtest results in the TradingView Strategy Tester will still differ")
    add("// slightly from the platform's: TradingView fills intrabar stops using its")
    add("// own assumptions about bar traversal.")
    add("// " + "=" * 76)
    add("")
    add("//@version=6")

    # --- declaration ---------------------------------------------------------
    commission = spec.costs.commission_bps / 100  # bps -> percent
    slippage_ticks = max(1, int(round(spec.costs.slippage_bps / 2)))
    add(f'strategy("{_escape(spec.name)}",')
    add(f'     overlay              = true,')
    add(f'     initial_capital      = {spec.initial_capital:.0f},')
    add(f'     default_qty_type     = strategy.percent_of_equity,')
    add(f'     default_qty_value    = 100,')
    add(f'     commission_type      = strategy.commission.percent,')
    add(f'     commission_value     = {commission:.4f},')
    add(f'     slippage             = {slippage_ticks},')
    add(f'     calc_on_every_tick   = false,')
    add(f'     process_orders_on_close = false,')
    add(f'     pyramiding           = 0)')
    add("")

    # --- inputs --------------------------------------------------------------
    add("// " + "-" * 76)
    add("// Inputs — every parameter is exposed so the strategy can be re-optimised")
    add("// on the chart without editing code.")
    add("// " + "-" * 76)
    add(f'riskPercent = input.float({spec.risk_percent}, "Risk per trade (%)", '
        f'minval=0.1, maxval=10, step=0.1, group="Risk")')
    add(f'stopValue   = input.float({spec.stop.value}, "Stop ({spec.stop.type.value})", '
        f'minval=0.1, step=0.1, group="Risk")')
    for i, value in enumerate(spec.targets.values, start=1):
        add(f'tp{i}Value    = input.float({value}, "Take Profit {i} ({spec.targets.type.value})", '
            f'minval=0.1, step=0.1, group="Risk")')
    add('useLongs    = input.bool(' + ("true" if spec.entry_long else "false") +
        ', "Enable long trades", group="Direction")')
    add('useShorts   = input.bool(' + ("true" if spec.entry_short else "false") +
        ', "Enable short trades", group="Direction")')
    for line in builder.inputs:
        add(line)

    if spec.session.active and spec.session.start_hour is not None:
        add(f'tradingSession = input.session("{spec.session.start_hour:02d}00-'
            f'{spec.session.end_hour:02d}00", "Trading session (exchange time)", group="Filters")')
    add("")

    # --- higher timeframe bias ----------------------------------------------
    add("// " + "-" * 76)
    add("// Higher-timeframe bias. request.security with lookahead_off is what keeps")
    add("// this free of repainting: the higher-timeframe value is only used once")
    add("// that bar has actually closed.")
    add("// " + "-" * 76)
    add('htfEnabled = input.bool(false, "Require higher-timeframe agreement", group="Filters")')
    add('htfTimeframe = input.timeframe("D", "Higher timeframe", group="Filters")')
    add("htfClose = request.security(syminfo.tickerid, htfTimeframe, close, "
        "lookahead=barmerge.lookahead_off)")
    add("htfEma   = request.security(syminfo.tickerid, htfTimeframe, ta.ema(close, 50), "
        "lookahead=barmerge.lookahead_off)")
    add("htfBullish = htfClose > htfEma")
    add("htfBearish = htfClose < htfEma")
    add("")

    # --- indicators ----------------------------------------------------------
    add("// " + "-" * 76)
    add("// Indicator calculations")
    add("// " + "-" * 76)
    for declaration in builder.declarations:
        add(declaration)
    add("")

    # --- filters -------------------------------------------------------------
    add("// " + "-" * 76)
    add("// Filters — conditions that must hold before any entry is considered.")
    add("// " + "-" * 76)
    add(f"passesFilters = {filter_expr}")
    if spec.session.active and spec.session.start_hour is not None:
        add("inSession = not na(time(timeframe.period, tradingSession))")
    else:
        add("inSession = true")
    add("tradingAllowed = passesFilters and inSession")
    add("")

    # --- entries -------------------------------------------------------------
    add("// " + "-" * 76)
    add("// Entry conditions")
    for condition in spec.entry_long:
        add(f"//   long : {condition.describe()}")
    for condition in spec.entry_short:
        add(f"//   short: {condition.describe()}")
    add("// " + "-" * 76)
    add(f"longSignal  = {long_expr}")
    add(f"shortSignal = {short_expr}")
    add("longCondition  = useLongs  and longSignal  and tradingAllowed and "
        "(not htfEnabled or htfBullish)")
    add("shortCondition = useShorts and shortSignal and tradingAllowed and "
        "(not htfEnabled or htfBearish)")
    add("")

    # --- risk levels ---------------------------------------------------------
    add("// " + "-" * 76)
    add(f"// Stop and target levels — {spec.stop.describe()}; {spec.targets.describe()}.")
    add("// " + "-" * 76)
    add(_stop_distance_pine(spec, atr_var))
    add("")
    add("// Position size comes from the stop distance so every trade risks the same")
    add("// percentage of equity regardless of volatility.")
    add("riskAmount = strategy.equity * (riskPercent / 100)")
    add("positionSize = stopDistance > 0 ? riskAmount / stopDistance : 0.0")
    add("")

    add("var float entryPrice = na")
    add("var float stopPrice  = na")
    for i in range(1, len(spec.targets.values) + 1):
        add(f"var float tp{i}Price = na")
    add("")

    # --- order placement -----------------------------------------------------
    add("// " + "-" * 76)
    add("// Order placement")
    add("// " + "-" * 76)
    add("if longCondition and strategy.position_size == 0")
    add("    entryPrice := close")
    add("    stopPrice  := close - stopDistance")
    for i, _ in enumerate(spec.targets.values, start=1):
        add(f"    tp{i}Price   := close + {_target_distance_pine(spec, i)}")
    add("    strategy.entry(\"Long\", strategy.long, qty=positionSize)")
    add("")
    add("if shortCondition and strategy.position_size == 0")
    add("    entryPrice := close")
    add("    stopPrice  := close + stopDistance")
    for i, _ in enumerate(spec.targets.values, start=1):
        add(f"    tp{i}Price   := close - {_target_distance_pine(spec, i)}")
    add("    strategy.entry(\"Short\", strategy.short, qty=positionSize)")
    add("")

    # --- exits ---------------------------------------------------------------
    add("// Scale out at each target. Every exit order carries the stop as well, so")
    add("// each remaining portion of the position stays protected after a partial")
    add("// fill. The final order omits qty_percent and therefore covers whatever is")
    add("// left — issuing a separate full-size stop order on top of these would")
    add("// close the position twice.")
    allocations = spec.targets.normalized_allocations()
    for i, allocation in enumerate(allocations, start=1):
        final = i == len(allocations)
        qty_arg = "" if final else f", qty_percent={allocation * 100:.0f}"
        add(f'strategy.exit("TP{i} L", from_entry="Long", limit=tp{i}Price, '
            f'stop=stopPrice{qty_arg})')
        add(f'strategy.exit("TP{i} S", from_entry="Short", limit=tp{i}Price, '
            f'stop=stopPrice{qty_arg})')
    add("")

    if spec.exit_long or spec.exit_short:
        add("// Rule-based exits")
        add(f"exitLong  = {exit_long_expr}")
        add(f"exitShort = {exit_short_expr}")
        add('if exitLong and strategy.position_size > 0')
        add('    strategy.close("Long", comment="Signal exit")')
        add('if exitShort and strategy.position_size < 0')
        add('    strategy.close("Short", comment="Signal exit")')
        add("")

    if spec.management.max_bars_in_trade:
        add(f"// Time stop — close anything still open after "
            f"{spec.management.max_bars_in_trade} bars.")
        add(f"if strategy.position_size != 0 and "
            f"(bar_index - strategy.opentrades.entry_bar_index(0)) >= "
            f"{spec.management.max_bars_in_trade}")
        add('    strategy.close_all(comment="Time stop")')
        add("")

    if spec.management.move_stop_to_breakeven_at_rr:
        rr = spec.management.move_stop_to_breakeven_at_rr
        add(f"// Move the stop to breakeven once the trade is {rr:g}R in profit.")
        add(f"if strategy.position_size > 0 and "
            f"high >= entryPrice + stopDistance * {rr:g}")
        add("    stopPrice := math.max(stopPrice, entryPrice)")
        add(f"if strategy.position_size < 0 and "
            f"low <= entryPrice - stopDistance * {rr:g}")
        add("    stopPrice := math.min(stopPrice, entryPrice)")
        add("")

    if spec.management.trailing_atr_multiple:
        multiple = spec.management.trailing_atr_multiple
        add(f"// Trailing stop at {multiple:g} x ATR, ratcheting one way only.")
        add(f"if strategy.position_size > 0")
        add(f"    stopPrice := math.max(stopPrice, close - {atr_var} * {multiple:g})")
        add(f"if strategy.position_size < 0")
        add(f"    stopPrice := math.min(stopPrice, close + {atr_var} * {multiple:g})")
        add("")

    # --- plots ---------------------------------------------------------------
    add("// " + "-" * 76)
    add("// Visualisation")
    add("// " + "-" * 76)
    add("plot(strategy.position_size != 0 ? stopPrice : na, \"Stop\", "
        "color=color.new(color.red, 0), style=plot.style_linebr, linewidth=2)")
    for i in range(1, len(spec.targets.values) + 1):
        add(f"plot(strategy.position_size != 0 ? tp{i}Price : na, \"TP{i}\", "
            f"color=color.new(color.teal, 30), style=plot.style_linebr)")
    add("plotshape(longCondition,  title=\"Long signal\",  location=location.belowbar, "
        "style=shape.triangleup,   size=size.tiny, color=color.new(color.teal, 0))")
    add("plotshape(shortCondition, title=\"Short signal\", location=location.abovebar, "
        "style=shape.triangledown, size=size.tiny, color=color.new(color.red, 0))")
    add("")

    # --- alerts --------------------------------------------------------------
    add("// " + "-" * 76)
    add("// Alerts. alertcondition() creates the alert options in the UI; alert()")
    add("// fires a message payload that a webhook receiver can act on.")
    add("// " + "-" * 76)
    add('alertcondition(longCondition,  title="Long entry",  message="LONG {{ticker}} @ {{close}}")')
    add('alertcondition(shortCondition, title="Short entry", message="SHORT {{ticker}} @ {{close}}")')
    add("")
    add("if longCondition")
    add('    alert("LONG " + syminfo.ticker + " @ " + str.tostring(close, format.mintick) + '
        '" | stop " + str.tostring(stopPrice, format.mintick), alert.freq_once_per_bar_close)')
    add("if shortCondition")
    add('    alert("SHORT " + syminfo.ticker + " @ " + str.tostring(close, format.mintick) + '
        '" | stop " + str.tostring(stopPrice, format.mintick), alert.freq_once_per_bar_close)')

    return "\n".join(lines) + "\n"


def _stop_distance_pine(spec: StrategySpec, atr_var: str) -> str:
    if spec.stop.type is StopType.ATR:
        return f"stopDistance = {atr_var} * stopValue"
    if spec.stop.type is StopType.PERCENT:
        return "stopDistance = close * (stopValue / 100)"
    if spec.stop.type is StopType.FIXED:
        return "stopDistance = stopValue"
    # STRUCTURE — distance from the recent swing, with an ATR buffer.
    return (
        "swingLow  = ta.lowest(low, 20)[1]\n"
        "swingHigh = ta.highest(high, 20)[1]\n"
        f"stopDistance = math.max(math.abs(close - swingLow), {atr_var} * stopValue)"
    )


def _target_distance_pine(spec: StrategySpec, index: int) -> str:
    if spec.targets.type is TargetType.RR:
        return f"stopDistance * tp{index}Value"
    if spec.targets.type is TargetType.PERCENT:
        return f"close * (tp{index}Value / 100)"
    return f"atrValue * tp{index}Value"


def generate_indicator(spec: StrategySpec) -> str:
    """Emit a signals-only Pine v6 indicator (no order placement).

    Useful for putting the entry logic on a chart alongside another strategy,
    or for driving alerts without running the Strategy Tester.
    """
    builder = _Builder()
    long_expr = _conditions_to_pine(spec.entry_long, builder) if spec.entry_long else "false"
    short_expr = _conditions_to_pine(spec.entry_short, builder) if spec.entry_short else "false"
    filter_expr = _conditions_to_pine(spec.filters, builder) if spec.filters else "true"

    lines: list[str] = []
    add = lines.append

    add("// " + "=" * 76)
    add(f"// {spec.name} — signals only")
    add("//")
    for chunk in _wrap(spec.description or "Generated indicator.", 74):
        add(f"// {chunk}")
    add("//")
    add("// Plots the same entry conditions as the strategy version without placing")
    add("// orders, so it can be overlaid on a chart that already runs a strategy.")
    add("// " + "=" * 76)
    add("")
    add("//@version=6")
    add(f'indicator("{_escape(spec.name)} [Signals]", overlay=true)')
    add("")

    if builder.inputs:
        add("// Inputs")
        for line in builder.inputs:
            add(line)
        add("")

    add("// Indicator calculations")
    for declaration in builder.declarations:
        add(declaration)
    add("")

    add("// Entry conditions")
    for condition in spec.entry_long:
        add(f"//   long : {condition.describe()}")
    for condition in spec.entry_short:
        add(f"//   short: {condition.describe()}")
    add(f"passesFilters = {filter_expr}")
    add(f"longSignal  = ({long_expr}) and passesFilters")
    add(f"shortSignal = ({short_expr}) and passesFilters")
    add("")

    add("plotshape(longSignal,  title=\"Long\",  location=location.belowbar, "
        "style=shape.triangleup,   size=size.small, color=color.new(color.teal, 0), text=\"L\")")
    add("plotshape(shortSignal, title=\"Short\", location=location.abovebar, "
        "style=shape.triangledown, size=size.small, color=color.new(color.red, 0), text=\"S\")")
    add("")
    add("bgcolor(longSignal ? color.new(color.teal, 88) : shortSignal ? "
        "color.new(color.red, 88) : na)")
    add("")
    add('alertcondition(longSignal,  title="Long signal",  message="LONG {{ticker}} @ {{close}}")')
    add('alertcondition(shortSignal, title="Short signal", message="SHORT {{ticker}} @ {{close}}")')

    return "\n".join(lines) + "\n"


def generate(spec: StrategySpec, kind: str = "strategy") -> dict:
    """Generate Pine and return it with an explanation of what was produced."""
    if kind == "indicator":
        code = generate_indicator(spec)
    else:
        code = generate_strategy(spec)

    return {
        "kind": kind,
        "version": 6,
        "code": code,
        "line_count": code.count("\n"),
        "explanation": _explain(spec, kind),
        "install_steps": [
            "Open the chart on TradingView and click Pine Editor at the bottom.",
            "Paste the script over the default template, replacing everything.",
            "Click Save, give it a name, then click Add to chart.",
            "For the strategy version, open the Strategy Tester tab to see performance.",
            "To trade the signals, open the alert dialog, pick this script as the condition, "
            "and point the webhook at your receiver.",
        ],
        "notes": [
            "TradingView's intrabar fill assumptions differ from this platform's backtester, so "
            "Strategy Tester numbers will not match exactly. The platform's engine assumes the "
            "stop fills first when a bar spans both stop and target; TradingView does not always.",
            "Breakout lookbacks are compiled with [1] so the current bar is excluded from its own "
            "high/low window — without that the condition could never be true.",
            "request.security uses lookahead_off, so the higher-timeframe filter does not repaint.",
        ],
    }


def _explain(spec: StrategySpec, kind: str) -> list[str]:
    described = spec.describe()
    explanation = [
        f"This is a Pine Script v6 {kind} implementing '{spec.name}'.",
        f"Entry (long): {'; '.join(described['entry_logic']['long'])}.",
        f"Entry (short): {'; '.join(described['entry_logic']['short'])}.",
        f"Filters: {'; '.join(described['filters'])}.",
        f"Stop: {described['stop_logic']}.",
        f"Targets: {described['take_profit_logic']}.",
        f"Sizing: {described['position_sizing']}",
    ]
    if kind == "strategy":
        explanation.append(f"Costs declared to the Strategy Tester: {described['costs']}.")
    explanation.extend(described["risk_management"])
    return explanation


def _escape(text: str) -> str:
    return text.replace('"', "'")


def _wrap(text: str, width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        if len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines or [""]
