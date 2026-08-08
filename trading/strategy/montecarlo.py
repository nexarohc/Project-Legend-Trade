"""Monte Carlo simulation over a backtest's trade sequence.

A single backtest is one sample from the distribution of outcomes the strategy
could have produced. The equity curve you happened to get depends heavily on
the *order* the wins and losses arrived in — the same trades in a different
sequence can turn a smooth ride into a 40% drawdown.

Two resampling methods are run because they answer different questions:

* **Shuffle** keeps the exact same trades and only reorders them. It isolates
  sequence risk: how bad could the drawdown have been with the same edge?
* **Bootstrap** resamples trades with replacement. It isolates sample risk: how
  much of this result depends on a handful of outliers that might not recur?

Probability of ruin is computed against a stated threshold rather than
literal zero, since most traders stop long before an account is actually empty.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from trading.strategy.backtest import BacktestResult


@dataclass
class MonteCarloResult:
    simulations: int = 0
    method: str = ""
    initial_capital: float = 0.0

    median_final_equity: float = 0.0
    mean_final_equity: float = 0.0
    best_case: float = 0.0
    worst_case: float = 0.0
    percentile_5: float = 0.0
    percentile_25: float = 0.0
    percentile_75: float = 0.0
    percentile_95: float = 0.0

    median_return_percent: float = 0.0
    confidence_interval_90: tuple[float, float] = (0.0, 0.0)

    median_max_drawdown: float = 0.0
    worst_max_drawdown: float = 0.0
    drawdown_percentile_95: float = 0.0

    probability_of_ruin: float = 0.0
    ruin_threshold_percent: float = 50.0
    probability_of_loss: float = 0.0

    interpretation: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "simulations": self.simulations,
            "method": self.method,
            "initial_capital": round(self.initial_capital, 2),
            "median_final_equity": round(self.median_final_equity, 2),
            "mean_final_equity": round(self.mean_final_equity, 2),
            "best_case": round(self.best_case, 2),
            "worst_case": round(self.worst_case, 2),
            "percentile_5": round(self.percentile_5, 2),
            "percentile_25": round(self.percentile_25, 2),
            "percentile_75": round(self.percentile_75, 2),
            "percentile_95": round(self.percentile_95, 2),
            "median_return_percent": round(self.median_return_percent, 2),
            "confidence_interval_90": [
                round(self.confidence_interval_90[0], 2),
                round(self.confidence_interval_90[1], 2),
            ],
            "median_max_drawdown": round(self.median_max_drawdown, 2),
            "worst_max_drawdown": round(self.worst_max_drawdown, 2),
            "drawdown_percentile_95": round(self.drawdown_percentile_95, 2),
            "probability_of_ruin": round(self.probability_of_ruin, 2),
            "ruin_threshold_percent": self.ruin_threshold_percent,
            "probability_of_loss": round(self.probability_of_loss, 2),
            "interpretation": self.interpretation,
        }


def run_monte_carlo(
    result: BacktestResult,
    simulations: int = 2000,
    method: str = "bootstrap",
    ruin_threshold_percent: float = 50.0,
    seed: int | None = 42,
) -> MonteCarloResult:
    """Resample the trade sequence `simulations` times and describe the spread.

    `seed` is fixed by default so the same backtest reproduces the same figures;
    pass `None` for a fresh draw each run.
    """
    mc = MonteCarloResult(
        simulations=simulations,
        method=method,
        initial_capital=result.initial_capital,
        ruin_threshold_percent=ruin_threshold_percent,
    )

    trades = result.trades
    if len(trades) < 10:
        mc.interpretation.append(
            f"Only {len(trades)} trades available. Monte Carlo needs at least 10 to say anything "
            f"useful, and really wants 50+; these figures are not reliable."
        )
        if not trades:
            return mc

    rng = random.Random(seed)
    # Resample fractional returns rather than cash P&L, so compounding is
    # modelled correctly at every equity level.
    returns = [
        t.net_pnl / result.initial_capital for t in trades
    ]
    ruin_level = result.initial_capital * (1 - ruin_threshold_percent / 100)

    final_equities: list[float] = []
    max_drawdowns: list[float] = []
    ruined = 0

    for _ in range(simulations):
        if method == "shuffle":
            sample = returns[:]
            rng.shuffle(sample)
        else:
            sample = [rng.choice(returns) for _ in returns]

        equity = result.initial_capital
        peak = equity
        worst_dd = 0.0
        hit_ruin = False

        for r in sample:
            equity += result.initial_capital * r
            if equity <= 0:
                equity = 0.0
                hit_ruin = True
                break
            peak = max(peak, equity)
            if peak > 0:
                worst_dd = max(worst_dd, 100 * (peak - equity) / peak)
            if equity <= ruin_level:
                hit_ruin = True

        final_equities.append(equity)
        max_drawdowns.append(worst_dd)
        if hit_ruin:
            ruined += 1

    final_equities.sort()
    max_drawdowns.sort()

    mc.median_final_equity = _percentile(final_equities, 50)
    mc.mean_final_equity = sum(final_equities) / len(final_equities)
    mc.best_case = final_equities[-1]
    mc.worst_case = final_equities[0]
    mc.percentile_5 = _percentile(final_equities, 5)
    mc.percentile_25 = _percentile(final_equities, 25)
    mc.percentile_75 = _percentile(final_equities, 75)
    mc.percentile_95 = _percentile(final_equities, 95)
    mc.confidence_interval_90 = (mc.percentile_5, mc.percentile_95)

    mc.median_return_percent = (
        100 * (mc.median_final_equity - result.initial_capital) / result.initial_capital
        if result.initial_capital else 0.0
    )
    mc.median_max_drawdown = _percentile(max_drawdowns, 50)
    mc.drawdown_percentile_95 = _percentile(max_drawdowns, 95)
    mc.worst_max_drawdown = max_drawdowns[-1]

    mc.probability_of_ruin = 100 * ruined / simulations
    mc.probability_of_loss = 100 * sum(
        1 for e in final_equities if e < result.initial_capital
    ) / simulations

    mc.interpretation.extend(_interpret(mc, result, method))
    return mc


def _interpret(mc: MonteCarloResult, result: BacktestResult, method: str) -> list[str]:
    """Plain-English reading of the distribution."""
    notes = []
    method_note = (
        "Trades were resampled with replacement, so this measures how much the result depends "
        "on the specific trades that occurred."
        if method == "bootstrap" else
        "The same trades were reordered, so this measures pure sequence risk with the edge held constant."
    )
    notes.append(f"{mc.simulations:,} simulations. {method_note}")

    notes.append(
        f"Median outcome is {mc.median_final_equity:,.0f} ({mc.median_return_percent:+.1f}%), "
        f"with a 90% confidence interval of {mc.confidence_interval_90[0]:,.0f} to "
        f"{mc.confidence_interval_90[1]:,.0f}. The backtest itself finished at "
        f"{result.final_equity:,.0f}."
    )

    if result.final_equity > mc.percentile_75:
        notes.append(
            "The actual backtest landed in the top quartile of simulated outcomes — the historical "
            "run was luckier than typical, so expect live results nearer the median."
        )
    elif result.final_equity < mc.percentile_25:
        notes.append(
            "The actual backtest landed in the bottom quartile of simulated outcomes, which means "
            "the sequence encountered was unusually unfavourable."
        )

    notes.append(
        f"Drawdown: median {mc.median_max_drawdown:.1f}%, 95th percentile "
        f"{mc.drawdown_percentile_95:.1f}%, worst simulated {mc.worst_max_drawdown:.1f}%. "
        f"Size the account to survive the 95th percentile, not the median."
    )

    if mc.probability_of_ruin > 5:
        notes.append(
            f"Probability of losing {mc.ruin_threshold_percent:.0f}% of capital is "
            f"{mc.probability_of_ruin:.1f}% — unacceptably high. Cut risk per trade "
            f"substantially before trading this."
        )
    elif mc.probability_of_ruin > 1:
        notes.append(
            f"Probability of a {mc.ruin_threshold_percent:.0f}% loss is {mc.probability_of_ruin:.1f}%. "
            f"Survivable, but a smaller risk per trade would improve it materially."
        )
    else:
        notes.append(
            f"Probability of a {mc.ruin_threshold_percent:.0f}% capital loss is "
            f"{mc.probability_of_ruin:.1f}% at the tested risk level."
        )

    notes.append(
        f"{mc.probability_of_loss:.1f}% of simulations ended below the starting balance."
    )
    return notes


def _percentile(sorted_values: list[float], percentile: float) -> float:
    """Linear-interpolated percentile of an already-sorted list."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (percentile / 100) * (len(sorted_values) - 1)
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = position - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight
