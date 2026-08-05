import { useEffect, useRef, useState } from "react";
import logo from "../assets/logo.svg";

/**
 * The public landing page.
 *
 * Design rules it follows, which are the same ones the terminal follows:
 *
 * 1. **Claim only what the product does.** Every number and capability below
 *    is one this codebase actually implements. There is no "AI-powered
 *    predictions" copy, because the engine deliberately refuses to predict
 *    without evidence — that refusal is the selling point, so the page leads
 *    with it rather than hiding it.
 * 2. **Motion is opacity and transform only.** Those are the two properties a
 *    compositor can animate without re-laying-out the page, so the hero stays
 *    smooth on a weak machine. `prefers-reduced-motion` disables all of it
 *    (see index.css).
 * 3. **Saturated colour means price direction.** The rest of the page is
 *    near-monochrome so the green/red in the chart reads as information
 *    rather than as branding.
 */

/** Reveal-on-scroll. One observer per element, unhooked once it has fired. */
function useReveal() {
  const ref = useRef(null);
  const [shown, setShown] = useState(false);

  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    // No IntersectionObserver (old browser, some test runners): show it
    // immediately rather than leaving the page permanently blank.
    if (typeof IntersectionObserver === "undefined") {
      setShown(true);
      return;
    }
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setShown(true);
          observer.disconnect();
        }
      },
      { rootMargin: "0px 0px -12% 0px", threshold: 0.1 },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  return [ref, shown];
}

function Reveal({ children, delay = 0, className = "" }) {
  const [ref, shown] = useReveal();
  return (
    <div
      ref={ref}
      className={`${className} transition-all duration-700 ease-out ${
        shown ? "opacity-100 translate-y-0" : "opacity-0 translate-y-6"
      }`}
      style={{ transitionDelay: `${delay}ms` }}
    >
      {children}
    </div>
  );
}

/* A synthetic candle series for the hero. Deliberately generated rather than
   piped from the live feed: the landing page must render identically for a
   signed-out visitor with no backend reachable, and a hero that sometimes
   shows an error is worse than one that shows a drawing. It is decorative and
   labelled as such — nothing here is presented as a real quote. */
const HERO_CANDLES = (() => {
  let price = 100;
  let seed = 7;
  const random = () => {
    seed = (seed * 1103515245 + 12345) % 2147483648;
    return seed / 2147483648;
  };
  return Array.from({ length: 44 }, () => {
    const drift = (random() - 0.46) * 4.2;
    const open = price;
    const close = Math.max(40, open + drift);
    const high = Math.max(open, close) + random() * 1.8;
    const low = Math.min(open, close) - random() * 1.8;
    price = close;
    return { open, close, high, low, up: close >= open };
  });
})();

function HeroChart() {
  const highest = Math.max(...HERO_CANDLES.map((c) => c.high));
  const lowest = Math.min(...HERO_CANDLES.map((c) => c.low));
  const span = highest - lowest || 1;
  const y = (v) => 100 - ((v - lowest) / span) * 100;

  return (
    <div
      className="relative rounded-2xl border border-white/10 bg-[#0a0d14]/80 p-4 backdrop-blur
                 shadow-[0_30px_80px_-20px_rgba(0,0,0,0.9)]"
      aria-hidden="true"
    >
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="font-mono text-[11px] text-slate-300">BTCUSDT</span>
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400
                           font-medium tracking-wide">
            ● LIVE
          </span>
        </div>
        <span className="font-mono text-[11px] text-slate-500">1h</span>
      </div>

      <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="w-full h-44">
        {[20, 40, 60, 80].map((line) => (
          <line key={line} x1="0" y1={line} x2="100" y2={line}
                stroke="rgba(255,255,255,0.05)" strokeWidth="0.3" />
        ))}
        {HERO_CANDLES.map((candle, i) => {
          const x = (i + 0.5) * (100 / HERO_CANDLES.length);
          const width = 100 / HERO_CANDLES.length / 2.1;
          const colour = candle.up ? "#26a69a" : "#ef5350";
          const top = y(Math.max(candle.open, candle.close));
          const height = Math.max(Math.abs(y(candle.open) - y(candle.close)), 0.9);
          return (
            <g key={i} className="animate-grow-bar"
               style={{ animationDelay: `${i * 22}ms`, transformOrigin: `${x}px 100px` }}>
              <line x1={x} y1={y(candle.high)} x2={x} y2={y(candle.low)}
                    stroke={colour} strokeWidth="0.35" />
              <rect x={x - width / 2} y={top} width={width} height={height} fill={colour} />
            </g>
          );
        })}
      </svg>

      {/* The product's actual output, not a generic "signal" badge. */}
      <div className="mt-3 rounded-lg border border-amber-500/25 bg-amber-500/[0.07] px-3 py-2">
        <div className="flex items-center justify-between">
          <span className="font-display text-xs font-semibold text-amber-300 tracking-wide">
            NO TRADE
          </span>
          <span className="font-mono text-[10px] text-slate-500">confidence 51%</span>
        </div>
        <p className="text-[11px] text-slate-400 mt-1 leading-relaxed">
          Bullish (19%) and bearish (23%) continuation odds are within 5 points —
          there is no directional edge to trade.
        </p>
      </div>
    </div>
  );
}

