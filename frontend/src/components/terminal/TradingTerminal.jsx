import { useCallback, useEffect, useState } from "react";
import ChartPanel from "./ChartPanel.jsx";
import AnalysisPanel from "./AnalysisPanel.jsx";
import WatchlistPanel from "./WatchlistPanel.jsx";
import StrategyPanel from "./StrategyPanel.jsx";
import ResearchPanel from "./ResearchPanel.jsx";
import ExecutionPanel from "./ExecutionPanel.jsx";
import ScannerPanel from "./ScannerPanel.jsx";
import CalendarPanel from "./CalendarPanel.jsx";
import OptionsPanel from "./OptionsPanel.jsx";
import { trading } from "../../lib/tradingApi.js";
import { useMarketStream } from "../../lib/useMarketStream.js";

/**
 * The terminal workspace.
 *
 * Layout is watchlist / chart / side panel, with the side panel switching
 * between analysis, strategy and research. State for the active symbol and
 * timeframe lives here so every panel stays in sync — changing the symbol on
 * the chart re-points the analysis, alerts and strategy backtest at the same
 * instrument without any further clicks.
 */

const SHORTCUTS = [
  ["A", "Run analysis"],
  ["N", "Analysis + narrative"],
  ["S", "Strategy panel"],
  ["R", "Research panel"],
  ["I", "Analysis panel"],
  ["E", "Execution panel"],
  ["C", "Scanner panel"],
  ["L", "Calendar panel"],
  ["O", "Options panel"],
  ["1-8", "Switch timeframe"],
  ["/", "Focus symbol"],
  ["?", "Toggle this help"],
];

const TIMEFRAME_KEYS = ["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"];

