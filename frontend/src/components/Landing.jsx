import { useEffect, useState } from "react";
import logo from "../assets/logo.svg";
import ArchitectureStack from "./landing/ArchitectureStack.jsx";
import { Counter, Reveal } from "./landing/effects.jsx";

/**
 * The public landing page — enterprise register, not product register.
 *
 * This is deliberately a different visual language from the terminal it leads
 * into. The terminal is dark because a trader stares at it for eight hours and
 * the market should be the only bright thing on screen. A marketing surface has
 * eight *seconds* to establish that the software is serious, and that is not
 * the same problem: light ground, generous whitespace, large restrained
 * typography, one accent colour, no neon.
 *
 * What it will not do, and why:
 *
 * - **No customer logos, testimonials or success stories.** There are no
 *   customers. Inventing them is the single fastest way to destroy the trust
 *   every other decision here is spent building, and it is the kind of lie that
 *   gets found. Those sections are simply absent.
 * - **No pricing.** Nothing is charged yet.
 * - **Every figure is real.** The test count comes from the suite, the section
 *   and check counts from the analysis and audit code, the provider count from
 *   `trading/providers`. Nothing is rounded up for effect.
 * - **The disclosure section stays.** A page that only advertises strengths is
 *   telling half the story, and on a financial tool that omission is not
 *   neutral.
 */

const DOMAINS = [
  {
    title: "Market Data",
    body: "Eight providers behind one interface, probed in order with automatic failover. Crypto streams tick-by-tick with no credentials at all.",
    meta: "8 providers",
  },
  {
    title: "Market Analysis",
    body: "Context, structure, price action, volume, 15+ indicators, Smart Money Concepts and key levels — each section computed and explained before any probability is quoted.",
    meta: "20 sections",
  },
  {
    title: "Probability Engine",
    body: "Base rates measured on the symbol's own history, combined with individually weighted factors. Every number traces back to something measured.",
    meta: "Evidence-first",
  },
  {
    title: "Strategy & Backtesting",
    body: "Plain English parsed to an executable spec, backtested with realistic costs and next-bar fills, then validated with Monte Carlo, walk-forward and regime tests.",
    meta: "No look-ahead",
  },
  {
    title: "Adversarial Audit",
    body: "Fifteen mechanical checks returning APPROVED, REVISE or REJECT with reasoning. A strategy the audit rejects cannot reach the execution layer.",
    meta: "15 checks",
  },
  {
    title: "Options Pricing",
    body: "Black-Scholes and a Cox-Ross-Rubinstein binomial tree for American exercise, all five Greeks, implied volatility by bisection, multi-leg payoff diagrams.",
    meta: "Verified vs. Hull",
  },
  {
    title: "Execution & Guardrails",
    body: "The only layer permitted to refuse an order. No entry without a protective stop, hard position and exposure caps, and a kill switch that flattens everything.",
    meta: "3 brokers",
  },
  {
    title: "Identity & Access",
    body: "Accounts with per-user data isolation, TOTP two-factor authentication, email verification, admin account management and Redis-backed rate limiting.",
    meta: "MFA + isolation",
  },
  {
    title: "Observability",
    body: "Structured JSON logging including the web server's own access lines, and a Prometheus endpoint with route-template labels that keep cardinality bounded.",
    meta: "Prometheus",
  },
];

const STATS = [
  { value: 653, label: "Automated tests", note: "passing, no network required" },
  { value: 20, label: "Analysis sections", note: "each one explained" },
  { value: 15, label: "Audit checks", note: "any can reject a strategy" },
  { value: 8, label: "Data providers", note: "one interface, automatic failover" },
];

const PRINCIPLES = [
  {
    title: "Describe before predicting",
    body: "The evidence is computed and shown before a probability is quoted. There is no code path that emits a directional call without its reasoning attached.",
  },
  {
    title: "“No trade” is a valid answer",
    body: "When the evidence does not support a position, the setup builder returns an explicit refusal with reasons. A tool that always finds a trade is a tool that is lying.",
  },
  {
    title: "Guardrails fail closed",
    body: "Any check that cannot be evaluated refuses. A guardrail that silently passes when its input is missing is not a guardrail.",
  },
  {
    title: "Degrade to a known-good state",
    body: "Where a dependency can fail, the fallback is a working configuration rather than fail-open or fail-closed — a cache outage drops back to in-process limits.",
  },
];

