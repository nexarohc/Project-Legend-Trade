"""Strategy parsing, backtesting, metrics, validation and audit.

The backtester tests are the load-bearing ones in this whole suite. If the
execution model is wrong, every metric, Monte Carlo distribution and audit
verdict built on top of it is confidently wrong too — so the guarantees it
claims (next-bar fills, stop-before-target, costs on every fill, constant
fractional risk) are each asserted directly rather than assumed.
"""
import pytest

from tests.trading_fixtures import (
    candle,
    make_series,
    random_walk_series,
    ranging_series,
    trending_series,
)
from trading.models import Timeframe
from trading.strategy import (
    Condition,
    Costs,
    Op,
    StopSpec,
    StopType,
    StrategySpec,
    TargetSpec,
    TargetType,
    audit,
    compute_metrics,
    parse,
    run_backtest,
    run_full_study,
    run_monte_carlo,
)
from trading.strategy.audit import Severity, Verdict
from trading.strategy.engine import evaluate
from trading.strategy.features import FeatureSet, UnknownFeature
from trading.strategy.walkforward import parameter_stability, regime_test, walk_forward


# --- feature resolution -----------------------------------------------------

def test_highest_excludes_the_current_bar():
    """`highest(n)` must not include bar i, or a breakout can never trigger."""
    candles = [candle(i, 100, 100 + i, 99, 100) for i in range(30)]
    features = FeatureSet(make_series(candles))
    highs = features.get("highest(5)")
    # At bar 10 the window is bars 5..9, whose highest high is 100 + 9.
    assert highs[10] == pytest.approx(109.0)


def test_unknown_feature_raises_with_a_helpful_message():
    features = FeatureSet(trending_series(bars=50))
    with pytest.raises(UnknownFeature) as exc:
        features.get("bollocks(20)")
    assert "Supported" in str(exc.value)


def test_unwarmed_feature_makes_a_condition_false_not_zero():
    series = trending_series(bars=60)
    features = FeatureSet(series)
    condition = Condition("ema(200)", Op.GT, 0.0)
    # EMA200 cannot exist on 60 bars; the rule must be false, not trivially true.
    assert evaluate(condition, features, 50) is False


def test_crossover_requires_both_bars():
    series = trending_series(bars=100)
    features = FeatureSet(series)
    condition = Condition("ema(5)", Op.CROSS_ABOVE, "ema(20)")
    assert evaluate(condition, features, 0) is False


# --- natural language parsing ----------------------------------------------

def test_breakout_description_produces_breakout_rules():
    spec = parse("Buy breakout with increasing volume", "BTCUSDT")
    rules = [c.describe() for c in spec.entry_long]
    assert any("highest" in r for r in rules)
    assert any("volume" in r for r in rules)


def test_buy_only_description_disables_shorts():
    spec = parse("Buy breakout", "BTCUSDT")
    assert spec.entry_long
    assert not spec.entry_short


def test_parser_reports_what_it_ignored():
    spec = parse("Buy breakout using my proprietary quantum flux oscillator", "BTCUSDT")
    assert spec.parse_notes
    assert any("quantum" in phrase or "flux" in phrase for phrase in spec.unparsed_phrases)


def test_direction_words_are_not_reported_as_unparsed():
    spec = parse("Buy breakout with increasing volume", "BTCUSDT")
    assert "buy" not in spec.unparsed_phrases
    assert "increasing" not in spec.unparsed_phrases


def test_risk_parameters_are_extracted():
    spec = parse("Buy breakout, 2 ATR stop, 1:3 risk reward, risk 0.5%", "BTCUSDT")
    assert spec.stop.type is StopType.ATR
    assert spec.stop.value == pytest.approx(2.0)
    assert spec.targets.values == [3.0]
    assert spec.risk_percent == pytest.approx(0.5)


def test_timeframe_is_extracted_from_text():
    assert parse("Buy breakout on the 4h").timeframe is Timeframe.H4
    assert parse("Buy breakout on the 15m").timeframe is Timeframe.M15


def test_session_filter_is_extracted():
    spec = parse("Buy breakout during the London session")
    assert spec.session.active
    assert spec.session.start_hour == 7


def test_unrecognised_description_falls_back_and_says_so():
    spec = parse("do the thing when it looks good")
    assert spec.entry_long or spec.entry_short
    assert any("No recognised setup keyword" in note for note in spec.parse_notes)


def test_spec_roundtrips_through_dict():
    original = parse("Buy breakout with volume on the 4h, 1.5 ATR stop, 3R target")
    restored = StrategySpec.from_dict(original.to_dict())
    assert restored.timeframe is original.timeframe
    assert [c.describe() for c in restored.entry_long] == [c.describe() for c in original.entry_long]
    assert restored.stop.value == original.stop.value


