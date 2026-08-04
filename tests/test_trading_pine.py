"""Pine Script generation.

We cannot compile Pine here, so these tests assert the properties that actually
break scripts in practice: the version pragma, balanced structure, the `[1]` on
breakout lookbacks (without which a breakout rule can never fire), non-repainting
`request.security`, and exactly one set of exit orders per entry.
"""
import re

import pytest

from trading.pine import PineGenerationError, generate, generate_indicator, generate_strategy
from trading.strategy import Condition, Op, StrategySpec, parse
from trading.strategy.spec import StopSpec, StopType, TargetSpec, TargetType


@pytest.fixture()
def spec():
    return parse(
        "Buy breakout with increasing volume on the 4h, 1.5 ATR stop, 3R target, "
        "risk 1%, breakeven at 1R, trailing 2 ATR, adx above 25",
        "BTCUSDT",
    )


def test_declares_version_six_first(spec):
    code = generate_strategy(spec)
    directives = [line for line in code.splitlines() if line.startswith("//@version")]
    assert directives == ["//@version=6"]


def test_strategy_declaration_includes_costs(spec):
    code = generate_strategy(spec)
    assert "strategy(" in code
    assert "commission_type" in code
    assert "slippage" in code


def test_execution_settings_match_the_backtester(spec):
    """Bar-close evaluation and next-bar fills, same as the local engine."""
    code = generate_strategy(spec)
    assert "calc_on_every_tick   = false" in code
    assert "process_orders_on_close = false" in code


def test_breakout_lookback_excludes_the_current_bar(spec):
    """Without [1] the current bar is inside its own window and never breaks out."""
    code = generate_strategy(spec)
    lookbacks = re.findall(r"ta\.(highest|lowest)\([^)]*\)(\[1\])?", code)
    assert lookbacks, "expected a breakout lookback in this strategy"
    for _, suffix in lookbacks:
        assert suffix == "[1]", "ta.highest/lowest must be offset by [1]"


def test_higher_timeframe_request_does_not_repaint(spec):
    code = generate_strategy(spec)
    calls = [line for line in code.splitlines() if "request.security(" in line]
    assert calls, "expected a higher-timeframe request"
    for call in calls:
        assert "lookahead=barmerge.lookahead_off" in call


def test_entries_and_exits_are_emitted(spec):
    code = generate_strategy(spec)
    assert 'strategy.entry("Long"' in code
    assert 'strategy.entry("Short"' in code
    assert "strategy.exit(" in code


def test_no_duplicate_full_size_stop_orders(spec):
    """A separate full-size stop alongside a final exit would close twice."""
    code = generate_strategy(spec)
    assert 'strategy.exit("Stop L"' not in code
    assert 'strategy.exit("Stop S"' not in code
    # Every exit still carries stop protection.
    for call in re.findall(r"strategy\.exit\([^)]*\)", code):
        assert "stop=stopPrice" in call


def test_alerts_are_generated(spec):
    code = generate_strategy(spec)
    assert "alertcondition(" in code
    assert "alert(" in code


def test_inputs_are_exposed_for_optimisation(spec):
    code = generate_strategy(spec)
    assert "input.float" in code
    assert "input.int" in code
    assert 'group="Risk"' in code


def test_trade_management_is_translated(spec):
    code = generate_strategy(spec)
    assert "breakeven" in code.lower()
    assert "trail" in code.lower()


def test_conditions_use_pine_cross_functions():
    spec = StrategySpec(
        name="cross",
        entry_long=[Condition("ema(20)", Op.CROSS_ABOVE, "ema(50)")],
        entry_short=[Condition("ema(20)", Op.CROSS_BELOW, "ema(50)")],
    )
    code = generate_strategy(spec)
    assert "ta.crossover(" in code
    assert "ta.crossunder(" in code


def test_indicator_calculations_are_not_duplicated():
    spec = StrategySpec(
        name="dup",
        entry_long=[
            Condition("ema(20)", Op.CROSS_ABOVE, "ema(50)"),
            Condition("ema(20)", Op.GT, "close"),
        ],
    )
    code = generate_strategy(spec)
    assert len(re.findall(r"^ema20 = ta\.ema", code, re.MULTILINE)) == 1


def test_supertrend_direction_is_normalised():
    """Pine reports -1 for bullish; our rules assume +1."""
    spec = StrategySpec(
        name="st",
        entry_long=[Condition("supertrend_dir", Op.GT, 0.0)],
    )
    code = generate_strategy(spec)
    assert "stDirNormalised" in code


def test_comment_density_is_meaningful(spec):
    code = generate_strategy(spec)
    lines = [line for line in code.splitlines() if line.strip()]
    comments = [line for line in lines if line.strip().startswith("//")]
    assert len(comments) / len(lines) > 0.2, "generated Pine should be well commented"


def test_indicator_variant_places_no_orders(spec):
    code = generate_indicator(spec)
    assert "indicator(" in code
    assert "strategy.entry" not in code
    assert "strategy.exit" not in code
    assert "plotshape(" in code


def test_generate_returns_explanation_and_steps(spec):
    result = generate(spec, "strategy")
    assert result["version"] == 6
    assert result["code"]
    assert result["explanation"]
    assert result["install_steps"]
    assert result["notes"]


def test_unsupported_feature_is_reported_clearly():
    spec = StrategySpec(name="bad", entry_long=[Condition("moon_phase(28)", Op.GT, 1.0)])
    with pytest.raises(PineGenerationError) as exc:
        generate_strategy(spec)
    assert "moon_phase" in str(exc.value)


def test_quotes_in_names_do_not_break_the_script():
    spec = StrategySpec(name='My "Best" Strategy', entry_long=[Condition("close", Op.GT, 1.0)])
    code = generate_strategy(spec)
    header = next(line for line in code.splitlines() if line.startswith("strategy("))
    assert header.count('"') == 2, "an unescaped quote would terminate the string early"


def test_percent_stop_translates_to_percent_maths():
    spec = StrategySpec(
        name="pct",
        entry_long=[Condition("close", Op.GT, 1.0)],
        stop=StopSpec(StopType.PERCENT, 2.0),
        targets=TargetSpec(TargetType.PERCENT, [4.0]),
    )
    code = generate_strategy(spec)
    assert "stopDistance = close * (stopValue / 100)" in code


def test_multiple_targets_scale_out():
    spec = StrategySpec(
        name="scale",
        entry_long=[Condition("close", Op.GT, 1.0)],
        targets=TargetSpec(TargetType.RR, [1.0, 2.0, 3.0]),
    )
    code = generate_strategy(spec)
    assert "tp1Price" in code and "tp2Price" in code and "tp3Price" in code
    assert "qty_percent" in code
