"""Walk-forward and regime testing.

Two questions a single backtest cannot answer:

1. **Does the edge hold on data the strategy was not shaped around?**
   `walk_forward` slides an in-sample / out-of-sample pair across the history
   and reports out-of-sample performance separately. Because this platform's
   strategies are rule-based rather than numerically optimised, the in-sample
   window is not used to fit parameters — it is used as the reference the
   out-of-sample window is compared against. A large IS/OOS gap still means the
   rules only worked on the period they were written for.

2. **Does the edge survive different market conditions?**
   `regime_test` slices history by trend and volatility and reports each
   bucket, so a strategy that only works in a bull market cannot hide inside a
   good aggregate number.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from trading.indicators import atr, ema
from trading.models import Series, Timeframe
from trading.strategy.backtest import run_backtest
from trading.strategy.metrics import Metrics, compute_metrics
from trading.strategy.spec import StrategySpec


@dataclass
class WindowResult:
    label: str
    kind: str            # in_sample | out_of_sample
    start_time: int
    end_time: int
    bars: int
    trades: int
    net_profit_percent: float
    win_rate: float
    profit_factor: float | None
    max_drawdown_percent: float
    expectancy_r: float

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "kind": self.kind,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "bars": self.bars,
            "trades": self.trades,
            "net_profit_percent": round(self.net_profit_percent, 2),
            "win_rate": round(self.win_rate, 2),
            "profit_factor": round(self.profit_factor, 2) if self.profit_factor is not None else None,
            "max_drawdown_percent": round(self.max_drawdown_percent, 2),
            "expectancy_r": round(self.expectancy_r, 3),
        }


@dataclass
class WalkForwardResult:
    windows: list[WindowResult] = field(default_factory=list)
    in_sample_return: float = 0.0
    out_of_sample_return: float = 0.0
    efficiency: float | None = None      # OOS / IS, the walk-forward efficiency ratio
    consistent: bool = False
    profitable_windows: int = 0
    total_windows: int = 0
    findings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "windows": [w.to_dict() for w in self.windows],
            "in_sample_return": round(self.in_sample_return, 2),
            "out_of_sample_return": round(self.out_of_sample_return, 2),
            "efficiency": round(self.efficiency, 2) if self.efficiency is not None else None,
            "consistent": self.consistent,
            "profitable_windows": self.profitable_windows,
            "total_windows": self.total_windows,
            "findings": self.findings,
        }


@dataclass
class RegimeResult:
    buckets: dict = field(default_factory=dict)
    findings: list[str] = field(default_factory=list)
    regime_dependent: bool = False

    def to_dict(self) -> dict:
        return {
            "buckets": self.buckets,
            "findings": self.findings,
            "regime_dependent": self.regime_dependent,
        }


def walk_forward(
    spec: StrategySpec,
    series: Series,
    folds: int = 4,
    in_sample_ratio: float = 0.6,
) -> WalkForwardResult:
    """Slide an in-sample/out-of-sample pair across the history."""
    wf = WalkForwardResult()
    n = len(series)

    min_bars_per_fold = 250
    if n < folds * min_bars_per_fold:
        possible = max(1, n // min_bars_per_fold)
        if possible < 2:
            wf.findings.append(
                f"Only {n} bars supplied. Walk-forward needs at least {2 * min_bars_per_fold} "
                f"to build two folds; the test was skipped."
            )
            return wf
        folds = possible
        wf.findings.append(f"Reduced to {folds} folds to keep at least {min_bars_per_fold} bars each.")

    fold_size = n // folds
    is_returns: list[float] = []
    oos_returns: list[float] = []

    for fold in range(folds):
        start = fold * fold_size
        end = start + fold_size if fold < folds - 1 else n
        split = start + int((end - start) * in_sample_ratio)

        for kind, (lo, hi) in (
            ("in_sample", (start, split)),
            ("out_of_sample", (split, end)),
        ):
            window = series.slice(lo, hi)
            if len(window) < 100:
                continue

            result = run_backtest(spec, window)
            metrics = compute_metrics(result, series.timeframe)
            wf.windows.append(WindowResult(
                label=f"Fold {fold + 1} {kind.replace('_', ' ')}",
                kind=kind,
                start_time=window.candles[0].timestamp,
                end_time=window.candles[-1].timestamp,
                bars=len(window),
                trades=metrics.total_trades,
                net_profit_percent=metrics.net_profit_percent,
                win_rate=metrics.win_rate,
                profit_factor=metrics.profit_factor,
                max_drawdown_percent=metrics.max_drawdown_percent,
                expectancy_r=metrics.expectancy_r,
            ))
            (is_returns if kind == "in_sample" else oos_returns).append(metrics.net_profit_percent)

    wf.total_windows = len([w for w in wf.windows if w.kind == "out_of_sample"])
    wf.profitable_windows = len(
        [w for w in wf.windows if w.kind == "out_of_sample" and w.net_profit_percent > 0]
    )
    wf.in_sample_return = sum(is_returns) / len(is_returns) if is_returns else 0.0
    wf.out_of_sample_return = sum(oos_returns) / len(oos_returns) if oos_returns else 0.0

    if wf.in_sample_return > 0:
        wf.efficiency = wf.out_of_sample_return / wf.in_sample_return

    wf.consistent = (
        wf.total_windows >= 2
        and wf.profitable_windows / wf.total_windows >= 0.6
        and wf.out_of_sample_return > 0
    )

    wf.findings.append(
        f"Average in-sample return {wf.in_sample_return:+.2f}% vs out-of-sample "
        f"{wf.out_of_sample_return:+.2f}% across {folds} folds."
    )
    if wf.efficiency is not None:
        if wf.efficiency >= 0.7:
            wf.findings.append(
                f"Walk-forward efficiency {wf.efficiency:.2f} — out-of-sample performance holds up "
                f"well against in-sample."
            )
        elif wf.efficiency >= 0.4:
            wf.findings.append(
                f"Walk-forward efficiency {wf.efficiency:.2f} — some degradation out of sample, "
                f"which is normal, but expect live results below the headline backtest."
            )
        else:
            wf.findings.append(
                f"Walk-forward efficiency is only {wf.efficiency:.2f}. Out-of-sample performance is "
                f"far worse than in-sample, which is the signature of rules fitted to the past."
            )
    if wf.total_windows:
        wf.findings.append(
            f"{wf.profitable_windows} of {wf.total_windows} out-of-sample windows were profitable."
        )
        if wf.profitable_windows / wf.total_windows < 0.5:
            wf.findings.append(
                "Fewer than half the out-of-sample windows made money — the edge is not consistent."
            )
    return wf


def regime_test(spec: StrategySpec, series: Series, min_bars: int = 150) -> RegimeResult:
    """Split the history by trend and volatility and backtest each slice separately."""
    rr = RegimeResult()
    n = len(series)
    if n < min_bars * 2:
        rr.findings.append(
            f"Only {n} bars supplied — not enough to split into regimes and still have "
            f"{min_bars} bars per bucket."
        )
        return rr

    closes = series.closes
    trend_ma = ema(closes, 200) if n >= 200 else ema(closes, 50)
    atr_values = atr(series, 14)
    warm = [v for v in atr_values if v is not None]
    if not warm:
        rr.findings.append("ATR could not be computed; regime split skipped.")
        return rr
    atr_median = sorted(warm)[len(warm) // 2]

    # Classify every bar, then group contiguous runs into testable segments.
    labels: list[str | None] = []
    for i in range(n):
        ma = trend_ma[i]
        a = atr_values[i]
        if ma is None or a is None:
            labels.append(None)
            continue
        trend = "bull" if closes[i] > ma else "bear"
        vol = "high volatility" if a > atr_median else "low volatility"
        labels.append(f"{trend} / {vol}")

    segments: dict[str, list[tuple[int, int]]] = {}
    start = 0
    for i in range(1, n):
        if labels[i] != labels[i - 1]:
            if labels[start] is not None and i - start >= 40:
                segments.setdefault(labels[start], []).append((start, i))
            start = i
    if labels[start] is not None and n - start >= 40:
        segments.setdefault(labels[start], []).append((start, n))

    for label, ranges in segments.items():
        total_bars = sum(hi - lo for lo, hi in ranges)
        if total_bars < min_bars:
            continue

        # Backtest each contiguous run and aggregate, rather than stitching
        # discontinuous slices into one fake series.
        trades = 0
        net = 0.0
        wins = 0
        capital_base = 0.0
        for lo, hi in ranges:
            window = series.slice(lo, hi)
            if len(window) < 60:
                continue
            result = run_backtest(spec, window)
            metrics = compute_metrics(result, series.timeframe)
            trades += metrics.total_trades
            net += metrics.net_profit
            wins += metrics.winning_trades
            capital_base += result.initial_capital

        rr.buckets[label] = {
            "bars": total_bars,
            "segments": len(ranges),
            "trades": trades,
            "net_profit": round(net, 2),
            "return_percent": round(100 * net / capital_base, 2) if capital_base else 0.0,
            "win_rate": round(100 * wins / trades, 2) if trades else 0.0,
        }

    if not rr.buckets:
        rr.findings.append("No regime bucket had enough contiguous data to test.")
        return rr

    profitable = [k for k, v in rr.buckets.items() if v["return_percent"] > 0]
    losing = [k for k, v in rr.buckets.items() if v["return_percent"] <= 0]

    for label, stats in sorted(rr.buckets.items()):
        rr.findings.append(
            f"{label}: {stats['trades']} trades over {stats['bars']} bars, "
            f"{stats['return_percent']:+.2f}% return, {stats['win_rate']:.0f}% win rate."
        )

    if len(rr.buckets) > 1 and len(profitable) == 1:
        rr.regime_dependent = True
        rr.findings.append(
            f"The strategy is only profitable in '{profitable[0]}' conditions and loses in "
            f"{', '.join(losing)}. This is regime dependency: it needs a filter that keeps it "
            f"flat outside its working environment."
        )
    elif losing and profitable:
        rr.findings.append(
            f"Profitable in {len(profitable)} of {len(rr.buckets)} regimes. Losing regimes: "
            f"{', '.join(losing)}."
        )
    elif not profitable:
        rr.findings.append("The strategy lost money in every regime tested.")
    else:
        rr.findings.append("The strategy was profitable across every regime tested, which is rare — "
                           "check the trade count per bucket before trusting it.")
    return rr


def parameter_stability(
    spec: StrategySpec, series: Series, perturbations: tuple[float, ...] = (0.8, 0.9, 1.1, 1.2)
) -> dict:
    """Re-run the strategy with the stop and target scaled up and down.

    A robust edge degrades smoothly when its parameters are nudged. One that
    collapses — or that only works at one exact setting — was fitted to the
    sample rather than discovered in it.
    """
    from copy import deepcopy

    baseline = compute_metrics(run_backtest(spec, series), series.timeframe)
    results = [{
        "scale": 1.0,
        "net_profit_percent": round(baseline.net_profit_percent, 2),
        "trades": baseline.total_trades,
        "profit_factor": round(baseline.profit_factor, 2) if baseline.profit_factor else None,
    }]

    for scale in perturbations:
        variant = deepcopy(spec)
        variant.stop.value = spec.stop.value * scale
        variant.targets.values = [v * scale for v in spec.targets.values]
        metrics = compute_metrics(run_backtest(variant, series), series.timeframe)
        results.append({
            "scale": scale,
            "net_profit_percent": round(metrics.net_profit_percent, 2),
            "trades": metrics.total_trades,
            "profit_factor": round(metrics.profit_factor, 2) if metrics.profit_factor else None,
        })

    returns = [r["net_profit_percent"] for r in results]
    profitable = sum(1 for r in returns if r > 0)
    stable = profitable >= len(returns) * 0.6

    findings = [
        f"Baseline returned {baseline.net_profit_percent:+.2f}%. Scaling stop and targets by "
        f"{', '.join(f'{p:g}x' for p in perturbations)} produced "
        f"{', '.join(f'{r:+.2f}%' for r in returns[1:])}."
    ]
    if stable:
        findings.append(
            f"{profitable} of {len(returns)} parameter settings stayed profitable — the edge is not "
            f"balanced on one exact configuration."
        )
    else:
        findings.append(
            f"Only {profitable} of {len(returns)} settings were profitable. Performance depends on "
            f"the precise parameter values, which is a strong overfitting signal."
        )

    return {
        "results": results,
        "stable": stable,
        "profitable_variants": profitable,
        "total_variants": len(returns),
        "findings": findings,
    }
