import { useCallback, useEffect, useRef, useState } from "react";
import { formatPercent, formatPrice, trading } from "../../lib/tradingApi.js";
import MarketBrowser from "./MarketBrowser.jsx";

/**
 * Watchlist, alerts and paper positions.
 *
 * Quotes refresh on a timer rather than over the WebSocket: the stream carries
 * candles for charted symbols, and opening a socket subscription per watchlist
 * row would multiply upstream connections for data that only needs to be
 * seconds-fresh.
 */
export default function WatchlistPanel({ symbol, onSelectSymbol }) {
  const [tab, setTab] = useState("watchlist");

  return (
    <div className="flex flex-col h-full bg-term-panel border-r border-term-border">
      {/* Scrolls rather than clips: "Positions" and "Alerts" ran past the
          192px column and became unclickable. */}
      <div className="flex overflow-x-auto scrollbar-none border-b border-term-border">
        {[
          ["watchlist", "Watch"],
          ["markets", "Markets"],
          ["positions", "Positions"],
          ["alerts", "Alerts"],
        ].map(([key, label]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`shrink-0 whitespace-nowrap px-2.5 py-2 text-[10px] font-semibold
                        uppercase tracking-wide transition-colors ${
              tab === key
                ? "text-term-text border-b-2 border-brand-accent"
                : "text-term-dim hover:text-term-muted"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "watchlist" && <Watchlist symbol={symbol} onSelectSymbol={onSelectSymbol} />}
      {tab === "markets" && <MarketBrowser onSelectSymbol={onSelectSymbol} />}
      {tab === "positions" && <Positions />}
      {tab === "alerts" && <Alerts symbol={symbol} />}
    </div>
  );
}

const REFRESH_MS = 10_000;

function Watchlist({ symbol, onSelectSymbol }) {
  const [items, setItems] = useState([]);
  const [input, setInput] = useState("");
  const [results, setResults] = useState([]);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const previousPrices = useRef({});

  // Search the provider's live index as the user types. This is how a small cap
  // gets found by name — nobody remembers that Serve Robotics is SERV.
  useEffect(() => {
    const query = input.trim();
    if (query.length < 2) {
      setResults([]);
      return undefined;
    }
    const timer = setTimeout(async () => {
      setSearching(true);
      try {
        setResults((await trading.search(query)).results.slice(0, 8));
      } catch {
        setResults([]);   // search is a convenience; typing a ticker still works
      } finally {
        setSearching(false);
      }
    }, 300);
    return () => clearTimeout(timer);
  }, [input]);

  const load = useCallback(async () => {
    try {
      const data = await trading.watchlist(true);
      setItems(data.items);
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => clearInterval(id);
  }, [load]);

  const add = async (rawSymbol) => {
    const next = (rawSymbol ?? input).trim().toUpperCase();
    if (!next) return;
    setBusy(true);
    try {
      await trading.addToWatchlist({ symbol: next });
      setInput("");
      setResults([]);
      await load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id) => {
    await trading.removeFromWatchlist(id);
    load();
  };

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          add();
        }}
        className="p-2 border-b border-term-border relative"
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ticker or company name…"
          disabled={busy}
          className="w-full bg-term-raised border border-term-border rounded px-2 py-1 text-xs
                     font-mono text-term-text placeholder:text-term-dim
                     focus:outline-none focus:border-brand-accent"
        />
        {searching && <p className="text-[9px] text-term-dim mt-1">searching…</p>}

        {results.length > 0 && (
          <div className="absolute left-2 right-2 top-full mt-1 z-20 bg-term-raised border
                          border-term-border rounded shadow-lg max-h-64 overflow-y-auto">
            {results.map((r) => (
              <button
                key={`${r.provider}:${r.symbol}`}
                type="button"
                onClick={() => add(r.symbol)}
                className="w-full text-left px-2 py-1.5 hover:bg-term-panel transition-colors
                           border-b border-term-border/40 last:border-0"
              >
                <div className="flex items-baseline justify-between gap-2">
                  <span className="text-[11px] font-mono font-semibold text-term-text">
                    {r.symbol}
                  </span>
                  <span className="text-[9px] uppercase text-term-dim shrink-0">
                    {r.asset_class}
                  </span>
                </div>
                {/* The company name and country are what separate four rows all
                    reading SERV — a US robotics company, a Pakistani footwear
                    maker, a Swedish listing and a Canadian one. Without them the
                    list is four identical buttons. */}
                <p className="text-[9px] text-term-muted truncate">
                  {r.name}
                  {r.country ? <span className="text-term-dim"> · {r.country}</span> : null}
                </p>
              </button>
            ))}
          </div>
        )}
      </form>

      {error && <p className="p-2 text-[10px] text-term-down leading-relaxed">{error}</p>}

      <div className="overflow-y-auto flex-1">
        {items.length === 0 && !error && (
          <p className="p-3 text-[11px] text-term-dim leading-relaxed">
            No symbols yet. Add one above — try BTCUSDT, ETHUSDT, or a stock ticker if you have a
            Polygon, Twelve Data or Finnhub key configured.
          </p>
        )}

        {items.map((item) => {
          const quote = item.quote;
          const previous = previousPrices.current[item.symbol];
          if (quote) previousPrices.current[item.symbol] = quote.price;
          const flash =
            previous && quote && quote.price !== previous
              ? quote.price > previous
                ? "bg-term-up/10"
                : "bg-term-down/10"
              : "";
          const active = item.symbol === symbol;

          return (
            <div
              key={item.id}
              onClick={() => onSelectSymbol(item.symbol)}
              className={`group px-2 py-1.5 border-b border-term-border/50 cursor-pointer
                          transition-colors ${flash} ${
                            active ? "bg-term-raised border-l-2 border-l-brand-accent" : "hover:bg-term-raised/50"
                          }`}
            >
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-xs font-mono font-semibold text-term-text truncate">
                  {item.symbol}
                </span>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    remove(item.id);
                  }}
                  className="opacity-0 group-hover:opacity-100 text-term-dim hover:text-term-down text-xs px-1"
                  aria-label={`Remove ${item.symbol}`}
                >
                  ×
                </button>
              </div>
              {quote ? (
                <div className="flex items-baseline justify-between gap-2">
                  <span className="text-[11px] font-mono text-term-text">
                    {formatPrice(quote.price)}
                  </span>
                  <span
                    className={`text-[10px] font-mono ${
                      (quote.change_percent ?? 0) >= 0 ? "text-term-up" : "text-term-down"
                    }`}
                  >
                    {formatPercent(quote.change_percent)}
                  </span>
                </div>
              ) : (
                <p className="text-[10px] text-term-dim truncate">{item.error ?? "loading…"}</p>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Positions() {
  const [positions, setPositions] = useState([]);
  const [performance, setPerformance] = useState(null);
  const [status, setStatus] = useState("open");
  const [message, setMessage] = useState(null);

  const load = useCallback(async () => {
    try {
      const [p, perf] = await Promise.all([
        trading.positions(status),
        trading.paperPerformance().catch(() => null),
      ]);
      setPositions(p.positions);
      setPerformance(perf);
    } catch (e) {
      setMessage(e.message);
    }
  }, [status]);

  useEffect(() => {
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => clearInterval(id);
  }, [load]);

  const close = async (id) => {
    try {
      const result = await trading.closePosition(id, { reason: "manual" });
      setMessage(`Closed at ${formatPrice(result.exit_price)} — P&L ${formatPrice(result.realized_pnl)}`);
      load();
    } catch (e) {
      setMessage(e.message);
    }
  };

  const sync = async () => {
    try {
      const result = await trading.syncPositions();
      setMessage(
        result.closed.length
          ? `${result.closed.length} position(s) hit stop or target and were closed.`
          : "No positions hit their stop or target.",
      );
      load();
    } catch (e) {
      setMessage(e.message);
    }
  };

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <div className="flex items-center gap-1 p-2 border-b border-term-border">
        {["open", "closed"].map((s) => (
          <button
            key={s}
            onClick={() => setStatus(s)}
            className={`px-2 py-0.5 text-[10px] rounded ${
              status === s ? "bg-brand-accent text-white" : "text-term-dim hover:text-term-muted"
            }`}
          >
            {s}
          </button>
        ))}
        <button onClick={sync} className="ml-auto btn-term text-[10px]">
          Sync
        </button>
      </div>

      {performance?.available && (
        <div className="px-2 py-1.5 border-b border-term-border grid grid-cols-2 gap-x-2 gap-y-0.5">
          <MiniStat label="Trades" value={performance.total_trades} />
          <MiniStat label="Win rate" value={`${performance.win_rate}%`} />
          <MiniStat
            label="Total P&L"
            value={formatPrice(performance.total_pnl)}
            tone={performance.total_pnl >= 0 ? "up" : "down"}
          />
          <MiniStat label="Avg R" value={performance.average_r ?? "—"} />
        </div>
      )}

      {message && <p className="p-2 text-[10px] text-term-info leading-relaxed">{message}</p>}

      <div className="overflow-y-auto flex-1">
        {positions.length === 0 && (
          <p className="p-3 text-[11px] text-term-dim leading-relaxed">
            No {status} paper positions. Open one from a trade setup — paper trading is the step
            that comes before risking real capital.
          </p>
        )}
        {positions.map((p) => (
          <div key={p.id} className="px-2 py-1.5 border-b border-term-border/50">
            <div className="flex items-baseline justify-between gap-2">
              <span className="text-xs font-mono font-semibold text-term-text">{p.symbol}</span>
              <span
                className={`text-[10px] uppercase font-semibold ${
                  p.direction === "long" ? "text-term-up" : "text-term-down"
                }`}
              >
                {p.direction}
              </span>
            </div>
            <div className="grid grid-cols-2 gap-x-2 text-[10px] font-mono text-term-muted">
              <span>in {formatPrice(p.entry_price)}</span>
              <span>stop {formatPrice(p.stop_price)}</span>
              {p.status === "open" ? (
                <>
                  <span>now {formatPrice(p.current_price)}</span>
                  <span className={(p.unrealized_pnl ?? 0) >= 0 ? "text-term-up" : "text-term-down"}>
                    {formatPrice(p.unrealized_pnl)} ({p.unrealized_r ?? "—"}R)
                  </span>
                </>
              ) : (
                <>
                  <span>out {formatPrice(p.exit_price)}</span>
                  <span className={(p.realized_pnl ?? 0) >= 0 ? "text-term-up" : "text-term-down"}>
                    {formatPrice(p.realized_pnl)} ({p.r_multiple ?? "—"}R)
                  </span>
                </>
              )}
            </div>
            {p.status === "open" && (
              <button onClick={() => close(p.id)} className="btn-term text-[10px] mt-1">
                Close
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function Alerts({ symbol }) {
  const [alerts, setAlerts] = useState([]);
  const [price, setPrice] = useState("");
  const [condition, setCondition] = useState("above");
  const [message, setMessage] = useState(null);

  const load = useCallback(async () => {
    try {
      setAlerts((await trading.alerts()).alerts);
    } catch (e) {
      setMessage(e.message);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Alerts are evaluated server-side on demand; poll while the panel is open.
  useEffect(() => {
    const id = setInterval(async () => {
      try {
        const result = await trading.checkAlerts();
        if (result.triggered.length) {
          setMessage(
            result.triggered
              .map((t) => `${t.symbol} ${t.condition} ${formatPrice(t.level)} — hit at ${formatPrice(t.price)}`)
              .join(" · "),
          );
          load();
        }
      } catch {
        /* transient network error; the next tick retries */
      }
    }, 15_000);
    return () => clearInterval(id);
  }, [load]);

  const create = async (event) => {
    event.preventDefault();
    const value = parseFloat(price);
    if (!Number.isFinite(value) || value <= 0) return;
    try {
      await trading.createAlert({ symbol, condition, price: value });
      setPrice("");
      load();
    } catch (e) {
      setMessage(e.message);
    }
  };

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <form onSubmit={create} className="p-2 border-b border-term-border space-y-1">
        <div className="text-[10px] text-term-dim font-mono">{symbol}</div>
        <div className="flex gap-1">
          <select
            value={condition}
            onChange={(e) => setCondition(e.target.value)}
            className="bg-term-raised border border-term-border rounded px-1 py-1 text-[10px] text-term-text"
          >
            <option value="above">above</option>
            <option value="below">below</option>
            <option value="crosses">crosses</option>
          </select>
          <input
            value={price}
            onChange={(e) => setPrice(e.target.value)}
            placeholder="price"
            inputMode="decimal"
            className="flex-1 min-w-0 bg-term-raised border border-term-border rounded px-2 py-1
                       text-[10px] font-mono text-term-text placeholder:text-term-dim
                       focus:outline-none focus:border-brand-accent"
          />
          <button type="submit" className="btn-term text-[10px]">Set</button>
        </div>
      </form>

      {message && <p className="p-2 text-[10px] text-term-warn leading-relaxed">{message}</p>}

      <div className="overflow-y-auto flex-1">
        {alerts.length === 0 && (
          <p className="p-3 text-[11px] text-term-dim leading-relaxed">
            No alerts. Alerts are checked every 15 seconds while this panel is open.
          </p>
        )}
        {alerts.map((a) => (
          <div key={a.id} className="group px-2 py-1.5 border-b border-term-border/50 flex items-center gap-2">
            <div className="flex-1 min-w-0">
              <div className="text-[11px] font-mono text-term-text truncate">
                {a.symbol} {a.condition} {formatPrice(a.price)}
              </div>
              <div className="text-[10px] text-term-dim">
                {a.triggered ? `triggered at ${formatPrice(a.triggered_price)}` : "waiting"}
              </div>
            </div>
            <span
              className={`h-1.5 w-1.5 rounded-full shrink-0 ${
                a.triggered ? "bg-term-warn" : "bg-term-up animate-pulse"
              }`}
            />
            <button
              onClick={async () => {
                await trading.deleteAlert(a.id);
                load();
              }}
              className="opacity-0 group-hover:opacity-100 text-term-dim hover:text-term-down text-xs"
              aria-label="Delete alert"
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

function MiniStat({ label, value, tone }) {
  const color = { up: "text-term-up", down: "text-term-down" }[tone] ?? "text-term-text";
  return (
    <div className="flex justify-between items-baseline gap-1">
      <span className="text-[9px] text-term-dim">{label}</span>
      <span className={`text-[10px] font-mono ${color}`}>{value}</span>
    </div>
  );
}
