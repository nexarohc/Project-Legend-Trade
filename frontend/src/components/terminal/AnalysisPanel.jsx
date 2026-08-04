import { useState } from "react";
import { formatNumber, formatPercent, formatPrice } from "../../lib/tradingApi.js";

const WYCKOFF_TONE = {
  spring: "up",
  sign_of_strength: "up",
  last_point_of_support: "up",
  upthrust: "down",
  sign_of_weakness: "down",
  last_point_of_supply: "down",
  test: "warn",
};

/**
 * The analysis report.
 *
 * Structured so the evidence is never more than one click from the
 * conclusion — every probability, level and setup carries the reasoning that
 * produced it. Sections render in the same order the backend computes them:
 * context first, prediction last.
 */
export default function AnalysisPanel({ analysis, loading, error, onRun, onFeedback, narrative }) {
  const [open, setOpen] = useState({ summary: true, context: true, probability: true, setup: true });

  const toggle = (key) => setOpen((prev) => ({ ...prev, [key]: !prev[key] }));

  if (loading) {
    return (
      <PanelShell>
        <div className="p-6 text-center text-term-muted text-sm">
          <div className="animate-pulse">Analysing market structure…</div>
          <p className="text-xs text-term-dim mt-2">
            Computing context, structure, volume, SMC, levels, then probabilities.
          </p>
        </div>
      </PanelShell>
    );
  }

  if (error) {
    return (
      <PanelShell>
        <div className="p-4">
          <p className="text-term-down text-sm font-medium mb-1">Analysis failed</p>
          <p className="text-term-muted text-xs leading-relaxed mb-3">{error}</p>
          <button onClick={onRun} className="btn-term">Try again</button>
        </div>
      </PanelShell>
    );
  }

  if (!analysis) {
    return (
      <PanelShell>
        <div className="p-6 text-center">
          <p className="text-term-muted text-sm mb-1">No analysis yet</p>
          <p className="text-term-dim text-xs mb-4 leading-relaxed">
            Runs the full pipeline: market context, structure, price action, volume, 15 indicators,
            Smart Money Concepts, key levels, probabilities, trade setup and risk.
          </p>
          <button onClick={onRun} className="btn-term-primary">Run analysis</button>
        </div>
      </PanelShell>
    );
  }

  const {
    executive_summary, market_context, trend_analysis, market_structure, smart_money,
    volume_analysis, indicator_analysis, key_levels, price_action, probability_analysis,
    trade_setup, risk_review, strengths, weaknesses, improvements, verdict, warnings,
  } = analysis;

  return (
    <PanelShell>
      <div className="overflow-y-auto flex-1">
        {warnings?.length > 0 && (
          <div className="m-2 p-2 rounded border border-term-warn/40 bg-term-warn/10">
            {warnings.map((w, i) => (
              <p key={i} className="text-[11px] text-term-warn leading-relaxed">{w}</p>
            ))}
          </div>
        )}

        <Verdict verdict={verdict} />

        <Section title="1. Executive Summary" open={open.summary} onToggle={() => toggle("summary")}>
          <p className="text-xs text-term-text leading-relaxed">{executive_summary}</p>
        </Section>

        {narrative?.narrative && (
          <Section title="Narrative Walkthrough" open={open.narrative} onToggle={() => toggle("narrative")}>
            <p className="text-xs text-term-text leading-relaxed whitespace-pre-wrap">
              {narrative.narrative}
            </p>
            {narrative.note && (
              <p className="text-[10px] text-term-dim mt-2 italic">{narrative.note}</p>
            )}
          </Section>
        )}

        <Section title="2. Market Context" open={open.context} onToggle={() => toggle("context")}>
          <Grid>
            <Stat label="Trend" value={market_context.trend} tone={toneFor(market_context.trend)} />
            <Stat label="Strength" value={market_context.trend_strength_label} />
            <Stat label="Momentum" value={market_context.momentum} tone={toneFor(market_context.momentum)} />
            <Stat label="Volatility" value={`${market_context.volatility} (${formatNumber(market_context.volatility_percentile, 0)}th pct)`} />
            <Stat label="Liquidity" value={market_context.liquidity} />
            <Stat label="Regime" value={market_context.regime} />
            <Stat label="HTF bias" value={market_context.htf_bias} tone={toneFor(market_context.htf_bias)} />
            <Stat label="LTF bias" value={market_context.ltf_bias} tone={toneFor(market_context.ltf_bias)} />
            <Stat label="Institutional" value={market_context.institutional_bias} />
          </Grid>
          <Evidence items={market_context.evidence} />
        </Section>

        <Section title="3. Trend Analysis" open={open.trend} onToggle={() => toggle("trend")}>
          <Grid>
            <Stat label="Internal" value={trend_analysis.internal} tone={toneFor(trend_analysis.internal)} />
            <Stat label="External" value={trend_analysis.external} tone={toneFor(trend_analysis.external)} />
            <Stat label="Phase" value={trend_analysis.phase?.replace(/_/g, " ")} />
            <Stat label="Condition" value={trend_analysis.condition} />
          </Grid>
          <Evidence items={trend_analysis.evidence} />
        </Section>

        <Section title="4. Market Structure" open={open.structure} onToggle={() => toggle("structure")}>
          <Grid>
            <Stat label="Swings" value={market_structure.swings?.length ?? 0} />
            <Stat label="BOS / CHOCH" value={market_structure.events?.length ?? 0} />
            <Stat label="Sweeps" value={market_structure.sweeps?.length ?? 0} />
            <Stat label="Equal H/L" value={`${market_structure.equal_highs?.length ?? 0} / ${market_structure.equal_lows?.length ?? 0}`} />
          </Grid>
          {market_structure.events?.slice(-4).reverse().map((e, i) => (
            <Row key={i} tone={e.direction === "bullish" ? "up" : "down"}
                 label={`${e.direction} ${e.kind}`} value={formatPrice(e.price)} detail={e.evidence} />
          ))}
          {market_structure.sweeps?.slice(-3).reverse().map((s, i) => (
            <Row key={`sw${i}`} tone="warn" label={`${s.direction} sweep`}
                 value={formatPrice(s.level)} detail={s.evidence} />
          ))}
          <Evidence items={market_structure.notes} />

          {market_structure.wyckoff_events?.events?.length > 0 && (
            <>
              <p className="text-[10px] uppercase tracking-wide text-term-dim mt-3 mb-1">
                Wyckoff — {market_structure.wyckoff_events.campaign?.replace(/_/g, " ")}
              </p>
              {market_structure.wyckoff_events.events.map((e, i) => (
                <Row key={`wy${i}`}
                     tone={WYCKOFF_TONE[e.type] ?? "neutral"}
                     label={e.type.replace(/_/g, " ")}
                     value={formatPrice(e.price)}
                     detail={e.evidence} />
              ))}
            </>
          )}
        </Section>

        <Section title="5. Smart Money Concepts" open={open.smc} onToggle={() => toggle("smc")}>
          <Grid>
            <Stat label="Order blocks" value={smart_money.order_blocks?.length ?? 0} />
            <Stat label="Fair value gaps" value={smart_money.fair_value_gaps?.length ?? 0} />
            <Stat label="Breakers" value={smart_money.breaker_blocks?.length ?? 0} />
            <Stat label="Liquidity pools" value={smart_money.liquidity_pools?.length ?? 0} />
          </Grid>
          {smart_money.premium_discount?.available && (
            <Row tone={smart_money.premium_discount.zone === "discount" ? "up" : "down"}
                 label={`Price in ${smart_money.premium_discount.zone}`}
                 value={`${formatNumber(smart_money.premium_discount.position_percent, 0)}%`}
                 detail={smart_money.premium_discount.evidence} />
          )}
          {smart_money.order_blocks?.filter((b) => !b.mitigated).slice(0, 3).map((b, i) => (
            <Row key={i} tone={b.direction === "bullish" ? "up" : "down"}
                 label={`${b.direction} order block`}
                 value={`${formatPrice(b.bottom)}–${formatPrice(b.top)}`} detail={b.evidence} />
          ))}
          {smart_money.fair_value_gaps?.slice(-2).map((g, i) => (
            <Row key={`f${i}`} tone={g.direction === "bullish" ? "up" : "down"}
                 label={`${g.direction} FVG`}
                 value={`${formatPrice(g.bottom)}–${formatPrice(g.top)}`} detail={g.evidence} />
          ))}
          {smart_money.liquidity_pools?.slice(0, 4).map((p, i) => (
            <Row key={`l${i}`} tone={p.direction === "buyside" ? "up" : "down"}
                 label={`${p.direction} liquidity (${p.scope})`}
                 value={formatPrice(p.price)} detail={p.evidence} />
          ))}
          <Evidence items={smart_money.narrative} />
        </Section>

        <Section title="6. Volume Analysis" open={open.volume} onToggle={() => toggle("volume")}>
          {volume_analysis.available ? (
            <>
              <Grid>
                <Stat label="Trend" value={volume_analysis.volume_trend} />
                <Stat label="Relative" value={`${formatNumber(volume_analysis.relative_volume)}x`} />
                <Stat label="Pressure" value={volume_analysis.pressure} />
                <Stat label="Buy share" value={`${formatNumber(volume_analysis.buy_share_percent, 0)}%`} />
                <Stat label="POC" value={formatPrice(volume_analysis.poc)} />
                <Stat label="Value area" value={`${formatPrice(volume_analysis.val)}–${formatPrice(volume_analysis.vah)}`} />
              </Grid>
              {volume_analysis.divergence?.detected && (
                <Row tone="warn" label={`${volume_analysis.divergence.type} divergence`} value=""
                     detail={volume_analysis.divergence.evidence} />
              )}
              <Evidence items={volume_analysis.evidence} />
            </>
          ) : (
            <p className="text-xs text-term-muted">{volume_analysis.reason}</p>
          )}
        </Section>

        <Section title="7. Indicator Analysis" open={open.indicators} onToggle={() => toggle("indicators")}>
          <div className="flex items-center gap-3 mb-2 text-xs">
            <span className="text-term-up">{indicator_analysis.bullish_count} bullish</span>
            <span className="text-term-down">{indicator_analysis.bearish_count} bearish</span>
            <span className="text-term-muted">consensus: {indicator_analysis.consensus}</span>
          </div>
          <p className="text-[11px] text-term-muted mb-2 leading-relaxed">{indicator_analysis.summary}</p>
          {indicator_analysis.readings?.map((r, i) => (
            <Row key={i} tone={r.bias === "bullish" ? "up" : r.bias === "bearish" ? "down" : "neutral"}
                 label={r.name} value={r.reading} detail={r.explanation} />
          ))}
        </Section>

        <Section title="8. Key Levels" open={open.levels} onToggle={() => toggle("levels")}>
          {key_levels.nearest_resistance && (
            <Row tone="down" label="Nearest resistance"
                 value={formatPrice(key_levels.nearest_resistance.price)}
                 detail={key_levels.nearest_resistance.evidence} />
          )}
          {key_levels.nearest_support && (
            <Row tone="up" label="Nearest support"
                 value={formatPrice(key_levels.nearest_support.price)}
                 detail={key_levels.nearest_support.evidence} />
          )}
          {key_levels.period_levels?.slice(0, 6).map((l, i) => (
            <Row key={i} tone="neutral" label={l.label} value={formatPrice(l.price)} detail={l.evidence} />
          ))}
          {key_levels.trendlines?.map((t, i) => (
            <Row key={`t${i}`} tone="neutral" label={`${t.direction} trendline`}
                 value={`${t.touches} touches`} detail={t.evidence} />
          ))}
          <Evidence items={key_levels.evidence} />
        </Section>

        <Section title="Price Action" open={open.pa} onToggle={() => toggle("pa")}>
          <Grid>
            <Stat label="Bias" value={price_action.bias} tone={toneFor(price_action.bias)} />
            <Stat label="Agreement" value={`${formatNumber(price_action.confidence * 100, 0)}%`} />
          </Grid>
          {price_action.recent?.slice(-6).reverse().map((p, i) => (
            <Row key={i} tone={p.bias === "bullish" ? "up" : p.bias === "bearish" ? "down" : "neutral"}
                 label={p.name} value={`${formatNumber(p.strength * 100, 0)}%`} detail={p.evidence} />
          ))}
        </Section>

        <Section title="9. Probability Analysis" open={open.probability} onToggle={() => toggle("probability")}>
          <div className="space-y-1.5 mb-3">
            <Bar label="Bullish continuation" value={probability_analysis.bullish_continuation} color="#26a69a" />
            <Bar label="Bearish continuation" value={probability_analysis.bearish_continuation} color="#ef5350" />
            <Bar label="Reversal" value={probability_analysis.reversal} color="#f0a500" />
            <Bar label="Breakout" value={probability_analysis.breakout} color="#4a9eff" />
            <Bar label="Range" value={probability_analysis.range_bound} color="#8b93a7" />
            <Bar label="False breakout" value={probability_analysis.false_breakout} color="#c74aff" />
          </div>
          <Grid>
            <Stat label="Confidence" value={`${formatNumber(probability_analysis.confidence, 0)}%`} />
            <Stat label="Historical sample" value={`${probability_analysis.sample_size} analogues`} />
          </Grid>
          <p className="text-[11px] text-term-muted leading-relaxed mt-2">
            {probability_analysis.base_rates?.note}
          </p>
          <div className="mt-2">
            <p className="text-[10px] uppercase tracking-wide text-term-dim mb-1">
              Contributing evidence (weighted)
            </p>
            {probability_analysis.factors?.slice(0, 10).map((f, i) => (
              <div key={i} className="mb-1.5 pl-2 border-l border-term-border">
                <div className="flex items-baseline gap-2">
                  <span className="text-[11px] text-term-text font-medium">{f.name}</span>
                  <span className={`text-[10px] font-mono ${f.weight >= 0 ? "text-term-up" : "text-term-down"}`}>
                    {f.weight >= 0 ? "+" : ""}{f.weight}
                  </span>
                  <span className="text-[10px] text-term-dim">→ {f.scenario.replace(/_/g, " ")}</span>
                </div>
                <p className="text-[10px] text-term-muted leading-relaxed">{f.explanation}</p>
              </div>
            ))}
          </div>
          <Evidence items={probability_analysis.caveats} tone="warn" />
        </Section>

        <Section title="10. Trade Setup" open={open.setup} onToggle={() => toggle("setup")}>
          {trade_setup.valid ? (
            <>
              <div className="flex items-center gap-2 mb-2">
                <span className={`px-2 py-0.5 rounded text-xs font-bold uppercase ${
                  trade_setup.direction === "long"
                    ? "bg-term-up/20 text-term-up" : "bg-term-down/20 text-term-down"}`}>
                  {trade_setup.direction}
                </span>
                <span className="text-xs text-term-muted">{trade_setup.entry_type} entry</span>
                <span className="ml-auto text-xs font-mono text-term-text">
                  {formatNumber(trade_setup.primary_rr)}R
                </span>
              </div>
              <Grid>
                <Stat label="Entry" value={formatPrice(trade_setup.entry)} />
                <Stat label="Stop" value={formatPrice(trade_setup.stop_loss)} tone="down" />
                {trade_setup.take_profits?.map((tp, i) => (
                  <Stat key={i} label={`TP${i + 1} (${formatNumber(trade_setup.risk_reward[i])}R)`}
                        value={formatPrice(tp)} tone="up" />
                ))}
                <Stat label="Confidence" value={`${formatNumber(trade_setup.confidence, 0)}%`} />
                <Stat label="Hold time" value={trade_setup.holding_time} />
                <Stat label="Expected move" value={formatPercent(trade_setup.expected_move_percent)} />
              </Grid>
              <p className="text-[11px] text-term-warn leading-relaxed mt-2">
                <span className="font-semibold">Invalidation: </span>{trade_setup.invalidation}
              </p>
              <Evidence items={trade_setup.rationale} label="Why this setup" />
              <Evidence items={trade_setup.warnings} tone="warn" label="Warnings" />
            </>
          ) : (
            <div className="p-2 rounded bg-term-raised">
              <p className="text-xs font-semibold text-term-muted mb-1">No trade</p>
              <p className="text-[11px] text-term-muted leading-relaxed">{trade_setup.no_trade_reason}</p>
            </div>
          )}
        </Section>

        <Section title="11. Risk Review" open={open.risk} onToggle={() => toggle("risk")}>
          <Grid>
            <Stat label="Volatility risk" value={risk_review.volatility_risk} />
            <Stat label="Liquidity risk" value={risk_review.liquidity_risk} />
            <Stat label="Gap risk" value={risk_review.gap_risk} />
            <Stat label="News risk" value={risk_review.news_risk} />
            <Stat label="Correlation" value={risk_review.correlation_risk} />
            <Stat label="Est. max DD" value={formatPercent(risk_review.max_drawdown_estimate, 1)} />
            {risk_review.position_size && (
              <Stat label="Position size" value={formatNumber(risk_review.position_size, 6)} />
            )}
            {risk_review.risk_amount && (
              <Stat label="Cash at risk" value={formatPrice(risk_review.risk_amount)} />
            )}
          </Grid>
          <Evidence items={risk_review.evidence} />
          <Evidence items={risk_review.warnings} tone="warn" label="Warnings" />
        </Section>

        <Section title="16–18. Strengths, Weaknesses, Improvements" open={open.grade}
                 onToggle={() => toggle("grade")}>
          <Evidence items={strengths} tone="up" label="Strengths" />
          <Evidence items={weaknesses} tone="down" label="Weaknesses" />
          <Evidence items={improvements} tone="info" label="Improvements" />
        </Section>

        {onFeedback && (
          <div className="p-3 border-t border-term-border flex items-center gap-2">
            <span className="text-[10px] text-term-dim uppercase tracking-wide">Was this useful?</span>
            {["useful", "wrong", "unclear"].map((rating) => (
              <button key={rating} onClick={() => onFeedback(rating)} className="btn-term text-[10px]">
                {rating}
              </button>
            ))}
          </div>
        )}
      </div>
    </PanelShell>
  );
}

