import { useMemo, useState } from "react";

/**
 * An interactive configuration builder, not a feature list with switches on it.
 *
 * A row of toggles that only animate is a lie told in CSS: it implies the
 * visitor is changing something when nothing anywhere is different afterwards.
 * These switches drive a real artefact — the `.env` block below updates as you
 * flip them, and it is the actual file the server reads, with the actual
 * variable names. Someone can set up an instance by using this and pressing
 * copy.
 *
 * That also makes the section honest about defaults. Live trading is off and
 * takes three separate deliberate acts to turn on; open signup is off; auth
 * enforces itself the moment the server is reachable from anywhere but its own
 * machine. Those are the product's real positions, and showing them as
 * switches someone has to move themselves states them better than a paragraph.
 */

const CAPABILITIES = [
  {
    id: "live",
    label: "Live broker execution",
    blurb:
      "Sends real orders with real money. Off by default, and this variable is only "
      + "the first of four gates — the mode switch, a typed confirmation phrase and "
      + "separate credentials all still apply.",
    env: "LEGEND_ENABLE_LIVE_TRADING",
    on: "true",
    off: "false",
    initial: false,
    danger: true,
  },
  {
    id: "auth",
    label: "Always require sign-in",
    blurb:
      "Force authentication even on localhost. Left on `auto`, the server enforces it "
      + "automatically as soon as it is bound to anything other than its own loopback.",
    env: "LEGEND_AUTH_REQUIRED",
    on: "always",
    off: "auto",
    initial: false,
  },
  {
    id: "signup",
    label: "Open registration",
    blurb:
      "Lets anyone create an account. Off means the first account claims the instance "
      + "and no more can be made — the right answer for a terminal that is yours.",
    env: "LEGEND_ALLOW_SIGNUP",
    on: "true",
    off: "false",
    initial: false,
  },
  {
    id: "proxy",
    label: "Trust proxy headers",
    blurb:
      "Read the client IP from X-Forwarded-For. Turn this on only behind a reverse proxy "
      + "you control: anywhere else it lets a caller forge the address that rate limiting keys on.",
    env: "LEGEND_TRUST_PROXY_HEADERS",
    on: "true",
    off: "false",
    initial: false,
  },
  {
    id: "metrics",
    label: "Prometheus metrics",
    blurb:
      "Exposes /metrics with request counts and latencies by route template, so cardinality "
      + "stays bounded no matter how many symbols get charted.",
    env: "METRICS_ENABLED",
    on: "true",
    off: "false",
    initial: true,
  },
  {
    id: "json",
    label: "Structured JSON logging",
    blurb:
      "One JSON object per line instead of prose, including the web server's own access "
      + "lines. What a log shipper needs; harder for a human to skim.",
    env: "LOG_FORMAT",
    on: "json",
    off: "text",
    initial: false,
  },
  {
    id: "redis",
    label: "Shared rate limiting",
    blurb:
      "Point at Redis and rate-limit buckets are shared across workers. Without it the "
      + "in-process limiter runs and limits apply per worker, which is correct for a single instance.",
    env: "REDIS_URL",
    on: "redis://localhost:6379/0",
    off: "",
    initial: false,
  },
  {
    id: "smtp",
    label: "Outbound email",
    blurb:
      "Delivers password-reset and verification links. Without it those links are written "
      + "to the server log instead, which means the operator can read them.",
    env: "SMTP_HOST",
    on: "smtp.example.com",
    off: "",
    initial: false,
  },
];