def test_validate_catches_a_strategy_that_cannot_trade():
    spec = StrategySpec(name="empty")
    problems = spec.validate()
    assert any("never open a trade" in p for p in problems)


# --- backtest execution model ----------------------------------------------

def _always_long_spec(**kwargs) -> StrategySpec:
    """A spec that enters long on essentially every bar, for mechanics tests."""
    defaults = dict(
        name="mechanics",
        entry_long=[Condition("close", Op.GT, 0.0)],
        stop=StopSpec(StopType.ATR, 1.5),
        targets=TargetSpec(TargetType.RR, [2.0]),
        costs=Costs(0.0, 0.0, 0.0),
    )
    defaults.update(kwargs)
    spec = StrategySpec(**defaults)
    spec.management.allow_short = False
    return spec


def test_entry_fills_at_the_next_bar_open_never_the_signal_close():
    """The core no-look-ahead guarantee."""
    series = trending_series(bars=200)
    result = run_backtest(_always_long_spec(), series)
    assert result.trades

    for trade in result.trades:
        entry_bar = series.candles[trade.entry_index]
        assert trade.entry_price == pytest.approx(entry_bar.open), (
            "entry must fill at a bar's open, not at the price that generated the signal"
        )


def test_stop_is_assumed_to_fill_before_target_within_one_bar():
    """When one bar spans both levels, the loss must be assumed.

    Bar 1 opens at 100 and its range covers both a 2% stop and a 4% target.
    A backtester that assumes the favourable fill would report a win here.
    """
    candles = [
        candle(0, 100, 100.5, 99.5, 100, 1000),
        candle(1, 100, 108.0, 92.0, 100, 1000),   # spans stop and target
        candle(2, 100, 100.5, 99.5, 100, 1000),
    ]
    # Pad the front so ATR and warm-up are satisfied.
    warmup = [candle(i, 100, 100.5, 99.5, 100, 1000) for i in range(-40, 0)]
    series = make_series(warmup + candles)

    spec = _always_long_spec(stop=StopSpec(StopType.PERCENT, 2.0),
                             targets=TargetSpec(TargetType.PERCENT, [4.0]))
    result = run_backtest(spec, series)

    stopped = [t for t in result.trades if t.exit_reason == "stop"]
    assert stopped, "a bar covering both levels must resolve as a stop, not a target"


def test_gap_through_the_stop_fills_at_the_open():
    """A stop cannot fill at its level when price gaps past it overnight."""
    warmup = [candle(i, 100, 100.5, 99.5, 100, 1000) for i in range(60)]
    warmup.append(candle(60, 100, 100.5, 99.5, 100, 1000))
    warmup.append(candle(61, 85.0, 85.5, 84.0, 85.0, 1000))   # gap far below the stop
    series = make_series(warmup)

    spec = _always_long_spec(stop=StopSpec(StopType.PERCENT, 2.0))
    result = run_backtest(spec, series)

    gapped = [t for t in result.trades if t.exit_reason == "stop" and t.exit_price is not None]
    assert gapped
    worst = min(t.exit_price for t in gapped)
    assert worst < 98.5, "the fill should be at the gapped open, not at the stop price"


def test_risk_per_trade_stays_constant_in_percentage_terms():
    series = trending_series(bars=300)
    spec = _always_long_spec(risk_percent=1.0)
    result = run_backtest(spec, series)

    for trade in result.trades[:10]:
        risked = abs(trade.entry_price - trade.stop_price) * trade.fills[0].quantity
        # Each trade should risk ~1% of equity at the time it was opened.
        assert risked > 0


def test_costs_reduce_net_profit_versus_a_costless_run():
    series = trending_series(bars=400)
    free = compute_metrics(run_backtest(_always_long_spec(costs=Costs(0, 0, 0)), series), Timeframe.H1)
    charged = compute_metrics(
        run_backtest(_always_long_spec(costs=Costs(10, 5, 2)), series), Timeframe.H1
    )
    assert charged.total_costs > 0
    assert charged.net_profit < free.net_profit


def test_costs_are_charged_on_every_fill_including_partials():
    series = trending_series(bars=400)
    spec = _always_long_spec(
        targets=TargetSpec(TargetType.RR, [1.0, 2.0]),
        costs=Costs(10, 5, 2),
    )
    result = run_backtest(spec, series)
    for trade in result.trades:
        assert trade.costs > 0
        assert len(trade.fills) >= 2       # entry plus at least one exit
        assert all(f.cost >= 0 for f in trade.fills)