/* ------------------------------------------------------------------ */

function PanelShell({ children }) {
  return <div className="flex flex-col h-full bg-term-panel">{children}</div>;
}

function Verdict({ verdict }) {
  if (!verdict) return null;
  const tone = {
    TAKE: "border-term-up bg-term-up/10 text-term-up",
    WATCH: "border-term-warn bg-term-warn/10 text-term-warn",
    "STAND ASIDE": "border-term-dim bg-term-raised text-term-muted",
    "NO TRADE": "border-term-dim bg-term-raised text-term-muted",
  }[verdict.decision] ?? "border-term-dim bg-term-raised text-term-muted";

  return (
    <div className={`m-2 p-3 rounded border ${tone}`}>
      <div className="flex items-center justify-between mb-1">
        <span className="text-sm font-bold tracking-wide">{verdict.decision}</span>
        <span className="text-xs font-mono opacity-80">{formatNumber(verdict.confidence, 0)}%</span>
      </div>
      <p className="text-[11px] leading-relaxed opacity-90">{verdict.reasoning}</p>
      <p className="text-[11px] leading-relaxed mt-1 font-medium">{verdict.action}</p>
    </div>
  );
}

function Section({ title, open, onToggle, children }) {
  return (
    <div className="border-b border-term-border">
      <button
        onClick={onToggle}
        className="w-full flex items-center justify-between px-3 py-2 hover:bg-term-raised transition-colors"
      >
        <span className="text-[11px] font-semibold uppercase tracking-wide text-term-muted">{title}</span>
        <span className="text-term-dim text-xs">{open ? "−" : "+"}</span>
      </button>
      {open && <div className="px-3 pb-3">{children}</div>}
    </div>
  );
}

