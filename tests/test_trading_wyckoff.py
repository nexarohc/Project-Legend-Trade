"""Wyckoff event labelling: spring, upthrust, test, sign of strength/weakness, LPS/LPSY.

The fixture below was not hand-guessed into passing — it was built bar by bar
and measured against the real detectors (`structure.analyze_structure`,
`wyckoff.analyze_wyckoff`) until the full sequence confirmed, the same way the
random-walk backtest proof was built. The mirrored distribution fixture is the
same candles reflected around a price axis (high/low swapped, `price -> 200 -
price`), which is what makes it a real check that the bearish path is a mirror
of the bullish one rather than a second hand-tuned coincidence.
"""
from __future__ import annotations

import math
import random

import pytest

from trading.models import Candle, Series, Timeframe
from trading import structure as st
from trading import wyckoff as wy
from trading.wyckoff import WyckoffEventType

BASE_TIME = 1_700_000_000
STEP = 3600


def _candle(i: int, o: float, h: float, l: float, c: float, v: float) -> Candle:
    return Candle(BASE_TIME + i * STEP, o, h, l, c, v)


def _spring_to_lps_ohlcv() -> list[tuple[float, float, float, float, float]]:
    """60 bars ranging 95-105, a spring, a test, a volume breakout, a last
    point of support. See the module docstring for how this was constructed."""
    rows: list[tuple[float, float, float, float, float]] = []
    rnd = random.Random(1)
    for k in range(60):
        mid = 100 + math.sin(k / 6) * 4.5
        o = mid + rnd.uniform(-0.3, 0.3)
        c = mid + rnd.uniform(-0.3, 0.3)
        h = max(o, c) + abs(rnd.uniform(0, 0.4))
        l = min(o, c) - abs(rnd.uniform(0, 0.4))
        rows.append((o, h, l, c, 1000 + rnd.uniform(-50, 50)))
    rows.append((97.0, 97.5, 93.0, 98.0, 1400))                    # 60: spring
    for k in range(4):
        rows.append((98 + k * 0.2, 98.6 + k * 0.2, 97.6 + k * 0.2, 98.3 + k * 0.2, 700))
    rows.append((97.0, 97.2, 95.3, 96.8, 500))                     # 65: test
    for k in range(3):
        rows.append((97 + k * 0.3, 97.8 + k * 0.3, 96.6 + k * 0.3, 97.5 + k * 0.3, 800))
    rows.append((100.0, 109.0, 99.5, 108.0, 3000))                 # 69: sign of strength
    rows.append((108.0, 109.2, 107.0, 108.8, 900))
    rows.append((108.8, 110.2, 108.0, 109.8, 950))
    rows.append((109.8, 109.9, 106.2, 106.9, 1000))
    rows.append((106.9, 107.3, 105.9, 106.5, 900))                 # 73: last point of support
    rows.append((106.5, 108.0, 106.3, 107.7, 850))
    rows.append((107.7, 109.0, 106.8, 108.8, 900))
    rows.append((108.8, 110.5, 107.5, 109.7, 950))
    return rows


def _accumulation_series() -> Series:
    candles = [_candle(i, o, h, l, c, v) for i, (o, h, l, c, v) in enumerate(_spring_to_lps_ohlcv())]
    return Series(symbol="WYCKTEST", timeframe=Timeframe.H1, candles=candles, provider="synthetic")


def _distribution_series() -> Series:
    """The same campaign, reflected into a distribution: upthrust, test, sign
    of weakness, last point of supply. Reflecting price around an axis and
    swapping high/low turns a bullish accumulation into its bearish mirror
    exactly, which is what makes this a real symmetry check rather than a
    second hand-tuned fixture."""
    axis = 200.0
    candles = []
    for i, (o, h, l, c, v) in enumerate(_spring_to_lps_ohlcv()):
        candles.append(_candle(i, axis - o, axis - l, axis - h, axis - c, v))
    return Series(symbol="WYCKMIRROR", timeframe=Timeframe.H1, candles=candles, provider="synthetic")


def _types(report: wy.WyckoffReport) -> list[WyckoffEventType]:
    return [e.type for e in report.events]


# --- the accumulation campaign -----------------------------------------------

def test_full_accumulation_sequence_is_detected_in_order():
    series = _accumulation_series()
    struct = st.analyze_structure(series)
    report = wy.analyze_wyckoff(series, struct)

    types = _types(report)
    for required in (
        WyckoffEventType.SPRING,
        WyckoffEventType.TEST,
        WyckoffEventType.SIGN_OF_STRENGTH,
        WyckoffEventType.LAST_POINT_OF_SUPPORT,
    ):
        assert required in types, f"{required.value} missing from {types}"

    # And in the order Wyckoff describes them, not just present anywhere.
    indices = {e.type: e.index for e in report.events}
    assert (
        indices[WyckoffEventType.SPRING]
        < indices[WyckoffEventType.TEST]
        < indices[WyckoffEventType.SIGN_OF_STRENGTH]
        < indices[WyckoffEventType.LAST_POINT_OF_SUPPORT]
    )
    assert report.campaign == "markup_confirmed"