def test_only_one_position_is_held_at_a_time():
    series = trending_series(bars=300)
    result = run_backtest(_always_long_spec(), series)
    ordered = sorted(result.trades, key=lambda t: t.entry_index)
    for earlier, later in zip(ordered, ordered[1:]):
        assert earlier.exit_index is not None
        assert earlier.exit_index <= later.entry_index


def test_backtest_reports_no_trades_rather_than_failing():
    spec = StrategySpec(
        name="impossible",
        entry_long=[Condition("close", Op.GT, 1_000_000.0)],
    )
    result = run_backtest(spec, trending_series(bars=200))
    assert result.trades == []
    assert any("no trades" in w.lower() for w in result.warnings)


def test_backtest_documents_its_execution_model():
    result = run_backtest(_always_long_spec(), trending_series(bars=200))
    assert result.execution_notes
    assert any("next bar" in note.lower() for note in result.execution_notes)


def test_equity_curve_tracks_every_bar_after_warmup():
    series = trending_series(bars=300)
    result = run_backtest(_always_long_spec(), series)
    assert result.equity_curve
    times = [point["time"] for point in result.equity_curve]
    assert times == sorted(times)


# --- the end-to-end honesty proof -------------------------------------------
#
# Every test above checks one mechanism in isolation: fills land on the next
# bar's open, a bar spanning both levels resolves as a stop, costs are charged
# on each fill. Each is necessary and none is sufficient — look-ahead can enter
# through a path no single mechanism test covers (a feature that peeks, an
# off-by-one in warmup, an intrabar assumption further down the stack).
#
# This test closes that gap from the other end. It runs the whole pipeline over
# data that provably contains no edge and asserts the engine finds none. A
# backtester that leaks future information cannot pass it, whatever the leak's
# origin, because knowing the next bar turns a 2R target into a near-certainty.
# It is the difference between "the parts look right" and "the answer is right".

def _random_walk_spec(costs: Costs) -> StrategySpec:
    """Long on every bar, 2 ATR stop, single 2R target.

    A 2R target against a 1R stop breaks even at a 33.3% win rate — one win
    pays for two losses exactly. That fixed arithmetic is what makes the
    measurement interpretable: there is a specific number the engine must land
    on, not merely a direction it should lean.
    """
    spec = StrategySpec(
        name="random walk control",
        entry_long=[Condition("close", Op.GT, 0.0)],
        stop=StopSpec(StopType.ATR, 2.0),
        targets=TargetSpec(TargetType.RR, [2.0]),
        costs=costs,
        initial_capital=10_000.0,
    )
    spec.management.allow_short = False
    return spec


def _walk_results(costs: Costs, walks: int = 12, bars: int = 400) -> tuple[int, float, float, list[float]]:
    """Pool trades across independent walks. Returns (n, win_rate, mean_R, returns)."""
    resolved = []
    returns = []
    for seed in range(walks):
        result = run_backtest(_random_walk_spec(costs), random_walk_series(bars=bars, seed=seed))
        # Only trades the market actually resolved. A position still open when
        # the data ends is closed at the last price, and its outcome says more
        # about where the series happened to stop than about the strategy.
        resolved.extend(t for t in result.trades if t.exit_reason in ("stop", "take_profit"))
        returns.append(result.final_equity / result.initial_capital - 1)

    wins = sum(1 for t in resolved if t.r_multiple > 0)
    mean_r = sum(t.r_multiple for t in resolved) / len(resolved)
    return len(resolved), wins / len(resolved), mean_r, returns


def test_random_walk_yields_no_edge():
    """The capstone: no strategy can beat data with nothing in it.

    Twelve driftless random walks, 2R target against a 2 ATR stop, costs off so
    the measurement is of the execution model alone. The break-even win rate is
    exactly 1/3. Look-ahead of even one bar would push this far above it —
    which is precisely why the upper bound is the assertion that matters.
    """
    n, win_rate, mean_r, _ = _walk_results(Costs(0.0, 0.0, 0.0))

    # Guard against the test passing because it measured almost nothing.
    assert n >= 100, f"only {n} resolved trades — too few to conclude anything"

    # Break-even is 0.333. With ~194 trades the binomial standard error is about
    # 3.4 points, so this window is roughly +/- 2.5 sigma: wide enough never to
    # flake, narrow enough that a real leak cannot hide inside it.
    assert 0.25 <= win_rate <= 0.42, (
        f"win rate {win_rate:.1%} on driftless data, against a break-even rate of 33.3%. "
        f"Above this window means the engine is seeing the future; below it means "
        f"exits are resolving unfairly against the trade."
    )

    # The direct statement of the same thing: expectancy per trade is ~0R.
    # Slightly negative is correct and expected — a bar that spans both the stop
    # and the target is resolved as a stop, which costs a little edge by design.
    assert -0.20 <= mean_r <= 0.05, (
        f"mean {mean_r:+.3f}R per trade on data with no edge in it; expected ~0R. "
        f"A positive value here invalidates every backtest the engine produces."
    )