const DOCS = [
  {
    title: "Trading Terminal",
    body: "Market coverage, the analysis pipeline, backtester internals, execution model and the full API reference.",
    file: "docs/TRADING_TERMINAL.md",
  },
  {
    title: "Deployment",
    body: "TLS and reverse proxy, worker topology, backups with verified restores, monitoring and the broker connection procedure.",
    file: "docs/DEPLOYMENT.md",
  },
  {
    title: "Project State",
    body: "The authoritative handoff document: what is built, every decision and why, and what is deliberately absent.",
    file: "docs/PROJECT_STATE.md",
  },
];

const FAQ = [
  [
    "Is this financial advice?",
    "No. It is a measurement tool. It computes analysis and probabilities from price data and shows the reasoning; every decision remains yours. Nothing it produces is a recommendation.",
  ],
  [
    "Does it need an API key?",
    "Not for crypto, which streams tick-by-tick from Binance or Coinbase with no credentials. For equities the keyless source rate-limits per IP and can refuse an entire network, so plan on a free Twelve Data key — this was measured, not assumed.",
  ],
  [
    "Can it place real orders?",
    "It can, and it is off by default. Live execution requires a server-side flag, an explicit mode switch and a typed confirmation phrase. Paper trading is the default and needs no broker account.",
  ],
  [
    "Have the broker integrations been tested against real accounts?",
    "No. The Alpaca, Tradier and Interactive Brokers adapters are tested against recorded wire formats. No order has been sent to a live account from this codebase, and that is stated in the documentation as well as here.",
  ],
  [
    "Is my data shared with anyone?",
    "There are no cookies, no analytics and no third-party scripts. Market-data vendors see the symbols requested but no account identifier. The full detail is in the privacy policy.",
  ],
  [
    "Is it open about what it cannot do?",
    "Yes — see the disclosure section above. Known limitations live in the documentation and on this page rather than being discovered later.",
  ],
];

const LIMITS = [
  [
    "Backtests are not predictions",
    "The Monte Carlo section exists specifically to show how wide the range of outcomes really is.",
  ],
  [
    "No live option chain",
    "No free vendor serves real strikes, open interest or implied volatility, so it prices contracts you specify rather than inventing a chain.",
  ],
  [
    "The keyless equity source rate-limits hard",
    "Measured, not theorised: on a limited network every non-crypto symbol fails. Crypto never needs a key; equities want one.",
  ],
  [
    "No broker adapter is verified live",
    "Alpaca, Tradier and IBKR are tested against recorded wire formats. No order has been sent to a real account.",
  ],
];

/** Section eyebrow — small, blue, uppercase. Used once per section. */
function Eyebrow({ children }) {
  return (
    <div className="flex items-center gap-2.5">
      <span className="h-1.5 w-1.5 rounded-full bg-ent-blue" />
      <span className="font-mono text-[11px] uppercase tracking-[0.16em] text-ent-blue">
        {children}
      </span>
    </div>
  );
}

function SectionHead({ eyebrow, title, children, center = false }) {
  return (
    <Reveal className={center ? "text-center" : ""}>
      <div className={center ? "flex justify-center" : ""}>
        <Eyebrow>{eyebrow}</Eyebrow>
      </div>
      <h2
        className="mt-5 font-display text-[2rem] font-bold leading-[1.12] tracking-[-0.025em]
                   text-ent-navy sm:text-[2.75rem]"
      >
        {title}
      </h2>
      {children && (
        <p
          className={`mt-5 text-[17px] leading-[1.7] text-ent-muted ${
            center ? "mx-auto max-w-2xl" : "max-w-2xl"
          }`}
        >
          {children}
        </p>
      )}
    </Reveal>
  );
}

