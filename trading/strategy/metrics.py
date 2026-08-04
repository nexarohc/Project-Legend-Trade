"""Performance metrics computed from a backtest result.

Risk-adjusted ratios are annualised from the equity curve's own sampling
frequency rather than from a hard-coded 252, because a 5-minute crypto
strategy and a daily equity strategy do not share a bar count per year.

Every ratio that can be undefined (no losing trades, zero volatility, zero
drawdown) returns `None` with a stated reason instead of a fabricated infinity.
A Sharpe of 999 because there were three trades is worse than no number.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from trading.models import Timeframe
from trading.strategy.backtest import BacktestResult

# Trading periods per year, used to annualise returns per timeframe.
_PERIODS_PER_YEAR = {
    Timeframe.M1: 525_600,
    Timeframe.M5: 105_120,
    Timeframe.M15: 35_040,
    Timeframe.M30: 17_520,
    Timeframe.H1: 8_760,
    Timeframe.H4: 2_190,
    Timeframe.D1: 365,
    Timeframe.W1: 52,
    Timeframe.MN1: 12,
}


@dataclass
class Metrics:
    # Headline
    net_profit: float = 0.0
    net_profit_percent: float = 0.0
    total_return_percent: float = 0.0
    annual_return_percent: float | None = None
    final_equity: float = 0.0

    # Risk-adjusted
    sharpe_ratio: float | None = None
    sortino_ratio: float | None = None
    calmar_ratio: float | None = None
    profit_factor: float | None = None
    expectancy: float = 0.0
    expectancy_r: float = 0.0

    # Trade statistics
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    loss_rate: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0
    average_trade: float = 0.0
    average_win: float = 0.0
    average_loss: float = 0.0
    average_bars_held: float = 0.0
    average_hold_time: str = ""
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0

    # Drawdown
    max_drawdown_percent: float = 0.0
    max_drawdown_absolute: float = 0.0
    max_drawdown_duration_bars: int = 0
    recovery_factor: float | None = None

    # Split by side
    long_trades: int = 0
    short_trades: int = 0
    long_win_rate: float = 0.0
    short_win_rate: float = 0.0
    long_net_profit: float = 0.0
    short_net_profit: float = 0.0

    # Costs
    total_costs: float = 0.0
    gross_profit: float = 0.0
    cost_as_percent_of_gross: float | None = None

    # Distribution
    r_multiple_distribution: dict = field(default_factory=dict)
    trade_returns: list[float] = field(default_factory=list)

    undefined: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "net_profit": round(self.net_profit, 2),
            "net_profit_percent": round(self.net_profit_percent, 2),
            "total_return_percent": round(self.total_return_percent, 2),
            "annual_return_percent": round(self.annual_return_percent, 2) if self.annual_return_percent is not None else None,
            "final_equity": round(self.final_equity, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 2) if self.sharpe_ratio is not None else None,
            "sortino_ratio": round(self.sortino_ratio, 2) if self.sortino_ratio is not None else None,
            "calmar_ratio": round(self.calmar_ratio, 2) if self.calmar_ratio is not None else None,
            "profit_factor": round(self.profit_factor, 2) if self.profit_factor is not None else None,
            "expectancy": round(self.expectancy, 2),
            "expectancy_r": round(self.expectancy_r, 3),
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": round(self.win_rate, 2),
            "loss_rate": round(self.loss_rate, 2),
            "largest_win": round(self.largest_win, 2),
            "largest_loss": round(self.largest_loss, 2),
            "average_trade": round(self.average_trade, 2),
            "average_win": round(self.average_win, 2),
            "average_loss": round(self.average_loss, 2),
            "average_bars_held": round(self.average_bars_held, 1),
            "average_hold_time": self.average_hold_time,
            "max_consecutive_wins": self.max_consecutive_wins,
            "max_consecutive_losses": self.max_consecutive_losses,
            "max_drawdown_percent": round(self.max_drawdown_percent, 2),
            "max_drawdown_absolute": round(self.max_drawdown_absolute, 2),
            "max_drawdown_duration_bars": self.max_drawdown_duration_bars,
            "recovery_factor": round(self.recovery_factor, 2) if self.recovery_factor is not None else None,
            "long_trades": self.long_trades,
            "short_trades": self.short_trades,
            "long_win_rate": round(self.long_win_rate, 2),
            "short_win_rate": round(self.short_win_rate, 2),
            "long_net_profit": round(self.long_net_profit, 2),
            "short_net_profit": round(self.short_net_profit, 2),
            "total_costs": round(self.total_costs, 2),
            "gross_profit": round(self.gross_profit, 2),
            "cost_as_percent_of_gross": (
                round(self.cost_as_percent_of_gross, 2)
                if self.cost_as_percent_of_gross is not None else None
            ),
            "r_multiple_distribution": self.r_multiple_distribution,
            "undefined": self.undefined,
        }


def compute_metrics(result: BacktestResult, timeframe: Timeframe) -> Metrics:
    """Derive every reported statistic from a completed backtest."""
    m = Metrics()
    m.final_equity = result.final_equity
    m.net_profit = result.final_equity - result.initial_capital
    m.net_profit_percent = (
        100 * m.net_profit / result.initial_capital if result.initial_capital else 0.0
    )
    m.total_return_percent = m.net_profit_percent

    trades = result.trades
    m.total_trades = len(trades)
    if not trades:
        m.undefined["all"] = "No trades were taken, so no performance statistics exist."
        return m

    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl < 0]
    m.winning_trades = len(wins)
    m.losing_trades = len(losses)
    m.win_rate = 100 * len(wins) / len(trades)
    m.loss_rate = 100 * len(losses) / len(trades)

    m.gross_profit = sum(t.gross_pnl for t in trades)
    m.total_costs = sum(t.costs for t in trades)
    if abs(m.gross_profit) > 1e-9:
        m.cost_as_percent_of_gross = 100 * m.total_costs / abs(m.gross_profit)

    m.largest_win = max((t.net_pnl for t in wins), default=0.0)
    m.largest_loss = min((t.net_pnl for t in losses), default=0.0)
    m.average_trade = sum(t.net_pnl for t in trades) / len(trades)
    m.average_win = sum(t.net_pnl for t in wins) / len(wins) if wins else 0.0
    m.average_loss = sum(t.net_pnl for t in losses) / len(losses) if losses else 0.0
    m.expectancy = m.average_trade
    m.expectancy_r = sum(t.r_multiple for t in trades) / len(trades)

    m.average_bars_held = sum(t.bars_held for t in trades) / len(trades)
    m.average_hold_time = _format_duration(m.average_bars_held * timeframe.minutes)

    m.max_consecutive_wins, m.max_consecutive_losses = _streaks(trades)

    # Profit factor.
    gross_wins = sum(t.net_pnl for t in wins)
    gross_losses = abs(sum(t.net_pnl for t in losses))
    if gross_losses > 0:
        m.profit_factor = gross_wins / gross_losses
    else:
        m.undefined["profit_factor"] = (
            "No losing trades in this sample, so profit factor is undefined rather than infinite. "
            "With this few trades that is a sign of a small sample, not of a flawless strategy."
        )

    # Long/short split.
    longs = [t for t in trades if t.direction == "long"]
    shorts = [t for t in trades if t.direction == "short"]
    m.long_trades, m.short_trades = len(longs), len(shorts)
    m.long_win_rate = 100 * sum(1 for t in longs if t.net_pnl > 0) / len(longs) if longs else 0.0
    m.short_win_rate = 100 * sum(1 for t in shorts if t.net_pnl > 0) / len(shorts) if shorts else 0.0
    m.long_net_profit = sum(t.net_pnl for t in longs)
    m.short_net_profit = sum(t.net_pnl for t in shorts)

    # Drawdown from the equity curve.
    m.max_drawdown_percent, m.max_drawdown_absolute, m.max_drawdown_duration_bars = _drawdown(
        result.equity_curve
    )
    if m.max_drawdown_absolute > 0:
        m.recovery_factor = m.net_profit / m.max_drawdown_absolute
        if m.max_drawdown_percent > 0:
            annual = _annual_return(result, timeframe)
            m.annual_return_percent = annual
            if annual is not None:
                m.calmar_ratio = annual / m.max_drawdown_percent
    else:
        m.undefined["calmar_ratio"] = "No drawdown was recorded, so Calmar is undefined."
        m.annual_return_percent = _annual_return(result, timeframe)

    if m.annual_return_percent is None:
        m.annual_return_percent = _annual_return(result, timeframe)

    # Sharpe/Sortino from per-bar equity returns.
    returns = _equity_returns(result.equity_curve)
    m.trade_returns = [t.r_multiple for t in trades]
    periods = _PERIODS_PER_YEAR.get(timeframe, 8_760)

    if len(returns) > 2:
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        std = math.sqrt(variance)
        if std > 0:
            m.sharpe_ratio = (mean / std) * math.sqrt(periods)
        else:
            m.undefined["sharpe_ratio"] = "Equity never varied, so volatility is zero and Sharpe is undefined."

        downside = [r for r in returns if r < 0]
        if downside:
            downside_var = sum(r ** 2 for r in downside) / len(returns)
            downside_std = math.sqrt(downside_var)
            if downside_std > 0:
                m.sortino_ratio = (mean / downside_std) * math.sqrt(periods)
        else:
            m.undefined["sortino_ratio"] = (
                "No negative equity periods in this sample, so downside deviation is zero and "
                "Sortino is undefined."
            )
    else:
        m.undefined["sharpe_ratio"] = "Too few equity observations to estimate volatility."

    m.r_multiple_distribution = _distribution([t.r_multiple for t in trades])
    return m


def _equity_returns(curve: list[dict]) -> list[float]:
    """Simple per-bar returns from the equity curve."""
    returns = []
    for i in range(1, len(curve)):
        prev = curve[i - 1]["equity"]
        if prev > 0:
            returns.append((curve[i]["equity"] - prev) / prev)
    return returns


def _drawdown(curve: list[dict]) -> tuple[float, float, int]:
    """Maximum peak-to-trough decline: (percent, absolute, duration in bars)."""
    if not curve:
        return 0.0, 0.0, 0

    peak = curve[0]["equity"]
    peak_index = 0
    worst_percent = 0.0
    worst_absolute = 0.0
    worst_duration = 0

    for i, point in enumerate(curve):
        equity = point["equity"]
        if equity > peak:
            peak = equity
            peak_index = i
        elif peak > 0:
            decline = peak - equity
            percent = 100 * decline / peak
            if percent > worst_percent:
                worst_percent = percent
                worst_absolute = decline
                worst_duration = i - peak_index

    return worst_percent, worst_absolute, worst_duration


def _annual_return(result: BacktestResult, timeframe: Timeframe) -> float | None:
    """CAGR over the tested span."""
    if result.initial_capital <= 0 or result.final_equity <= 0:
        return None
    span_seconds = result.end_time - result.start_time
    if span_seconds <= 0:
        return None
    years = span_seconds / (365.25 * 86400)
    if years < 0.02:  # under about a week — annualising is meaningless
        return None
    growth = result.final_equity / result.initial_capital
    return (growth ** (1 / years) - 1) * 100


def _streaks(trades) -> tuple[int, int]:
    best_win = best_loss = current_win = current_loss = 0
    for trade in trades:
        if trade.net_pnl > 0:
            current_win += 1
            current_loss = 0
        elif trade.net_pnl < 0:
            current_loss += 1
            current_win = 0
        best_win = max(best_win, current_win)
        best_loss = max(best_loss, current_loss)
    return best_win, best_loss


def _distribution(r_multiples: list[float]) -> dict:
    """Histogram of trade outcomes in R, so the shape of the edge is visible."""
    buckets = {
        "<= -2R": 0, "-2R to -1R": 0, "-1R to 0": 0,
        "0 to 1R": 0, "1R to 2R": 0, "2R to 3R": 0, "> 3R": 0,
    }
    for r in r_multiples:
        if r <= -2:
            buckets["<= -2R"] += 1
        elif r <= -1:
            buckets["-2R to -1R"] += 1
        elif r < 0:
            buckets["-1R to 0"] += 1
        elif r < 1:
            buckets["0 to 1R"] += 1
        elif r < 2:
            buckets["1R to 2R"] += 1
        elif r < 3:
            buckets["2R to 3R"] += 1
        else:
            buckets["> 3R"] += 1
    return buckets


def _format_duration(minutes: float) -> str:
    if minutes < 60:
        return f"{minutes:.0f} minutes"
    if minutes < 1440:
        return f"{minutes / 60:.1f} hours"
    return f"{minutes / 1440:.1f} days"
