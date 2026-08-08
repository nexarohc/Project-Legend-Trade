import { useEffect, useRef, useState } from "react";
import { createChart, CrosshairMode, LineStyle } from "lightweight-charts";
import { formatPrice, formatPercent, TIMEFRAMES } from "../../lib/tradingApi.js";
import { useCandles } from "../../lib/useMarketStream.js";

/**
 * The chart.
 *
 * Built on lightweight-charts — TradingView's own open-source library
 * (Apache 2.0), which is the legal way to get TradingView-grade panning,
 * zooming and crosshair behaviour without their proprietary Charting Library
 * licence.
 *
 * Live updates go through `series.update()` rather than `setData()`. That
 * distinction matters: `setData` replaces the whole dataset and resets the
 * user's zoom and scroll position on every tick, which makes a live chart
 * unusable. `update` mutates the last bar in place and leaves the viewport
 * exactly where the user put it.
 */

const OVERLAY_STYLES = {
  ema20: { color: "#4a9eff", title: "EMA 20", width: 1 },
  ema50: { color: "#f0a500", title: "EMA 50", width: 1 },
  ema200: { color: "#c74aff", title: "EMA 200", width: 2 },
  vwap: { color: "#22d3c7", title: "VWAP", width: 1, style: LineStyle.Dashed },
  bb_upper: { color: "#3a4358", title: "BB Upper", width: 1 },
  bb_lower: { color: "#3a4358", title: "BB Lower", width: 1 },
  supertrend: { color: "#26a69a", title: "SuperTrend", width: 2, style: LineStyle.Dotted },
};