function PrimaryButton({ children, onClick, className = "" }) {
  return (
    <button
      onClick={onClick}
      className={`rounded-xl bg-ent-blue px-6 py-3 text-[15px] font-semibold text-white
                  shadow-[0_8px_24px_-8px_rgba(37,99,235,0.55)] transition-all
                  hover:bg-[#1D4ED8] hover:shadow-[0_14px_34px_-10px_rgba(37,99,235,0.65)]
                  focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2
                  focus-visible:outline-ent-blue active:translate-y-px ${className}`}
    >
      {children}
    </button>
  );
}

function FaqItem({ question, answer, index }) {
  const [open, setOpen] = useState(index === 0);
  return (
    <div className="border-b border-ent-border last:border-0">
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-6 py-5 text-left
                   transition-colors hover:text-ent-blue"
      >
        <span className="font-display text-[16px] font-semibold text-ent-ink">{question}</span>
        <span
          aria-hidden="true"
          className={`grid h-7 w-7 shrink-0 place-items-center rounded-lg border
                      border-ent-border text-ent-muted transition-transform duration-300 ${
                        open ? "rotate-45" : ""
                      }`}
        >
          +
        </span>
      </button>
      {/* Grid-rows trick: animates height without needing a measured pixel
          value, and collapses to genuinely zero so the text is not selectable
          while hidden. */}
      <div
        className={`grid transition-all duration-300 ease-out ${
          open ? "grid-rows-[1fr] pb-5 opacity-100" : "grid-rows-[0fr] opacity-0"
        }`}
      >
        <div className="overflow-hidden">
          <p className="max-w-3xl pr-12 text-[15px] leading-[1.7] text-ent-muted">{answer}</p>
        </div>
      </div>
    </div>
  );
}

