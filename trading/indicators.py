"""Technical indicators, implemented in pure Python.

No numpy/pandas dependency on purpose: the whole analysis stack stays
importable and testable anywhere the backend runs, and these are all O(n)
single-pass calculations over at most a few thousand bars.

Every function returns a list the same length as the input, left-padded with
`None` for the warm-up period. Keeping the alignment 1:1 with the candle list
means a caller can always index an indicator by bar index without bookkeeping —
and, importantly, `None` makes an unwarmed indicator impossible to mistake for
a real value of 0.
"""
from __future__ import annotations

import math

from trading.models import Series

Number = float | None


def _pad(values: list[float], length: int) -> list[Number]:
    """Left-pad a computed tail so it aligns with the original series."""
    return [None] * (length - len(values)) + list(values)


def sma(values: list[float], period: int) -> list[Number]:
    """Simple moving average via a rolling sum."""
    if period <= 0 or len(values) < period:
        return [None] * len(values)
    out: list[Number] = [None] * (period - 1)
    window = sum(values[:period])
    out.append(window / period)
    for i in range(period, len(values)):
        window += values[i] - values[i - period]
        out.append(window / period)
    return out


def ema(values: list[float], period: int) -> list[Number]:
    """Exponential moving average, seeded with the first SMA."""
    if period <= 0 or len(values) < period:
        return [None] * len(values)
    k = 2 / (period + 1)
    out: list[Number] = [None] * (period - 1)
    prev = sum(values[:period]) / period
    out.append(prev)
    for value in values[period:]:
        prev = value * k + prev * (1 - k)
        out.append(prev)
    return out


def rma(values: list[float], period: int) -> list[Number]:
    """Wilder's smoothing — the average RSI/ATR/ADX are built on."""
    if period <= 0 or len(values) < period:
        return [None] * len(values)
    out: list[Number] = [None] * (period - 1)
    prev = sum(values[:period]) / period
    out.append(prev)
    for value in values[period:]:
        prev = (prev * (period - 1) + value) / period
        out.append(prev)
    return out


def stdev(values: list[float], period: int) -> list[Number]:
    if period <= 1 or len(values) < period:
        return [None] * len(values)
    out: list[Number] = [None] * (period - 1)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1 : i + 1]
        mean = sum(window) / period
        out.append(math.sqrt(sum((v - mean) ** 2 for v in window) / period))
    return out


def true_range(series: Series) -> list[float]:
    """True range per bar; the first bar falls back to its own high-low."""
    tr = []
    for i, c in enumerate(series.candles):
        if i == 0:
            tr.append(c.high - c.low)
        else:
            prev_close = series.candles[i - 1].close
            tr.append(max(c.high - c.low, abs(c.high - prev_close), abs(c.low - prev_close)))
    return tr


def atr(series: Series, period: int = 14) -> list[Number]:
    return rma(true_range(series), period)


def rsi(values: list[float], period: int = 14) -> list[Number]:
    """Wilder's RSI."""
    if len(values) <= period:
        return [None] * len(values)
    gains, losses = [], []
    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    avg_gain = rma(gains, period)
    avg_loss = rma(losses, period)
    out: list[Number] = [None]  # no change is defined for the first bar
    for g, l in zip(avg_gain, avg_loss):
        if g is None or l is None:
            out.append(None)
        elif l == 0:
            out.append(100.0)
        else:
            rs = g / l
            out.append(100 - (100 / (1 + rs)))
    return out


