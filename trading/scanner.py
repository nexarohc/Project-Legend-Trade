"""Market scanner: filter a caller-supplied symbol list by measurable conditions.

There is no bundled ticker list anywhere in this platform, and the scanner does
not change that. It filters symbols the caller supplies — a watchlist, a
market's example list, anything — it does not enumerate "every NASDAQ stock".
No keyless provider exposes a screener API; building one here would mean
either a paid data vendor or silently maintaining a static ticker universe,
which is exactly the shortcut the rest of the platform avoids (see
`trading/markets.py`).

Filters run against numbers the analysis pipeline already computes
(`regime.build_context`, `indicators.compute_all`), so a scanner match and a
full analysis of the same symbol can never disagree about what "RSI" or
"trend" means.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from trading import regime
from trading.indicators import compute_all, last_valid
from trading.models import Series, Timeframe


class ScanField(str, Enum):
    PRICE = "price"
    VOLUME = "volume"
    CHANGE_PERCENT = "change_percent"
    RSI = "rsi"
    ADX = "adx"
    ATR_PERCENT = "atr_percent"
    TREND = "trend"                    # bullish | bearish | ranging
    TREND_STRENGTH = "trend_strength"  # 0-100
    VOLATILITY = "volatility"          # low | moderate | elevated | high
    MACD_HIST = "macd_hist"


class Op(str, Enum):
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    EQ = "eq"
    BETWEEN = "between"


_OP_SYMBOLS = {Op.GT: ">", Op.GTE: ">=", Op.LT: "<", Op.LTE: "<=", Op.EQ: "=="}


@dataclass
class ScanFilter:
    field: ScanField
    op: Op
    value: float | str
    value2: float | None = None  # upper bound, only for Op.BETWEEN

    def describe(self) -> str:
        if self.op is Op.BETWEEN:
            return f"{self.field.value} between {self.value} and {self.value2}"
        return f"{self.field.value} {_OP_SYMBOLS[self.op]} {self.value}"


@dataclass
class ScanMatch:
    symbol: str
    price: float
    values: dict[str, float | str | None]
    evidence: list[str]

    def to_dict(self) -> dict:
        return {"symbol": self.symbol, "price": self.price, "values": self.values,
                "evidence": self.evidence}


@dataclass
class ScanReport:
    matches: list[ScanMatch] = field(default_factory=list)
    scanned: int = 0
    failed: dict[str, str] = field(default_factory=dict)  # symbol -> reason

    def to_dict(self) -> dict:
        return {
            "matches": [m.to_dict() for m in self.matches],
            "scanned": self.scanned,
            "matched": len(self.matches),
            "failed": self.failed,
        }


def _measure(series: Series) -> dict[str, float | str | None]:
    """Every field a filter can reference, computed once per symbol."""
    last = series.last
    candles = series.candles
    computed = compute_all(series)
    ctx = regime.build_context(series)

    change_percent = None
    if len(candles) >= 2 and candles[-2].close:
        change_percent = 100 * (last.close - candles[-2].close) / candles[-2].close

    return {
        ScanField.PRICE.value: last.close,
        ScanField.VOLUME.value: last.volume,
        ScanField.CHANGE_PERCENT.value: change_percent,
        ScanField.RSI.value: last_valid(computed["arrays"]["rsi"]),
        ScanField.ADX.value: last_valid(computed["arrays"]["adx"]),
        ScanField.ATR_PERCENT.value: ctx.atr_percent,
        ScanField.TREND.value: ctx.trend,
        ScanField.TREND_STRENGTH.value: ctx.trend_strength,
        ScanField.VOLATILITY.value: ctx.volatility,
        ScanField.MACD_HIST.value: last_valid(computed["arrays"]["macd_hist"]),
    }


def _passes(value, filt: ScanFilter) -> bool:
    """A field with no measurable value fails closed — never matches on data
    that could not be verified, the same rule the risk and execution
    guardrails use elsewhere in this codebase."""
    if value is None:
        return False

    if filt.op is Op.EQ:
        if isinstance(value, str):
            return value.lower() == str(filt.value).lower()
        return value == filt.value

    if isinstance(value, str):
        return False  # gt/lt/between are meaningless on a string field

    if filt.op is Op.BETWEEN:
        return filt.value <= value <= filt.value2

    return {
        Op.GT: value > filt.value,
        Op.GTE: value >= filt.value,
        Op.LT: value < filt.value,
        Op.LTE: value <= filt.value,
    }[filt.op]


def scan_series(symbol: str, series: Series, filters: list[ScanFilter]) -> ScanMatch | None:
    """Pure: evaluate one already-fetched series against every filter (AND).

    Network-free and fully testable — the caller is responsible for fetching
    `series`, matching the rest of trading/'s layering (`risk.py`,
    `analysis.py`) where the network boundary sits one level up.
    """
    if len(series) < 30:
        return None

    values = _measure(series)
    for filt in filters:
        if not _passes(values.get(filt.field.value), filt):
            return None

    evidence = [f"{filt.describe()} — measured {values.get(filt.field.value)!r}" for filt in filters]
    return ScanMatch(symbol=symbol, price=values[ScanField.PRICE.value], values=values, evidence=evidence)


# A scan fetches one series per symbol; this bounds how many a single request
# can trigger, the same reasoning `MAX_CORRELATION_POSITIONS` uses in the
# analysis router for the same class of fan-out cost.
MAX_SCAN_SYMBOLS = 50


def run_scan(
    symbols: list[str], timeframe: Timeframe, filters: list[ScanFilter],
    bars: int = 200, provider: str | None = None,
) -> ScanReport:
    """Fetch each symbol and evaluate it. One bad symbol never sinks the scan."""
    from trading.market import market_service

    report = ScanReport()
    seen: set[str] = set()

    for symbol in symbols[:MAX_SCAN_SYMBOLS]:
        key = symbol.upper().strip()
        if not key or key in seen:
            continue
        seen.add(key)
        report.scanned += 1

        try:
            series = market_service.candles(symbol, timeframe, bars, provider)
        except Exception as exc:  # noqa: BLE001 - one symbol's failure isn't the scan's
            report.failed[symbol] = str(exc)
            continue

        match = scan_series(symbol, series, filters)
        if match:
            report.matches.append(match)

    report.matches.sort(key=lambda m: m.symbol)
    return report