export default function Landing({ onGetStarted }) {
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 12);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <div className="min-h-screen bg-white font-sans text-ent-ink antialiased">
      {/* ---------- navigation ---------- */}
      <header
        className={`sticky top-0 z-40 transition-all duration-300 ${
          scrolled
            ? "border-b border-ent-border bg-white/85 backdrop-blur-xl"
            : "border-b border-transparent bg-transparent"
        }`}
      >
        <div className="mx-auto flex h-[72px] max-w-7xl items-center justify-between px-6 lg:px-8">
          <div className="flex items-center gap-2.5">
            <img src={logo} alt="" className="h-8 w-8 rounded-lg" />
            <span className="font-display text-[17px] font-bold tracking-[-0.02em] text-ent-navy">
              Legend Trade
            </span>
          </div>

          <nav className="hidden items-center gap-9 lg:flex">
            {[
              ["Platform", "#platform"],
              ["Architecture", "#architecture"],
              ["Principles", "#principles"],
              ["Documentation", "#docs"],
              ["Disclosure", "#disclosure"],
            ].map(([label, href]) => (
              <a
                key={label}
                href={href}
                className="text-[14px] font-medium text-ent-muted transition-colors hover:text-ent-navy"
              >
                {label}
              </a>
            ))}
          </nav>

          <div className="flex items-center gap-3">
            <button
              onClick={onGetStarted}
              className="hidden text-[14px] font-medium text-ent-muted transition-colors
                         hover:text-ent-navy sm:block"
            >
              Sign in
            </button>
            <PrimaryButton onClick={onGetStarted} className="!px-5 !py-2.5 !text-[14px]">
              Get started
            </PrimaryButton>
          </div>
        </div>
      </header>

      {/* ---------- hero ---------- */}
      <section className="relative overflow-hidden">
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0
                     bg-[radial-gradient(ellipse_60%_50%_at_50%_-10%,rgba(37,99,235,0.07),transparent)]"
        />
        {/* Very faint grid, fading out before it reaches the content. Structure
            without texture noise. */}
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-x-0 top-0 h-[520px] opacity-[0.5]
                     [background-image:linear-gradient(#E2E8F0_1px,transparent_1px),linear-gradient(90deg,#E2E8F0_1px,transparent_1px)]
                     [background-size:64px_64px]
                     [mask-image:radial-gradient(ellipse_70%_60%_at_50%_0%,black,transparent)]"
        />

        <div className="relative mx-auto max-w-7xl px-6 pb-24 pt-16 lg:px-8 lg:pb-28 lg:pt-24">
          <div className="grid items-center gap-16 lg:grid-cols-[1.05fr_0.95fr]">
            <div>
              <div className="animate-fade-in inline-flex items-center gap-2.5 rounded-full border border-ent-border bg-ent-surface px-3.5 py-1.5">
                <span className="h-1.5 w-1.5 rounded-full bg-ent-success" />
                <span className="text-[13px] font-medium text-ent-slate">
                  Live market data — no API key required
                </span>
              </div>

              <h1 className="mt-7 font-display text-[2.75rem] font-bold leading-[1.06] tracking-[-0.035em] text-ent-navy sm:text-[3.75rem]">
                Describe the market
                <br />
                <span className="text-ent-blue">before predicting it.</span>
              </h1>

              <p className="mt-7 max-w-xl text-[18px] leading-[1.65] text-ent-muted">
                Legend Trade is an institutional-grade analysis and execution platform. It
                computes the evidence for every conclusion, quantifies what it can measure,
                declares what it cannot, and returns an explicit <strong className="font-semibold text-ent-ink">no trade</strong> when
                the evidence does not support one.
              </p>

              <div className="mt-9 flex flex-wrap items-center gap-3">
                <PrimaryButton onClick={onGetStarted}>Explore the platform</PrimaryButton>
                <a
                  href="#docs"
                  className="rounded-xl border border-ent-border bg-white px-6 py-3 text-[15px]
                             font-semibold text-ent-navy shadow-[0_1px_2px_rgba(15,23,42,0.05)]
                             transition-all hover:border-ent-blue/40 hover:text-ent-blue"
                >
                  View documentation
                </a>
              </div>

              <div className="mt-10 flex flex-wrap items-center gap-x-8 gap-y-3 border-t border-ent-border pt-7">
                {[
                  ["653", "tests passing"],
                  ["20", "analysis sections"],
                  ["3", "broker adapters"],
                ].map(([value, label]) => (
                  <div key={label} className="flex items-baseline gap-2">
                    <span className="font-display text-[22px] font-bold text-ent-navy">
                      {value}
                    </span>
                    <span className="text-[13px] text-ent-muted">{label}</span>
                  </div>
                ))}
              </div>
            </div>

            <div className="lg:pl-4">
              <ArchitectureStack />
            </div>
          </div>
        </div>
      </section>

      {/* ---------- statistics ---------- */}
      <section className="border-y border-ent-border bg-ent-surface">
        <div className="mx-auto grid max-w-7xl grid-cols-2 gap-px overflow-hidden bg-ent-border px-0 lg:grid-cols-4">
          {STATS.map((stat, index) => (
            <Reveal key={stat.label} delay={index * 70}>
              <div className="h-full bg-ent-surface px-6 py-10 lg:px-8">
                <div className="font-display text-[2.5rem] font-bold leading-none tracking-[-0.03em] text-ent-navy">
                  <Counter value={stat.value} />
                </div>
                <div className="mt-3 text-[14px] font-semibold text-ent-ink">{stat.label}</div>
                <div className="mt-1 text-[13px] text-ent-muted">{stat.note}</div>
              </div>
            </Reveal>
          ))}
        </div>
      </section>

      {/* ---------- platform / domains ---------- */}
      <section id="platform" className="mx-auto max-w-7xl px-6 py-24 lg:px-8 lg:py-28">
        <SectionHead eyebrow="Platform" title="Nine domains, one coherent system">
          Each domain is a real module in the codebase with its own tests and its own
          documented limitations. Nothing below is a roadmap item described in the present
          tense.
        </SectionHead>

        <div className="mt-14 grid gap-5 md:grid-cols-2 lg:grid-cols-3">
          {DOMAINS.map((domain, index) => (
            <Reveal key={domain.title} delay={(index % 3) * 80}>
              <div
                className="group h-full rounded-2xl border border-ent-border bg-white p-7
                           shadow-[0_1px_2px_rgba(15,23,42,0.04)] transition-all duration-300
                           hover:-translate-y-1 hover:border-ent-blue/30
                           hover:shadow-[0_18px_44px_-16px_rgba(15,23,42,0.18)]"
              >
                <div className="flex items-center justify-between">
                  <div
                    className="grid h-11 w-11 place-items-center rounded-xl border
                               border-ent-border bg-ent-surface transition-colors duration-300
                               group-hover:border-ent-blue/25 group-hover:bg-ent-blue/[0.07]"
                  >
                    <span className="font-mono text-[12px] font-semibold text-ent-blue">
                      {String(index + 1).padStart(2, "0")}
                    </span>
                  </div>
                  <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-ent-muted">
                    {domain.meta}
                  </span>
                </div>

                <h3 className="mt-6 font-display text-[17px] font-semibold tracking-[-0.01em] text-ent-navy">
                  {domain.title}
                </h3>
                <p className="mt-2.5 text-[14px] leading-[1.65] text-ent-muted">{domain.body}</p>
              </div>
            </Reveal>
          ))}
        </div>
      </section>

      {/* ---------- architecture ---------- */}
      <section id="architecture" className="border-y border-ent-border bg-ent-surface">
        <div className="mx-auto max-w-7xl px-6 py-24 lg:px-8 lg:py-28">
          {/* `min-w-0` on both columns is load-bearing, not tidiness: a grid item
              defaults to `min-width: auto`, so it refuses to shrink below its
              content. The code block below then forced the column wider than the
              viewport and gave the whole document a horizontal scrollbar on
              mobile, despite the block's own `overflow-x-auto`. */}
          <div className="grid gap-16 lg:grid-cols-2 lg:items-center">
            <div className="min-w-0">
              <SectionHead eyebrow="Architecture" title="Data flows one way, and stops at a gate">
                Providers produce candles, analysis describes them, strategy tests hypotheses
                against them, and execution acts — but only once the guardrail layer agrees. It
                is the single place in the codebase permitted to refuse an order, so every
                reason a trade can be rejected lives in one file.
              </SectionHead>

              <div className="mt-8 rounded-2xl border border-ent-border bg-white p-5">
                <div className="mb-3 font-mono text-[11px] uppercase tracking-[0.14em] text-ent-muted">
                  guardrails.evaluate()
                </div>
                <pre className="overflow-x-auto font-mono text-[12.5px] leading-[1.75] text-ent-slate">
                  <code>{`decision = guardrails.evaluate(request, context)

if not decision.allowed:
    # Refusals name the rule and the number
    # that broke it — "rejected" with no reason
    # trains people to disable the check.
    return decision.blocking_reasons`}</code>
                </pre>
              </div>
            </div>

            <div className="min-w-0 overflow-hidden rounded-3xl border border-ent-border bg-white p-6 shadow-[0_20px_60px_-30px_rgba(15,23,42,0.25)] lg:p-8">
              <ArchitectureStack />
            </div>
          </div>
        </div>
      </section>

      {/* ---------- principles ---------- */}
      <section id="principles" className="mx-auto max-w-7xl px-6 py-24 lg:px-8 lg:py-28">
        <SectionHead eyebrow="Principles" title="Rules the codebase is held to" center>
          These are not aspirations on a slide. Each one is enforced somewhere specific, and
          the reasoning is recorded so it does not get quietly reversed later.
        </SectionHead>

        <div className="mt-14 grid gap-5 md:grid-cols-2">
          {PRINCIPLES.map((principle, index) => (
            <Reveal key={principle.title} delay={(index % 2) * 80}>
              <div className="h-full rounded-2xl border border-ent-border bg-white p-8 transition-shadow duration-300 hover:shadow-[0_18px_44px_-18px_rgba(15,23,42,0.16)]">
                <div className="font-mono text-[11px] text-ent-blue">
                  {String(index + 1).padStart(2, "0")}
                </div>
                <h3 className="mt-4 font-display text-[19px] font-semibold tracking-[-0.015em] text-ent-navy">
                  {principle.title}
                </h3>
                <p className="mt-3 text-[15px] leading-[1.7] text-ent-muted">{principle.body}</p>
              </div>
            </Reveal>
          ))}
        </div>
      </section>

      {/* ---------- documentation ---------- */}
      <section id="docs" className="border-y border-ent-border bg-ent-surface">
        <div className="mx-auto max-w-7xl px-6 py-24 lg:px-8 lg:py-28">
          <SectionHead eyebrow="Documentation" title="Written to be read, not to exist">
            Every decision worth not re-litigating is recorded with its reasoning, and every
            unverified claim is labelled as unverified in the same paragraph that describes it.
          </SectionHead>

          <div className="mt-14 grid gap-5 md:grid-cols-3">
            {DOCS.map((doc, index) => (
              <Reveal key={doc.title} delay={index * 80}>
                <a
                  href={`https://github.com/nexarohc/Project-Legend-Trade/blob/main/${doc.file}`}
                  target="_blank"
                  rel="noreferrer"
                  className="group flex h-full flex-col rounded-2xl border border-ent-border
                             bg-white p-7 transition-all duration-300 hover:-translate-y-1
                             hover:border-ent-blue/30
                             hover:shadow-[0_18px_44px_-16px_rgba(15,23,42,0.18)]"
                >
                  <h3 className="font-display text-[17px] font-semibold text-ent-navy">
                    {doc.title}
                  </h3>
                  <p className="mt-2.5 flex-1 text-[14px] leading-[1.65] text-ent-muted">
                    {doc.body}
                  </p>
                  <div className="mt-6 flex items-center justify-between border-t border-ent-border pt-4">
                    <code className="font-mono text-[11px] text-ent-muted">{doc.file}</code>
                    <span className="text-[13px] font-semibold text-ent-blue transition-transform duration-300 group-hover:translate-x-0.5">
                      Read →
                    </span>
                  </div>
                </a>
              </Reveal>
            ))}
          </div>
        </div>
      </section>

      {/* ---------- disclosure ---------- */}
      <section id="disclosure" className="mx-auto max-w-7xl px-6 py-24 lg:px-8 lg:py-28">
        <div className="grid gap-14 lg:grid-cols-[0.9fr_1.1fr]">
          <SectionHead eyebrow="Disclosure" title="What this platform does not do">
            A platform that only advertises its strengths is telling you half the story. On a
            financial tool, that omission is not neutral — so the limitations are here, on the
            front page, rather than discovered later.
          </SectionHead>

          <Reveal delay={100}>
            <div className="divide-y divide-ent-border rounded-2xl border border-ent-border bg-white">
              {LIMITS.map(([title, body]) => (
                <div key={title} className="flex gap-4 p-6">
                  <span
                    aria-hidden="true"
                    className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-ent-warning"
                  />
                  <div>
                    <div className="font-display text-[15px] font-semibold text-ent-navy">
                      {title}
                    </div>
                    <p className="mt-1.5 text-[14px] leading-[1.65] text-ent-muted">{body}</p>
                  </div>
                </div>
              ))}
            </div>
          </Reveal>
        </div>
      </section>

      {/* ---------- faq ---------- */}
      <section className="border-y border-ent-border bg-ent-surface">
        <div className="mx-auto max-w-4xl px-6 py-24 lg:px-8 lg:py-28">
          <SectionHead eyebrow="FAQ" title="Questions worth answering plainly" center />
          <Reveal delay={80}>
            <div className="mt-12 rounded-2xl border border-ent-border bg-white px-7">
              {FAQ.map(([question, answer], index) => (
                <FaqItem key={question} question={question} answer={answer} index={index} />
              ))}
            </div>
          </Reveal>
        </div>
      </section>

      {/* ---------- final CTA ---------- */}
      <section className="mx-auto max-w-7xl px-6 py-24 lg:px-8 lg:py-28">
        <Reveal>
          <div className="relative overflow-hidden rounded-3xl bg-ent-navy px-8 py-16 text-center sm:px-16 sm:py-20">
            <div
              aria-hidden="true"
              className="pointer-events-none absolute inset-0
                         bg-[radial-gradient(ellipse_60%_70%_at_50%_0%,rgba(56,189,248,0.16),transparent)]"
            />
            <div
              aria-hidden="true"
              className="pointer-events-none absolute inset-0 opacity-[0.14]
                         [background-image:linear-gradient(rgba(255,255,255,0.5)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,0.5)_1px,transparent_1px)]
                         [background-size:56px_56px]
                         [mask-image:radial-gradient(ellipse_60%_60%_at_50%_50%,black,transparent)]"
            />
            <h2 className="relative font-display text-[2rem] font-bold leading-[1.12] tracking-[-0.03em] text-white sm:text-[2.75rem]">
              Open it and check the evidence yourself
            </h2>
            <p className="relative mx-auto mt-5 max-w-2xl text-[17px] leading-[1.7] text-slate-300">
              Paper trading is the default and needs no broker account. Nothing reaches real
              money without deliberate, separate steps.
            </p>
            <div className="relative mt-9 flex flex-wrap justify-center gap-3">
              <button
                onClick={onGetStarted}
                className="rounded-xl bg-white px-7 py-3 text-[15px] font-semibold text-ent-navy
                           transition-all hover:bg-slate-100 active:translate-y-px"
              >
                Get started
              </button>
              <a
                href="#docs"
                className="rounded-xl border border-white/20 px-7 py-3 text-[15px]
                           font-semibold text-white transition-colors hover:bg-white/10"
              >
                View documentation
              </a>
            </div>
          </div>
        </Reveal>
      </section>

      {/* ---------- footer ---------- */}
      <footer className="border-t border-ent-border bg-white">
        <div className="mx-auto max-w-7xl px-6 py-16 lg:px-8">
          <div className="grid gap-12 lg:grid-cols-[1.4fr_1fr_1fr_1fr]">
            <div>
              <div className="flex items-center gap-2.5">
                <img src={logo} alt="" className="h-8 w-8 rounded-lg" />
                <span className="font-display text-[16px] font-bold text-ent-navy">
                  Legend Trade
                </span>
              </div>
              <p className="mt-4 max-w-xs text-[14px] leading-[1.7] text-ent-muted">
                An institutional-grade analysis and execution platform. Market analysis, not
                financial advice.
              </p>
            </div>

            {[
              [
                "Platform",
                [
                  ["Domains", "#platform"],
                  ["Architecture", "#architecture"],
                  ["Principles", "#principles"],
                ],
              ],
              [
                "Documentation",
                [
                  [
                    "Trading terminal",
                    "https://github.com/nexarohc/Project-Legend-Trade/blob/main/docs/TRADING_TERMINAL.md",
                  ],
                  [
                    "Deployment",
                    "https://github.com/nexarohc/Project-Legend-Trade/blob/main/docs/DEPLOYMENT.md",
                  ],
                  [
                    "Project state",
                    "https://github.com/nexarohc/Project-Legend-Trade/blob/main/docs/PROJECT_STATE.md",
                  ],
                ],
              ],
              [
                "Legal",
                [
                  [
                    "Privacy",
                    "https://github.com/nexarohc/Project-Legend-Trade/blob/main/PRIVACY.md",
                  ],
                  ["Terms", "https://github.com/nexarohc/Project-Legend-Trade/blob/main/TERMS.md"],
                  ["Disclosure", "#disclosure"],
                ],
              ],
            ].map(([heading, links]) => (
              <div key={heading}>
                <div className="font-display text-[13px] font-semibold uppercase tracking-[0.1em] text-ent-navy">
                  {heading}
                </div>
                <ul className="mt-4 space-y-3">
                  {links.map(([label, href]) => (
                    <li key={label}>
                      <a
                        href={href}
                        {...(href.startsWith("http")
                          ? { target: "_blank", rel: "noreferrer" }
                          : {})}
                        className="text-[14px] text-ent-muted transition-colors hover:text-ent-blue"
                      >
                        {label}
                      </a>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>

          <div className="mt-14 flex flex-col items-start justify-between gap-4 border-t border-ent-border pt-8 sm:flex-row sm:items-center">
            <span className="text-[13px] text-ent-muted">
              © {new Date().getFullYear()} Legend Trade. All rights reserved.
            </span>
            <span className="text-[13px] text-ent-muted">
              Paper trading by default · Live execution is off unless you turn it on
            </span>
          </div>
        </div>
      </footer>
    </div>
  );
}