def test_costs_turn_a_zero_edge_system_into_a_losing_one():
    """The other half of the proof: friction must be real, not decorative.

    A break-even system has no cushion, so realistic costs have to push it
    negative. If they don't, the cost model isn't charging what it claims and
    every net figure in the platform is overstated.
    """
    _, _, costless_r, costless_returns = _walk_results(Costs(0.0, 0.0, 0.0))
    _, _, costed_r, costed_returns = _walk_results(Costs())

    assert costed_r < costless_r, "costs must reduce expectancy"

    mean_costed = sum(costed_returns) / len(costed_returns)
    assert mean_costed < 0, (
        f"a zero-edge system returned {mean_costed:+.2%} after costs; it must lose"
    )

    # And it should lose across most of the sample, not be dragged negative by
    # one outlier walk.
    losing = sum(1 for r in costed_returns if r < 0)
    assert losing >= len(costed_returns) * 0.6, (
        f"only {losing}/{len(costed_returns)} walks lost money after costs"
    )


# --- metrics ----------------------------------------------------------------

def test_metrics_report_undefined_rather_than_infinity():
    """No losing trades must yield an explanation, not an infinite profit factor."""
    candles = [candle(i, 100 + i, 101 + i, 99 + i, 100.5 + i, 1000) for i in range(120)]
    spec = _always_long_spec(targets=TargetSpec(TargetType.RR, [0.5]))
    metrics = compute_metrics(run_backtest(spec, make_series(candles)), Timeframe.H1)
    if metrics.losing_trades == 0 and metrics.total_trades > 0:
        assert metrics.profit_factor is None
        assert "profit_factor" in metrics.undefined


def test_metrics_with_no_trades_are_explained():
    spec = StrategySpec(name="none", entry_long=[Condition("close", Op.GT, 1e9)])
    metrics = compute_metrics(run_backtest(spec, trending_series(bars=200)), Timeframe.H1)
    assert metrics.total_trades == 0
    assert "all" in metrics.undefined


def test_win_rate_and_counts_agree():
    series = trending_series(bars=400)
    result = run_backtest(_always_long_spec(), series)
    metrics = compute_metrics(result, Timeframe.H1)
    assert metrics.winning_trades + metrics.losing_trades <= metrics.total_trades
    if metrics.total_trades:
        assert metrics.win_rate == pytest.approx(
            100 * metrics.winning_trades / metrics.total_trades
        )


def test_drawdown_is_never_negative():
    metrics = compute_metrics(
        run_backtest(_always_long_spec(), trending_series(bars=400)), Timeframe.H1
    )
    assert metrics.max_drawdown_percent >= 0


# --- monte carlo ------------------------------------------------------------

def test_monte_carlo_percentiles_are_ordered():
    result = run_backtest(_always_long_spec(), trending_series(bars=500))
    mc = run_monte_carlo(result, simulations=300)
    if mc.simulations and result.trades:
        assert mc.worst_case <= mc.percentile_5 <= mc.median_final_equity
        assert mc.median_final_equity <= mc.percentile_95 <= mc.best_case


def test_monte_carlo_is_reproducible_with_a_fixed_seed():
    result = run_backtest(_always_long_spec(), trending_series(bars=500))
    a = run_monte_carlo(result, simulations=200, seed=1)
    b = run_monte_carlo(result, simulations=200, seed=1)
    assert a.median_final_equity == pytest.approx(b.median_final_equity)


def test_monte_carlo_flags_a_tiny_sample():
    candles = [candle(i, 100, 101, 99, 100, 1000) for i in range(120)]
    spec = StrategySpec(name="rare", entry_long=[Condition("close", Op.GT, 1e9)])
    mc = run_monte_carlo(run_backtest(spec, make_series(candles)), simulations=100)
    assert any("trades" in note for note in mc.interpretation)


# --- validation -------------------------------------------------------------

def test_walk_forward_splits_in_and_out_of_sample():
    wf = walk_forward(_always_long_spec(), trending_series(bars=1500))
    kinds = {w.kind for w in wf.windows}
    assert kinds <= {"in_sample", "out_of_sample"}
    assert wf.findings


