import logo from "../assets/logo.svg";
import MarketField from "./landing/MarketField.jsx";
import { Counter, Reveal, Scramble, Spotlight } from "./landing/effects.jsx";

/**
 * The public landing page, built as an instrument panel rather than a brochure.
 *
 * The first version of this page was a competent dark-SaaS layout — gradient
 * headline, blurred orbs, a grid of cards. It could have been for any product,
 * which is exactly what was wrong with it. This one commits to a single idea:
 * **the page is a readout.** Corner brackets, engraved grid, monospace
 * numerals, a live candle field drifting behind everything, a headline that
 * decodes out of noise. A visitor should be able to tell what this software is
 * for before reading a word.
 *
 * The rules it still follows, unchanged:
 *
 * 1. **Claim only what the product does.** Every figure below is one this
 *    codebase actually produces. There is no "AI-powered predictions" copy,
 *    because the engine deliberately refuses to predict without evidence.
 * 2. **Motion is opacity and transform.** The one canvas is frame-gated to
 *    24fps, stops when the tab is hidden, and draws a single static frame under
 *    `prefers-reduced-motion` — which also disables every animation here.
 * 3. **Saturated colour means price direction.** Teal and violet carry the
 *    brand; green and red are reserved for the market itself.
 */

const FEATURES = [
  {
    code: "EVD",
    title: "Describes before it predicts",
    body: "Market context, structure, price action, volume, 15+ indicators, Smart Money Concepts and key levels are computed and explained before any probability is quoted.",
  },
  {
    code: "PRB",
    title: "Probabilities showing their working",
    body: "Base rates measured on the symbol's own history, combined with individually-weighted factors. Every number traces back to something measured, never asserted.",
  },
  {
    code: "AUD",
    title: "An audit that rejects",
    body: "Strategies are backtested with realistic costs and next-bar fills, validated with Monte Carlo, walk-forward and regime tests, then put through a 15-check audit returning APPROVED, REVISE or REJECT.",
  },
  {
    code: "GRD",
    title: "Guardrails that only say no",
    body: "No entry without a protective stop. Nothing from a strategy the audit rejected. A kill switch that flattens everything. Live trading needs four independent switches to line up.",
  },
  {
    code: "OPT",
    title: "Options priced, not guessed",
    body: "Black-Scholes and a binomial tree for American exercise, all five Greeks, implied volatility by bisection, and multi-leg payoff diagrams — checked against textbook reference values.",
  },
  {
    code: "LIM",
    title: "Honest about its own limits",
    body: "A polled feed is badged DELAYED, never LIVE. Unmeasurable risk is reported as unmodelled rather than guessed. What it cannot do is written down, not buried.",
  },
];

const PIPELINE = [
  { step: "01", label: "Ingest", detail: "Tick or candle, from the first provider that answers" },
  { step: "02", label: "Describe", detail: "20 sections of evidence, each one explained" },
  { step: "03", label: "Weigh", detail: "Base rates + factors, every weight visible" },
  { step: "04", label: "Refuse", detail: "No stop, no edge, no audit — no order" },
];

const TAPE = [
  ["US equities", "every market cap"],
  ["ETFs", "keyless"],
  ["Indices", "^RUT · ^GSPC"],
  ["Crypto", "tick-by-tick"],
  ["Forex", "EUR/USD"],
  ["Futures", "ES=F · GC=F"],
  ["Commodities", "XAU/USD"],
  ["International", "SHOP.TO · 7203.T"],
  ["Options", "Black-Scholes + CRR"],
];

const STATS = [
  { value: 653, label: "tests passing", suffix: "" },
  { value: 20, label: "analysis sections", suffix: "" },
  { value: 15, label: "audit checks", suffix: "" },
  { value: 3, label: "broker adapters", suffix: "" },
];

const LIMITS = [
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
    "No broker adapter is verified live",
    "Alpaca, Tradier and IBKR are tested against recorded wire formats. No order has been sent to a real account.",
  ],
  [
    "Nothing here is financial advice",
    "It is a measurement tool. Every number is reproducible from the same inputs, which is not the same as being right.",
  ],
];

