"""Natural language -> StrategySpec.

A deterministic phrase parser over a documented vocabulary. It is honest about
its own limits: every clause it recognises is recorded in `parse_notes`, and
every phrase it could not interpret is recorded in `unparsed_phrases` so the
user can see exactly what made it into the strategy and what did not. Silently
dropping half a sentence and backtesting the rest is how people end up trading
something they never described.

Example
-------
>>> spec = parse("Buy breakout with increasing volume on the 4h, 1.5 ATR stop, 3R target")
>>> [c.describe() for c in spec.entry_long]
['close crosses above highest(20)', 'volume_ratio(20) is greater than 1.5']
"""
from __future__ import annotations

import re

from trading.models import Timeframe
from trading.strategy.spec import (
    Condition,
    Costs,
    Op,
    SessionFilter,
    StopSpec,
    StopType,
    StrategySpec,
    TargetSpec,
    TargetType,
    TradeManagement,
)

# Named sessions in UTC hours.
_SESSIONS = {
    "london": (7, 16),
    "new york": (12, 21),
    "newyork": (12, 21),
    "ny": (12, 21),
    "asian": (0, 9),
    "asia": (0, 9),
    "tokyo": (0, 9),
    "sydney": (21, 6),
}

_TIMEFRAME_WORDS = {
    r"\b1\s*(?:m|min|minute)\b": Timeframe.M1,
    r"\b5\s*(?:m|min|minute)s?\b": Timeframe.M5,
    r"\b15\s*(?:m|min|minute)s?\b": Timeframe.M15,
    r"\b30\s*(?:m|min|minute)s?\b": Timeframe.M30,
    r"\b(?:1\s*)?(?:h|hour|hourly)\b": Timeframe.H1,
    r"\b4\s*(?:h|hour)s?\b": Timeframe.H4,
    r"\b(?:d|daily|1d|day)\b": Timeframe.D1,
    r"\b(?:w|weekly|1w|week)\b": Timeframe.W1,
}


