import { useEffect, useRef, useState } from "react";

/**
 * The hero visual: the platform's real layer stack, with data flowing down it.
 *
 * The brief asked for an animated architecture diagram. The temptation with
 * those is to draw a generic seven-box tower that could belong to any platform.
 * This one is the *actual* pipeline — the layers below correspond to real
 * modules in this repository, in the order a request passes through them, and
 * the bottom layer is the guardrail stage that can refuse. A diagram that
 * flatters the architecture rather than describing it is worse than no diagram,
 * because someone will eventually check.
 *
 * The flow pulse walks one layer at a time rather than animating all of them at
 * once: a stack where everything glows simultaneously reads as decoration,
 * where a single travelling pulse reads as a request being served.
 *
 * Under `prefers-reduced-motion` the pulse stops on the first layer and the
 * diagram renders complete and legible — the information is in the boxes, not
 * in the movement.
 */

const LAYERS = [
  {
    id: "data",
    label: "Market Data",
    detail: "8 providers · automatic failover",
    module: "trading/providers",
  },
  {
    id: "analysis",
    label: "Analysis",
    detail: "20 sections · structure, volume, SMC, levels",
    module: "trading/analysis",
  },
  {
    id: "probability",
    label: "Probability",
    detail: "Base rates + individually weighted factors",
    module: "trading/probability",
  },
  {
    id: "strategy",
    label: "Strategy & Audit",
    detail: "Backtest · Monte Carlo · walk-forward · 15 checks",
    module: "trading/strategy",
  },
  {
    id: "guardrails",
    label: "Guardrails",
    detail: "The only layer permitted to refuse",
    module: "trading/execution/guardrails",
    terminal: true,
  },
];

export default function ArchitectureStack() {
  const [active, setActive] = useState(0);
  const containerRef = useRef(null);

  useEffect(() => {
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return undefined;

    // Pause while off-screen. A diagram animating in a viewport nobody is
    // looking at is pure battery cost.
    let visible = true;
    let observer;
    if (typeof IntersectionObserver !== "undefined" && containerRef.current) {
      observer = new IntersectionObserver(
        ([entry]) => {
          visible = entry.isIntersecting;
        },
        { threshold: 0.15 },
      );
      observer.observe(containerRef.current);
    }

    const id = window.setInterval(() => {
      if (!visible || document.hidden) return;
      setActive((current) => (current + 1) % LAYERS.length);
    }, 1500);

    return () => {
      window.clearInterval(id);
      observer?.disconnect();
    };
  }, []);

  return (
    <div ref={containerRef} className="relative">
      {/* Soft platform glow behind the stack. Static, not animated — the
          movement in this composition should be the pulse, nothing else. */}
      {/* The negative inset is what makes the glow read as light spilling from
          behind the stack rather than as a box around it. It is smaller on
          narrow screens because at 390px a 32px bleed pushes past the viewport
          and gives the whole document a horizontal scrollbar — caught by
          measuring every element's bounding box against the client width, not
          by looking at it. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -inset-3 rounded-[2rem] sm:-inset-8
                   bg-[radial-gradient(ellipse_at_50%_30%,rgba(37,99,235,0.10),transparent_70%)]"
      />

      <div className="relative space-y-2.5">
        <div className="mb-4 flex items-center justify-between px-1">
          <span className="font-mono text-[11px] uppercase tracking-[0.16em] text-ent-muted">
            Request path
          </span>
          <span className="font-mono text-[11px] text-ent-muted">top → bottom</span>
        </div>

        {LAYERS.map((layer, index) => {
          const isActive = index === active;
          return (
            <div key={layer.id}>
              <div
                className={`group relative overflow-hidden rounded-2xl border bg-white p-4
                            transition-all duration-500 sm:p-5 ${
                              isActive
                                ? "border-ent-blue/35 shadow-[0_12px_40px_-12px_rgba(37,99,235,0.35)]"
                                : "border-ent-border shadow-[0_1px_2px_rgba(15,23,42,0.04)]"
                            }`}
              >
                {/* A single sheen sweeping across the active layer. Keyed on the
                    active index so it replays each time the pulse arrives. */}
                {isActive && (
                  <span
                    key={active}
                    aria-hidden="true"
                    className="pointer-events-none absolute inset-y-0 -left-1/3 w-1/3 animate-sheen
                               bg-gradient-to-r from-transparent via-ent-blue/[0.07] to-transparent"
                  />
                )}

                <div className="relative flex items-center gap-4">
                  <span
                    className={`grid h-9 w-9 shrink-0 place-items-center rounded-xl border
                                font-mono text-[11px] transition-colors duration-500 ${
                                  isActive
                                    ? "border-ent-blue/30 bg-ent-blue/10 text-ent-blue"
                                    : "border-ent-border bg-ent-surface text-ent-muted"
                                }`}
                  >
                    {String(index + 1).padStart(2, "0")}
                  </span>

                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                      <span className="font-display text-[15px] font-semibold text-ent-ink">
                        {layer.label}
                      </span>
                      <code className="font-mono text-[10px] text-ent-muted">{layer.module}</code>
                    </div>
                    <p className="mt-1 text-[13px] leading-relaxed text-ent-muted">
                      {layer.detail}
                    </p>
                  </div>

                  {layer.terminal && (
                    <span
                      className="hidden shrink-0 rounded-lg border border-ent-warning/30
                                 bg-ent-warning/10 px-2.5 py-1 font-mono text-[10px]
                                 uppercase tracking-[0.12em] text-[#B45309] sm:inline"
                    >
                      Can refuse
                    </span>
                  )}
                </div>
              </div>

              {/* Connector. The dash animation runs only on the segment the
                  pulse is currently crossing. */}
              {index < LAYERS.length - 1 && (
                <div className="flex justify-center py-1" aria-hidden="true">
                  <svg width="2" height="22" viewBox="0 0 2 22" fill="none">
                    <line
                      x1="1"
                      y1="0"
                      x2="1"
                      y2="22"
                      stroke="#E2E8F0"
                      strokeWidth="2"
                      strokeLinecap="round"
                    />
                    {index === active && (
                      <line
                        x1="1"
                        y1="0"
                        x2="1"
                        y2="22"
                        stroke="#2563EB"
                        strokeWidth="2"
                        strokeLinecap="round"
                        strokeDasharray="8 20"
                        className="animate-flow-down"
                      />
                    )}
                  </svg>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