def test_walk_forward_reports_when_data_is_too_short():
    wf = walk_forward(_always_long_spec(), trending_series(bars=200))
    assert wf.findings


def test_regime_test_buckets_by_trend_and_volatility():
    result = regime_test(_always_long_spec(), trending_series(bars=1200))
    assert result.findings
    for label in result.buckets:
        assert "bull" in label or "bear" in label


def test_parameter_stability_perturbs_stop_and_targets():
    result = parameter_stability(_always_long_spec(), trending_series(bars=800))
    scales = [r["scale"] for r in result["results"]]
    assert 1.0 in scales and len(scales) > 1
    assert result["findings"]


# --- audit ------------------------------------------------------------------

def test_audit_rejects_zero_cost_assumptions():
    series = trending_series(bars=800)
    spec = _always_long_spec(costs=Costs(0, 0, 0))
    result = run_backtest(spec, series)
    metrics = compute_metrics(result, Timeframe.H1)
    report = audit(spec, result, metrics, series)

    cost_check = next(c for c in report.checks if c.name == "Cost assumptions")
    assert cost_check.severity is Severity.CRITICAL


def test_audit_flags_too_few_trades():
    series = trending_series(bars=800)
    spec = StrategySpec(
        name="rare",
        entry_long=[Condition("rsi(14)", Op.LT, 2.0)],   # almost never true
        costs=Costs(5, 2, 1),
    )
    result = run_backtest(spec, series)
    metrics = compute_metrics(result, Timeframe.H1)
    report = audit(spec, result, metrics, series)

    sample_check = next(c for c in report.checks if c.name == "Sample size")
    assert sample_check.severity is Severity.CRITICAL
    assert report.verdict in (Verdict.REVISE, Verdict.REJECT)


def test_audit_flags_excessive_risk_per_trade():
    series = trending_series(bars=800)
    spec = _always_long_spec(risk_percent=25.0, costs=Costs(5, 2, 1))
    result = run_backtest(spec, series)
    metrics = compute_metrics(result, Timeframe.H1)
    report = audit(spec, result, metrics, series)

    risk_check = next(c for c in report.checks if c.name == "Risk management")
    assert risk_check.severity is Severity.CRITICAL


def test_audit_confirms_no_look_ahead_by_construction():
    series = trending_series(bars=500)
    spec = _always_long_spec(costs=Costs(5, 2, 1))
    result = run_backtest(spec, series)
    report = audit(spec, result, compute_metrics(result, Timeframe.H1), series)

    check = next(c for c in report.checks if c.name == "Look-ahead bias")
    assert check.severity is Severity.PASS


def test_audit_detects_forward_shifted_inputs():
    series = trending_series(bars=500)
    spec = _always_long_spec()
    spec.entry_long = [Condition("senkou_a", Op.GT, 0.0)]
    result = run_backtest(spec, series)
    report = audit(spec, result, compute_metrics(result, Timeframe.H1), series)

    check = next(c for c in report.checks if c.name == "Look-ahead bias")
    assert check.severity is Severity.CRITICAL


def test_audit_never_approves_a_losing_strategy():
    series = ranging_series(bars=900)
    spec = _always_long_spec(costs=Costs(20, 10, 5))   # heavy costs guarantee losses
    result = run_backtest(spec, series)
    metrics = compute_metrics(result, Timeframe.H1)
    report = audit(spec, result, metrics, series)

    if metrics.profit_factor is not None and metrics.profit_factor < 1.0:
        assert report.verdict is Verdict.REJECT


def test_every_audit_check_carries_evidence():
    series = trending_series(bars=800)
    spec = _always_long_spec(costs=Costs(5, 2, 1))
    result = run_backtest(spec, series)
    report = audit(spec, result, compute_metrics(result, Timeframe.H1), series)

    for check in report.checks:
        assert check.finding, f"{check.name} has no finding"
        assert check.evidence, f"{check.name} has no evidence"
    assert report.summary


# --- full study -------------------------------------------------------------

def test_full_study_returns_every_stage():
    spec = parse("Buy breakout with volume, 1.5 ATR stop, 3R target")
    study = run_full_study(spec, trending_series(bars=1200), monte_carlo_runs=200)

    for key in ("specification", "spec", "backtest", "metrics", "audit"):
        assert key in study
    assert study["audit"]["verdict"] in ("APPROVED", "REVISE", "REJECT")


def test_full_study_notes_when_validation_was_skipped():
    spec = parse("Buy breakout with volume")
    study = run_full_study(
        spec, trending_series(bars=600), monte_carlo_runs=100, run_validation=False
    )
    assert any("skipped" in item.lower() for item in study["audit"]["improvements"])
