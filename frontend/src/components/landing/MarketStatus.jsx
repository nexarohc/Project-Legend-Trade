import { useEffect, useState } from "react";
import { trading } from "../../lib/tradingApi.js";

/**
 * A live market strip under the headline.
 *
 * The brief asked for a sentiment reading and an index row. Two decisions kept
 * it honest:
 *
 * **The numbers are real or they are absent.** Hardcoding `NIFTY 50 +1.84%`
 * onto a page about a trading product is a made-up quote presented as a live
 * one, whatever a caption says. This calls the same `/market/quotes` endpoint
 * the terminal uses. If the backend is unreachable or a provider has no data,
 * the strip removes itself rather than showing a plausible fiction.
 *
 * **Sentiment is defined, not asserted.** "BULLISH 72%" means nothing unless
 * you can say what produced it, so this is simply the share of the tracked
 * symbols currently up on the day, with the count shown next to it. It is a
 * tiny sample and says so — but it is arithmetic anyone can check, which is the
 * whole argument of the product.
 *
 * Crypto by default: those providers need no API key, so a fresh clone shows
 * live data instead of an empty strip.
 */

const SYMBOLS = ["BINANCE:BTCUSDT", "BINANCE:ETHUSDT", "BINANCE:SOLUSDT"];
const REFRESH_MS = 30000;

/**
 * The provider normalises symbols on the way back — ask for `BINANCE:BTCUSDT`
 * and the quote returns as `BTC-USD`. Keying labels off what was *requested*
 * therefore silently misses, so this derives the ticker from what actually
 * arrived: strip any exchange prefix, then the quote currency.
 */
function shortName(symbol = "") {
  const bare = symbol.split(":").pop();
  return bare.replace(/[-/]?(USDT|USDC|USD)$/i, "") || bare;
}

export default function MarketStatus() {
  const [quotes, setQuotes] = useState(null);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        const data = await trading.quotes(SYMBOLS);
        if (cancelled) return;
        const usable = (data.quotes || data || []).filter(
          (q) => q && !q.error && typeof q.change_percent === "number");
        setQuotes(usable.length ? usable : null);
      } catch {
        if (!cancelled) setQuotes(null);   // backend down: show nothing
      }
    };

    load();
    const timer = setInterval(load, REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  // Nothing rendered until there is something true to render. A skeleton would
  // reserve space on a page that may never fill it.
  if (!quotes) return null;

  const up = quotes.filter((q) => q.change_percent > 0).length;
  const share = Math.round((up / quotes.length) * 100);
  const bullish = share >= 50;

  return (
    <div className="animate-fade-in mx-auto mt-10 flex max-w-2xl flex-wrap items-center
                    justify-center gap-x-7 gap-y-3 rounded-2xl border border-white/10
                    bg-white/[0.03] px-6 py-3.5 backdrop-blur-md">
      <div className="flex items-center gap-2.5">
        <span className="relative flex h-2 w-2">
          <span className={`absolute inline-flex h-full w-full animate-ping rounded-full opacity-60
                            ${bullish ? "bg-emerald-400" : "bg-red-400"}`} />
          <span className={`relative inline-flex h-2 w-2 rounded-full
                            ${bullish ? "bg-emerald-400" : "bg-red-400"}`} />
        </span>
        <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-slate-500">
          Sentiment
        </span>
        <span className={`font-mono text-[13px] font-semibold tabular-nums
                          ${bullish ? "text-emerald-400" : "text-red-400"}`}>
          {bullish ? "BULLISH" : "BEARISH"} {bullish ? share : 100 - share}%
        </span>
        <span className="font-mono text-[10px] text-slate-600">
          {up}/{quotes.length} up
        </span>
      </div>

      <div className="hidden h-4 w-px bg-white/10 sm:block" />

      {quotes.map((q) => {
        const rising = q.change_percent >= 0;
        return (
          <div key={q.symbol} className="flex items-baseline gap-2">
            <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-slate-500">
              {shortName(q.symbol)}
            </span>
            <span className={`font-mono text-[13px] font-semibold tabular-nums
                              ${rising ? "text-emerald-400" : "text-red-400"}`}>
              {rising ? "+" : ""}{q.change_percent.toFixed(2)}%
            </span>
          </div>
        );
      })}
    </div>
  );
}