function Grid({ children }) {
  return <div className="grid grid-cols-2 gap-x-3 gap-y-1 mb-2">{children}</div>;
}

function Stat({ label, value, tone }) {
  const color = {
    up: "text-term-up", down: "text-term-down", warn: "text-term-warn",
  }[tone] ?? "text-term-text";
  return (
    <div className="flex justify-between items-baseline gap-2">
      <span className="text-[10px] text-term-dim truncate">{label}</span>
      <span className={`text-[11px] font-mono ${color} text-right truncate`}>{value ?? "—"}</span>
    </div>
  );
}

function Row({ label, value, detail, tone }) {
  const color = {
    up: "text-term-up", down: "text-term-down", warn: "text-term-warn",
  }[tone] ?? "text-term-muted";
  return (
    <div className="mb-1.5 pl-2 border-l border-term-border">
      <div className="flex items-baseline justify-between gap-2">
        <span className={`text-[11px] font-medium ${color}`}>{label}</span>
        <span className="text-[10px] font-mono text-term-text">{value}</span>
      </div>
      {detail && <p className="text-[10px] text-term-muted leading-relaxed">{detail}</p>}
    </div>
  );
}

function Evidence({ items, tone = "muted", label }) {
  if (!items?.length) return null;
  const color = {
    up: "text-term-up", down: "text-term-down", warn: "text-term-warn",
    info: "text-term-info", muted: "text-term-muted",
  }[tone];
  return (
    <div className="mt-2">
      {label && (
        <p className="text-[10px] uppercase tracking-wide text-term-dim mb-1">{label}</p>
      )}
      <ul className="space-y-1">
        {items.map((item, i) => (
          <li key={i} className={`text-[10px] leading-relaxed ${color} flex gap-1.5`}>
            <span className="text-term-dim shrink-0">·</span>
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Bar({ label, value, color }) {
  return (
    <div>
      <div className="flex justify-between text-[10px] mb-0.5">
        <span className="text-term-muted">{label}</span>
        <span className="font-mono text-term-text">{formatNumber(value, 1)}%</span>
      </div>
      <div className="h-1.5 bg-term-raised rounded-sm overflow-hidden">
        <div
          className="h-full rounded-sm transition-all duration-500"
          style={{ width: `${Math.min(100, value)}%`, backgroundColor: color }}
        />
      </div>
    </div>
  );
}

function toneFor(value) {
  if (typeof value !== "string") return undefined;
  if (value.includes("bull") || value.includes("accumulat")) return "up";
  if (value.includes("bear") || value.includes("distribut")) return "down";
  return undefined;
}