/** A small uppercase monospace label — the page's connective tissue. */
function Tag({ children, tone = "teal" }) {
  const tones = {
    teal: "text-teal-300/70 border-teal-400/20 bg-teal-400/[0.06]",
    violet: "text-violet-300/70 border-violet-400/20 bg-violet-400/[0.06]",
    amber: "text-amber-300/80 border-amber-400/25 bg-amber-400/[0.06]",
  };
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded border px-2 py-1 font-mono
                  text-[10px] uppercase tracking-[0.18em] ${tones[tone]}`}
    >
      {children}
    </span>
  );
}

/** Section heading with a rule and an index, like a panel label. */
function SectionHead({ index, kicker, title, children }) {
  return (
    <Reveal>
      <div className="flex items-center gap-3">
        <span className="font-mono text-[10px] tracking-[0.3em] text-teal-400/60">{index}</span>
        <span className="h-px flex-1 bg-gradient-to-r from-teal-400/30 to-transparent" />
        <span className="font-mono text-[10px] uppercase tracking-[0.22em] text-slate-500">
          {kicker}
        </span>
      </div>
      <h2 className="mt-6 font-display text-3xl font-bold tracking-[-0.025em] sm:text-[2.6rem] sm:leading-[1.08]">
        {title}
      </h2>
      {children && (
        <p className="mt-4 max-w-2xl leading-relaxed text-slate-400">{children}</p>
      )}
    </Reveal>
  );
}

/** The verdict readout in the hero — the product's actual output. */
function VerdictPanel() {
  return (
    <div className="relative">
      {/* Offset frame behind the panel, so it reads as mounted hardware
          rather than a floating rectangle. */}
      <div
        aria-hidden="true"
        className="absolute -inset-3 rounded-2xl border border-white/[0.05] bg-white/[0.012]"
      />
      <div
        className="hud-corners relative overflow-hidden rounded-xl border border-white/10
                   bg-[#070a10]/85 backdrop-blur-xl
                   shadow-[0_40px_120px_-30px_rgba(0,0,0,0.95)]"
      >
        {/* Sensor sweep. One element, transform-only. */}
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-x-0 top-0 h-16 animate-scan
                     bg-gradient-to-b from-teal-400/10 to-transparent"
        />

        <div className="flex items-center justify-between border-b border-white/[0.07] px-4 py-2.5">
          <div className="flex items-center gap-2.5">
            <span className="font-mono text-[11px] font-medium text-slate-200">BTCUSDT</span>
            <span className="inline-flex items-center gap-1.5 rounded bg-emerald-500/10 px-1.5 py-0.5">
              <span className="h-1.5 w-1.5 animate-pulse-dot rounded-full bg-emerald-400" />
              <span className="font-mono text-[9px] uppercase tracking-wider text-emerald-400">
                Live
              </span>
            </span>
          </div>
          <span className="font-mono text-[10px] text-slate-600">1h · coinbase</span>
        </div>

        <div className="grid grid-cols-3 divide-x divide-white/[0.06] border-b border-white/[0.06]">
          {[
            ["Trend", "bullish", "text-teal-300"],
            ["Regime", "balanced range", "text-slate-300"],
            ["Volatility", "33rd pct", "text-slate-300"],
          ].map(([label, value, tone]) => (
            <div key={label} className="px-4 py-3">
              <div className="font-mono text-[9px] uppercase tracking-[0.16em] text-slate-600">
                {label}
              </div>
              <div className={`mt-1 font-mono text-[12px] ${tone}`}>{value}</div>
            </div>
          ))}
        </div>

        {/* The refusal. This is the selling point, so it gets the weight. */}
        <div className="p-4">
          <div className="relative overflow-hidden rounded-lg border border-amber-500/25 bg-amber-500/[0.05] p-4">
            <div
              aria-hidden="true"
              className="absolute inset-x-0 top-0 h-px animate-flare bg-amber-400/60"
            />
            <div className="flex items-baseline justify-between gap-3">
              <span className="font-display text-lg font-bold tracking-tight text-amber-300">
                STAND ASIDE
              </span>
              <span className="font-mono text-[11px] text-slate-500">confidence 32%</span>
            </div>
            <p className="mt-2 text-[12px] leading-relaxed text-slate-400">
              Bullish and bearish continuation odds are within 5 points. Trend setups have
              poor expectancy in this regime.{" "}
              <span className="text-slate-300">The evidence is too thin to justify risk.</span>
            </p>
          </div>

          <div className="mt-3 grid grid-cols-3 gap-2">
            {[
              ["Entry", "—"],
              ["Stop", "—"],
              ["Target", "—"],
            ].map(([label, value]) => (
              <div
                key={label}
                className="rounded border border-white/[0.06] bg-white/[0.015] px-2.5 py-2"
              >
                <div className="font-mono text-[9px] uppercase tracking-wider text-slate-600">
                  {label}
                </div>
                <div className="mt-0.5 font-mono text-[13px] text-slate-500">{value}</div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

export default function Landing({ onGetStarted }) {
  return (
    <div className="relative min-h-screen overflow-x-hidden bg-[#04060a] text-slate-100">
      {/* ---------- fixed backdrop ---------- */}
      <div aria-hidden="true" className="pointer-events-none fixed inset-0 z-0">
        <div className="hud-grid absolute inset-0 opacity-[0.55]" />
        <MarketField className="absolute inset-x-0 top-0 h-[86vh] w-full" />
        <div className="absolute -left-40 top-[-10rem] h-[38rem] w-[38rem] animate-drift rounded-full bg-violet-600/[0.13] blur-[130px]" />
        <div className="absolute -right-40 top-32 h-[32rem] w-[32rem] animate-drift-slow rounded-full bg-teal-400/[0.09] blur-[130px]" />
        {/* Vignette: pulls the eye to the middle and stops the grid reaching
            the edges, which is what makes it read as a screen. */}
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_80%_60%_at_50%_10%,transparent_10%,rgba(4,6,10,0.55)_55%,#04060a_88%)]" />
      </div>
      <Spotlight />

      <div className="relative z-20">
        {/* ---------- nav ---------- */}
        <header className="sticky top-0 z-30 border-b border-white/[0.06] bg-[#04060a]/70 backdrop-blur-xl">
          <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-6">
            <div className="flex items-center gap-2.5">
              <img src={logo} alt="" className="h-7 w-7 rounded-lg" />
              <span className="font-display text-[15px] font-semibold tracking-tight">
                Legend Trade
              </span>
              <span className="ml-1 hidden font-mono text-[9px] uppercase tracking-[0.2em] text-teal-400/50 sm:inline">
                v1
              </span>
            </div>
            <nav className="hidden items-center gap-8 font-mono text-[11px] uppercase tracking-[0.14em] text-slate-500 md:flex">
              <a href="#how" className="transition-colors hover:text-teal-300">
                Method
              </a>
              <a href="#markets" className="transition-colors hover:text-teal-300">
                Coverage
              </a>
              <a href="#honest" className="transition-colors hover:text-teal-300">
                Limits
              </a>
            </nav>
            <button
              onClick={onGetStarted}
              className="group relative overflow-hidden rounded-lg border border-teal-400/30
                         bg-teal-400/10 px-4 py-2 font-mono text-[11px] uppercase
                         tracking-[0.14em] text-teal-200 transition-all
                         hover:border-teal-400/60 hover:bg-teal-400/20
                         hover:shadow-[0_0_28px_-6px_rgba(34,211,199,0.6)]"
            >
              Open terminal
            </button>
          </div>
        </header>

        {/* ---------- hero ---------- */}
        <section className="mx-auto max-w-6xl px-6 pb-20 pt-14 sm:pt-20">
          <div className="grid items-center gap-14 lg:grid-cols-[1.05fr_1fr]">
            <div>
              <div className="animate-fade-in">
                <Tag>
                  <span className="h-1.5 w-1.5 animate-pulse-dot rounded-full bg-teal-400" />
                  Live market data · no API key required
                </Tag>
              </div>

              <h1 className="mt-7 font-display text-[2.9rem] font-bold leading-[1.02] tracking-[-0.035em] sm:text-6xl">
                <span className="block text-white">
                  <Scramble text="Describe the market" />
                </span>
                <span className="mt-1 block bg-gradient-to-r from-violet-400 via-sky-300 to-teal-300 bg-clip-text text-transparent">
                  <Scramble text="before predicting it." delay={520} />
                </span>
              </h1>

              <p className="mt-7 max-w-xl text-[15px] leading-relaxed text-slate-400">
                An institutional-grade terminal that shows the evidence for every conclusion —
                and returns an explicit{" "}
                <span className="font-mono text-amber-300/90">no trade</span>, with reasons,
                when the evidence does not support one.
              </p>

              <div className="mt-9 flex flex-wrap items-center gap-3">
                <button
                  onClick={onGetStarted}
                  className="group relative overflow-hidden rounded-lg bg-white px-7 py-3.5
                             text-sm font-semibold text-[#04060a] transition-all
                             hover:shadow-[0_0_45px_-8px_rgba(255,255,255,0.65)]
                             active:scale-[0.98]"
                >
                  Open the terminal
                </button>
                <a
                  href="#how"
                  className="rounded-lg border border-white/12 px-6 py-3.5 text-sm
                             text-slate-300 transition-colors hover:border-teal-400/40
                             hover:text-teal-200"
                >
                  See how it decides
                </a>
              </div>

              <p className="mt-6 max-w-md font-mono text-[11px] leading-relaxed text-slate-600">
                Paper trading is the default and needs no broker. Live execution stays off
                until four independent switches line up.
              </p>
            </div>

            <div className="animate-float lg:animate-none">
              <VerdictPanel />
            </div>
          </div>
        </section>

        {/* ---------- coverage tape ---------- */}
        <div className="relative overflow-hidden border-y border-white/[0.07] bg-white/[0.012] py-3.5">
          <div className="flex w-max animate-ticker-slow gap-10 px-4" aria-hidden="true">
            {[...TAPE, ...TAPE].map(([name, detail], index) => (
              <span key={index} className="flex items-center gap-2.5 whitespace-nowrap">
                <span className="h-1 w-1 rounded-full bg-teal-400/50" />
                <span className="text-[12px] text-slate-400">{name}</span>
                <span className="font-mono text-[10px] text-slate-600">{detail}</span>
              </span>
            ))}
          </div>
          {/* Feathered edges, so the tape appears to run under the page rather
              than to start and stop at the viewport. */}
          <div className="pointer-events-none absolute inset-y-0 left-0 w-24 bg-gradient-to-r from-[#04060a] to-transparent" />
          <div className="pointer-events-none absolute inset-y-0 right-0 w-24 bg-gradient-to-l from-[#04060a] to-transparent" />
        </div>

        {/* ---------- stats ---------- */}
        <section className="mx-auto max-w-6xl px-6 py-16">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            {STATS.map((stat, index) => (
              <Reveal key={stat.label} delay={index * 80}>
                <div className="hud-corners relative rounded-lg border border-white/[0.07] bg-white/[0.02] p-5">
                  <div className="font-mono text-[2rem] font-semibold leading-none tracking-tight text-white">
                    <Counter value={stat.value} />
                  </div>
                  <div className="mt-2 font-mono text-[10px] uppercase tracking-[0.14em] text-slate-500">
                    {stat.label}
                  </div>
                </div>
              </Reveal>
            ))}
          </div>
        </section>

        {/* ---------- pipeline ---------- */}
        <section className="mx-auto max-w-6xl px-6 py-16">
          <SectionHead index="/ 01" kicker="Method" title="Four stages, one of which is refusal">
            Data becomes evidence, evidence becomes a weighted probability, and a probability
            only becomes an order if every guardrail agrees. The fourth stage is the one most
            tools leave out.
          </SectionHead>

          <div className="mt-12 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {PIPELINE.map((stage, index) => (
              <Reveal key={stage.step} delay={index * 90}>
                <div className="group relative h-full rounded-lg border border-white/[0.07] bg-white/[0.02] p-5 transition-colors hover:border-teal-400/25">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-[10px] tracking-[0.2em] text-teal-400/70">
                      {stage.step}
                    </span>
                    <span className="h-px flex-1 bg-white/[0.08]" />
                  </div>
                  <div className="mt-4 font-display text-[15px] font-semibold text-white">
                    {stage.label}
                  </div>
                  <p className="mt-1.5 text-[12px] leading-relaxed text-slate-500">
                    {stage.detail}
                  </p>
                </div>
              </Reveal>
            ))}
          </div>
        </section>

        {/* ---------- features ---------- */}
        <section id="how" className="mx-auto max-w-6xl px-6 py-16">
          <SectionHead
            index="/ 02"
            kicker="Capabilities"
            title="Built to be checked, not trusted"
          >
            Every conclusion carries the reasoning that produced it. There is no code path
            that emits a directional call without showing its work.
          </SectionHead>

          <div className="mt-12 grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {FEATURES.map((feature, index) => (
              <Reveal key={feature.title} delay={(index % 3) * 90}>
                {/* The rotating conic border is the one purely-decorative flourish
                    on the page, and it is confined to these six cards. */}
                <div className="hud-border group h-full animate-spin-border rounded-xl p-6 transition-transform duration-300 hover:-translate-y-1">
                  <div className="flex items-center justify-between">
                    <span className="rounded border border-teal-400/20 bg-teal-400/[0.07] px-2 py-1 font-mono text-[10px] tracking-[0.16em] text-teal-300/80">
                      {feature.code}
                    </span>
                    <span className="font-mono text-[10px] text-slate-700">
                      {String(index + 1).padStart(2, "0")}
                    </span>
                  </div>
                  <h3 className="mt-5 font-display text-[15px] font-semibold leading-snug tracking-tight text-white">
                    {feature.title}
                  </h3>
                  <p className="mt-2.5 text-[13px] leading-relaxed text-slate-400">
                    {feature.body}
                  </p>
                </div>
              </Reveal>
            ))}
          </div>
        </section>

        {/* ---------- markets ---------- */}
        <section id="markets" className="mx-auto max-w-6xl px-6 py-16">
          <div className="hud-corners relative overflow-hidden rounded-2xl border border-white/[0.08] bg-gradient-to-b from-white/[0.035] to-transparent p-8 sm:p-12">
            <div
              aria-hidden="true"
              className="pointer-events-none absolute -right-24 -top-24 h-72 w-72 animate-glow-pulse rounded-full bg-teal-400/10 blur-[90px]"
            />
            <SectionHead
              index="/ 03"
              kicker="Coverage"
              title="Every listed market, no key required"
            >
              Crypto streams tick-by-tick with no credentials at all. Equities, indices, forex
              and futures come from a keyless source with automatic failover — and a free
              Twelve Data key streams stocks live too.
            </SectionHead>

            <div className="mt-10 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {[
                ["US stocks", "AAPL · NVDA · SERV · IONQ", "any market cap"],
                ["Crypto", "BTCUSDT · ETHUSDT · SOLUSDT", "no key, ever"],
                ["Forex & futures", "EUR/USD · ES=F · GC=F", "spot + contracts"],
                ["International", "SHOP.TO · 7203.T · 0700.HK", "exchange-qualified"],
              ].map(([title, examples, note], index) => (
                <Reveal key={title} delay={index * 80}>
                  <div className="h-full rounded-lg border border-white/[0.07] bg-[#06090f] p-4">
                    <div className="text-[13px] font-medium text-slate-200">{title}</div>
                    <div className="mt-2 font-mono text-[10px] leading-relaxed text-slate-500">
                      {examples}
                    </div>
                    <div className="mt-3 border-t border-white/[0.06] pt-2 font-mono text-[9px] uppercase tracking-[0.14em] text-teal-400/50">
                      {note}
                    </div>
                  </div>
                </Reveal>
              ))}
            </div>
          </div>
        </section>

        {/* ---------- limits ---------- */}
        <section id="honest" className="mx-auto max-w-6xl px-6 py-16">
          <Reveal>
            <div className="hud-corners relative overflow-hidden rounded-2xl border border-amber-500/20 bg-amber-500/[0.03] p-8 sm:p-12">
              <div className="flex items-center gap-3">
                <Tag tone="amber">/ 04 — Disclosure</Tag>
              </div>
              <h2 className="mt-6 font-display text-3xl font-bold tracking-[-0.025em] sm:text-[2.4rem]">
                What it does not do
              </h2>
              <p className="mt-4 max-w-2xl leading-relaxed text-slate-400">
                A platform that only advertises its strengths is telling you half the story.
                These are written into the documentation, not buried in it.
              </p>

              <ul className="mt-10 grid gap-x-12 gap-y-6 md:grid-cols-2">
                {LIMITS.map(([title, body], index) => (
                  <li key={title} className="flex gap-3.5">
                    <span className="mt-1 font-mono text-[10px] text-amber-400/60">
                      {String(index + 1).padStart(2, "0")}
                    </span>
                    <div>
                      <div className="text-[13px] font-medium text-slate-200">{title}</div>
                      <p className="mt-1 text-[13px] leading-relaxed text-slate-400">{body}</p>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          </Reveal>
        </section>

        {/* ---------- closing ---------- */}
        <section className="mx-auto max-w-6xl px-6 pb-24">
          <Reveal>
            <div className="hud-corners relative overflow-hidden rounded-2xl border border-white/10 p-12 text-center sm:p-16">
              <div
                aria-hidden="true"
                className="pointer-events-none absolute inset-0 bg-gradient-to-br from-violet-600/15 via-transparent to-teal-400/12"
              />
              <div
                aria-hidden="true"
                className="pointer-events-none absolute -top-28 left-1/2 h-72 w-72 -translate-x-1/2 animate-drift rounded-full bg-violet-500/20 blur-[110px]"
              />
              <h2 className="relative font-display text-3xl font-bold tracking-[-0.025em] sm:text-[2.6rem]">
                Open it and check the evidence yourself
              </h2>
              <p className="relative mx-auto mt-4 max-w-xl leading-relaxed text-slate-400">
                Paper trading is the default and needs no broker. Nothing reaches real money
                without deliberate, separate steps.
              </p>
              <button
                onClick={onGetStarted}
                className="relative mt-9 rounded-lg bg-white px-8 py-3.5 text-sm font-semibold
                           text-[#04060a] transition-all
                           hover:shadow-[0_0_55px_-8px_rgba(255,255,255,0.7)]
                           active:scale-[0.98]"
              >
                Open the terminal
              </button>
            </div>
          </Reveal>
        </section>

        <footer className="border-t border-white/[0.06]">
          <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 px-6 py-8 font-mono text-[11px] text-slate-600 sm:flex-row">
            <span>Legend Trade — market analysis, not financial advice.</span>
            {/* Both documents are still drafts with unfilled placeholders and an
                open regulatory question. They point at the repository on purpose:
                a hosted /privacy that reads as final would imply a review that has
                not happened. Swap for real routes once a lawyer has signed off. */}
            <div className="flex items-center gap-6">
              <a
                className="uppercase tracking-[0.14em] transition-colors hover:text-teal-300"
                href="https://github.com/nexarohc/Project-Legend-Trade/blob/main/PRIVACY.md"
                target="_blank"
                rel="noreferrer"
              >
                Privacy
              </a>
              <a
                className="uppercase tracking-[0.14em] transition-colors hover:text-teal-300"
                href="https://github.com/nexarohc/Project-Legend-Trade/blob/main/TERMS.md"
                target="_blank"
                rel="noreferrer"
              >
                Terms
              </a>
              <span>Paper by default</span>
            </div>
          </div>
        </footer>
      </div>
    </div>
  );
}