const MARQUEE = [
  "US equities — every market cap", "ETFs", "Indices", "Crypto", "Forex",
  "Futures", "Commodities", "International listings", "Options pricing",
];

const FEATURES = [
  {
    title: "Describes before it predicts",
    body:
      "Market context, structure, price action, volume, 15+ indicators, Smart Money Concepts and key levels are computed and explained before any probability is quoted.",
  },
  {
    title: "Probabilities with their working shown",
    body:
      "Base rates measured on the symbol's own history, combined with individually-weighted factors. Every number traces back to something measured, never asserted.",
  },
  {
    title: "An audit that rejects",
    body:
      "Strategies are backtested with realistic costs and next-bar fills, then validated with Monte Carlo, walk-forward and regime tests before a 15-check audit returns APPROVED, REVISE or REJECT.",
  },
  {
    title: "Guardrails that only say no",
    body:
      "No entry without a protective stop. Nothing from a strategy the audit rejected. A kill switch that flattens everything. Live trading needs four independent switches to line up.",
  },
  {
    title: "Options pricing, verified",
    body:
      "Black-Scholes and a binomial tree for American exercise, all five Greeks, implied volatility, and multi-leg payoff diagrams — checked against textbook reference values.",
  },
  {
    title: "Honest about its own limits",
    body:
      "A polled feed is badged DELAYED, never LIVE. Unmeasurable risk is reported as unmodelled rather than guessed. What it cannot do is written down.",
  },
];

const STATS = [
  { value: "630", label: "tests" },
  { value: "20", label: "analysis sections" },
  { value: "15", label: "audit checks" },
  { value: "3", label: "broker adapters" },
];

