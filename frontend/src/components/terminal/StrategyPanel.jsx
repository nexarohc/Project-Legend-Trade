import { useState } from "react";
import { formatNumber, formatPercent, trading } from "../../lib/tradingApi.js";

/**
 * Strategy workspace: describe a strategy in English, see exactly how it was
 * interpreted, backtest it, and read the audit before anything else.
 *
 * The audit verdict is shown above the performance numbers on purpose. A
 * headline return means nothing until you know whether the backtest that
 * produced it is believable, and putting the profit first invites people to
 * stop reading there.
 */
export default function StrategyPanel({ symbol, timeframe }) {
  const [description, setDescription] = useState(
    "Buy pullback in an uptrend with increasing volume, adx above 25, 1.5 ATR stop, 3R target, risk 1%",
  );
  const [spec, setSpec] = useState(null);
  const [study, setStudy] = useState(null);
  const [pine, setPine] = useState(null);
  const [tab, setTab] = useState("build");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [copied, setCopied] = useState(false);

  const run = async (fn) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const build = () =>
    run(async () => {
      const result = await trading.buildStrategy({ description, symbol, timeframe });
      setSpec(result);
      setStudy(null);
      setPine(null);
      setTab("build");
    });

  const backtest = () =>
    run(async () => {
      let current = spec;
      if (!current) {
        current = await trading.buildStrategy({ description, symbol, timeframe });
        setSpec(current);
      }
      const result = await trading.studyStrategy({
        spec: current.spec,
        symbol,
        timeframe,
        bars: 2000,
        include_trades: false,
      });
      setStudy(result);
      setTab("results");
    });

  const generatePine = () =>
    run(async () => {
      let current = spec;
      if (!current) {
        current = await trading.buildStrategy({ description, symbol, timeframe });
        setSpec(current);
      }
      setPine(await trading.pine({ spec: current.spec, kind: "strategy" }));
      setTab("pine");
    });

  const copyPine = async () => {
    if (!pine?.code) return;
    try {
      await navigator.clipboard.writeText(pine.code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setError("Clipboard access was blocked. Select the code and copy it manually.");
    }
  };

  return (
    <div className="flex flex-col h-full bg-term-panel">
      <div className="p-2 border-b border-term-border">
        <textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          rows={3}
          placeholder="Describe a strategy in plain English…"
          className="w-full bg-term-raised border border-term-border rounded px-2 py-1.5 text-[11px]
                     text-term-text placeholder:text-term-dim resize-none leading-relaxed
                     focus:outline-none focus:border-brand-accent"
        />
        <div className="flex gap-1 mt-1.5">
          <button onClick={build} disabled={busy} className="btn-term text-[10px]">Build</button>
          <button onClick={backtest} disabled={busy} className="btn-term-primary text-[10px]">
            Backtest &amp; audit
          </button>
          <button onClick={generatePine} disabled={busy} className="btn-term text-[10px]">Pine</button>
          <span className="ml-auto text-[10px] font-mono text-term-dim self-center">
            {symbol} · {timeframe}
          </span>
        </div>
      </div>

      {busy && (
        <div className="p-3 text-[11px] text-term-muted animate-pulse">
          Running… a full study backtests the strategy, then re-runs it across walk-forward
          windows, market regimes and perturbed parameters.
        </div>
      )}

      {error && (
        <div className="m-2 p-2 rounded border border-term-down/40 bg-term-down/10">
          <p className="text-[11px] text-term-down leading-relaxed">{error}</p>
        </div>
      )}

      {(spec || study || pine) && (
        <div className="flex border-b border-term-border">
          {[
            ["build", "Spec"],
            ["results", "Results"],
            ["pine", "Pine"],
          ].map(([key, label]) => (
            <button
              key={key}
              onClick={() => setTab(key)}
              className={`px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wide transition-colors ${
                tab === key
                  ? "text-term-text border-b-2 border-brand-accent"
                  : "text-term-dim hover:text-term-muted"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      )}

      <div className="overflow-y-auto flex-1">
        {tab === "build" && spec && <SpecView result={spec} />}
        {tab === "results" && study && <StudyView study={study} />}
        {tab === "pine" && pine && (
          <PineView pine={pine} onCopy={copyPine} copied={copied} />
        )}
        {!spec && !study && !pine && !busy && (
          <div className="p-4 text-[11px] text-term-dim leading-relaxed space-y-2">
            <p>
              Describe a strategy above and press Build. The parser reports exactly which phrases it
              understood and which it ignored — nothing gets into the strategy silently.
            </p>
            <p className="text-term-muted font-medium">Vocabulary it recognises:</p>
            <ul className="space-y-0.5 pl-3">
              <li>· setups: breakout, pullback, mean reversion, MA crossover, MACD, Bollinger, SuperTrend</li>
              <li>· filters: ADX above N, volume, VWAP, above the 200</li>
              <li>· risk: N ATR stop, N% stop, 1:N risk reward, NR target, risk N%</li>
              <li>· management: breakeven at NR, trailing N ATR, close after N bars</li>
              <li>· sessions: London, New York, Asian, Tokyo</li>
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */

function SpecView({ result }) {
  const { spec, specification, valid, problems, parsed_by } = result;

  return (
    <div className="p-3 space-y-3">
      {!valid && (
        <div className="p-2 rounded border border-term-down/40 bg-term-down/10">
          {problems.map((p, i) => (
            <p key={i} className="text-[11px] text-term-down leading-relaxed">{p}</p>
          ))}
        </div>
      )}

      {parsed_by === "model" && (
        <div className="p-2 rounded border border-term-warn/40 bg-term-warn/10">
          <p className="text-[10px] text-term-warn leading-relaxed">
            The built-in parser did not recognise this description, so a language model drafted the
            rules. Read them carefully before trusting the backtest.
          </p>
        </div>
      )}

      <Block title="Entry logic">
        <RuleList label="Long" rules={specification.entry_logic.long} tone="up" />
        <RuleList label="Short" rules={specification.entry_logic.short} tone="down" />
        <p className="text-[10px] text-term-dim mt-1">{specification.entry_logic.note}</p>
      </Block>

      <Block title="Exit logic">
        <RuleList label="Long" rules={specification.exit_logic.long} />
        <RuleList label="Short" rules={specification.exit_logic.short} />
      </Block>

      <Block title="Risk">
        <Line label="Stop" value={specification.stop_logic} />
        <Line label="Targets" value={specification.take_profit_logic} />
        <Line label="Sizing" value={specification.position_sizing} />
        <Line label="Costs" value={specification.costs} />
        <Line label="Filters" value={specification.filters.join("; ")} />
        <Line label="Session" value={specification.session_filter} />
        <Line label="Parameters" value={`${specification.parameter_count} free parameters`} />
      </Block>

      {specification.risk_management?.length > 0 && (
        <Block title="Trade management">
          {specification.risk_management.map((r, i) => (
            <p key={i} className="text-[11px] text-term-muted leading-relaxed">· {r}</p>
          ))}
        </Block>
      )}

      <Block title="How your description was read">
        {spec.parse_notes.map((n, i) => (
          <p key={i} className="text-[10px] text-term-muted leading-relaxed">· {n}</p>
        ))}
        {spec.unparsed_phrases?.length > 0 && (
          <p className="text-[10px] text-term-warn leading-relaxed mt-1">
            Ignored: {spec.unparsed_phrases.join(", ")}
          </p>
        )}
      </Block>
    </div>
  );
}

function StudyView({ study }) {
  const { metrics, audit, monte_carlo, walk_forward, regime_test, parameter_stability, backtest } = study;

  const verdictTone = {
    APPROVED: "border-term-up bg-term-up/10 text-term-up",
    REVISE: "border-term-warn bg-term-warn/10 text-term-warn",
    REJECT: "border-term-down bg-term-down/10 text-term-down",
  }[audit.verdict];

  return (
    <div className="p-3 space-y-3">
      {/* Audit first — it decides whether the numbers below mean anything. */}
      <div className={`p-3 rounded border ${verdictTone}`}>
        <div className="flex items-center justify-between mb-1">
          <span className="text-sm font-bold tracking-wide">{audit.verdict}</span>
          <span className="text-xs font-mono opacity-80">{formatNumber(audit.score, 0)}/100</span>
        </div>
        <p className="text-[11px] leading-relaxed opacity-90">{audit.summary}</p>
      </div>

      <Block title="Performance">
        <div className="grid grid-cols-2 gap-x-3 gap-y-0.5">
          <Stat label="Trades" value={metrics.total_trades} />
          <Stat label="Win rate" value={`${formatNumber(metrics.win_rate, 1)}%`} />
          <Stat label="Net profit" value={formatPercent(metrics.net_profit_percent)}
                tone={metrics.net_profit_percent >= 0 ? "up" : "down"} />
          <Stat label="Annual return" value={formatPercent(metrics.annual_return_percent)} />
          <Stat label="Profit factor" value={metrics.profit_factor ?? "undefined"} />
          <Stat label="Expectancy" value={`${formatNumber(metrics.expectancy_r, 3)}R`} />
          <Stat label="Sharpe" value={metrics.sharpe_ratio ?? "—"} />
          <Stat label="Sortino" value={metrics.sortino_ratio ?? "—"} />
          <Stat label="Calmar" value={metrics.calmar_ratio ?? "—"} />
          <Stat label="Max drawdown" value={`${formatNumber(metrics.max_drawdown_percent, 1)}%`} tone="down" />
          <Stat label="Largest win" value={formatNumber(metrics.largest_win)} tone="up" />
          <Stat label="Largest loss" value={formatNumber(metrics.largest_loss)} tone="down" />
          <Stat label="Avg hold" value={metrics.average_hold_time} />
          <Stat label="Worst streak" value={`${metrics.max_consecutive_losses} losses`} />
          <Stat label="Long / short" value={`${metrics.long_trades} / ${metrics.short_trades}`} />
          <Stat label="Total costs" value={formatNumber(metrics.total_costs)} />
        </div>
        {metrics.cost_as_percent_of_gross !== null && (
          <p className="text-[10px] text-term-dim mt-1">
            Costs consumed {formatNumber(metrics.cost_as_percent_of_gross, 0)}% of gross profit.
          </p>
        )}
        {Object.entries(metrics.undefined ?? {}).map(([key, reason]) => (
          <p key={key} className="text-[10px] text-term-warn leading-relaxed mt-1">
            {key}: {reason}
          </p>
        ))}
      </Block>

      <Block title="Trade distribution (R multiples)">
        {Object.entries(metrics.r_multiple_distribution ?? {}).map(([bucket, count]) => (
          <div key={bucket} className="flex items-center gap-2 mb-0.5">
            <span className="text-[10px] font-mono text-term-dim w-20 shrink-0">{bucket}</span>
            <div className="flex-1 h-1.5 bg-term-raised rounded-sm overflow-hidden">
              <div
                className="h-full rounded-sm"
                style={{
                  width: `${metrics.total_trades ? (count / metrics.total_trades) * 100 : 0}%`,
                  backgroundColor: bucket.startsWith("<") || bucket.startsWith("-") ? "#ef5350" : "#26a69a",
                }}
              />
            </div>
            <span className="text-[10px] font-mono text-term-text w-6 text-right">{count}</span>
          </div>
        ))}
      </Block>

      {monte_carlo && (
        <Block title="Monte Carlo">
          <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 mb-1">
            <Stat label="Simulations" value={formatNumber(monte_carlo.simulations, 0)} />
            <Stat label="Median return" value={formatPercent(monte_carlo.median_return_percent)} />
            <Stat label="5th pct" value={formatNumber(monte_carlo.percentile_5)} />
            <Stat label="95th pct" value={formatNumber(monte_carlo.percentile_95)} />
            <Stat label="Median DD" value={`${formatNumber(monte_carlo.median_max_drawdown, 1)}%`} />
            <Stat label="95th pct DD" value={`${formatNumber(monte_carlo.drawdown_percentile_95, 1)}%`} tone="down" />
            <Stat label="Prob. of ruin" value={`${formatNumber(monte_carlo.probability_of_ruin, 1)}%`}
                  tone={monte_carlo.probability_of_ruin > 5 ? "down" : undefined} />
            <Stat label="Prob. of loss" value={`${formatNumber(monte_carlo.probability_of_loss, 1)}%`} />
          </div>
          {monte_carlo.interpretation.map((line, i) => (
            <p key={i} className="text-[10px] text-term-muted leading-relaxed mb-0.5">· {line}</p>
          ))}
        </Block>
      )}

      {walk_forward && (
        <Block title="Walk-forward">
          {walk_forward.findings.map((f, i) => (
            <p key={i} className="text-[10px] text-term-muted leading-relaxed mb-0.5">· {f}</p>
          ))}
        </Block>
      )}

      {regime_test && (
        <Block title="Regime robustness">
          {regime_test.findings.map((f, i) => (
            <p key={i} className="text-[10px] text-term-muted leading-relaxed mb-0.5">· {f}</p>
          ))}
        </Block>
      )}

      {parameter_stability && (
        <Block title="Parameter stability">
          {parameter_stability.findings.map((f, i) => (
            <p key={i} className="text-[10px] text-term-muted leading-relaxed mb-0.5">· {f}</p>
          ))}
        </Block>
      )}

      <Block title={`Audit — ${audit.checks.length} checks`}>
        {audit.checks.map((c, i) => (
          <div key={i} className="mb-2 pl-2 border-l border-term-border">
            <div className="flex items-baseline gap-2">
              <span
                className={`text-[9px] font-bold uppercase px-1 rounded ${
                  {
                    pass: "bg-term-up/20 text-term-up",
                    info: "bg-term-info/20 text-term-info",
                    warning: "bg-term-warn/20 text-term-warn",
                    critical: "bg-term-down/20 text-term-down",
                  }[c.severity]
                }`}
              >
                {c.severity}
              </span>
              <span className="text-[11px] text-term-text font-medium">{c.name}</span>
            </div>
            <p className="text-[10px] text-term-muted leading-relaxed mt-0.5">{c.finding}</p>
            <p className="text-[10px] text-term-dim leading-relaxed">{c.evidence}</p>
            {c.recommendation && (
              <p className="text-[10px] text-term-info leading-relaxed mt-0.5">
                → {c.recommendation}
              </p>
            )}
          </div>
        ))}
      </Block>

      <Block title="Execution model">
        {backtest.execution_notes?.map((n, i) => (
          <p key={i} className="text-[10px] text-term-dim leading-relaxed mb-0.5">· {n}</p>
        ))}
      </Block>
    </div>
  );
}

function PineView({ pine, onCopy, copied }) {
  return (
    <div className="p-3 space-y-3">
      <div className="flex items-center gap-2">
        <span className="text-[11px] text-term-muted">
          Pine Script v{pine.version} · {pine.line_count} lines
        </span>
        <button onClick={onCopy} className="btn-term-primary text-[10px] ml-auto">
          {copied ? "Copied" : "Copy"}
        </button>
      </div>

      <pre className="bg-term-bg border border-term-border rounded p-2 overflow-x-auto max-h-96
                      text-[10px] font-mono text-term-text leading-relaxed">
        {pine.code}
      </pre>

      <Block title="What this script does">
        {pine.explanation.map((line, i) => (
          <p key={i} className="text-[10px] text-term-muted leading-relaxed mb-0.5">· {line}</p>
        ))}
      </Block>

      <Block title="Install">
        {pine.install_steps.map((step, i) => (
          <p key={i} className="text-[10px] text-term-muted leading-relaxed mb-0.5">
            {i + 1}. {step}
          </p>
        ))}
      </Block>

      <Block title="Notes">
        {pine.notes.map((note, i) => (
          <p key={i} className="text-[10px] text-term-warn leading-relaxed mb-0.5">· {note}</p>
        ))}
      </Block>
    </div>
  );
}

/* ------------------------------------------------------------------ */

function Block({ title, children }) {
  return (
    <div>
      <p className="text-[10px] uppercase tracking-wide text-term-dim mb-1">{title}</p>
      <div className="pl-1">{children}</div>
    </div>
  );
}

function RuleList({ label, rules, tone }) {
  const color = { up: "text-term-up", down: "text-term-down" }[tone] ?? "text-term-muted";
  return (
    <div className="mb-1">
      <span className={`text-[10px] font-semibold ${color}`}>{label}</span>
      {rules.map((rule, i) => (
        <p key={i} className="text-[11px] text-term-text font-mono leading-relaxed pl-2">{rule}</p>
      ))}
    </div>
  );
}

function Line({ label, value }) {
  return (
    <div className="flex gap-2 mb-0.5">
      <span className="text-[10px] text-term-dim w-20 shrink-0">{label}</span>
      <span className="text-[10px] text-term-muted leading-relaxed flex-1">{value || "—"}</span>
    </div>
  );
}

function Stat({ label, value, tone }) {
  const color = { up: "text-term-up", down: "text-term-down" }[tone] ?? "text-term-text";
  return (
    <div className="flex justify-between items-baseline gap-2">
      <span className="text-[10px] text-term-dim truncate">{label}</span>
      <span className={`text-[10px] font-mono ${color} text-right`}>{value ?? "—"}</span>
    </div>
  );
}