def macd(
    values: list[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[list[Number], list[Number], list[Number]]:
    """Returns (macd line, signal line, histogram)."""
    fast_ema, slow_ema = ema(values, fast), ema(values, slow)
    line: list[Number] = [
        (f - s) if (f is not None and s is not None) else None for f, s in zip(fast_ema, slow_ema)
    ]
    warm = [v for v in line if v is not None]
    signal_tail = ema(warm, signal)
    signal_line = _pad([v for v in signal_tail if v is not None], len(values))
    hist: list[Number] = [
        (m - s) if (m is not None and s is not None) else None for m, s in zip(line, signal_line)
    ]
    return line, signal_line, hist


def bollinger(
    values: list[float], period: int = 20, mult: float = 2.0
) -> tuple[list[Number], list[Number], list[Number]]:
    """Returns (upper, middle, lower)."""
    mid = sma(values, period)
    sd = stdev(values, period)
    upper = [(m + mult * s) if (m is not None and s is not None) else None for m, s in zip(mid, sd)]
    lower = [(m - mult * s) if (m is not None and s is not None) else None for m, s in zip(mid, sd)]
    return upper, mid, lower


def adx(series: Series, period: int = 14) -> tuple[list[Number], list[Number], list[Number]]:
    """Average Directional Index. Returns (adx, +DI, -DI)."""
    n = len(series)
    if n < period * 2:
        return [None] * n, [None] * n, [None] * n

    plus_dm, minus_dm = [], []
    for i in range(1, n):
        up = series.candles[i].high - series.candles[i - 1].high
        down = series.candles[i - 1].low - series.candles[i].low
        plus_dm.append(up if (up > down and up > 0) else 0.0)
        minus_dm.append(down if (down > up and down > 0) else 0.0)

    tr = true_range(series)[1:]
    atr_vals = rma(tr, period)
    plus_smooth = rma(plus_dm, period)
    minus_smooth = rma(minus_dm, period)

    plus_di: list[Number] = [None]
    minus_di: list[Number] = [None]
    dx: list[float] = []
    for a, p, m in zip(atr_vals, plus_smooth, minus_smooth):
        if a is None or p is None or m is None or a == 0:
            plus_di.append(None)
            minus_di.append(None)
            continue
        pdi, mdi = 100 * p / a, 100 * m / a
        plus_di.append(pdi)
        minus_di.append(mdi)
        total = pdi + mdi
        dx.append(100 * abs(pdi - mdi) / total if total else 0.0)

    adx_tail = [v for v in rma(dx, period) if v is not None]
    return _pad(adx_tail, n), plus_di, minus_di


def stochastic(
    series: Series, k_period: int = 14, d_period: int = 3
) -> tuple[list[Number], list[Number]]:
    """Classic stochastic oscillator. Returns (%K, %D)."""
    n = len(series)
    k: list[Number] = [None] * min(k_period - 1, n)
    for i in range(k_period - 1, n):
        window = series.candles[i - k_period + 1 : i + 1]
        hh = max(c.high for c in window)
        ll = min(c.low for c in window)
        k.append(100 * (series.candles[i].close - ll) / (hh - ll) if hh != ll else 50.0)
    warm = [v for v in k if v is not None]
    d = _pad([v for v in sma(warm, d_period) if v is not None], n)
    return k, d


def stoch_rsi(
    values: list[float], rsi_period: int = 14, stoch_period: int = 14, smooth: int = 3
) -> tuple[list[Number], list[Number]]:
    """Stochastic RSI — the stochastic formula applied to the RSI line."""
    r = rsi(values, rsi_period)
    warm = [v for v in r if v is not None]
    raw: list[float] = []
    for i in range(len(warm)):
        if i < stoch_period - 1:
            continue
        window = warm[i - stoch_period + 1 : i + 1]
        hi, lo = max(window), min(window)
        raw.append(100 * (warm[i] - lo) / (hi - lo) if hi != lo else 50.0)
    k = _pad([v for v in sma(raw, smooth) if v is not None], len(values))
    warm_k = [v for v in k if v is not None]
    d = _pad([v for v in sma(warm_k, smooth) if v is not None], len(values))
    return k, d


def cci(series: Series, period: int = 20) -> list[Number]:
    """Commodity Channel Index using mean absolute deviation."""
    tp = [c.typical_price for c in series.candles]
    ma = sma(tp, period)
    out: list[Number] = []
    for i, m in enumerate(ma):
        if m is None:
            out.append(None)
            continue
        window = tp[i - period + 1 : i + 1]
        mad = sum(abs(v - m) for v in window) / period
        out.append((tp[i] - m) / (0.015 * mad) if mad else 0.0)
    return out


def mfi(series: Series, period: int = 14) -> list[Number]:
    """Money Flow Index — RSI weighted by volume."""
    n = len(series)
    if n <= period:
        return [None] * n
    pos, neg = [], []
    for i in range(1, n):
        tp_now = series.candles[i].typical_price
        tp_prev = series.candles[i - 1].typical_price
        flow = tp_now * series.candles[i].volume
        pos.append(flow if tp_now > tp_prev else 0.0)
        neg.append(flow if tp_now < tp_prev else 0.0)

    out: list[Number] = [None] * (period)
    for i in range(period - 1, len(pos)):
        p = sum(pos[i - period + 1 : i + 1])
        ne = sum(neg[i - period + 1 : i + 1])
        out.append(100.0 if ne == 0 else 100 - (100 / (1 + p / ne)))
    return out[:n] + [None] * max(0, n - len(out))


def obv(series: Series) -> list[float]:
    """On-Balance Volume."""
    out = [0.0]
    for i in range(1, len(series)):
        prev, cur = series.candles[i - 1], series.candles[i]
        if cur.close > prev.close:
            out.append(out[-1] + cur.volume)
        elif cur.close < prev.close:
            out.append(out[-1] - cur.volume)
        else:
            out.append(out[-1])
    return out


def vwap(series: Series, anchor_seconds: int = 86400) -> list[Number]:
    """Session-anchored VWAP.

    Resets whenever the bar's timestamp crosses into a new anchor period
    (daily by default), which is what makes it a session VWAP rather than a
    cumulative one that drifts uselessly over months.
    """
    out: list[Number] = []
    cum_pv = cum_v = 0.0
    current_anchor: int | None = None

    for c in series.candles:
        anchor = c.timestamp - (c.timestamp % anchor_seconds)
        if anchor != current_anchor:
            current_anchor = anchor
            cum_pv = cum_v = 0.0
        cum_pv += c.typical_price * c.volume
        cum_v += c.volume
        out.append(cum_pv / cum_v if cum_v else c.close)
    return out


def supertrend(
    series: Series, period: int = 10, mult: float = 3.0
) -> tuple[list[Number], list[Number]]:
    """SuperTrend. Returns (line, direction) where direction is 1 up / -1 down."""
    n = len(series)
    atr_vals = atr(series, period)
    line: list[Number] = [None] * n
    direction: list[Number] = [None] * n

    upper = lower = None
    trend = 1
    for i, c in enumerate(series.candles):
        a = atr_vals[i]
        if a is None:
            continue
        mid = (c.high + c.low) / 2
        basic_upper = mid + mult * a
        basic_lower = mid - mult * a

        # Bands ratchet in the direction of trend and only reset on a close through.
        upper = basic_upper if upper is None or c.close > upper else min(basic_upper, upper)
        lower = basic_lower if lower is None or c.close < lower else max(basic_lower, lower)

        if c.close > (upper if trend == -1 else lower):
            trend = 1
        elif c.close < (lower if trend == 1 else upper):
            trend = -1

        line[i] = lower if trend == 1 else upper
        direction[i] = trend
    return line, direction


def ichimoku(
    series: Series, conversion: int = 9, base: int = 26, span_b: int = 52
) -> dict[str, list[Number]]:
    """Ichimoku Kinko Hyo.

    Spans are returned unshifted (aligned to the bar they were computed from);
    the chart layer applies the forward displacement so the backtester never
    accidentally reads a value that would not have existed yet.
    """
    n = len(series)

    def midpoint(period: int) -> list[Number]:
        out: list[Number] = [None] * min(period - 1, n)
        for i in range(period - 1, n):
            window = series.candles[i - period + 1 : i + 1]
            out.append((max(c.high for c in window) + min(c.low for c in window)) / 2)
        return out

    tenkan = midpoint(conversion)
    kijun = midpoint(base)
    senkou_a: list[Number] = [
        (t + k) / 2 if (t is not None and k is not None) else None for t, k in zip(tenkan, kijun)
    ]
    return {
        "tenkan": tenkan,
        "kijun": kijun,
        "senkou_a": senkou_a,
        "senkou_b": midpoint(span_b),
        "chikou": series.closes,
        "displacement": [base] * n,
    }


def compute_all(series: Series) -> dict[str, object]:
    """Compute the full indicator set once, for the analysis pipeline.

    Returns full arrays (for charting) alongside the latest scalar of each
    (for the narrative layer), so callers never recompute.
    """
    closes = series.closes
    macd_line, macd_signal, macd_hist = macd(closes)
    bb_up, bb_mid, bb_low = bollinger(closes)
    adx_vals, plus_di, minus_di = adx(series)
    st_line, st_dir = supertrend(series)
    stoch_k, stoch_d = stoch_rsi(closes)
    ich = ichimoku(series)

    arrays: dict[str, list] = {
        "ema20": ema(closes, 20),
        "ema50": ema(closes, 50),
        "ema200": ema(closes, 200),
        "sma50": sma(closes, 50),
        "sma200": sma(closes, 200),
        "vwap": vwap(series),
        "atr": atr(series),
        "rsi": rsi(closes),
        "macd": macd_line,
        "macd_signal": macd_signal,
        "macd_hist": macd_hist,
        "bb_upper": bb_up,
        "bb_middle": bb_mid,
        "bb_lower": bb_low,
        "adx": adx_vals,
        "plus_di": plus_di,
        "minus_di": minus_di,
        "cci": cci(series),
        "mfi": mfi(series),
        "stoch_rsi_k": stoch_k,
        "stoch_rsi_d": stoch_d,
        "obv": obv(series),
        "supertrend": st_line,
        "supertrend_dir": st_dir,
        "ichimoku_tenkan": ich["tenkan"],
        "ichimoku_kijun": ich["kijun"],
        "ichimoku_senkou_a": ich["senkou_a"],
        "ichimoku_senkou_b": ich["senkou_b"],
    }
    latest = {name: (values[-1] if values else None) for name, values in arrays.items()}
    return {"arrays": arrays, "latest": latest}


def last_valid(values: list[Number]) -> float | None:
    """Most recent non-None entry — useful when the final bar is still warming up."""
    for value in reversed(values):
        if value is not None:
            return value
    return None