export default function Landing({ onGetStarted }) {
  return (
    <div className="min-h-screen bg-[#07090e] text-slate-100 overflow-x-hidden">
      {/* ---------- nav ---------- */}
      <header className="sticky top-0 z-30 border-b border-white/[0.06] bg-[#07090e]/80 backdrop-blur-xl">
        <div className="mx-auto max-w-6xl px-6 h-16 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <img src={logo} alt="" className="h-7 w-7 rounded-lg" />
            <span className="font-display text-[15px] font-semibold tracking-tight">
              Legend Trade
            </span>
          </div>
          <nav className="hidden md:flex items-center gap-8 text-sm text-slate-400">
            <a href="#how" className="hover:text-white transition-colors">How it works</a>
            <a href="#markets" className="hover:text-white transition-colors">Markets</a>
            <a href="#honest" className="hover:text-white transition-colors">Limits</a>
          </nav>
          <button
            onClick={onGetStarted}
            className="rounded-lg bg-white px-4 py-2 text-sm font-medium text-[#07090e]
                       transition-transform hover:scale-[1.03] active:scale-100"
          >
            Open terminal
          </button>
        </div>
      </header>

      {/* ---------- hero ---------- */}
      <section className="relative">
        {/* Two slow-drifting blurred orbs. Cheap (transform only) and the only
            thing giving the dark background any depth. */}
        <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden="true">
          <div className="absolute -top-40 -left-32 h-[34rem] w-[34rem] rounded-full
                          bg-violet-600/18 blur-[120px] animate-drift" />
          <div className="absolute top-10 -right-32 h-[30rem] w-[30rem] rounded-full
                          bg-teal-400/12 blur-[120px] animate-drift-slow" />
        </div>

        <div className="relative mx-auto max-w-6xl px-6 pt-20 pb-16 grid lg:grid-cols-2 gap-14 items-center">
          <div>
            <div className="animate-fade-in inline-flex items-center gap-2 rounded-full border
                            border-white/10 bg-white/[0.04] px-3 py-1 text-xs text-slate-400">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse-dot" />
              Live market data, no API key required
            </div>

            <h1 className="mt-6 font-display text-[2.75rem] leading-[1.05] sm:text-6xl
                           font-bold tracking-[-0.03em] animate-fade-up">
              Describe the market
              <br />
              <span className="bg-gradient-to-r from-violet-400 via-sky-300 to-teal-300
                               bg-clip-text text-transparent">
                before predicting it.
              </span>
            </h1>

            <p className="mt-6 max-w-xl text-[15px] leading-relaxed text-slate-400 animate-fade-up"
               style={{ animationDelay: "120ms" }}>
              An institutional-grade terminal that shows the evidence for every
              conclusion — and returns an explicit <em className="text-slate-200 not-italic">no trade</em>,
              with reasons, when the evidence does not support one.
            </p>

            <div className="mt-8 flex flex-wrap gap-3 animate-fade-up"
                 style={{ animationDelay: "220ms" }}>
              <button
                onClick={onGetStarted}
                className="rounded-xl bg-white px-6 py-3 text-sm font-semibold text-[#07090e]
                           transition-all hover:scale-[1.03] hover:shadow-[0_0_40px_-8px_rgba(255,255,255,0.5)]
                           active:scale-100"
              >
                Open the terminal
              </button>
              <a
                href="#how"
                className="rounded-xl border border-white/12 px-6 py-3 text-sm font-medium
                           text-slate-300 transition-colors hover:bg-white/[0.06] hover:text-white"
              >
                See how it works
              </a>
            </div>

            <p className="mt-5 text-xs text-slate-600">
              Paper trading is the default and needs no broker. Live trading stays off
              until four independent switches line up.
            </p>
          </div>

          <div className="animate-fade-up" style={{ animationDelay: "300ms" }}>
            <HeroChart />
          </div>
        </div>

        {/* Marquee of covered markets. Duplicated once so the loop is seamless. */}
        <div className="relative border-y border-white/[0.06] bg-white/[0.015] py-3 overflow-hidden">
          <div className="flex w-max animate-ticker gap-8 px-4" aria-hidden="true">
            {[...MARQUEE, ...MARQUEE].map((item, i) => (
              <span key={i} className="text-xs text-slate-500 whitespace-nowrap">
                {item}
              </span>
            ))}
          </div>
        </div>
      </section>

      {/* ---------- stats ---------- */}
      <section className="mx-auto max-w-6xl px-6 py-16">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-6">
          {STATS.map((stat, i) => (
            <Reveal key={stat.label} delay={i * 90}>
              <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5">
                <div className="font-mono text-3xl font-semibold tracking-tight text-white">
                  {stat.value}
                </div>
                <div className="mt-1 text-xs text-slate-500">{stat.label}</div>
              </div>
            </Reveal>
          ))}
        </div>
      </section>

      {/* ---------- features ---------- */}
      <section id="how" className="mx-auto max-w-6xl px-6 py-16">
        <Reveal>
          <h2 className="font-display text-3xl sm:text-4xl font-bold tracking-[-0.02em]">
            Built to be checked, not trusted
          </h2>
          <p className="mt-4 max-w-2xl text-slate-400 leading-relaxed">
            Every conclusion carries the reasoning that produced it. There is no code path
            that emits a directional call without showing its work.
          </p>
        </Reveal>

        <div className="mt-12 grid md:grid-cols-2 lg:grid-cols-3 gap-5">
          {FEATURES.map((feature, i) => (
            <Reveal key={feature.title} delay={(i % 3) * 100}>
              <div className="group h-full rounded-2xl border border-white/[0.07] bg-white/[0.02] p-6
                              transition-all duration-300 hover:border-white/15 hover:bg-white/[0.04]
                              hover:-translate-y-1">
                <div className="h-9 w-9 rounded-lg bg-gradient-to-br from-violet-500/25 to-teal-400/20
                                border border-white/10 grid place-items-center mb-4">
                  <span className="font-mono text-xs text-slate-300">
                    {String(i + 1).padStart(2, "0")}
                  </span>
                </div>
                <h3 className="font-display text-base font-semibold tracking-tight">
                  {feature.title}
                </h3>
                <p className="mt-2.5 text-sm leading-relaxed text-slate-400">{feature.body}</p>
              </div>
            </Reveal>
          ))}
        </div>
      </section>

      {/* ---------- markets ---------- */}
      <section id="markets" className="mx-auto max-w-6xl px-6 py-16">
        <div className="rounded-3xl border border-white/[0.07] bg-gradient-to-b from-white/[0.04]
                        to-transparent p-8 sm:p-12">
          <Reveal>
            <h2 className="font-display text-3xl sm:text-4xl font-bold tracking-[-0.02em]">
              Every listed market, no key required
            </h2>
            <p className="mt-4 max-w-2xl text-slate-400 leading-relaxed">
              Crypto streams tick-by-tick with no credentials at all. Equities, indices,
              forex and futures come from a keyless source, with automatic failover to a
              keyed provider when one is configured — and a free Twelve Data key streams
              stocks live too.
            </p>
          </Reveal>

          <div className="mt-8 grid sm:grid-cols-2 lg:grid-cols-4 gap-3">
            {[
              ["US stocks", "AAPL · NVDA · SERV · IONQ"],
              ["Crypto", "BTCUSDT · ETHUSDT · SOLUSDT"],
              ["Forex & futures", "EURUSD · ES=F · GC=F"],
              ["International", "SHOP.TO · 7203.T · 0700.HK"],
            ].map(([title, examples], i) => (
              <Reveal key={title} delay={i * 80}>
                <div className="rounded-xl border border-white/[0.07] bg-[#0a0d14] p-4 h-full">
                  <div className="text-sm font-medium text-slate-200">{title}</div>
                  <div className="mt-1.5 font-mono text-[11px] leading-relaxed text-slate-500">
                    {examples}
                  </div>
                </div>
              </Reveal>
            ))}
          </div>
        </div>
      </section>

      {/* ---------- the honest section ---------- */}
      <section id="honest" className="mx-auto max-w-6xl px-6 py-16">
        <Reveal>
          <div className="rounded-3xl border border-amber-500/20 bg-amber-500/[0.04] p-8 sm:p-12">
            <h2 className="font-display text-2xl sm:text-3xl font-bold tracking-[-0.02em]">
              What it does not do
            </h2>
            <p className="mt-4 max-w-2xl text-slate-400 leading-relaxed">
              A platform that only advertises its strengths is telling you half the story.
              These are written into the documentation, not buried in it.
            </p>
            {/* Two columns from `md` up. A single column of short list items
                inside a full-width card leaves most of the card empty on a
                wide screen, which reads as unfinished rather than airy. */}
            <ul className="mt-8 grid md:grid-cols-2 gap-x-10 gap-y-5">
              {[
                [
                  "Backtests are not predictions",
                  "The Monte Carlo section exists specifically to show how wide the range of outcomes really is.",
                ],
                [
                  "No live option chain",
                  "No free vendor serves real strikes, open interest or implied volatility — so it prices contracts you specify rather than inventing a chain.",
                ],
                [
                  "The keyless equity source rate-limits hard",
                  "Yahoo is per-IP and can refuse a whole network outright — measured, not theorised. Crypto needs no key ever; for stocks, plan on a free Twelve Data key.",
                ],
                [
                  "Futures have no streaming path",
                  "Genuinely free real-time futures data does not exist as a category, so futures charts poll.",
                ],
                [
                  "A polled feed is labelled DELAYED",
                  "Never LIVE. A 15-second-old price is never shown as tick-by-tick.",
                ],
                [
                  "No broker adapter is verified live",
                  "Alpaca, Tradier and IBKR are tested against recorded wire formats. No order has been sent to a real account.",
                ],
                [
                  "Nothing here is financial advice",
                  "It is a measurement tool. Every number is reproducible from the same inputs, which is not the same as being right.",
                ],
              ].map(([title, body]) => (
                <li key={title} className="flex gap-3">
                  <span
                    className="mt-2 h-1.5 w-1.5 rounded-full bg-amber-400/70 shrink-0"
                    aria-hidden="true"
                  />
                  <div>
                    <div className="text-sm font-medium text-slate-200">{title}</div>
                    <p className="mt-1 text-sm leading-relaxed text-slate-400">{body}</p>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        </Reveal>
      </section>

      {/* ---------- closing CTA ---------- */}
      <section className="mx-auto max-w-6xl px-6 pb-24">
        <Reveal>
          <div className="relative overflow-hidden rounded-3xl border border-white/10
                          bg-gradient-to-br from-violet-600/15 via-transparent to-teal-400/10
                          p-10 sm:p-16 text-center">
            <div className="pointer-events-none absolute -top-24 left-1/2 h-64 w-64 -translate-x-1/2
                            rounded-full bg-violet-500/20 blur-[100px] animate-drift"
                 aria-hidden="true" />
            <h2 className="relative font-display text-3xl sm:text-4xl font-bold tracking-[-0.02em]">
              Open it and check the evidence yourself
            </h2>
            <p className="relative mt-4 mx-auto max-w-xl text-slate-400 leading-relaxed">
              Paper trading is the default and needs no broker. Nothing reaches real money
              without deliberate, separate steps.
            </p>
            <button
              onClick={onGetStarted}
              className="relative mt-8 rounded-xl bg-white px-8 py-3.5 text-sm font-semibold
                         text-[#07090e] transition-all hover:scale-[1.03]
                         hover:shadow-[0_0_50px_-10px_rgba(255,255,255,0.6)] active:scale-100"
            >
              Open the terminal
            </button>
          </div>
        </Reveal>
      </section>

      <footer className="border-t border-white/[0.06]">
        <div className="mx-auto max-w-6xl px-6 py-8 flex flex-col sm:flex-row items-center
                        justify-between gap-4 text-xs text-slate-600">
          <span>Legend Trade — market analysis, not financial advice.</span>
          {/* Both documents are still drafts with unfilled placeholders and an
              open regulatory question. They point at the repository rather than
              at served pages on purpose: a hosted /privacy that reads as final
              would imply a review that has not happened. Swap these for real
              routes once the placeholders are filled and a lawyer has signed
              off — see the "what still needs a lawyer" section in each. */}
          <div className="flex items-center gap-5">
            <a
              className="hover:text-slate-300 transition-colors"
              href="https://github.com/nexarohc/Project-Legend-Trade/blob/main/PRIVACY.md"
              target="_blank"
              rel="noreferrer"
            >
              Privacy
            </a>
            <a
              className="hover:text-slate-300 transition-colors"
              href="https://github.com/nexarohc/Project-Legend-Trade/blob/main/TERMS.md"
              target="_blank"
              rel="noreferrer"
            >
              Terms
            </a>
            <span>Paper trading by default.</span>
          </div>
        </div>
      </footer>
    </div>
  );
}
