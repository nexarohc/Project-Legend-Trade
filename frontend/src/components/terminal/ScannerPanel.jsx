import { useEffect, useState } from "react";
import { formatPrice, scanner, trading, TIMEFRAMES } from "../../lib/tradingApi.js";

/**
 * Market scanner: filter a symbol list by measurable conditions.
 *
 * There is no bundled ticker list here, matching the rest of the platform —
 * a scan runs over symbols you supply (your watchlist by default, or any
 * list you type), not "every NASDAQ stock". No keyless provider exposes a
 * screener API, so pretending otherwise would mean silently maintaining a
 * static universe, which is exactly what this app avoids everywhere else.
 */
const STRING_FIELDS = new Set(["trend", "volatility"]);

export default function ScannerPanel({ onSelectSymbol }) {
  const [availableFields, setAvailableFields] = useState([]);
  const [availableOps, setAvailableOps] = useState([]);
  const [symbolsText, setSymbolsText] = useState("");
  const [timeframe, setTimeframe] = useState("1d");
  const [filters, setFilters] = useState([{ field: "trend", op: "eq", value: "bullish", value2: "" }]);
  const [report, setReport] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    scanner
      .fields()
      .then((data) => {
        setAvailableFields(data.fields);
        setAvailableOps(data.operators);
      })
      .catch(() => {});
  }, []);

  const loadFromWatchlist = async () => {
    try {
      const data = await trading.watchlist(false);
      setSymbolsText(data.items.map((i) => i.symbol).join(", "));
    } catch (e) {
      setError(e.message);
    }
  };

  const addFilter = () => {
    setFilters((prev) => [...prev, { field: availableFields[0] ?? "price", op: "gt", value: "", value2: "" }]);
  };

  const updateFilter = (index, patch) => {
    setFilters((prev) => prev.map((f, i) => (i === index ? { ...f, ...patch } : f)));
  };

  const removeFilter = (index) => {
    setFilters((prev) => prev.filter((_, i) => i !== index));
  };

  const run = async (event) => {
    event.preventDefault();
    const symbols = symbolsText
      .split(/[,\s]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    if (!symbols.length) {
      setError("Enter at least one symbol, or load your watchlist.");
      return;
    }

    setBusy(true);
    setError(null);
    setReport(null);
    try {
      const body = {
        symbols,
        timeframe,
        filters: filters
          .filter((f) => f.value !== "")
          .map((f) => ({
            field: f.field,
            op: f.op,
            value: STRING_FIELDS.has(f.field) ? f.value : Number(f.value),
            value2: f.op === "between" && f.value2 !== "" ? Number(f.value2) : null,
          })),
      };
      setReport(await scanner.run(body));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col h-full bg-term-panel">
      <div className="overflow-y-auto flex-1">
        <Section title="Symbols">
          <textarea
            value={symbolsText}
            onChange={(e) => setSymbolsText(e.target.value)}
            placeholder="AAPL, MSFT, NASDAQ:LASR…"
            rows={2}
            className="w-full bg-term-raised border border-term-border rounded px-2 py-1 text-[11px]
                       font-mono text-term-text placeholder:text-term-dim resize-none
                       focus:outline-none focus:border-brand-accent"
          />
          <div className="flex items-center gap-2 mt-1.5">
            <button type="button" onClick={loadFromWatchlist} className="btn-term text-[10px]">
              Load watchlist
            </button>
            <select
              value={timeframe}
              onChange={(e) => setTimeframe(e.target.value)}
              className="bg-term-raised border border-term-border rounded px-1.5 py-1 text-[10px] text-term-text"
            >
              {(TIMEFRAMES ?? ["1h", "4h", "1d", "1w"]).map((tf) => (
                <option key={tf} value={tf}>{tf}</option>
              ))}
            </select>
          </div>
        </Section>

        <Section title="Filters (all must pass)">
          {filters.map((filter, i) => (
            <div key={i} className="flex items-center gap-1 mb-1.5">
              <select
                value={filter.field}
                onChange={(e) => updateFilter(i, { field: e.target.value })}
                className="bg-term-raised border border-term-border rounded px-1 py-1 text-[10px] text-term-text"
              >
                {availableFields.map((f) => (
                  <option key={f} value={f}>{f.replace(/_/g, " ")}</option>
                ))}
              </select>
              <select
                value={filter.op}
                onChange={(e) => updateFilter(i, { op: e.target.value })}
                className="bg-term-raised border border-term-border rounded px-1 py-1 text-[10px] text-term-text"
              >
                {(STRING_FIELDS.has(filter.field) ? ["eq"] : availableOps).map((op) => (
                  <option key={op} value={op}>{op}</option>
                ))}
              </select>
              <input
                value={filter.value}
                onChange={(e) => updateFilter(i, { value: e.target.value })}
                placeholder="value"
                className="w-16 min-w-0 bg-term-raised border border-term-border rounded px-1.5 py-1
                           text-[10px] font-mono text-term-text placeholder:text-term-dim
                           focus:outline-none focus:border-brand-accent"
              />
              {filter.op === "between" && (
                <input
                  value={filter.value2}
                  onChange={(e) => updateFilter(i, { value2: e.target.value })}
                  placeholder="to"
                  className="w-16 min-w-0 bg-term-raised border border-term-border rounded px-1.5 py-1
                             text-[10px] font-mono text-term-text placeholder:text-term-dim
                             focus:outline-none focus:border-brand-accent"
                />
              )}
              <button
                type="button"
                onClick={() => removeFilter(i)}
                className="text-term-dim hover:text-term-down text-xs px-1"
                aria-label="Remove filter"
              >
                ×
              </button>
            </div>
          ))}
          <button type="button" onClick={addFilter} className="btn-term text-[10px] w-full">
            + Add filter
          </button>
        </Section>

        <div className="px-3 py-2">
          <button onClick={run} disabled={busy} className="btn-term-primary text-[11px] w-full">
            {busy ? "Scanning…" : "Run scan"}
          </button>
        </div>

        {error && (
          <div className="mx-3 mb-2 p-2 rounded border border-term-down/40 bg-term-down/10">
            <p className="text-[11px] text-term-down leading-relaxed">{error}</p>
          </div>
        )}

        {report && (
          <Section title={`Results — ${report.matched} of ${report.scanned} matched`}>
            {report.matches.length === 0 && (
              <p className="text-[10px] text-term-dim leading-relaxed">
                No symbols matched every filter.
              </p>
            )}
            {report.matches.map((match) => (
              <div
                key={match.symbol}
                onClick={() => onSelectSymbol?.(match.symbol)}
                className="mb-1.5 pl-2 border-l border-term-border cursor-pointer hover:bg-term-raised/50"
              >
                <div className="flex items-baseline justify-between gap-2">
                  <span className="text-[11px] font-mono font-semibold text-term-text">
                    {match.symbol}
                  </span>
                  <span className="text-[10px] font-mono text-term-muted">
                    {formatPrice(match.price)}
                  </span>
                </div>
                {match.evidence.map((line, i) => (
                  <p key={i} className="text-[9px] text-term-dim leading-relaxed">{line}</p>
                ))}
              </div>
            ))}
            {Object.keys(report.failed).length > 0 && (
              <>
                <p className="text-[9px] uppercase tracking-wide text-term-dim mt-2 mb-1">
                  Could not scan
                </p>
                {Object.entries(report.failed).map(([symbol, reason]) => (
                  <p key={symbol} className="text-[9px] text-term-down leading-relaxed">
                    {symbol}: {reason}
                  </p>
                ))}
              </>
            )}
          </Section>
        )}
      </div>
    </div>
  );
}

function Section({ title, children }) {
  return (
    <div className="px-3 py-2 border-b border-term-border">
      <p className="text-[10px] uppercase tracking-wide text-term-dim mb-1.5">{title}</p>
      {children}
    </div>
  );
}
