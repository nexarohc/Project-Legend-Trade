"""Market scanner: filters evaluated against numbers the analysis pipeline
already computes, so a scanner match can never disagree with a full analysis
of the same symbol.

Threshold values in these tests were measured directly against the fixtures
first (not guessed), the same way the random-walk backtest proof was built:
trending_series is bullish/strength≈83/RSI≈68.6; ranging_series is
ranging/strength=50/RSI≈53.6; flat_series is ranging/RSI=100 (a flat series
has no losses at all, so RSI saturates — a useful edge case in its own right).
"""
import pytest

from tests.trading_fixtures import flat_series, ranging_series, trending_series
from trading.models import Timeframe
from trading.scanner import (
    MAX_SCAN_SYMBOLS,
    Op,
    ScanFilter,
    ScanField,
    run_scan,
    scan_series,
)


def _f(field, op, value, value2=None):
    return ScanFilter(field=field, op=op, value=value, value2=value2)


def test_gt_filter_matches_above_threshold():
    series = trending_series(bars=300)
    match = scan_series("TREND", series, [_f(ScanField.PRICE, Op.GT, 100)])
    assert match is not None
    assert match.symbol == "TREND"
    assert match.price == pytest.approx(series.last.close)


def test_gt_filter_excludes_below_threshold():
    series = trending_series(bars=300)
    match = scan_series("TREND", series, [_f(ScanField.PRICE, Op.GT, 1_000_000)])
    assert match is None


def test_trend_equality_filter():
    trending = trending_series(bars=300)
    ranging = ranging_series(bars=300)

    assert scan_series("A", trending, [_f(ScanField.TREND, Op.EQ, "bullish")]) is not None
    assert scan_series("B", ranging, [_f(ScanField.TREND, Op.EQ, "bullish")]) is None


def test_trend_equality_is_case_insensitive():
    series = trending_series(bars=300)
    match = scan_series("A", series, [_f(ScanField.TREND, Op.EQ, "BULLISH")])
    assert match is not None


def test_between_filter_on_rsi():
    trending = trending_series(bars=300)   # RSI ~68.6
    ranging = ranging_series(bars=300)      # RSI ~53.6

    filt = _f(ScanField.RSI, Op.BETWEEN, 60.0, 80.0)
    assert scan_series("A", trending, [filt]) is not None
    assert scan_series("B", ranging, [filt]) is None


def test_multiple_filters_are_anded():
    series = trending_series(bars=300)  # bullish, strength ~83, RSI ~68.6

    both_true = [_f(ScanField.TREND, Op.EQ, "bullish"), _f(ScanField.RSI, Op.GT, 60)]
    assert scan_series("A", series, both_true) is not None

    one_false = [_f(ScanField.TREND, Op.EQ, "bullish"), _f(ScanField.RSI, Op.GT, 95)]
    assert scan_series("A", series, one_false) is None


def test_gt_lt_on_a_string_field_never_matches():
    """trend is a string; a numeric comparison operator on it is meaningless
    and must fail rather than raise or coerce."""
    series = trending_series(bars=300)
    match = scan_series("A", series, [_f(ScanField.TREND, Op.GT, 0)])
    assert match is None


def test_too_short_a_series_never_matches():
    """Fewer than 30 bars: even a filter with no real constraint must not match,
    because the underlying measurements are not yet meaningful."""
    series = flat_series(bars=10)
    match = scan_series("A", series, [_f(ScanField.PRICE, Op.GT, 0)])
    assert match is None


def test_evidence_lists_every_filter():
    series = trending_series(bars=300)
    filters = [_f(ScanField.PRICE, Op.GT, 100), _f(ScanField.TREND, Op.EQ, "bullish")]
    match = scan_series("A", series, filters)
    assert len(match.evidence) == 2
    assert "price" in match.evidence[0]
    assert "trend" in match.evidence[1]


def test_describe_formats_comparison_and_between():
    assert _f(ScanField.RSI, Op.GT, 70).describe() == "rsi > 70"
    assert _f(ScanField.RSI, Op.BETWEEN, 30, 70).describe() == "rsi between 30 and 70"


# --- run_scan: the network-touching wrapper ---------------------------------

@pytest.fixture()
def stub_market(monkeypatch):
    """Serve a fixed series per symbol with no network access."""
    import trading.scanner as scanner_module  # noqa: F401 - ensures module imported before patch
    from trading.market import market_service

    series_by_symbol = {
        "BULL": trending_series(bars=300),
        "RANGE": ranging_series(bars=300),
        "FLATSYM": flat_series(bars=60),
    }

    def fake_candles(symbol, timeframe, limit=500, provider=None):
        key = symbol.upper()
        if key not in series_by_symbol:
            raise ValueError(f"no stub data for {symbol}")
        return series_by_symbol[key]

    monkeypatch.setattr(market_service, "candles", fake_candles)
    return series_by_symbol


def test_run_scan_returns_only_matching_symbols(stub_market):
    report = run_scan(
        ["BULL", "RANGE", "FLATSYM"], Timeframe.H1,
        [ScanFilter(ScanField.TREND, Op.EQ, "bullish")],
    )
    assert report.scanned == 3
    assert [m.symbol for m in report.matches] == ["BULL"]
    assert report.failed == {}


def test_run_scan_deduplicates_case_insensitively(stub_market):
    report = run_scan(["BULL", "bull", "BULL"], Timeframe.H1, [])
    assert report.scanned == 1


def test_run_scan_isolates_one_symbols_failure(stub_market):
    report = run_scan(["BULL", "NOPE"], Timeframe.H1, [])
    assert "NOPE" in report.failed
    assert any(m.symbol == "BULL" for m in report.matches)


def test_run_scan_caps_at_max_symbols(stub_market, monkeypatch):
    from trading.market import market_service

    calls = []

    def counting_candles(symbol, timeframe, limit=500, provider=None):
        calls.append(symbol)
        return trending_series(bars=300)

    monkeypatch.setattr(market_service, "candles", counting_candles)

    oversized = [f"SYM{i}" for i in range(MAX_SCAN_SYMBOLS + 20)]
    report = run_scan(oversized, Timeframe.H1, [])
    assert report.scanned == MAX_SCAN_SYMBOLS
    assert len(calls) == MAX_SCAN_SYMBOLS


def test_run_scan_results_are_sorted_by_symbol(stub_market):
    report = run_scan(["RANGE", "BULL"], Timeframe.H1, [])
    assert [m.symbol for m in report.matches] == ["BULL", "RANGE"]
