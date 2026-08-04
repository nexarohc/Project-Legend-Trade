"""Strategy audit — the gate every strategy must pass before it is trusted.

The audit is adversarial by design. Its job is to find the reasons a backtest
is lying, and the default assumption is that it is. Each check returns a
severity, the evidence behind it, and what to do about it; the verdict is
derived from those checks rather than from the headline return, so a strategy
that made 400% with four trades and no costs gets rejected exactly as it should.

Checks map to the classic ways backtests mislead:
look-ahead bias, overfitting, data snooping, unrealistic cost assumptions,
insufficient sample, weak risk control, regime dependency and parameter
fragility.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from trading.models import Series
from trading.strategy.backtest import BacktestResult
from trading.strategy.metrics import Metrics
from trading.strategy.spec import StopType, StrategySpec


class Severity(str, Enum):
    PASS = "pass"
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class Verdict(str, Enum):
    APPROVED = "APPROVED"
    REVISE = "REVISE"
    REJECT = "REJECT"


@dataclass
class Check:
    name: str
    severity: Severity
    finding: str
    evidence: str
    recommendation: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "severity": self.severity.value,
            "finding": self.finding,
            "evidence": self.evidence,
            "recommendation": self.recommendation,
        }


@dataclass
class AuditReport:
    verdict: Verdict = Verdict.REVISE
    score: float = 0.0                 # 0-100
    checks: list[Check] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    improvements: list[str] = field(default_factory=list)
    summary: str = ""

    @property
    def critical_count(self) -> int:
        return sum(1 for c in self.checks if c.severity is Severity.CRITICAL)

    @property
    def warning_count(self) -> int:
        return sum(1 for c in self.checks if c.severity is Severity.WARNING)

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict.value,
            "score": round(self.score, 1),
            "checks": [c.to_dict() for c in self.checks],
            "critical_count": self.critical_count,
            "warning_count": self.warning_count,
            "strengths": self.strengths,
            "weaknesses": self.weaknesses,
            "improvements": self.improvements,
            "summary": self.summary,
        }


# Below this many trades, no performance statistic is trustworthy.
MIN_TRADES_MEANINGFUL = 30
MIN_TRADES_RELIABLE = 100


def audit(
    spec: StrategySpec,
    result: BacktestResult,
    metrics: Metrics,
    series: Series,
    walk_forward_result=None,
    regime_result=None,
    stability_result=None,
    monte_carlo_result=None,
) -> AuditReport:
    """Run every check and produce a verdict with reasoning."""
    report = AuditReport()
    checks: list[Check] = []

    checks.append(_check_look_ahead(spec))
    checks.append(_check_sample_size(metrics))
    checks.append(_check_costs(spec, metrics))
    checks.append(_check_overfitting(spec, metrics))
    checks.append(_check_stop_loss(spec, metrics))
    checks.append(_check_risk_management(spec, metrics))
    checks.append(_check_drawdown(metrics))
    checks.append(_check_profit_factor(metrics))
    checks.append(_check_outlier_dependency(result, metrics))
    checks.append(_check_data_span(result, series))
    checks.append(_check_side_balance(metrics))

    if walk_forward_result is not None:
        checks.append(_check_walk_forward(walk_forward_result))
    if regime_result is not None:
        checks.append(_check_regime(regime_result))
    if stability_result is not None:
        checks.append(_check_stability(stability_result))
    if monte_carlo_result is not None:
        checks.append(_check_monte_carlo(monte_carlo_result))

    report.checks = checks
    report.score = _score(checks)
    report.verdict = _verdict(checks, metrics, report.score)

    report.strengths = [f"{c.name}: {c.finding}" for c in checks if c.severity is Severity.PASS]
    report.weaknesses = [
        f"{c.name}: {c.finding}" for c in checks
        if c.severity in (Severity.WARNING, Severity.CRITICAL)
    ]
    report.improvements = [c.recommendation for c in checks if c.recommendation]
    report.summary = _summarize(report, metrics)
    return report


# --- individual checks ------------------------------------------------------

def _check_look_ahead(spec: StrategySpec) -> Check:
    """Structural check on the execution model, not a statistical guess.

    The backtester fills at the next bar's open and its feature layer never
    indexes forward, so look-ahead bias is prevented by construction. What is
    still worth flagging is the one place a user can reintroduce it: writing a
    rule against an indicator that is itself forward-shifted.
    """
    forward_shifted = []
    for group in (spec.entry_long, spec.entry_short, spec.exit_long, spec.exit_short, spec.filters):
        for condition in group:
            for operand in (condition.left, condition.right):
                if isinstance(operand, str) and any(
                    token in operand.lower() for token in ("senkou", "chikou", "future", "shift")
                ):
                    forward_shifted.append(operand)

    if forward_shifted:
        return Check(
            "Look-ahead bias", Severity.CRITICAL,
            "The strategy references a forward-displaced series.",
            f"Rules use {', '.join(sorted(set(forward_shifted)))}, which are plotted into the future "
            f"and are not knowable at the bar they appear on.",
            "Replace forward-shifted inputs with values available at the decision bar.",
        )

    return Check(
        "Look-ahead bias", Severity.PASS,
        "No look-ahead detected; the execution model prevents it structurally.",
        "Signals are evaluated on closed bars and filled at the next bar's open, and no feature "
        "reads an index beyond the current bar. Stops are assumed to fill before targets when a "
        "single bar spans both.",
    )


def _check_sample_size(metrics: Metrics) -> Check:
    n = metrics.total_trades
    if n == 0:
        return Check(
            "Sample size", Severity.CRITICAL,
            "The strategy took no trades.",
            "Zero trades over the tested period.",
            "Loosen the entry conditions or test a longer history.",
        )
    if n < MIN_TRADES_MEANINGFUL:
        return Check(
            "Sample size", Severity.CRITICAL,
            f"Only {n} trades — far too few to conclude anything.",
            f"With {n} trades, a win rate of {metrics.win_rate:.0f}% has a margin of error wide "
            f"enough to include both a strong edge and no edge at all.",
            f"Test over a longer history or a lower timeframe until at least "
            f"{MIN_TRADES_MEANINGFUL} trades (ideally {MIN_TRADES_RELIABLE}) are generated.",
        )
    if n < MIN_TRADES_RELIABLE:
        return Check(
            "Sample size", Severity.WARNING,
            f"{n} trades is enough to be suggestive but not conclusive.",
            f"Statistics stabilise around {MIN_TRADES_RELIABLE} trades; below that, "
            f"a handful of outcomes still move every metric materially.",
            f"Extend the test period to reach {MIN_TRADES_RELIABLE}+ trades before committing capital.",
        )
    return Check(
        "Sample size", Severity.PASS,
        f"{n} trades is a workable sample.",
        f"{n} trades gives the win rate and profit factor reasonable stability.",
    )


def _check_costs(spec: StrategySpec, metrics: Metrics) -> Check:
    total_bps = spec.costs.total_round_trip_bps
    if total_bps <= 0:
        return Check(
            "Cost assumptions", Severity.CRITICAL,
            "The backtest charges no commission, slippage or spread.",
            "All cost inputs are zero, so every reported figure is the frictionless best case. "
            "High-frequency and scalping strategies routinely flip from profitable to losing once "
            "real costs are applied.",
            "Set realistic costs for the venue — typically 5-10 bps commission and 1-5 bps "
            "slippage per side — and re-run.",
        )

    if metrics.cost_as_percent_of_gross is not None and metrics.cost_as_percent_of_gross > 50:
        return Check(
            "Cost assumptions", Severity.WARNING,
            f"Costs consume {metrics.cost_as_percent_of_gross:.0f}% of gross profit.",
            f"Gross profit {metrics.gross_profit:,.2f} against {metrics.total_costs:,.2f} in costs "
            f"({spec.costs.describe()}). The edge is thin relative to friction, so a modest increase "
            f"in spread or slippage would erase it.",
            "Target larger moves per trade, or trade less frequently, so costs are a smaller share "
            "of the edge.",
        )

    return Check(
        "Cost assumptions", Severity.PASS,
        f"Realistic costs applied ({spec.costs.describe()}).",
        f"Costs totalled {metrics.total_costs:,.2f}"
        + (f", {metrics.cost_as_percent_of_gross:.0f}% of gross profit."
           if metrics.cost_as_percent_of_gross is not None else "."),
    )


def _check_overfitting(spec: StrategySpec, metrics: Metrics) -> Check:
    params = spec.parameter_count
    trades = metrics.total_trades
    if trades == 0:
        return Check(
            "Overfitting risk", Severity.WARNING,
            "Cannot assess overfitting with no trades.",
            f"The strategy has {params} free parameters and produced no trades.",
        )

    ratio = trades / params if params else trades
    if ratio < 10:
        return Check(
            "Overfitting risk", Severity.CRITICAL,
            f"Only {ratio:.1f} trades per free parameter.",
            f"{params} tunable parameters against {trades} trades. With this little data per "
            f"parameter, the rules can fit noise in the sample and show an edge that does not exist.",
            "Reduce the number of conditions and thresholds, or test over far more data. "
            "A rule of thumb is at least 10-20 trades per parameter.",
        )
    if ratio < 20:
        return Check(
            "Overfitting risk", Severity.WARNING,
            f"{ratio:.1f} trades per parameter is on the thin side.",
            f"{params} parameters against {trades} trades.",
            "Simplify the rules or extend the test window to raise the ratio above 20.",
        )
    return Check(
        "Overfitting risk", Severity.PASS,
        f"{ratio:.1f} trades per parameter — a reasonable ratio.",
        f"{params} parameters against {trades} trades.",
    )


def _check_stop_loss(spec: StrategySpec, metrics: Metrics) -> Check:
    if spec.stop.type is StopType.PERCENT and spec.stop.value > 20:
        return Check(
            "Stop loss", Severity.CRITICAL,
            f"The {spec.stop.value:g}% stop is wide enough to be nominal.",
            "A stop this far away rarely triggers, which flatters the win rate while leaving a "
            "very large loss whenever it does fire.",
            "Use a volatility-based stop (1-3 x ATR) or place it at structural invalidation.",
        )
    if spec.stop.type is StopType.ATR and spec.stop.value > 5:
        return Check(
            "Stop loss", Severity.WARNING,
            f"A {spec.stop.value:g} x ATR stop is unusually wide.",
            "Wide stops inflate win rate and hide tail risk in the average loss.",
            "Consider 1-3 x ATR unless the strategy is explicitly a long-horizon position system.",
        )
    if metrics.largest_loss and metrics.average_loss and abs(metrics.average_loss) > 0:
        ratio = abs(metrics.largest_loss / metrics.average_loss)
        if ratio > 4:
            return Check(
                "Stop loss", Severity.WARNING,
                f"The largest loss is {ratio:.1f}x the average loss.",
                f"Largest loss {metrics.largest_loss:,.2f} against an average of "
                f"{metrics.average_loss:,.2f}. That gap usually means gaps or slippage are "
                f"defeating the stop.",
                "Check whether the instrument gaps overnight, and size for the worst case rather "
                "than the average.",
            )
    return Check(
        "Stop loss", Severity.PASS,
        f"Stop is defined and proportionate ({spec.stop.describe()}).",
        f"Largest loss {metrics.largest_loss:,.2f} against an average loss of "
        f"{metrics.average_loss:,.2f}.",
    )


def _check_risk_management(spec: StrategySpec, metrics: Metrics) -> Check:
    if spec.risk_percent > 5:
        return Check(
            "Risk management", Severity.CRITICAL,
            f"Risking {spec.risk_percent:g}% of equity per trade is far too aggressive.",
            f"At {spec.risk_percent:g}% per trade, the {metrics.max_consecutive_losses}-loss streak "
            f"seen in this very backtest would cost roughly "
            f"{100 * (1 - (1 - spec.risk_percent / 100) ** metrics.max_consecutive_losses):.0f}% of the account.",
            "Reduce risk to 0.5-2% per trade.",
        )
    if spec.risk_percent > 2:
        return Check(
            "Risk management", Severity.WARNING,
            f"{spec.risk_percent:g}% risk per trade is above the conventional range.",
            f"A {metrics.max_consecutive_losses}-trade losing streak occurred in this sample.",
            "Most professional systems risk 0.5-2% per trade.",
        )
    return Check(
        "Risk management", Severity.PASS,
        f"Risk per trade is {spec.risk_percent:g}% of equity, within the conventional range.",
        f"Worst losing streak in the sample was {metrics.max_consecutive_losses} trades, "
        f"which at this risk level costs roughly "
        f"{100 * (1 - (1 - spec.risk_percent / 100) ** max(metrics.max_consecutive_losses, 1)):.1f}%.",
    )


def _check_drawdown(metrics: Metrics) -> Check:
    dd = metrics.max_drawdown_percent
    if dd > 40:
        return Check(
            "Drawdown", Severity.CRITICAL,
            f"Maximum drawdown of {dd:.1f}% is not survivable in practice.",
            f"A {dd:.1f}% decline requires a {100 * (1 / (1 - dd / 100) - 1):.0f}% gain just to "
            f"break even, and almost nobody keeps trading a system through it.",
            "Cut risk per trade, add a regime filter, or tighten the stop.",
        )
    if dd > 25:
        return Check(
            "Drawdown", Severity.WARNING,
            f"Maximum drawdown of {dd:.1f}% is uncomfortably deep.",
            f"Recovery requires a {100 * (1 / (1 - dd / 100) - 1):.0f}% gain.",
            "Reduce position size to bring the peak decline under 20%.",
        )
    return Check(
        "Drawdown", Severity.PASS,
        f"Maximum drawdown of {dd:.1f}% is manageable.",
        f"Recovery from the worst decline needs a "
        f"{100 * (1 / (1 - dd / 100) - 1):.1f}% gain." if dd > 0 else "No meaningful drawdown recorded.",
    )


def _check_profit_factor(metrics: Metrics) -> Check:
    pf = metrics.profit_factor
    if pf is None:
        return Check(
            "Profit factor", Severity.WARNING,
            "Profit factor is undefined.",
            metrics.undefined.get("profit_factor", "No losing trades in the sample."),
            "Test over more data so both winning and losing trades are represented.",
        )
    if pf < 1.0:
        return Check(
            "Profit factor", Severity.CRITICAL,
            f"Profit factor of {pf:.2f} means the strategy loses money.",
            f"Gross losses exceed gross wins over {metrics.total_trades} trades.",
            "The rules have no edge as written. Change the premise rather than the parameters.",
        )
    if pf < 1.25:
        return Check(
            "Profit factor", Severity.WARNING,
            f"Profit factor of {pf:.2f} leaves almost no margin.",
            f"Expectancy is {metrics.expectancy_r:+.3f}R per trade. A small increase in slippage "
            f"or a modest regime change would take this below 1.0.",
            "Look for a higher-quality filter to raise average win or cut the loss rate.",
        )
    if pf > 4 and metrics.total_trades < MIN_TRADES_RELIABLE:
        return Check(
            "Profit factor", Severity.WARNING,
            f"Profit factor of {pf:.2f} is implausibly high for {metrics.total_trades} trades.",
            "Results this good on a small sample usually reflect a handful of outliers or a "
            "fitted rule rather than a durable edge.",
            "Validate out of sample before believing it.",
        )
    return Check(
        "Profit factor", Severity.PASS,
        f"Profit factor of {pf:.2f} with {metrics.expectancy_r:+.3f}R expectancy per trade.",
        f"Across {metrics.total_trades} trades at a {metrics.win_rate:.0f}% win rate.",
    )


def _check_outlier_dependency(result: BacktestResult, metrics: Metrics) -> Check:
    """Does one trade carry the entire result?"""
    if not result.trades or metrics.net_profit <= 0:
        return Check(
            "Outlier dependency", Severity.INFO,
            "Not applicable — the strategy was not net profitable.",
            f"Net profit {metrics.net_profit:,.2f}.",
        )

    best = max(result.trades, key=lambda t: t.net_pnl)
    share = 100 * best.net_pnl / metrics.net_profit if metrics.net_profit else 0

    if share > 50:
        return Check(
            "Outlier dependency", Severity.CRITICAL,
            f"A single trade produced {share:.0f}% of all profit.",
            f"The best trade made {best.net_pnl:,.2f} of {metrics.net_profit:,.2f} total. "
            f"Remove that one trade and the strategy is barely profitable.",
            "Verify the outlier was not a data artefact, and treat the remaining edge as the "
            "realistic one.",
        )
    if share > 30:
        return Check(
            "Outlier dependency", Severity.WARNING,
            f"The best single trade accounts for {share:.0f}% of total profit.",
            f"Best trade {best.net_pnl:,.2f} against {metrics.net_profit:,.2f} net.",
            "Check that performance holds with the top trade excluded.",
        )
    return Check(
        "Outlier dependency", Severity.PASS,
        f"Profit is spread across trades; the best contributes {share:.0f}%.",
        f"Best trade {best.net_pnl:,.2f} of {metrics.net_profit:,.2f} net profit.",
    )


def _check_data_span(result: BacktestResult, series: Series) -> Check:
    span_days = (result.end_time - result.start_time) / 86400 if result.end_time else 0
    if span_days < 90:
        return Check(
            "Data span", Severity.CRITICAL,
            f"Only {span_days:.0f} days of history tested.",
            "A window this short cannot contain more than one market condition, so the result "
            "says nothing about how the strategy behaves when conditions change.",
            "Test across at least a year, covering both trending and ranging periods.",
        )
    if span_days < 365:
        return Check(
            "Data span", Severity.WARNING,
            f"{span_days:.0f} days tested — under a full year.",
            "Seasonal and regime effects may not be represented.",
            "Extend the history to at least one year, ideally covering a bull and a bear phase.",
        )
    return Check(
        "Data span", Severity.PASS,
        f"{span_days:.0f} days ({span_days / 365:.1f} years) of history tested.",
        f"{len(series)} bars on the {series.timeframe.value} timeframe.",
    )


def _check_side_balance(metrics: Metrics) -> Check:
    if metrics.long_trades == 0 or metrics.short_trades == 0:
        side = "long" if metrics.short_trades == 0 else "short"
        return Check(
            "Directional balance", Severity.INFO,
            f"The strategy trades {side} only.",
            f"{metrics.total_trades} trades, all {side}. That is a legitimate design, but the "
            f"result will track the underlying's direction over the test period.",
            f"Confirm the test window is not simply a {side}-favourable market.",
        )

    long_share = 100 * metrics.long_net_profit / (metrics.long_net_profit + metrics.short_net_profit) \
        if (metrics.long_net_profit + metrics.short_net_profit) else 0

    if metrics.long_net_profit > 0 > metrics.short_net_profit or \
       metrics.short_net_profit > 0 > metrics.long_net_profit:
        winning = "long" if metrics.long_net_profit > 0 else "short"
        return Check(
            "Directional balance", Severity.WARNING,
            f"Only the {winning} side is profitable.",
            f"Longs {metrics.long_net_profit:+,.2f} ({metrics.long_win_rate:.0f}% win rate), "
            f"shorts {metrics.short_net_profit:+,.2f} ({metrics.short_win_rate:.0f}%).",
            f"Consider disabling the losing side, or find out why the logic does not mirror.",
        )
    return Check(
        "Directional balance", Severity.PASS,
        "Both directions contribute.",
        f"Longs {metrics.long_net_profit:+,.2f} over {metrics.long_trades} trades, "
        f"shorts {metrics.short_net_profit:+,.2f} over {metrics.short_trades} trades "
        f"({long_share:.0f}% of profit from longs).",
    )


def _check_walk_forward(wf) -> Check:
    if not wf.windows:
        return Check(
            "Walk-forward validation", Severity.WARNING,
            "Walk-forward could not be run.",
            "; ".join(wf.findings) or "Insufficient data.",
            "Load more history so out-of-sample windows can be tested.",
        )
    if wf.efficiency is not None and wf.efficiency < 0.4:
        return Check(
            "Walk-forward validation", Severity.CRITICAL,
            f"Out-of-sample performance collapses (efficiency {wf.efficiency:.2f}).",
            " ".join(wf.findings),
            "The rules do not generalise. Simplify them and re-validate.",
        )
    if not wf.consistent:
        return Check(
            "Walk-forward validation", Severity.WARNING,
            f"Only {wf.profitable_windows} of {wf.total_windows} out-of-sample windows were profitable.",
            " ".join(wf.findings),
            "Look for a filter that keeps the strategy flat during the losing windows.",
        )
    return Check(
        "Walk-forward validation", Severity.PASS,
        f"Out-of-sample performance holds ({wf.profitable_windows}/{wf.total_windows} windows profitable).",
        " ".join(wf.findings),
    )


def _check_regime(regime) -> Check:
    if not regime.buckets:
        return Check(
            "Regime robustness", Severity.INFO,
            "Regime test could not be run.",
            "; ".join(regime.findings) or "Insufficient data.",
        )
    if regime.regime_dependent:
        return Check(
            "Regime robustness", Severity.CRITICAL,
            "The strategy only works in one market regime.",
            " ".join(regime.findings),
            "Add a regime filter so it stands aside outside its working environment.",
        )
    losing = [k for k, v in regime.buckets.items() if v["return_percent"] <= 0]
    if losing:
        return Check(
            "Regime robustness", Severity.WARNING,
            f"Unprofitable in {len(losing)} of {len(regime.buckets)} regimes.",
            " ".join(regime.findings),
            f"Consider filtering out {', '.join(losing)} conditions.",
        )
    return Check(
        "Regime robustness", Severity.PASS,
        "Profitable across every regime tested.",
        " ".join(regime.findings),
    )


def _check_stability(stability: dict) -> Check:
    if stability.get("stable"):
        return Check(
            "Parameter stability", Severity.PASS,
            f"{stability['profitable_variants']} of {stability['total_variants']} parameter "
            f"variations remained profitable.",
            " ".join(stability["findings"]),
        )
    return Check(
        "Parameter stability", Severity.CRITICAL,
        "Performance depends on precise parameter values.",
        " ".join(stability["findings"]),
        "An edge that vanishes when the stop moves 10% was fitted to the sample. Rebuild around "
        "a premise that survives perturbation.",
    )


def _check_monte_carlo(mc) -> Check:
    if mc.probability_of_ruin > 5:
        return Check(
            "Monte Carlo risk", Severity.CRITICAL,
            f"{mc.probability_of_ruin:.1f}% of simulations lost "
            f"{mc.ruin_threshold_percent:.0f}% of capital.",
            " ".join(mc.interpretation[:3]),
            "Cut risk per trade until the probability of ruin is under 1%.",
        )
    if mc.percentile_5 < mc.initial_capital:
        return Check(
            "Monte Carlo risk", Severity.WARNING,
            f"The 5th percentile outcome ({mc.percentile_5:,.0f}) is below the starting capital.",
            f"{mc.probability_of_loss:.1f}% of simulations ended at a loss; 95th-percentile "
            f"drawdown was {mc.drawdown_percentile_95:.1f}%.",
            "Plan for the 95th-percentile drawdown rather than the backtested one.",
        )
    return Check(
        "Monte Carlo risk", Severity.PASS,
        f"90% confidence interval is {mc.confidence_interval_90[0]:,.0f} to "
        f"{mc.confidence_interval_90[1]:,.0f}.",
        f"Probability of ruin {mc.probability_of_ruin:.1f}%; median drawdown "
        f"{mc.median_max_drawdown:.1f}%.",
    )


# --- scoring and verdict ----------------------------------------------------

def _score(checks: list[Check]) -> float:
    """0-100, weighted so critical failures dominate."""
    weights = {
        Severity.PASS: 1.0,
        Severity.INFO: 0.75,
        Severity.WARNING: 0.4,
        Severity.CRITICAL: 0.0,
    }
    scored = [c for c in checks if c.severity is not Severity.INFO]
    if not scored:
        return 0.0
    return 100 * sum(weights[c.severity] for c in scored) / len(scored)


def _verdict(checks: list[Check], metrics: Metrics, score: float) -> Verdict:
    critical = sum(1 for c in checks if c.severity is Severity.CRITICAL)
    warnings = sum(1 for c in checks if c.severity is Severity.WARNING)

    # Any critical finding blocks approval outright — these are the checks that
    # determine whether the numbers mean anything at all.
    if critical > 0:
        return Verdict.REJECT if critical >= 2 else Verdict.REVISE

    if metrics.profit_factor is not None and metrics.profit_factor < 1.0:
        return Verdict.REJECT

    if score >= 80 and warnings <= 2 and metrics.total_trades >= MIN_TRADES_MEANINGFUL:
        return Verdict.APPROVED

    return Verdict.REVISE


def _summarize(report: AuditReport, metrics: Metrics) -> str:
    verdict_text = {
        Verdict.APPROVED: (
            "The strategy passes every structural check and is fit for paper trading. "
            "Approval is not a prediction of profit — it means the backtest is believable."
        ),
        Verdict.REVISE: (
            "The strategy has real problems that must be addressed before it can be trusted. "
            "Fix the items below and re-run the audit."
        ),
        Verdict.REJECT: (
            "The strategy fails on grounds serious enough that its backtest cannot be believed. "
            "Do not trade it, and do not attempt to fix it by tuning parameters."
        ),
    }[report.verdict]

    return (
        f"{report.verdict.value} (audit score {report.score:.0f}/100). "
        f"{report.critical_count} critical and {report.warning_count} warning findings across "
        f"{len(report.checks)} checks, on {metrics.total_trades} trades with a "
        f"{metrics.win_rate:.0f}% win rate and "
        f"{f'{metrics.profit_factor:.2f} profit factor' if metrics.profit_factor else 'undefined profit factor'}. "
        f"{verdict_text}"
    )