export default function ChartPanel({
  symbol,
  timeframe,
  onTimeframeChange,
  onSymbolChange,
  stream,
  analysis,
  overlays,
  onToggleOverlay,
  showZones,
  onToggleZones,
}) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const candleSeriesRef = useRef(null);
  const volumeSeriesRef = useRef(null);
  const overlaySeriesRef = useRef({});
  const priceLinesRef = useRef([]);
  const seededRef = useRef(false);

  const [hovered, setHovered] = useState(null);
  const [symbolInput, setSymbolInput] = useState(symbol);

  // Bumped every time the chart is (re)created. Data effects depend on it so a
  // rebuilt chart is always re-seeded: without this, a remount that happens
  // after the snapshot arrived would leave the new series empty, because the
  // snapshot state itself never changed and its effect would not re-run.
  const [chartEpoch, setChartEpoch] = useState(0);

  const { snapshot, lastCandle, error, loading } = useCandles(stream, symbol, timeframe);

  useEffect(() => setSymbolInput(symbol), [symbol]);

  // --- create the chart once ------------------------------------------------
  useEffect(() => {
    if (!containerRef.current) return undefined;

    const chart = createChart(containerRef.current, {
      layout: {
        background: { color: "transparent" },
        textColor: "#8b93a7",
        fontFamily: "'JetBrains Mono', 'SF Mono', Menlo, monospace",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: "rgba(35, 42, 58, 0.4)" },
        horzLines: { color: "rgba(35, 42, 58, 0.4)" },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: "#4a9eff", width: 1, style: LineStyle.Dashed, labelBackgroundColor: "#1b2030" },
        horzLine: { color: "#4a9eff", width: 1, style: LineStyle.Dashed, labelBackgroundColor: "#1b2030" },
      },
      rightPriceScale: { borderColor: "#232a3a", scaleMargins: { top: 0.08, bottom: 0.28 } },
      timeScale: { borderColor: "#232a3a", timeVisible: true, secondsVisible: false, rightOffset: 6 },
      handleScroll: { mouseWheel: true, pressedMouseMove: true },
      handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true },
    });

    const candleSeries = chart.addCandlestickSeries({
      upColor: "#26a69a",
      downColor: "#ef5350",
      borderUpColor: "#26a69a",
      borderDownColor: "#ef5350",
      wickUpColor: "#26a69a",
      wickDownColor: "#ef5350",
    });

    const volumeSeries = chart.addHistogramSeries({
      priceFormat: { type: "volume" },
      priceScaleId: "volume",
    });
    // Pin volume to the bottom quarter so it never competes with price.
    chart.priceScale("volume").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });

    chart.subscribeCrosshairMove((param) => {
      if (!param.time || !param.seriesData) {
        setHovered(null);
        return;
      }
      const bar = param.seriesData.get(candleSeries);
      const vol = param.seriesData.get(volumeSeries);
      if (bar) setHovered({ ...bar, volume: vol?.value });
    });

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    volumeSeriesRef.current = volumeSeries;
    // A brand-new chart has no viewport worth preserving, so allow the next
    // seed to fit content. Done here rather than in a separate effect because
    // effects run in declaration order and the seed effect is declared first.
    seededRef.current = false;
    setChartEpoch((epoch) => epoch + 1);

    const resize = () => {
      if (!containerRef.current) return;
      chart.applyOptions({
        width: containerRef.current.clientWidth,
        height: containerRef.current.clientHeight,
      });
    };
    resize();

    const observer = new ResizeObserver(resize);
    observer.observe(containerRef.current);

    return () => {
      observer.disconnect();
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      volumeSeriesRef.current = null;
      overlaySeriesRef.current = {};
    };
  }, []);

  // --- seed history ---------------------------------------------------------
  useEffect(() => {
    if (!snapshot || !candleSeriesRef.current || !volumeSeriesRef.current) return;

    const bars = snapshot.candles.map((c) => ({
      time: c.time,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    }));
    candleSeriesRef.current.setData(bars);

    volumeSeriesRef.current.setData(
      snapshot.candles.map((c) => ({
        time: c.time,
        value: c.volume,
        color: c.close >= c.open ? "rgba(38, 166, 154, 0.35)" : "rgba(239, 83, 80, 0.35)",
      })),
    );

    // Only fit the view the first time a symbol loads. Re-fitting on every
    // reconnect would yank the viewport out from under the user.
    if (!seededRef.current) {
      chartRef.current?.timeScale().fitContent();
      seededRef.current = true;
    }
  }, [snapshot, chartEpoch]);

  // A new instrument should fit to its own data rather than inherit the
  // previous symbol's viewport.
  useEffect(() => {
    seededRef.current = false;
  }, [symbol, timeframe]);

  // --- live tick updates ----------------------------------------------------
  useEffect(() => {
    if (!lastCandle || !candleSeriesRef.current || !volumeSeriesRef.current) return;
    candleSeriesRef.current.update({
      time: lastCandle.time,
      open: lastCandle.open,
      high: lastCandle.high,
      low: lastCandle.low,
      close: lastCandle.close,
    });
    volumeSeriesRef.current.update({
      time: lastCandle.time,
      value: lastCandle.volume,
      color: lastCandle.close >= lastCandle.open
        ? "rgba(38, 166, 154, 0.35)"
        : "rgba(239, 83, 80, 0.35)",
    });
  }, [lastCandle]);

  // --- indicator overlays ---------------------------------------------------
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    const data = analysis?.chart_data?.overlays ?? {};

    Object.entries(OVERLAY_STYLES).forEach(([key, style]) => {
      const enabled = overlays[key] && Array.isArray(data[key]) && data[key].length > 0;
      const existing = overlaySeriesRef.current[key];

      if (!enabled) {
        if (existing) {
          chart.removeSeries(existing);
          delete overlaySeriesRef.current[key];
        }
        return;
      }

      const series = existing ?? chart.addLineSeries({
        color: style.color,
        lineWidth: style.width ?? 1,
        lineStyle: style.style ?? LineStyle.Solid,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
        title: style.title,
      });
      series.setData(data[key]);
      overlaySeriesRef.current[key] = series;
    });
  }, [analysis, overlays, chartEpoch]);

  // --- analysis levels and the proposed trade -------------------------------
  useEffect(() => {
    const series = candleSeriesRef.current;
    if (!series) return;

    priceLinesRef.current.forEach((line) => {
      try {
        series.removePriceLine(line);
      } catch {
        /* series was recreated; nothing to remove */
      }
    });
    priceLinesRef.current = [];

    if (!analysis || !showZones) return;

    const draw = (price, color, title, style = LineStyle.Dashed) => {
      if (price === null || price === undefined) return;
      priceLinesRef.current.push(
        series.createPriceLine({
          price,
          color,
          lineWidth: 1,
          lineStyle: style,
          axisLabelVisible: true,
          title,
        }),
      );
    };

    const levels = analysis.key_levels ?? {};
    levels.support?.slice(0, 3).forEach((l) => draw(l.price, "#26a69a", "S"));
    levels.resistance?.slice(0, 3).forEach((l) => draw(l.price, "#ef5350", "R"));
    levels.period?.slice(0, 4).forEach((l) => draw(l.price, "#5a6377", l.label.replace(/[a-z ]/g, "")));

    const setup = analysis.trade_setup;
    if (setup?.valid) {
      draw(setup.entry, "#4a9eff", "ENTRY", LineStyle.Solid);
      draw(setup.stop_loss, "#ef5350", "STOP", LineStyle.Solid);
      setup.take_profits?.forEach((tp, i) => draw(tp, "#26a69a", `TP${i + 1}`, LineStyle.Dotted));
    }
  }, [analysis, showZones, chartEpoch]);

  const submitSymbol = (event) => {
    event.preventDefault();
    const next = symbolInput.trim().toUpperCase();
    if (next && next !== symbol) onSymbolChange(next);
  };

  const latest = lastCandle ?? snapshot?.candles?.[snapshot.candles.length - 1];
  const previous = snapshot?.candles?.[snapshot.candles.length - 2];
  const change = latest && previous ? latest.close - previous.close : null;
  const changePercent = change && previous ? (change / previous.close) * 100 : null;
  const bar = hovered ?? latest;
  const up = bar && bar.close >= bar.open;

  return (
    <div className="flex flex-col h-full bg-term-surface">
      {/* Header: symbol, timeframe, live OHLC */}
      <div className="flex items-center gap-3 px-3 py-2 border-b border-term-border flex-wrap">
        <form onSubmit={submitSymbol} className="flex items-center gap-1">
          <input
            value={symbolInput}
            onChange={(e) => setSymbolInput(e.target.value)}
            className="w-32 bg-term-raised border border-term-border rounded px-2 py-1 text-sm font-mono
                       font-semibold uppercase text-term-text focus:outline-none focus:border-brand-accent"
            aria-label="Symbol"
          />
        </form>

        <div className="flex gap-0.5">
          {TIMEFRAMES.map((tf) => (
            <button
              key={tf}
              onClick={() => onTimeframeChange(tf)}
              className={`px-2 py-1 text-xs font-mono rounded transition-colors ${
                tf === timeframe
                  ? "bg-brand-accent text-white"
                  : "text-term-muted hover:text-term-text hover:bg-term-raised"
              }`}
            >
              {tf}
            </button>
          ))}
        </div>

        {bar && (
          <div className="flex items-center gap-3 text-xs font-mono">
            <span className="text-term-dim">
              O <span className="text-term-text">{formatPrice(bar.open)}</span>
            </span>
            <span className="text-term-dim">
              H <span className="text-term-text">{formatPrice(bar.high)}</span>
            </span>
            <span className="text-term-dim">
              L <span className="text-term-text">{formatPrice(bar.low)}</span>
            </span>
            <span className="text-term-dim">
              C <span className={up ? "text-term-up" : "text-term-down"}>{formatPrice(bar.close)}</span>
            </span>
            {bar.volume !== undefined && (
              <span className="text-term-dim">
                V <span className="text-term-text">{formatPrice(bar.volume)}</span>
              </span>
            )}
            {changePercent !== null && !hovered && (
              <span className={changePercent >= 0 ? "text-term-up" : "text-term-down"}>
                {formatPercent(changePercent)}
              </span>
            )}
          </div>
        )}

        <div className="ml-auto flex items-center gap-2">
          {snapshot?.provider && (
            <span className="text-[10px] uppercase tracking-wide text-term-dim">
              {snapshot.provider}
            </span>
          )}
          <StreamBadge status={stream.status} live={snapshot?.live} />
        </div>
      </div>

      {/* Overlay toggles */}
      <div className="flex items-center gap-1 px-3 py-1.5 border-b border-term-border overflow-x-auto">
        {Object.entries(OVERLAY_STYLES).map(([key, style]) => (
          <button
            key={key}
            onClick={() => onToggleOverlay(key)}
            className={`px-2 py-0.5 text-[10px] font-mono rounded border transition-colors whitespace-nowrap ${
              overlays[key]
                ? "border-transparent text-term-bg"
                : "border-term-border text-term-dim hover:text-term-muted"
            }`}
            style={overlays[key] ? { backgroundColor: style.color } : undefined}
          >
            {style.title}
          </button>
        ))}
        <button
          onClick={onToggleZones}
          className={`px-2 py-0.5 text-[10px] font-mono rounded border transition-colors whitespace-nowrap ${
            showZones
              ? "bg-brand-accent border-transparent text-white"
              : "border-term-border text-term-dim hover:text-term-muted"
          }`}
        >
          LEVELS
        </button>
        {!analysis && (
          <span className="text-[10px] text-term-dim ml-2 whitespace-nowrap">
            Run an analysis (A) to populate indicators and levels
          </span>
        )}
      </div>

      {/* Chart surface */}
      <div className="relative flex-1 min-h-0">
        <div ref={containerRef} className="absolute inset-0" />
        {loading && (
          <div className="absolute inset-0 flex items-center justify-center text-term-muted text-sm">
            Loading {symbol} {timeframe}…
          </div>
        )}
        {error && (
          <div className="absolute inset-0 flex items-center justify-center p-6">
            <div className="max-w-md text-center">
              <p className="text-term-down text-sm font-medium mb-1">Could not load {symbol}</p>
              <p className="text-term-muted text-xs leading-relaxed">{error}</p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * The socket being connected does not mean this symbol is streaming —
 * `live === false` means the backend is polling a REST endpoint every ~15s
 * and relaying it over the same connection (no keyed provider configured for
 * this market). Showing "LIVE" in that case would misrepresent a delayed
 * feed as tick-by-tick, so it gets its own distinct badge instead.
 */
function StreamBadge({ status, live }) {
  if (status === "live" && live === false) {
    return (
      <span
        className="flex items-center gap-1.5 text-[10px] font-mono tracking-wide text-term-warn"
        title="Refreshing every ~15s, not a real-time push. Set POLYGON_API_KEY, TWELVEDATA_API_KEY or FINNHUB_API_KEY to stream this market live."
      >
        <span className="h-1.5 w-1.5 rounded-full bg-term-warn" />
        DELAYED
      </span>
    );
  }

  const config = {
    live: { color: "bg-term-up", label: "LIVE", pulse: true },
    connecting: { color: "bg-term-warn", label: "CONNECTING", pulse: true },
    reconnecting: { color: "bg-term-warn", label: "RECONNECTING", pulse: true },
    disconnected: { color: "bg-term-down", label: "OFFLINE", pulse: false },
  }[status] ?? { color: "bg-term-dim", label: status.toUpperCase(), pulse: false };

  return (
    <span className="flex items-center gap-1.5 text-[10px] font-mono tracking-wide text-term-muted">
      <span className={`h-1.5 w-1.5 rounded-full ${config.color} ${config.pulse ? "animate-pulse" : ""}`} />
      {config.label}
    </span>
  );
}
