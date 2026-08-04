import { useEffect, useState } from "react";
import { trading } from "../../lib/tradingApi.js";

/**
 * Browse the full market catalog by category and exchange, then search or
 * resolve a specific symbol within it — "US Stocks → NASDAQ → LASR" as an
 * explicit drill-down, rather than only a flat search box.
 *
 * There is no bundled ticker list backing this: every market/exchange/example
 * here comes from `trading.markets()` (the same catalog `/market/markets`
 * serves), and search hits the provider's live index. The category and
 * exchange picked here are a filter on top of that live search, not a
 * separate, narrower universe — a company that IPO'd this morning is still
 * findable, in whichever category it belongs to.
 */
export default function MarketBrowser({ onSelectSymbol }) {
  const [catalog, setCatalog] = useState(null);
  const [error, setError] = useState(null);
  const [marketKey, setMarketKey] = useState(null);
  const [exchange, setExchange] = useState(null);

  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [searching, setSearching] = useState(false);
  const [resolved, setResolved] = useState(null);
  const [resolving, setResolving] = useState(false);
  const [resolveError, setResolveError] = useState(null);

  useEffect(() => {
    trading.markets().then(setCatalog).catch((e) => setError(e.message));
  }, []);

  const market = catalog?.markets?.find((m) => m.key === marketKey) ?? null;

  // Live search, scoped to the chosen market's asset class where possible —
  // a best-effort filter, not a hard boundary, so a mismatched result set
  // never hides an instrument that's actually there.
  useEffect(() => {
    const q = query.trim();
    setResolved(null);
    setResolveError(null);
    if (q.length < 2) {
      setResults([]);
      return undefined;
    }
    const timer = setTimeout(async () => {
      setSearching(true);
      try {
        const { results: raw } = await trading.search(q);
        const scoped = market ? raw.filter((r) => r.asset_class === market.asset_class) : raw;
        setResults((scoped.length ? scoped : raw).slice(0, 10));
      } catch {
        setResults([]);
      } finally {
        setSearching(false);
      }
    }, 300);
    return () => clearTimeout(timer);
  }, [query, market]);

  const resolveTyped = async () => {
    const raw = query.trim();
    if (!raw) return;
    // A chosen exchange chip only means something if it actually reaches the
    // resolver: "NASDAQ" selected + "LASR" typed should resolve as
    // NASDAQ:LASR, not as a bare, exchange-less ticker.
    const qualified = exchange && !raw.includes(":") ? `${exchange}:${raw}` : raw;
    setResolving(true);
    setResolveError(null);
    try {
      setResolved(await trading.resolve(qualified));
    } catch (e) {
      setResolved(null);
      setResolveError(e.message);
    } finally {
      setResolving(false);
    }
  };

  const pick = (symbol) => {
    onSelectSymbol(symbol.toUpperCase());
    setQuery("");
    setResults([]);
    setResolved(null);
  };

  if (error) {
    return <p className="p-3 text-[11px] text-term-down leading-relaxed">{error}</p>;
  }
  if (!catalog) {
    return <p className="p-3 text-[11px] text-term-dim leading-relaxed">Loading markets…</p>;
  }

  // --- category picker ------------------------------------------------------
  if (!market) {
    return (
      <div className="overflow-y-auto flex-1">
        <p className="p-2 text-[10px] text-term-dim leading-relaxed border-b border-term-border">
          Every listed market is chartable — no bundled ticker list, symbol lookup queries the
          provider's live index.
        </p>
        {catalog.markets.map((m) => (
          <button
            key={m.key}
            onClick={() => setMarketKey(m.key)}
            className="w-full text-left px-2 py-2 border-b border-term-border/50 hover:bg-term-raised/50
                       transition-colors"
          >
            <div className="text-[11px] font-semibold text-term-text">{m.name}</div>
            <div className="text-[9px] text-term-dim truncate mt-0.5">
              {m.examples.slice(0, 5).join(", ")}
            </div>
          </button>
        ))}
      </div>
    );
  }

  // --- exchange + search within a category -----------------------------------
  return (
    <div className="flex flex-col flex-1 min-h-0">
      <div className="p-2 border-b border-term-border">
        <button
          onClick={() => { setMarketKey(null); setExchange(null); setQuery(""); setResults([]); setResolved(null); }}
          className="text-[10px] text-term-dim hover:text-term-text mb-2"
        >
          ‹ All markets
        </button>
        <div className="text-[11px] font-semibold text-term-text">{market.name}</div>

        {market.exchanges.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-1.5">
            <button
              onClick={() => setExchange(null)}
              className={`px-1.5 py-0.5 text-[9px] rounded ${
                !exchange ? "bg-brand-accent text-white" : "border border-term-border text-term-dim"
              }`}
            >
              All
            </button>
            {market.exchanges.map((ex) => (
              <button
                key={ex}
                onClick={() => setExchange(ex)}
                className={`px-1.5 py-0.5 text-[9px] rounded ${
                  exchange === ex ? "bg-brand-accent text-white" : "border border-term-border text-term-dim"
                }`}
              >
                {ex}
              </button>
            ))}
          </div>
        )}

        <p className="text-[9px] text-term-dim leading-relaxed mt-1.5">{market.notes}</p>
      </div>

      <div className="p-2 border-b border-term-border relative">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (results.length) pick(results[0].symbol);
            else resolveTyped();
          }}
        >
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={`Search ${market.name}${exchange ? ` · ${exchange}` : ""}, e.g. ${market.examples[0]}`}
            className="w-full bg-term-raised border border-term-border rounded px-2 py-1 text-xs
                       font-mono text-term-text placeholder:text-term-dim
                       focus:outline-none focus:border-brand-accent"
          />
        </form>
        {searching && <p className="text-[9px] text-term-dim mt-1">searching…</p>}

        {results.length > 0 && (
          <div className="mt-1 border border-term-border rounded max-h-56 overflow-y-auto bg-term-raised">
            {results.map((r) => (
              <button
                key={`${r.provider}:${r.symbol}`}
                onClick={() => pick(r.symbol)}
                className="w-full text-left px-2 py-1.5 hover:bg-term-panel transition-colors
                           border-b border-term-border/40 last:border-0"
              >
                <div className="flex items-baseline justify-between gap-2">
                  <span className="text-[11px] font-mono font-semibold text-term-text">{r.symbol}</span>
                  <span className="text-[9px] uppercase text-term-dim shrink-0">{r.asset_class}</span>
                </div>
                <p className="text-[9px] text-term-muted truncate">{r.name}</p>
              </button>
            ))}
          </div>
        )}

        {!results.length && query.trim().length >= 2 && !searching && (
          <button
            onClick={resolveTyped}
            disabled={resolving}
            className="btn-term text-[10px] mt-1.5 w-full"
          >
            {resolving ? "Resolving…" : `Resolve "${query.trim()}" directly`}
          </button>
        )}

        {resolveError && (
          <p className="text-[10px] text-term-down leading-relaxed mt-1.5">{resolveError}</p>
        )}

        {resolved && (
          <div className="mt-1.5 p-2 rounded border border-term-border bg-term-raised">
            <div className="flex items-baseline justify-between gap-2">
              <span className="text-[11px] font-mono font-semibold text-term-text">
                {resolved.exchange ? `${resolved.exchange}:${resolved.ticker}` : resolved.ticker}
              </span>
              <span className="text-[9px] uppercase text-term-dim">{resolved.asset_class}</span>
            </div>
            <p className="text-[9px] text-term-muted leading-relaxed mt-0.5">{resolved.note}</p>
            <button
              onClick={() => pick(resolved.ticker)}
              className="btn-term-primary text-[10px] mt-1.5 w-full"
            >
              Chart {resolved.ticker}
            </button>
          </div>
        )}
      </div>

      <div className="overflow-y-auto flex-1 p-2">
        <p className="text-[9px] uppercase tracking-wide text-term-dim mb-1">Examples</p>
        <div className="flex flex-wrap gap-1">
          {market.examples.map((ex) => (
            <button
              key={ex}
              onClick={() => pick(ex)}
              className="px-1.5 py-0.5 text-[10px] font-mono rounded border border-term-border
                         text-term-muted hover:text-term-text hover:border-brand-accent transition-colors"
            >
              {ex}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