def parse(text: str, symbol: str = "BTCUSDT", timeframe: Timeframe | None = None) -> StrategySpec:
    """Build a StrategySpec from an English description."""
    original = text.strip()
    lowered = original.lower()

    spec = StrategySpec(
        name=_derive_name(original),
        description=original,
        symbol=symbol,
        source_text=original,
    )

    consumed: list[str] = []   # phrases we successfully interpreted

    # --- timeframe -----------------------------------------------------------
    if timeframe is not None:
        spec.timeframe = timeframe
    else:
        # Longest patterns first so "4 hour" doesn't match the generic "hour" rule.
        for pattern, tf in sorted(_TIMEFRAME_WORDS.items(), key=lambda kv: -len(kv[0])):
            match = re.search(pattern, lowered)
            if match:
                spec.timeframe = tf
                consumed.append(match.group(0))
                spec.parse_notes.append(f"Timeframe set to {tf.value} from '{match.group(0).strip()}'.")
                break

    # --- direction -----------------------------------------------------------
    long_word = re.search(r"\b(buy|long|bullish)\b", lowered)
    short_word = re.search(r"\b(sell|short|bearish)\b", lowered)
    wants_long = bool(long_word)
    wants_short = bool(short_word)
    for word in (long_word, short_word):
        if word:
            consumed.append(word.group(0))
    if re.search(r"\blong[- ]only\b", lowered):
        wants_short = False
        spec.management.allow_short = False
        spec.parse_notes.append("Restricted to long trades only.")
    if re.search(r"\bshort[- ]only\b", lowered):
        wants_long = False
        spec.management.allow_long = False
        spec.parse_notes.append("Restricted to short trades only.")
    if not wants_long and not wants_short:
        # No direction stated: build both sides, which is the neutral reading.
        wants_long = wants_short = True
        spec.parse_notes.append("No direction stated, so symmetric long and short rules were built.")

    long_rules: list[Condition] = []
    short_rules: list[Condition] = []
    filters: list[Condition] = []

    # --- setup archetype -----------------------------------------------------
    matched_archetype = False

    if re.search(r"\bbreak(?:out|s|ing)?\b|\bbreak above\b|\bbreak below\b", lowered):
        period = _number_near(lowered, r"(\d+)[- ]?(?:bar|period|candle)", default=20)
        long_rules.append(Condition("close", Op.CROSS_ABOVE, f"highest({period})"))
        short_rules.append(Condition("close", Op.CROSS_BELOW, f"lowest({period})"))
        spec.parse_notes.append(
            f"Recognised a breakout setup: entry when price closes through the {period}-bar "
            f"high (long) or low (short)."
        )
        consumed.append("breakout")
        matched_archetype = True

    if re.search(r"\bpull ?back|\bretrace|\bdip\b|\bcorrection\b", lowered):
        long_rules.append(Condition("close", Op.GT, "ema(200)"))
        long_rules.append(Condition("close", Op.CROSS_ABOVE, "ema(20)"))
        short_rules.append(Condition("close", Op.LT, "ema(200)"))
        short_rules.append(Condition("close", Op.CROSS_BELOW, "ema(20)"))
        spec.parse_notes.append(
            "Recognised a pullback setup: trade in the direction of the 200 EMA, entering as "
            "price reclaims the 20 EMA after the pullback."
        )
        consumed.append("pullback")
        matched_archetype = True

    if re.search(r"\bmean.?reversion|\brevert|\boversold\b|\boverbought\b", lowered):
        rsi_period = _number_near(lowered, r"rsi\s*\(?\s*(\d+)", default=14)
        oversold = _number_near(lowered, r"(?:oversold|below)\s*(?:at\s*)?(\d+)", default=30)
        overbought = _number_near(lowered, r"(?:overbought|above)\s*(?:at\s*)?(\d+)", default=70)
        long_rules.append(Condition(f"rsi({rsi_period})", Op.CROSS_ABOVE, float(oversold)))
        short_rules.append(Condition(f"rsi({rsi_period})", Op.CROSS_BELOW, float(overbought)))
        spec.parse_notes.append(
            f"Recognised a mean-reversion setup: long when RSI({rsi_period}) crosses back above "
            f"{oversold}, short when it crosses back below {overbought}."
        )
        consumed.append("mean reversion")
        matched_archetype = True

    # Moving-average crossover, e.g. "50 EMA crosses above the 200 EMA".
    ma_cross = re.search(
        r"(\d+)\s*(ema|sma|ma)\b.{0,30}?cross(?:es|ing)?\s*(?:above|over|under|below)?.{0,20}?(\d+)\s*(ema|sma|ma)\b",
        lowered,
    )
    if ma_cross:
        fast_period, fast_kind, slow_period, slow_kind = ma_cross.groups()
        fast = f"{'sma' if fast_kind == 'sma' else 'ema'}({fast_period})"
        slow = f"{'sma' if slow_kind == 'sma' else 'ema'}({slow_period})"
        long_rules.append(Condition(fast, Op.CROSS_ABOVE, slow))
        short_rules.append(Condition(fast, Op.CROSS_BELOW, slow))
        spec.parse_notes.append(f"Recognised a moving-average crossover: {fast} against {slow}.")
        consumed.append(ma_cross.group(0))
        matched_archetype = True

    if re.search(r"\btrend follow|\bwith the trend|\btrend\b", lowered) and not matched_archetype:
        long_rules.append(Condition("close", Op.GT, "ema(200)"))
        long_rules.append(Condition("ema(20)", Op.CROSS_ABOVE, "ema(50)"))
        short_rules.append(Condition("close", Op.LT, "ema(200)"))
        short_rules.append(Condition("ema(20)", Op.CROSS_BELOW, "ema(50)"))
        spec.parse_notes.append(
            "Recognised a trend-following setup: 20/50 EMA cross in the direction of the 200 EMA."
        )
        consumed.append("trend following")
        matched_archetype = True

    # --- volume conditions ----------------------------------------------------
    volume_phrase = re.search(
        r"\b(increasing|rising|high|strong|above average|expanding)\s+volume\b", lowered
    )
    if volume_phrase:
        multiple = _float_near(lowered, r"(\d+(?:\.\d+)?)\s*x\s*(?:average\s*)?volume", default=1.5)
        condition = Condition("volume_ratio(20)", Op.GT, multiple)
        long_rules.append(condition)
        short_rules.append(Condition("volume_ratio(20)", Op.GT, multiple))
        spec.parse_notes.append(
            f"Volume confirmation required: current volume above {multiple:g}x its 20-bar average."
        )
        consumed.append(volume_phrase.group(0))
    elif re.search(r"\bvolume\b", lowered):
        long_rules.append(Condition("volume", Op.GT, "volume_sma(20)"))
        short_rules.append(Condition("volume", Op.GT, "volume_sma(20)"))
        spec.parse_notes.append("Volume confirmation required: volume above its 20-bar average.")
        consumed.append("volume")

    # --- explicit indicator filters -------------------------------------------
    adx_match = re.search(r"adx\s*(?:\(\s*(\d+)\s*\))?\s*(?:above|>|greater than)\s*(\d+)", lowered)
    if adx_match:
        period = adx_match.group(1) or "14"
        threshold = float(adx_match.group(2))
        filters.append(Condition(f"adx({period})", Op.GT, threshold))
        spec.parse_notes.append(f"Trend-strength filter: ADX({period}) above {threshold:g}.")
        consumed.append(adx_match.group(0))
    elif re.search(r"\badx\b|\bstrong trend\b|\btrending\b", lowered):
        filters.append(Condition("adx(14)", Op.GT, 25.0))
        spec.parse_notes.append("Trend-strength filter: ADX(14) above 25 (the standard threshold).")
        consumed.append("adx")

    if re.search(r"\babove\s+(?:the\s+)?200\b|\bbull market\b", lowered):
        filters.append(Condition("close", Op.GT, "ema(200)"))
        spec.parse_notes.append("Regime filter: only trade while price is above the 200 EMA.")
        consumed.append("200 ema filter")

    if re.search(r"\bvwap\b", lowered):
        long_rules.append(Condition("close", Op.GT, "vwap"))
        short_rules.append(Condition("close", Op.LT, "vwap"))
        spec.parse_notes.append("VWAP filter: longs only above session VWAP, shorts only below.")
        consumed.append("vwap")

    if re.search(r"\bmacd\b", lowered):
        long_rules.append(Condition("macd", Op.CROSS_ABOVE, "macd_signal"))
        short_rules.append(Condition("macd", Op.CROSS_BELOW, "macd_signal"))
        spec.parse_notes.append("MACD confirmation: signal-line cross in the trade direction.")
        consumed.append("macd")
        matched_archetype = True

    if re.search(r"\bsupertrend\b", lowered):
        long_rules.append(Condition("supertrend_dir", Op.GT, 0.0))
        short_rules.append(Condition("supertrend_dir", Op.LT, 0.0))
        spec.parse_notes.append("SuperTrend filter: trade only in the direction of the SuperTrend flip.")
        consumed.append("supertrend")
        matched_archetype = True

    if re.search(r"\bbollinger\b|\bbb\b", lowered):
        long_rules.append(Condition("close", Op.CROSS_ABOVE, "bb_upper(20)"))
        short_rules.append(Condition("close", Op.CROSS_BELOW, "bb_lower(20)"))
        spec.parse_notes.append("Bollinger breakout: entry on a close outside the 20-period bands.")
        consumed.append("bollinger")
        matched_archetype = True

    # --- fallback -------------------------------------------------------------
    if not matched_archetype and not long_rules and not short_rules:
        long_rules.append(Condition("ema(20)", Op.CROSS_ABOVE, "ema(50)"))
        short_rules.append(Condition("ema(20)", Op.CROSS_BELOW, "ema(50)"))
        spec.parse_notes.append(
            "No recognised setup keyword, so a default 20/50 EMA crossover was used. "
            "Describe the entry explicitly (breakout, pullback, mean reversion, MA cross, "
            "MACD, Bollinger, SuperTrend) for a strategy that matches your intent."
        )

    # --- stop -----------------------------------------------------------------
    atr_stop = re.search(r"(\d+(?:\.\d+)?)\s*(?:x\s*)?atr\s*stop|stop\s*(?:of|at)?\s*(\d+(?:\.\d+)?)\s*(?:x\s*)?atr", lowered)
    percent_stop = re.search(r"(\d+(?:\.\d+)?)\s*%\s*stop|stop\s*(?:of|at)?\s*(\d+(?:\.\d+)?)\s*%", lowered)
    if atr_stop:
        value = float(next(g for g in atr_stop.groups() if g))
        spec.stop = StopSpec(StopType.ATR, value)
        spec.parse_notes.append(f"Stop set to {value:g} x ATR(14).")
        consumed.append(atr_stop.group(0))
    elif percent_stop:
        value = float(next(g for g in percent_stop.groups() if g))
        spec.stop = StopSpec(StopType.PERCENT, value)
        spec.parse_notes.append(f"Stop set to {value:g}% from entry.")
        consumed.append(percent_stop.group(0))
    elif re.search(r"\bstructure stop|\bswing (?:low|high) stop|\bbelow the swing\b", lowered):
        spec.stop = StopSpec(StopType.STRUCTURE, 0.35)
        spec.parse_notes.append("Stop placed beyond the last swing with a 0.35 x ATR buffer.")
        consumed.append("structure stop")
    else:
        spec.parse_notes.append("No stop specified, so the default 1.5 x ATR stop was used.")

    # --- targets ---------------------------------------------------------------
    rr_match = re.search(r"(?:1\s*[:to]\s*(\d+(?:\.\d+)?))|(\d+(?:\.\d+)?)\s*r\b(?:\s*target)?", lowered)
    percent_target = re.search(r"(\d+(?:\.\d+)?)\s*%\s*(?:target|take profit|tp)", lowered)
    if percent_target:
        value = float(percent_target.group(1))
        spec.targets = TargetSpec(TargetType.PERCENT, [value])
        spec.parse_notes.append(f"Take profit at {value:g}% from entry.")
        consumed.append(percent_target.group(0))
    elif rr_match:
        value = float(next(g for g in rr_match.groups() if g))
        spec.targets = TargetSpec(TargetType.RR, [value])
        spec.parse_notes.append(f"Take profit at {value:g}R (reward:risk of 1:{value:g}).")
        consumed.append(rr_match.group(0))
    else:
        spec.parse_notes.append("No target specified, so the default 1.5R / 3R scale-out was used.")

    # --- risk -------------------------------------------------------------------
    risk_match = re.search(r"risk(?:ing)?\s*(\d+(?:\.\d+)?)\s*%", lowered)
    if risk_match:
        spec.risk_percent = float(risk_match.group(1))
        spec.parse_notes.append(f"Risk per trade set to {spec.risk_percent:g}% of equity.")
        consumed.append(risk_match.group(0))

    # --- trade management --------------------------------------------------------
    if re.search(r"\bbreak ?even\b", lowered):
        at_rr = _float_near(lowered, r"break ?even\s*(?:at|after)?\s*(\d+(?:\.\d+)?)\s*r", default=1.0)
        spec.management.move_stop_to_breakeven_at_rr = at_rr
        spec.parse_notes.append(f"Stop moves to breakeven at {at_rr:g}R.")
        consumed.append("breakeven")
    trailing = re.search(
        r"\btrail(?:ing)?\b(?:\s*stop)?(?:\s*of)?\s*(?:(\d+(?:\.\d+)?)\s*(?:x\s*)?atr)?", lowered
    )
    if trailing:
        multiple = float(trailing.group(1)) if trailing.group(1) else 2.0
        spec.management.trailing_atr_multiple = multiple
        spec.parse_notes.append(f"Trailing stop set to {multiple:g} x ATR.")
        # Consume the whole matched phrase so its words are not later reported
        # as ignored — telling a user their instruction had no effect when it
        # did is worse than saying nothing.
        consumed.append(trailing.group(0))
    time_stop = re.search(r"(?:close|exit)\s*(?:after|within)\s*(\d+)\s*(?:bar|candle)s?", lowered)
    if time_stop:
        spec.management.max_bars_in_trade = int(time_stop.group(1))
        spec.parse_notes.append(f"Time stop: close any trade still open after {time_stop.group(1)} bars.")
        consumed.append(time_stop.group(0))

    # --- session -----------------------------------------------------------------
    for name, (start, end) in _SESSIONS.items():
        if re.search(rf"\b{re.escape(name)}\s*(?:session)?\b", lowered):
            spec.session = SessionFilter(start_hour=start, end_hour=end)
            spec.parse_notes.append(
                f"Session filter: trade only during the {name.title()} session "
                f"({start:02d}:00-{end:02d}:00 UTC)."
            )
            consumed.append(name)
            break

    # --- costs --------------------------------------------------------------------
    commission = re.search(r"(\d+(?:\.\d+)?)\s*(?:bps|basis points)\s*commission", lowered)
    if commission:
        spec.costs = Costs(commission_bps=float(commission.group(1)))
        spec.parse_notes.append(f"Commission set to {commission.group(1)} bps per side.")
        consumed.append(commission.group(0))

    # Apply direction preferences.
    spec.entry_long = long_rules if wants_long and spec.management.allow_long else []
    spec.entry_short = short_rules if wants_short and spec.management.allow_short else []
    spec.filters = filters

    if not spec.entry_long:
        spec.management.allow_long = False
    if not spec.entry_short:
        spec.management.allow_short = False

    spec.unparsed_phrases = _unparsed(original, consumed)
    if spec.unparsed_phrases:
        spec.parse_notes.append(
            "These phrases were not interpreted and had no effect on the strategy: "
            + "; ".join(f"'{p}'" for p in spec.unparsed_phrases)
        )
    return spec


