"""Indicator correctness and alignment.

The alignment contract matters as much as the maths: every indicator returns a
list the same length as the input, left-padded with None. A silently shortened
array would shift every subsequent index by the warm-up period and quietly
corrupt every downstream signal.
"""
import math

import pytest

from tests.trading_fixtures import candle, flat_series, make_series, trending_series
from trading import indicators as ind


def test_sma_matches_hand_calculation():
    values = [1, 2, 3, 4, 5, 6]
    result = ind.sma(values, 3)
    assert result[:2] == [None, None]
    assert result[2] == pytest.approx(2.0)   # (1+2+3)/3
    assert result[5] == pytest.approx(5.0)   # (4+5+6)/3


def test_ema_seeds_from_sma_then_smooths():
    values = [1, 2, 3, 4, 5]
    result = ind.ema(values, 3)
    assert result[:2] == [None, None]
    assert result[2] == pytest.approx(2.0)  # seeded with the first SMA
    k = 2 / 4
    assert result[3] == pytest.approx(4 * k + 2.0 * (1 - k))


def test_rsi_is_100_when_price_only_rises():
    values = list(range(1, 40))
    result = ind.rsi(values, 14)
    assert result[-1] == pytest.approx(100.0)


def test_rsi_is_zero_when_price_only_falls():
    values = list(range(40, 1, -1))
    result = ind.rsi(values, 14)
    assert result[-1] == pytest.approx(0.0)


def test_rsi_midrange_on_oscillating_input():
    values = [10 + (i % 2) for i in range(60)]
    result = ind.rsi(values, 14)
    assert 40 < result[-1] < 60


@pytest.mark.parametrize("func,args", [
    (ind.sma, (20,)),
    (ind.ema, (20,)),
    (ind.rma, (14,)),
    (ind.rsi, (14,)),
    (ind.stdev, (20,)),
])
def test_list_indicators_preserve_length(func, args):
    values = [float(i) for i in range(120)]
    assert len(func(values, *args)) == len(values)


@pytest.mark.parametrize("name", [
    "ema20", "ema50", "ema200", "sma50", "vwap", "atr", "rsi", "macd",
    "bb_upper", "adx", "cci", "mfi", "stoch_rsi_k", "obv", "supertrend",
])
def test_compute_all_arrays_align_with_candles(name):
    series = trending_series(bars=260)
    computed = ind.compute_all(series)
    assert len(computed["arrays"][name]) == len(series), f"{name} is misaligned"


def test_warmup_is_none_not_zero():
    """An unwarmed indicator must be None. Zero would look like a real reading."""
    series = trending_series(bars=250)
    rsi_values = ind.rsi(series.closes, 14)
    assert rsi_values[0] is None
    assert all(v is None for v in rsi_values[:14])
    assert rsi_values[20] is not None


def test_atr_is_positive_and_tracks_range():
    series = trending_series(bars=120)
    atr_values = ind.atr(series, 14)
    warm = [v for v in atr_values if v is not None]
    assert warm and all(v > 0 for v in warm)


def test_atr_of_flat_market_is_zero():
    result = ind.atr(flat_series(60), 14)
    assert result[-1] == pytest.approx(0.0)


def test_macd_histogram_is_line_minus_signal():
    series = trending_series(bars=200)
    line, signal, hist = ind.macd(series.closes)
    for l, s, h in zip(line, signal, hist):
        if l is not None and s is not None:
            assert h == pytest.approx(l - s)


def test_bollinger_bands_bracket_the_basis():
    series = trending_series(bars=150)
    upper, mid, lower = ind.bollinger(series.closes, 20, 2.0)
    for u, m, lo in zip(upper, mid, lower):
        if u is not None:
            assert lo <= m <= u


def test_adx_stays_in_range():
    series = trending_series(bars=200)
    adx_values, plus_di, minus_di = ind.adx(series, 14)
    for value in (v for v in adx_values if v is not None):
        assert 0 <= value <= 100
    for value in (v for v in plus_di if v is not None):
        assert 0 <= value <= 100


def test_adx_higher_in_trend_than_in_chop():
    from tests.trading_fixtures import ranging_series

    trend_adx = ind.last_valid(ind.adx(trending_series(bars=300, drift=0.4), 14)[0])
    range_adx = ind.last_valid(ind.adx(ranging_series(bars=300), 14)[0])
    assert trend_adx > range_adx


def test_vwap_resets_each_session():
    """VWAP must restart at the daily boundary, not accumulate forever."""
    candles = [candle(i, 100, 101, 99, 100, 10) for i in range(24)]
    candles += [candle(i, 200, 201, 199, 200, 10) for i in range(24, 48)]
    series = make_series(candles)

    result = ind.vwap(series, anchor_seconds=86400)
    # The last bar of day two must reflect day two's prices only.
    assert result[-1] == pytest.approx(200.0, rel=0.01)


def test_obv_accumulates_with_direction():
    candles = [
        candle(0, 100, 101, 99, 100, 500),
        candle(1, 100, 102, 100, 101, 300),   # up  -> +300
        candle(2, 101, 102, 100, 100, 200),   # down -> -200
        candle(3, 100, 101, 99, 100, 400),    # flat -> unchanged
    ]
    result = ind.obv(make_series(candles))
    assert result == [0.0, 300.0, 100.0, 100.0]


def test_supertrend_direction_is_binary():
    series = trending_series(bars=150)
    _, direction = ind.supertrend(series, 10, 3.0)
    for value in (v for v in direction if v is not None):
        assert value in (1, -1)


def test_indicators_survive_short_input():
    """Too little data must return all-None, never raise."""
    series = make_series([candle(i, 100, 101, 99, 100) for i in range(3)])
    assert ind.rsi(series.closes, 14) == [None, None, None]
    assert all(v is None for v in ind.atr(series, 14))
    computed = ind.compute_all(series)
    assert computed["latest"]["rsi"] is None


def test_last_valid_finds_most_recent_number():
    assert ind.last_valid([None, 1.0, 2.0, None]) == 2.0
    assert ind.last_valid([None, None]) is None
