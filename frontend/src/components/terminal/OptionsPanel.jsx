import { useState } from "react";
import { optionsPricing } from "../../lib/tradingApi.js";

/**
 * Options pricing: Black-Scholes / binomial fair value and Greeks for a
 * single contract, plus payoff diagrams for common multi-leg strategies.
 *
 * There is deliberately no live option chain here (strikes, bid/ask, open
 * interest) — none of this platform's free market-data vendors serve that,
 * and faking it would misrepresent real data the way this app consistently
 * refuses to elsewhere. Volatility can be typed in directly, or estimated
 * from the underlying's own real historical prices.
 */

const STRATEGY_FIELDS = {
  bull_call_spread: [
    ["lower_strike", "Lower strike"],
    ["upper_strike", "Upper strike"],
  ],
  bear_put_spread: [
    ["lower_strike", "Lower strike"],
    ["upper_strike", "Upper strike"],
  ],
  long_straddle: [["strike", "Strike"]],
  long_strangle: [
    ["put_strike", "Put strike"],
    ["call_strike", "Call strike"],
  ],
  iron_condor: [
    ["put_long", "Put (long)"],
    ["put_short", "Put (short)"],
    ["call_short", "Call (short)"],
    ["call_long", "Call (long)"],
  ],
  call_butterfly: [
    ["lower_strike", "Lower strike"],
    ["middle_strike", "Middle strike"],
    ["upper_strike", "Upper strike"],
  ],
};

function Field({ label, children }) {
  return (
    <label className="flex flex-col gap-0.5">
      <span className="text-[9px] uppercase tracking-wide text-term-dim">{label}</span>
      {children}
    </label>
  );
}

const inputClass =
  "bg-term-bg border border-term-border rounded px-1.5 py-1 text-[10px] font-mono text-term-text";