/** The sliding switch itself. A real button, so it is keyboard-reachable. */
function Switch({ on, danger, onChange, label }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      onClick={() => onChange(!on)}
      className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full
                  transition-colors duration-200 focus:outline-none focus-visible:ring-2
                  focus-visible:ring-offset-2 focus-visible:ring-offset-transparent
                  ${on
                    ? danger
                      ? "bg-ent-error focus-visible:ring-ent-error"
                      : "bg-ent-blue focus-visible:ring-ent-blue"
                    : "bg-ent-muted/35 focus-visible:ring-ent-muted"}`}
    >
      <span
        className={`inline-block h-[1.15rem] w-[1.15rem] transform rounded-full bg-white shadow
                    transition-transform duration-200 ease-out
                    ${on ? "translate-x-[1.45rem]" : "translate-x-[0.175rem]"}`}
      />
    </button>
  );
}

export default function CapabilityToggles() {
  const [state, setState] = useState(() =>
    Object.fromEntries(CAPABILITIES.map((c) => [c.id, c.initial])));
  const [copied, setCopied] = useState(false);

  const envText = useMemo(
    () =>
      CAPABILITIES.map((c) => `${c.env}=${state[c.id] ? c.on : c.off}`).join("\n"),
    [state],
  );

  const liveOn = state.live;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(`${envText}\n`);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);   // clipboard blocked; the text is selectable anyway
    }
  };

  return (
    <div className="grid gap-8 lg:grid-cols-[1.15fr_1fr] lg:gap-10">
      <div className="divide-y divide-ent-border rounded-2xl border border-ent-border bg-ent-card">
        {CAPABILITIES.map((c) => {
          const on = state[c.id];
          return (
            <div key={c.id} className="flex items-start gap-4 p-5">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <h3 className="text-[15px] font-semibold text-ent-navy">{c.label}</h3>
                  {c.danger && on && (
                    <span className="rounded-full bg-ent-error/12 px-2 py-0.5 font-mono text-[10px]
                                     font-semibold uppercase tracking-wider text-ent-error">
                      real money
                    </span>
                  )}
                </div>
                <p className="mt-1.5 text-[13px] leading-relaxed text-ent-muted">{c.blurb}</p>
                <code className="mt-2 inline-block font-mono text-[11px] text-ent-muted/80">
                  {c.env}
                </code>
              </div>
              <div className="pt-1">
                <Switch
                  on={on}
                  danger={c.danger}
                  label={c.label}
                  onChange={(next) => setState((s) => ({ ...s, [c.id]: next }))}
                />
              </div>
            </div>
          );
        })}
      </div>

      <div className="lg:sticky lg:top-24 lg:self-start">
        <div className="overflow-hidden rounded-2xl border border-ent-border bg-[#0B0F17]">
          <div className="flex items-center justify-between border-b border-white/10 px-5 py-3">
            <span className="font-mono text-[11px] uppercase tracking-[0.18em] text-slate-400">
              .env
            </span>
            <button
              type="button"
              onClick={copy}
              className="rounded-md border border-white/15 px-2.5 py-1 font-mono text-[11px]
                         text-slate-300 transition-colors hover:border-white/35 hover:text-white"
            >
              {copied ? "copied" : "copy"}
            </button>
          </div>
          <pre className="overflow-x-auto px-5 py-4 font-mono text-[12.5px] leading-relaxed text-slate-300">
            {CAPABILITIES.map((c) => {
              const value = state[c.id] ? c.on : c.off;
              return (
                <div key={c.id}>
                  <span className="text-sky-300">{c.env}</span>
                  <span className="text-slate-500">=</span>
                  <span className={c.danger && state[c.id] ? "text-red-400" : "text-emerald-300"}>
                    {value}
                  </span>
                </div>
              );
            })}
          </pre>
        </div>

        {/* The warning is conditional rather than permanent. A caution that is
            always on screen stops being read; one that appears the moment you
            arm the dangerous switch is still information. */}
        {liveOn ? (
          <p className="mt-4 rounded-xl border border-ent-error/30 bg-ent-error/[0.06] px-4 py-3
                        text-[13px] leading-relaxed text-ent-navy">
            <strong className="font-semibold">This variable alone changes nothing.</strong>{" "}
            Live mode additionally needs a server restart, an explicit mode switch, a typed
            confirmation phrase and separate live credentials. No order has ever been sent to a
            broker from this codebase — start on a paper account.
          </p>
        ) : (
          <p className="mt-4 px-1 text-[13px] leading-relaxed text-ent-muted">
            These are the real defaults, not a sample. Copy the block into <code
              className="font-mono text-[12px]">.env</code> and the server reads it on next start.
          </p>
        )}
      </div>
    </div>
  );
}
