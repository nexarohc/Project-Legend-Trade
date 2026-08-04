"""Strategy specification: the structured form every strategy is expressed in.

One spec drives three things — the backtester, the Pine Script generator, and
the human-readable description — so a strategy that backtests one way cannot
silently compile into Pine that does something else.

`from_text` turns plain English into a spec. It is a deterministic rule parser,
not a language model: it recognises a documented vocabulary and reports exactly
what it understood and what it ignored, so a user is never left guessing why
their sentence produced a particular rule. The LLM layer in `narrative.py` can
propose a spec too, but it emits this same structure and gets validated the same
way.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import Enum

from trading.models import Timeframe


class Op(str, Enum):
    GT = ">"
    LT = "<"
    GTE = ">="
    LTE = "<="
    CROSS_ABOVE = "crosses_above"
    CROSS_BELOW = "crosses_below"
    RISING = "rising"
    FALLING = "falling"

    @property
    def description(self) -> str:
        return {
            ">": "is greater than",
            "<": "is less than",
            ">=": "is at least",
            "<=": "is at most",
            "crosses_above": "crosses above",
            "crosses_below": "crosses below",
            "rising": "is rising over",
            "falling": "is falling over",
        }[self.value]


@dataclass
class Condition:
    """One comparison between a feature and either another feature or a constant."""

    left: str
    op: Op
    right: str | float

    def describe(self) -> str:
        right = self.right if isinstance(self.right, str) else f"{self.right:g}"
        if self.op in (Op.RISING, Op.FALLING):
            return f"{self.left} {self.op.description} {right} bars"
        return f"{self.left} {self.op.description} {right}"

    def to_dict(self) -> dict:
        return {"left": self.left, "op": self.op.value, "right": self.right}

    @classmethod
    def from_dict(cls, data: dict) -> "Condition":
        return cls(left=data["left"], op=Op(data["op"]), right=data["right"])


class StopType(str, Enum):
    ATR = "atr"                # multiple of ATR
    PERCENT = "percent"        # percentage of entry
    STRUCTURE = "structure"    # beyond the last swing
    FIXED = "fixed"            # absolute price distance


class TargetType(str, Enum):
    RR = "rr"                  # multiple of risk
    PERCENT = "percent"
    ATR = "atr"


@dataclass
class StopSpec:
    type: StopType = StopType.ATR
    value: float = 1.5

    def describe(self) -> str:
        return {
            StopType.ATR: f"{self.value:g} x ATR(14) from entry",
            StopType.PERCENT: f"{self.value:g}% from entry",
            StopType.STRUCTURE: f"beyond the last swing, with a {self.value:g} x ATR buffer",
            StopType.FIXED: f"{self.value:g} price units from entry",
        }[self.type]

    def to_dict(self) -> dict:
        return {"type": self.type.value, "value": self.value}


@dataclass
class TargetSpec:
    type: TargetType = TargetType.RR
    values: list[float] = field(default_factory=lambda: [1.5, 3.0])
    # Fraction of the position closed at each target; the remainder rides to the last.
    allocations: list[float] = field(default_factory=list)

    def describe(self) -> str:
        unit = {"rr": "R", "percent": "%", "atr": "x ATR"}[self.type.value]
        parts = [f"TP{i + 1} at {v:g}{unit}" for i, v in enumerate(self.values)]
        return ", ".join(parts)

    def normalized_allocations(self) -> list[float]:
        """Allocations that always sum to 1, defaulting to an even split."""
        if self.allocations and abs(sum(self.allocations) - 1.0) < 1e-6:
            return list(self.allocations)
        n = len(self.values) or 1
        return [1 / n] * n

    def to_dict(self) -> dict:
        return {"type": self.type.value, "values": self.values, "allocations": self.allocations}


@dataclass
class TradeManagement:
    move_stop_to_breakeven_at_rr: float | None = None
    trailing_atr_multiple: float | None = None
    max_bars_in_trade: int | None = None
    allow_long: bool = True
    allow_short: bool = True

    def describe(self) -> list[str]:
        out = []
        if self.move_stop_to_breakeven_at_rr:
            out.append(f"Move the stop to breakeven once the trade is {self.move_stop_to_breakeven_at_rr:g}R in profit.")
        if self.trailing_atr_multiple:
            out.append(f"Trail the stop {self.trailing_atr_multiple:g} x ATR behind price once in profit.")
        if self.max_bars_in_trade:
            out.append(f"Close any trade still open after {self.max_bars_in_trade} bars (time stop).")
        if not self.allow_short:
            out.append("Long only.")
        if not self.allow_long:
            out.append("Short only.")
        return out

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SessionFilter:
    """Restrict trading to an hour window in UTC. `None` means no restriction."""

    start_hour: int | None = None
    end_hour: int | None = None
    days: list[int] = field(default_factory=list)  # 0=Monday .. 6=Sunday; empty = all

    @property
    def active(self) -> bool:
        return self.start_hour is not None or bool(self.days)

    def describe(self) -> str:
        parts = []
        if self.start_hour is not None and self.end_hour is not None:
            parts.append(f"only between {self.start_hour:02d}:00 and {self.end_hour:02d}:00 UTC")
        if self.days:
            names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            parts.append("only on " + ", ".join(names[d] for d in self.days))
        return "; ".join(parts) if parts else "no session restriction"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Costs:
    """Execution costs. Defaults are deliberately non-zero.

    A backtest with zero costs is the single most common way a strategy looks
    profitable and is not, so the audit engine flags any spec that zeroes these
    out rather than letting it pass quietly.
    """

    commission_bps: float = 5.0     # 0.05% per side
    slippage_bps: float = 2.0       # 0.02% per fill
    spread_bps: float = 1.0         # half-spread paid on entry and exit

    @property
    def total_round_trip_bps(self) -> float:
        return (self.commission_bps + self.slippage_bps + self.spread_bps) * 2

    def describe(self) -> str:
        return (
            f"{self.commission_bps:g} bps commission, {self.slippage_bps:g} bps slippage and "
            f"{self.spread_bps:g} bps spread per side "
            f"({self.total_round_trip_bps:g} bps round trip)"
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StrategySpec:
    """A complete, executable strategy definition."""

    name: str = "Untitled Strategy"
    description: str = ""
    symbol: str = "BTCUSDT"
    timeframe: Timeframe = Timeframe.H1

    entry_long: list[Condition] = field(default_factory=list)
    entry_short: list[Condition] = field(default_factory=list)
    exit_long: list[Condition] = field(default_factory=list)
    exit_short: list[Condition] = field(default_factory=list)
    filters: list[Condition] = field(default_factory=list)

    stop: StopSpec = field(default_factory=StopSpec)
    targets: TargetSpec = field(default_factory=TargetSpec)
    management: TradeManagement = field(default_factory=TradeManagement)
    session: SessionFilter = field(default_factory=SessionFilter)
    costs: Costs = field(default_factory=Costs)

    risk_percent: float = 1.0
    initial_capital: float = 10_000.0

    # Provenance so a user can see how the spec came to be.
    source_text: str = ""
    parse_notes: list[str] = field(default_factory=list)
    unparsed_phrases: list[str] = field(default_factory=list)

    @property
    def parameter_count(self) -> int:
        """Free parameters, used by the audit engine to judge overfitting risk.

        Counts every numeric knob: indicator periods, thresholds, stop and
        target values, and management multiples.
        """
        count = 0
        for group in (self.entry_long, self.entry_short, self.exit_long, self.exit_short, self.filters):
            for condition in group:
                count += len(re.findall(r"\d+", condition.left))
                if isinstance(condition.right, (int, float)):
                    count += 1
                else:
                    count += len(re.findall(r"\d+", str(condition.right)))
        count += 1                              # stop value
        count += len(self.targets.values)
        if self.management.move_stop_to_breakeven_at_rr:
            count += 1
        if self.management.trailing_atr_multiple:
            count += 1
        if self.management.max_bars_in_trade:
            count += 1
        return count

    def describe(self) -> dict:
        """Human-readable specification — the "Strategy Specification" section."""
        def rules(conditions: list[Condition]) -> list[str]:
            return [c.describe() for c in conditions]

        return {
            "name": self.name,
            "description": self.description,
            "symbol": self.symbol,
            "timeframe": self.timeframe.value,
            "entry_logic": {
                "long": rules(self.entry_long) or ["no long entries defined"],
                "short": rules(self.entry_short) or ["no short entries defined"],
                "note": "All conditions in a list must be true on the same closed bar.",
            },
            "exit_logic": {
                "long": rules(self.exit_long) or ["exits handled by stop and targets only"],
                "short": rules(self.exit_short) or ["exits handled by stop and targets only"],
            },
            "filters": rules(self.filters) or ["none"],
            "stop_logic": self.stop.describe(),
            "take_profit_logic": self.targets.describe(),
            "position_sizing": (
                f"Risk {self.risk_percent:g}% of equity per trade, sized from the stop distance "
                f"so every loss costs the same percentage regardless of volatility."
            ),
            "risk_management": self.management.describe() or ["no additional trade management"],
            "session_filter": self.session.describe(),
            "costs": self.costs.describe(),
            "parameter_count": self.parameter_count,
        }

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "symbol": self.symbol,
            "timeframe": self.timeframe.value,
            "entry_long": [c.to_dict() for c in self.entry_long],
            "entry_short": [c.to_dict() for c in self.entry_short],
            "exit_long": [c.to_dict() for c in self.exit_long],
            "exit_short": [c.to_dict() for c in self.exit_short],
            "filters": [c.to_dict() for c in self.filters],
            "stop": self.stop.to_dict(),
            "targets": self.targets.to_dict(),
            "management": self.management.to_dict(),
            "session": self.session.to_dict(),
            "costs": self.costs.to_dict(),
            "risk_percent": self.risk_percent,
            "initial_capital": self.initial_capital,
            "source_text": self.source_text,
            "parse_notes": self.parse_notes,
            "unparsed_phrases": self.unparsed_phrases,
            "specification": self.describe(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "StrategySpec":
        """Rebuild a spec from its dict form, tolerating missing optional sections."""
        def conditions(key: str) -> list[Condition]:
            return [Condition.from_dict(c) for c in data.get(key, [])]

        stop_data = data.get("stop") or {}
        target_data = data.get("targets") or {}
        management_data = data.get("management") or {}
        session_data = data.get("session") or {}
        costs_data = data.get("costs") or {}

        return cls(
            name=data.get("name", "Untitled Strategy"),
            description=data.get("description", ""),
            symbol=data.get("symbol", "BTCUSDT"),
            timeframe=Timeframe.parse(data.get("timeframe", "1h")),
            entry_long=conditions("entry_long"),
            entry_short=conditions("entry_short"),
            exit_long=conditions("exit_long"),
            exit_short=conditions("exit_short"),
            filters=conditions("filters"),
            stop=StopSpec(
                type=StopType(stop_data.get("type", "atr")),
                value=float(stop_data.get("value", 1.5)),
            ),
            targets=TargetSpec(
                type=TargetType(target_data.get("type", "rr")),
                values=[float(v) for v in target_data.get("values", [1.5, 3.0])],
                allocations=[float(v) for v in target_data.get("allocations", [])],
            ),
            management=TradeManagement(
                move_stop_to_breakeven_at_rr=management_data.get("move_stop_to_breakeven_at_rr"),
                trailing_atr_multiple=management_data.get("trailing_atr_multiple"),
                max_bars_in_trade=management_data.get("max_bars_in_trade"),
                allow_long=management_data.get("allow_long", True),
                allow_short=management_data.get("allow_short", True),
            ),
            session=SessionFilter(
                start_hour=session_data.get("start_hour"),
                end_hour=session_data.get("end_hour"),
                days=session_data.get("days", []),
            ),
            costs=Costs(
                commission_bps=float(costs_data.get("commission_bps", 5.0)),
                slippage_bps=float(costs_data.get("slippage_bps", 2.0)),
                spread_bps=float(costs_data.get("spread_bps", 1.0)),
            ),
            risk_percent=float(data.get("risk_percent", 1.0)),
            initial_capital=float(data.get("initial_capital", 10_000.0)),
            source_text=data.get("source_text", ""),
            parse_notes=data.get("parse_notes", []),
            unparsed_phrases=data.get("unparsed_phrases", []),
        )

    def validate(self) -> list[str]:
        """Structural problems that would make a backtest meaningless."""
        problems: list[str] = []
        if not self.entry_long and not self.entry_short:
            problems.append("No entry conditions defined — the strategy can never open a trade.")
        if self.entry_long and not self.management.allow_long:
            problems.append("Long entries are defined but longs are disabled in trade management.")
        if self.entry_short and not self.management.allow_short:
            problems.append("Short entries are defined but shorts are disabled in trade management.")
        if self.stop.value <= 0:
            problems.append("Stop distance must be positive.")
        if not self.targets.values:
            problems.append("No take-profit levels defined.")
        if any(v <= 0 for v in self.targets.values):
            problems.append("Take-profit values must be positive.")
        if self.risk_percent <= 0 or self.risk_percent > 100:
            problems.append("Risk per trade must be between 0 and 100 percent.")
        if self.initial_capital <= 0:
            problems.append("Initial capital must be positive.")
        return problems