export default function OptionsPanel({ symbol }) {
  const [mode, setMode] = useState("price");

  return (
    <div className="flex flex-col h-full bg-term-panel">
      <div className="flex border-b border-term-border">
        {[
          ["price", "Price a contract"],
          ["payoff", "Strategy payoff"],
        ].map(([key, label]) => (
          <button
            key={key}
            onClick={() => setMode(key)}
            className={`flex-1 py-1.5 text-[10px] font-semibold uppercase tracking-wide ${
              mode === key
                ? "text-term-text border-b-2 border-brand-accent"
                : "text-term-dim hover:text-term-muted"
            }`}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto">
        {mode === "price" ? <PriceCalculator defaultSymbol={symbol} /> : <PayoffBuilder defaultSymbol={symbol} />}
      </div>
    </div>
  );
}

function PriceCalculator({ defaultSymbol }) {
  const [form, setForm] = useState({
    symbol: defaultSymbol || "",
    strike: "",
    daysToExpiry: "30",
    optionType: "call",
    exerciseStyle: "european",
    volatility: "",
    riskFreeRate: "0.05",
    dividendYield: "0",
  });
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      const body = {
        symbol: form.symbol || null,
        strike: parseFloat(form.strike),
        days_to_expiry: parseFloat(form.daysToExpiry),
        option_type: form.optionType,
        exercise_style: form.exerciseStyle,
        risk_free_rate: parseFloat(form.riskFreeRate),
        dividend_yield: parseFloat(form.dividendYield),
      };
      if (form.volatility.trim() !== "") body.volatility = parseFloat(form.volatility);
      const r = await optionsPricing.price(body);
      setResult(r);
    } catch (e) {
      setError(e.message);
      setResult(null);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="p-3 space-y-2 text-[10px]">
      <div className="grid grid-cols-2 gap-2">
        <Field label="Underlying symbol">
          <input className={inputClass} value={form.symbol} onChange={set("symbol")}
                 placeholder="e.g. AAPL (optional if spot given)" />
        </Field>
        <Field label="Strike">
          <input className={inputClass} type="number" value={form.strike} onChange={set("strike")} />
        </Field>
        <Field label="Days to expiry">
          <input className={inputClass} type="number" value={form.daysToExpiry} onChange={set("daysToExpiry")} />
        </Field>
        <Field label="Type">
          <select className={inputClass} value={form.optionType} onChange={set("optionType")}>
            <option value="call">Call</option>
            <option value="put">Put</option>
          </select>
        </Field>
        <Field label="Exercise style">
          <select className={inputClass} value={form.exerciseStyle} onChange={set("exerciseStyle")}>
            <option value="european">European</option>
            <option value="american">American</option>
          </select>
        </Field>
        <Field label="Volatility (blank = historical)">
          <input className={inputClass} type="number" step="0.01" value={form.volatility}
                 onChange={set("volatility")} placeholder="e.g. 0.30" />
        </Field>
        <Field label="Risk-free rate">
          <input className={inputClass} type="number" step="0.01" value={form.riskFreeRate}
                 onChange={set("riskFreeRate")} />
        </Field>
        <Field label="Dividend yield">
          <input className={inputClass} type="number" step="0.01" value={form.dividendYield}
                 onChange={set("dividendYield")} />
        </Field>
      </div>

      <button
        onClick={run}
        disabled={busy || !form.strike || !form.daysToExpiry || (!form.symbol && !form.volatility)}
        className="w-full py-1.5 rounded bg-term-info/20 text-term-info hover:bg-term-info/30
                   disabled:opacity-40 font-semibold uppercase tracking-wide"
      >
        {busy ? "Pricing…" : "Price"}
      </button>

      {error && <p className="text-term-down">{error}</p>}

      {result && (
        <div className="border border-term-border rounded p-2 space-y-1.5">
          <div className="flex justify-between">
            <span className="text-term-dim">Fair value</span>
            <span className="font-mono text-term-text">{result.fair_value.toFixed(4)}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-term-dim">Intrinsic / time value</span>
            <span className="font-mono text-term-text">
              {result.intrinsic_value.toFixed(4)} / {result.time_value.toFixed(4)}
            </span>
          </div>
          <p className="text-[9px] text-term-dim">
            Volatility used: {result.inputs.volatility.toFixed(4)} ({result.volatility_source}) ·
            model: {result.inputs.model}
          </p>
          <p className="text-[9px] uppercase tracking-wide text-term-dim pt-1">Greeks</p>
          <div className="grid grid-cols-5 gap-1 text-center">
            {["delta", "gamma", "theta", "vega", "rho"].map((g) => (
              <div key={g} className="bg-term-bg border border-term-border rounded py-1">
                <div className="text-term-dim text-[8px] uppercase">{g}</div>
                <div className="font-mono text-term-text">{result.greeks[g].toFixed(4)}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function PayoffBuilder({ defaultSymbol }) {
  const [strategy, setStrategy] = useState("bull_call_spread");
  const [symbol, setSymbol] = useState(defaultSymbol || "");
  const [daysToExpiry, setDaysToExpiry] = useState("30");
  const [volatility, setVolatility] = useState("");
  const [params, setParams] = useState({});
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const fields = STRATEGY_FIELDS[strategy];

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      const strategyParams = { days_to_expiry: parseFloat(daysToExpiry) };
      for (const [key] of fields) strategyParams[key] = parseFloat(params[key]);
      if (volatility.trim() !== "") strategyParams.volatility = parseFloat(volatility);

      const r = await optionsPricing.payoff({
        strategy, strategy_params: strategyParams, symbol: symbol || null,
      });
      setResult(r);
    } catch (e) {
      setError(e.message);
      setResult(null);
    } finally {
      setBusy(false);
    }
  };

  const allFilled = fields.every(([key]) => params[key] !== undefined && params[key] !== "")
    && daysToExpiry && (symbol || volatility);

  return (
    <div className="p-3 space-y-2 text-[10px]">
      <div className="grid grid-cols-2 gap-2">
        <Field label="Strategy">
          <select
            className={inputClass}
            value={strategy}
            onChange={(e) => { setStrategy(e.target.value); setParams({}); setResult(null); }}
          >
            {Object.keys(STRATEGY_FIELDS).map((name) => (
              <option key={name} value={name}>{name.replace(/_/g, " ")}</option>
            ))}
          </select>
        </Field>
        <Field label="Underlying symbol">
          <input className={inputClass} value={symbol} onChange={(e) => setSymbol(e.target.value)}
                 placeholder="for spot + historical vol" />
        </Field>
        {fields.map(([key, label]) => (
          <Field key={key} label={label}>
            <input className={inputClass} type="number" value={params[key] || ""}
                   onChange={(e) => setParams((p) => ({ ...p, [key]: e.target.value }))} />
          </Field>
        ))}
        <Field label="Days to expiry">
          <input className={inputClass} type="number" value={daysToExpiry}
                 onChange={(e) => setDaysToExpiry(e.target.value)} />
        </Field>
        <Field label="Volatility (blank = historical)">
          <input className={inputClass} type="number" step="0.01" value={volatility}
                 onChange={(e) => setVolatility(e.target.value)} />
        </Field>
      </div>

      <button
        onClick={run}
        disabled={busy || !allFilled}
        className="w-full py-1.5 rounded bg-term-info/20 text-term-info hover:bg-term-info/30
                   disabled:opacity-40 font-semibold uppercase tracking-wide"
      >
        {busy ? "Computing…" : "Show payoff"}
      </button>

      {error && <p className="text-term-down">{error}</p>}

      {result && <PayoffResult result={result} />}
    </div>
  );
}

function PayoffResult({ result }) {
  const { curve, max_profit, max_loss, breakevens, net_premium, legs } = result;
  const width = 400;
  const height = 140;
  const spots = curve.map((p) => p.spot);
  const pnls = curve.map((p) => p.pnl);
  const minSpot = Math.min(...spots);
  const maxSpot = Math.max(...spots);
  const minPnl = Math.min(0, ...pnls);
  const maxPnl = Math.max(0, ...pnls);
  const xScale = (s) => ((s - minSpot) / (maxSpot - minSpot || 1)) * width;
  const yScale = (p) => height - ((p - minPnl) / (maxPnl - minPnl || 1)) * height;
  const path = curve.map((pt, i) => `${i === 0 ? "M" : "L"}${xScale(pt.spot).toFixed(1)},${yScale(pt.pnl).toFixed(1)}`).join(" ");
  const zeroY = yScale(0);

  return (
    <div className="border border-term-border rounded p-2 space-y-2">
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-32 bg-term-bg rounded">
        <line x1="0" y1={zeroY} x2={width} y2={zeroY} stroke="#5a6377" strokeDasharray="3,3" strokeWidth="1" />
        <path d={path} fill="none" stroke="#4a9eff" strokeWidth="1.5" />
      </svg>
      <div className="grid grid-cols-2 gap-1">
        <div className="flex justify-between">
          <span className="text-term-dim">Max profit</span>
          <span className="font-mono text-term-up">{max_profit === null ? "Unbounded" : max_profit.toFixed(2)}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-term-dim">Max loss</span>
          <span className="font-mono text-term-down">{max_loss === null ? "Unbounded" : max_loss.toFixed(2)}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-term-dim">Net premium</span>
          <span className="font-mono text-term-text">{net_premium.toFixed(2)} {net_premium < 0 ? "(credit)" : "(debit)"}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-term-dim">Breakeven(s)</span>
          <span className="font-mono text-term-text">{breakevens.map((b) => b.toFixed(2)).join(", ") || "—"}</span>
        </div>
      </div>
      <div>
        <p className="text-[9px] uppercase tracking-wide text-term-dim mb-1">Legs (priced via Black-Scholes)</p>
        {legs.map((leg, i) => (
          <div key={i} className="flex justify-between text-[9px]">
            <span className="text-term-muted">
              {leg.quantity > 0 ? "Long" : "Short"} {Math.abs(leg.quantity)}x {leg.option_type} @ {leg.strike}
            </span>
            <span className="font-mono text-term-text">{leg.premium.toFixed(4)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