def _derive_name(text: str) -> str:
    words = re.sub(r"[^a-zA-Z0-9 ]", "", text).split()
    if not words:
        return "Untitled Strategy"
    return " ".join(w.capitalize() for w in words[:6])


def _number_near(text: str, pattern: str, default: int) -> int:
    match = re.search(pattern, text)
    if not match:
        return default
    for group in match.groups():
        if group and group.isdigit():
            return int(group)
    return default


def _float_near(text: str, pattern: str, default: float) -> float:
    match = re.search(pattern, text)
    if not match:
        return default
    for group in match.groups():
        if group:
            try:
                return float(group)
            except ValueError:
                continue
    return default


# Words that carry no strategy meaning and shouldn't be reported as "unparsed".
_STOPWORDS = {
    "a", "an", "the", "and", "or", "with", "on", "in", "at", "to", "of", "for", "when",
    "if", "then", "is", "are", "be", "using", "use", "my", "i", "want", "please", "strategy",
    "trade", "trading", "entry", "exit", "signal", "signals", "setup", "rule", "rules",
    "only", "also", "should", "would", "that", "this", "it", "as", "by", "from", "into",
}


def _unparsed(original: str, consumed: list[str]) -> list[str]:
    """Report meaningful words the parser did not act on."""
    remaining = original.lower()
    for phrase in consumed:
        remaining = remaining.replace(phrase.lower(), " ")

    words = re.findall(r"[a-zA-Z][a-zA-Z0-9%\.]*", remaining)
    leftovers = [
        w for w in words
        if w not in _STOPWORDS and len(w) > 2
    ]
    # Collapse duplicates while preserving order.
    seen: set[str] = set()
    unique = []
    for word in leftovers:
        if word not in seen:
            seen.add(word)
            unique.append(word)
    return unique[:12]