def test_last_point_of_support_is_linked_to_its_breakout():
    series = _accumulation_series()
    struct = st.analyze_structure(series)
    report = wy.analyze_wyckoff(series, struct)

    lps = next(e for e in report.events if e.type is WyckoffEventType.LAST_POINT_OF_SUPPORT)
    sos = next(e for e in report.events if e.type is WyckoffEventType.SIGN_OF_STRENGTH)
    assert lps.related_index == sos.index


# --- the distribution campaign (mirror) --------------------------------------

def test_full_distribution_sequence_is_detected_in_order():
    series = _distribution_series()
    struct = st.analyze_structure(series)
    report = wy.analyze_wyckoff(series, struct)

    types = _types(report)
    for required in (
        WyckoffEventType.UPTHRUST,
        WyckoffEventType.TEST,
        WyckoffEventType.SIGN_OF_WEAKNESS,
        WyckoffEventType.LAST_POINT_OF_SUPPLY,
    ):
        assert required in types, f"{required.value} missing from {types}"

    indices = {e.type: e.index for e in report.events}
    assert (
        indices[WyckoffEventType.UPTHRUST]
        < indices[WyckoffEventType.TEST]
        < indices[WyckoffEventType.SIGN_OF_WEAKNESS]
        < indices[WyckoffEventType.LAST_POINT_OF_SUPPLY]
    )
    assert report.campaign == "markdown_confirmed"


# --- negative controls -------------------------------------------------------

def test_a_persistent_slow_trend_produces_no_events():
    """The case that motivated the directional-efficiency check.

    A market drifting steadily at a small, constant rate can still look
    "contained" over any single 40-bar slice even though it is unambiguously
    trending zoomed out. Span alone cannot tell those apart; this is the test
    that catches a regression back to span-only containment.
    """
    from tests.trading_fixtures import trending_series

    series = trending_series(bars=300, drift=0.15, seed=42)
    struct = st.analyze_structure(series)
    report = wy.analyze_wyckoff(series, struct)
    assert report.events == [], (
        f"a steady trend produced {[e.type.value for e in report.events]} — "
        f"these should have been filtered as directional, not ranging"
    )


def test_directional_efficiency_guard_is_load_bearing():
    """Prove the guard in the test above actually does something: with it
    disabled, the same steady trend must produce false spring/upthrust labels."""
    from tests.trading_fixtures import trending_series

    series = trending_series(bars=300, drift=0.15, seed=42)
    struct = st.analyze_structure(series)

    original = wy.MAX_DIRECTIONAL_EFFICIENCY
    try:
        wy.MAX_DIRECTIONAL_EFFICIENCY = 1.0  # effectively disabled
        report = wy.analyze_wyckoff(series, struct)
    finally:
        wy.MAX_DIRECTIONAL_EFFICIENCY = original

    assert report.events, "disabling the guard should have let the false positives back in"


def test_flat_series_yields_no_events_and_explains_why():
    from tests.trading_fixtures import flat_series

    series = flat_series(bars=200)
    struct = st.analyze_structure(series)
    report = wy.analyze_wyckoff(series, struct)
    assert report.events == []
    assert report.campaign == "none"
    assert report.notes


def test_too_short_a_series_does_not_crash():
    candles = [_candle(i, 100, 101, 99, 100.5, 1000) for i in range(5)]
    series = Series(symbol="SHORT", timeframe=Timeframe.H1, candles=candles, provider="synthetic")
    struct = st.analyze_structure(series)
    report = wy.analyze_wyckoff(series, struct)
    assert report.events == []


# --- evidence and serialisation ----------------------------------------------

def test_every_event_carries_non_empty_evidence():
    """The platform's rule: no unexplained conclusions, anywhere."""
    series = _accumulation_series()
    struct = st.analyze_structure(series)
    report = wy.analyze_wyckoff(series, struct)
    assert report.events
    for event in report.events:
        assert event.evidence.strip()


def test_report_round_trips_through_to_dict():
    series = _accumulation_series()
    struct = st.analyze_structure(series)
    report = wy.analyze_wyckoff(series, struct)
    data = report.to_dict()
    assert data["campaign"] == report.campaign
    assert len(data["events"]) == len(report.events)
    for event_dict in data["events"]:
        assert {"type", "index", "time", "price", "evidence", "related_index"} <= event_dict.keys()


# --- wired into the pipeline --------------------------------------------------

def test_analysis_pipeline_includes_wyckoff_events():
    from trading.analysis import analyze

    series = _accumulation_series()
    result = analyze(series, include_chart_data=False)
    assert "wyckoff_events" in result.market_structure
    assert "campaign" in result.market_structure["wyckoff_events"]
