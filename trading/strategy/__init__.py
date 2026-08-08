"""Strategy layer: specification, backtest, validation, audit.

`run_full_study` is the entry point the API and UI use — it runs the complete
chain in the order the results depend on each other, so a caller cannot
accidentally present a Monte Carlo result without the audit that qualifies it.
"""
from __future__ import annotations

import logging
import time

from trading.models import Series
from trading.strategy.audit import AuditReport, audit
from trading.strategy.backtest import BacktestResult, run_backtest
from trading.strategy.metrics import Metrics, compute_metrics
from trading.strategy.montecarlo import run_monte_carlo
from trading.strategy.nl import parse
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
from trading.strategy.walkforward import parameter_stability, regime_test, walk_forward

logger = logging.getLogger("legend.trading.strategy")

__all__ = [
    "StrategySpec", "Condition", "Op", "StopSpec", "StopType", "TargetSpec", "TargetType",
    "TradeManagement", "SessionFilter", "Costs",
    "parse", "run_backtest", "compute_metrics", "run_monte_carlo",
    "walk_forward", "regime_test", "parameter_stability", "audit",
    "BacktestResult", "Metrics", "AuditReport", "run_full_study",
]


def run_full_study(
    spec: StrategySpec,
    series: Series,
    monte_carlo_runs: int = 2000,
    include_trades: bool = True,
    run_validation: bool = True,
) -> dict:
    """Backtest, validate and audit a strategy in one pass.

    Validation (walk-forward, regime, stability) re-runs the backtest many
    times, so it is skippable for quick iteration — but the audit says so
    explicitly when it was skipped rather than quietly reporting fewer checks.
    """
    started = time.time()

    result = run_backtest(spec, series)
    metrics = compute_metrics(result, series.timeframe)

    wf = regimes = stability = None
    if run_validation and metrics.total_trades > 0:
        wf = walk_forward(spec, series)
        regimes = regime_test(spec, series)
        stability = parameter_stability(spec, series)

    monte_carlo = run_monte_carlo(result, simulations=monte_carlo_runs) \
        if metrics.total_trades else None

    report = audit(spec, result, metrics, series, wf, regimes, stability, monte_carlo)

    if not run_validation:
        report.improvements.append(
            "Walk-forward, regime and parameter-stability checks were skipped for this run. "
            "Enable full validation before treating the verdict as final."
        )

    elapsed = (time.time() - started) * 1000
    logger.info(
        "strategy study complete: %s on %s — %d trades, verdict %s (%.0fms)",
        spec.name, series.symbol, metrics.total_trades, report.verdict.value, elapsed,
    )

    return {
        "specification": spec.describe(),
        "spec": spec.to_dict(),
        "backtest": result.to_dict(include_trades=include_trades),
        "metrics": metrics.to_dict(),
        "monte_carlo": monte_carlo.to_dict() if monte_carlo else None,
        "walk_forward": wf.to_dict() if wf else None,
        "regime_test": regimes.to_dict() if regimes else None,
        "parameter_stability": stability,
        "audit": report.to_dict(),
        "elapsed_ms": round(elapsed),
    }