export default function TradingTerminal() {
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [timeframe, setTimeframe] = useState("1h");
  const [sidePanel, setSidePanel] = useState("analysis");

  const [analysis, setAnalysis] = useState(null);
  const [narrative, setNarrative] = useState(null);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [analysisError, setAnalysisError] = useState(null);

  const [overlays, setOverlays] = useState({
    ema20: true, ema50: true, ema200: true,
    vwap: false, bb_upper: false, bb_lower: false, supertrend: false,
  });
  const [showZones, setShowZones] = useState(true);
  const [showHelp, setShowHelp] = useState(false);
  const [toast, setToast] = useState(null);

  const stream = useMarketStream();

  // Clear the previous instrument's analysis the moment the market changes —
  // leaving stale levels on a new chart is actively misleading.
  useEffect(() => {
    setAnalysis(null);
    setNarrative(null);
    setAnalysisError(null);
  }, [symbol, timeframe]);

  const runAnalysis = useCallback(async (withNarrative = false) => {
    setAnalysisLoading(true);
    setAnalysisError(null);
    setNarrative(null);
    setSidePanel("analysis");
    try {
      const body = { symbol, timeframe, bars: 800, include_mtf: true, include_chart_data: true };
      const result = withNarrative
        ? await trading.narrate({ ...body, audience: "intermediate" })
        : await trading.analyze(body);
      setAnalysis(result);
      if (result.narrative) setNarrative(result.narrative);
    } catch (e) {
      setAnalysisError(e.message);
    } finally {
      setAnalysisLoading(false);
    }
  }, [symbol, timeframe]);

  const sendFeedback = useCallback(async (rating) => {
    if (!analysis?.analysis_id) return;
    try {
      await trading.feedback({ analysis_id: analysis.analysis_id, rating });
      setToast(`Feedback recorded: ${rating}`);
      setTimeout(() => setToast(null), 2500);
    } catch (e) {
      setToast(e.message);
      setTimeout(() => setToast(null), 4000);
    }
  }, [analysis]);

  // --- keyboard shortcuts ---------------------------------------------------
  useEffect(() => {
    const handler = (event) => {
      // Never hijack keys while the user is typing into a field.
      const tag = event.target?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || event.metaKey || event.ctrlKey) {
        return;
      }

      const key = event.key.toLowerCase();

      if (key === "a") { event.preventDefault(); runAnalysis(false); }
      else if (key === "n") { event.preventDefault(); runAnalysis(true); }
      else if (key === "s") { event.preventDefault(); setSidePanel("strategy"); }
      else if (key === "r") { event.preventDefault(); setSidePanel("research"); }
      else if (key === "i") { event.preventDefault(); setSidePanel("analysis"); }
      else if (key === "e") { event.preventDefault(); setSidePanel("execution"); }
      else if (key === "c") { event.preventDefault(); setSidePanel("scanner"); }
      else if (key === "l") { event.preventDefault(); setSidePanel("calendar"); }
      else if (key === "o") { event.preventDefault(); setSidePanel("options"); }
      else if (key === "?") { event.preventDefault(); setShowHelp((v) => !v); }
      else if (key === "escape") { setShowHelp(false); }
      else if (key === "/") {
        event.preventDefault();
        document.querySelector('input[aria-label="Symbol"]')?.focus();
      } else if (/^[1-8]$/.test(key)) {
        event.preventDefault();
        setTimeframe(TIMEFRAME_KEYS[Number(key) - 1]);
      }
    };

    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [runAnalysis]);

  const toggleOverlay = (key) => setOverlays((prev) => ({ ...prev, [key]: !prev[key] }));

  return (
    <div className="flex h-full bg-term-bg text-term-text">
      <div className="w-48 shrink-0 hidden md:block">
        <WatchlistPanel symbol={symbol} onSelectSymbol={setSymbol} />
      </div>

      <div className="flex-1 min-w-0 flex flex-col">
        <ChartPanel
          symbol={symbol}
          timeframe={timeframe}
          onTimeframeChange={setTimeframe}
          onSymbolChange={setSymbol}
          stream={stream}
          analysis={analysis}
          overlays={overlays}
          onToggleOverlay={toggleOverlay}
          showZones={showZones}
          onToggleZones={() => setShowZones((v) => !v)}
        />
      </div>

      <div className="w-[26rem] shrink-0 hidden lg:flex flex-col border-l border-term-border">
        {/* Seven tabs do not fit across 26rem, and `flex-1` responded by
            clipping the last two — Execute was unreachable by mouse entirely,
            keyboard-only by accident rather than by design. The strip scrolls
            instead, and the help button is pinned outside it so it never
            scrolls away. */}
        <div className="flex border-b border-term-border bg-term-panel">
          <div className="flex-1 min-w-0 flex overflow-x-auto scrollbar-none">
            {[
              ["analysis", "Analysis"],
              ["strategy", "Strategy"],
              ["research", "Research"],
              ["scanner", "Scanner"],
              ["calendar", "Calendar"],
              ["options", "Options"],
              ["execution", "Execute"],
            ].map(([key, label]) => (
              <button
                key={key}
                onClick={() => setSidePanel(key)}
                className={`shrink-0 whitespace-nowrap px-2.5 py-2 text-[10px] font-semibold
                            uppercase tracking-wide transition-colors ${
                  sidePanel === key
                    ? "text-term-text border-b-2 border-brand-accent"
                    : "text-term-dim hover:text-term-muted"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          <button
            onClick={() => setShowHelp((v) => !v)}
            className="shrink-0 px-3 text-term-dim hover:text-term-text text-xs"
            title="Keyboard shortcuts (?)"
          >
            ?
          </button>
        </div>

        <div className="flex-1 min-h-0">
          {sidePanel === "analysis" && (
            <AnalysisPanel
              analysis={analysis}
              narrative={narrative}
              loading={analysisLoading}
              error={analysisError}
              onRun={() => runAnalysis(false)}
              onFeedback={analysis?.analysis_id ? sendFeedback : null}
            />
          )}
          {sidePanel === "strategy" && <StrategyPanel symbol={symbol} timeframe={timeframe} />}
          {sidePanel === "research" && <ResearchPanel symbol={symbol} timeframe={timeframe} />}
          {sidePanel === "scanner" && <ScannerPanel onSelectSymbol={setSymbol} />}
          {sidePanel === "calendar" && <CalendarPanel />}
          {sidePanel === "options" && <OptionsPanel symbol={symbol} />}
          {sidePanel === "execution" && <ExecutionPanel symbol={symbol} />}
        </div>
      </div>

      {showHelp && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
          onClick={() => setShowHelp(false)}
        >
          <div
            className="bg-term-panel border border-term-border rounded-lg p-5 w-80"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-sm font-semibold mb-3">Keyboard shortcuts</h3>
            <div className="space-y-1.5">
              {SHORTCUTS.map(([key, description]) => (
                <div key={key} className="flex items-center justify-between gap-3">
                  <kbd className="px-1.5 py-0.5 bg-term-raised border border-term-border rounded
                                  text-[10px] font-mono text-term-text min-w-[2.5rem] text-center">
                    {key}
                  </kbd>
                  <span className="text-[11px] text-term-muted flex-1 text-right">{description}</span>
                </div>
              ))}
            </div>
            <button onClick={() => setShowHelp(false)} className="btn-term text-[10px] mt-4 w-full">
              Close
            </button>
          </div>
        </div>
      )}

      {toast && (
        <div className="fixed bottom-4 right-4 z-50 px-3 py-2 rounded bg-term-raised
                        border border-term-border text-[11px] text-term-text shadow-lg">
          {toast}
        </div>
      )}
    </div>
  );
}
