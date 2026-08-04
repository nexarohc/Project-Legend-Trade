import { useCallback, useEffect, useState } from "react";
import { execution, formatNumber, formatPrice } from "../../lib/tradingApi.js";

/**
 * Execution control: mode, kill switch, orders.
 *
 * Live mode is made visually unmistakable on purpose — a red banner and a red
 * border around the whole panel. The most dangerous state this app can be in is
 * one where the user believes they are on paper. Ambiguity here costs money, so
 * the design spends attention rather than saving it.
 *
 * Refused orders are rendered as a normal, expected outcome with their reasons
 * shown in full, not as errors. A guardrail that fires without explaining
 * itself trains people to route around it.
 */
export default function ExecutionPanel({ symbol }) {
  const [status, setStatus] = useState(null);
  const [orders, setOrders] = useState([]);
  const [brokers, setBrokers] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [message, setMessage] = useState(null);
  const [confirmPhrase, setConfirmPhrase] = useState("");
  const [showRules, setShowRules] = useState(false);
  const [platformDefaults, setPlatformDefaults] = useState(null);
  const [limitDrafts, setLimitDrafts] = useState(null);
  const [limitsError, setLimitsError] = useState(null);

  const load = useCallback(async () => {
    try {
      const [s, o] = await Promise.all([execution.status(), execution.orders()]);
      setStatus(s);
      setOrders(o.orders);
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 15_000);
    return () => clearInterval(id);
  }, [load]);

  useEffect(() => {
    execution.brokers().then((r) => setBrokers(r.brokers)).catch(() => {});
  }, []);

  const loadLimits = useCallback(async () => {
    try {
      const l = await execution.limits();
      setPlatformDefaults(l.platform_defaults);
      setLimitDrafts(l.effective);
      setLimitsError(null);
    } catch (e) {
      setLimitsError(e.message);
    }
  }, []);

  useEffect(() => {
    if (showRules && limitDrafts === null) loadLimits();
  }, [showRules, limitDrafts, loadLimits]);

  const saveLimits = async () => {
    setBusy(true);
    setLimitsError(null);
    try {
      const overrides = {};
      for (const [key, value] of Object.entries(limitDrafts)) {
        if (platformDefaults && value !== platformDefaults[key]) overrides[key] = value;
      }
      if (Object.keys(overrides).length === 0) {
        setLimitsError("No changes to save.");
        return;
      }
      await execution.setLimits(overrides);
      await loadLimits();
      await load();
      setMessage("Limits updated.");
    } catch (e) {
      setLimitsError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const resetLimits = async () => {
    setBusy(true);
    setLimitsError(null);
    try {
      await execution.resetLimits();
      await loadLimits();
      await load();
      setMessage("Limits reset to platform defaults.");
    } catch (e) {
      setLimitsError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const run = async (fn, successMessage) => {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const result = await fn();
      if (successMessage) setMessage(successMessage(result));
      await load();
      return result;
    } catch (e) {
      setError(e.message);
      return null;
    } finally {
      setBusy(false);
    }
  };

  if (!status) {
    return (
      <div className="p-4 text-[11px] text-term-muted">
        {error ? <span className="text-term-down">{error}</span> : "Loading execution status…"}
      </div>
    );
  }

  const isLive = status.mode === "live";
  const killed = status.kill_switch_active;

  return (
    <div
      className={`flex flex-col h-full bg-term-panel ${
        isLive ? "ring-2 ring-inset ring-term-down" : ""
      }`}
    >
      {/* The two states that must never be mistaken for anything else. */}
      {isLive && (
        <div className="px-3 py-2 bg-term-down/20 border-b border-term-down">
          <p className="text-[11px] font-bold text-term-down tracking-wide">
            ● LIVE — ORDERS USE REAL MONEY
          </p>
          <p className="text-[10px] text-term-down/80 leading-relaxed mt-0.5">
            {status.live_confirmed
              ? `Armed until ${new Date(status.live_confirmed_until).toLocaleTimeString()}.`
              : "Not armed. Orders are refused until you confirm below."}
          </p>
        </div>
      )}

      {killed && (
        <div className="px-3 py-2 bg-term-warn/20 border-b border-term-warn">
          <p className="text-[11px] font-bold text-term-warn">■ KILL SWITCH ENGAGED</p>
          <p className="text-[10px] text-term-warn/80 leading-relaxed mt-0.5">
            All positions flattened, orders cancelled, new orders blocked.
            {status.kill_switch_reason ? ` Reason: ${status.kill_switch_reason}.` : ""}
          </p>
        </div>
      )}

      <div className="overflow-y-auto flex-1">
        {/* Mode */}
        <Section title="Execution mode">
          <div className="flex gap-1 mb-2">
            {[
              ["paper", "Paper"],
              ["broker_paper", "Broker paper"],
              ["live", "Live"],
            ].map(([value, label]) => {
              const active = status.mode === value;
              const needsAdmin = value !== "paper" && !status.user_is_admin;
              const blocked = (value === "live" && !status.live_enabled_on_instance) || needsAdmin;
              return (
                <button
                  key={value}
                  disabled={busy || blocked}
                  onClick={() => run(() => execution.setMode(value))}
                  title={
                    needsAdmin
                      ? "Broker-connected modes are limited to the instance administrator"
                      : value === "live" && !status.live_enabled_on_instance
                        ? "LEGEND_ENABLE_LIVE_TRADING is not set on this server"
                        : undefined
                  }
                  className={`px-2 py-1 text-[10px] rounded transition-colors ${
                    active
                      ? value === "live"
                        ? "bg-term-down text-white font-bold"
                        : "bg-brand-accent text-white"
                      : "border border-term-border text-term-dim hover:text-term-text"
                  } ${blocked ? "opacity-40 cursor-not-allowed" : ""}`}
                >
                  {label}
                </button>
              );
            })}
          </div>

          {!status.user_is_admin && (
            <p className="text-[10px] text-term-dim leading-relaxed">
              Broker paper and live modes are limited to the instance administrator — broker
              credentials are shared across the whole instance, not per-account.
            </p>
          )}
          {status.user_is_admin && !status.live_enabled_on_instance && (
            <p className="text-[10px] text-term-dim leading-relaxed">
              Live trading is disabled on this server. It can only be enabled by setting
              <code className="text-term-muted"> LEGEND_ENABLE_LIVE_TRADING=true</code> in the
              environment and restarting — deliberately not something an API request can change.
            </p>
          )}

          {status.broker_health && (
            <p className="text-[10px] text-term-muted leading-relaxed mt-1">
              {status.broker_health.connected
                ? `${status.broker_health.broker} connected — equity ${formatPrice(
                    status.broker_health.account?.equity,
                  )}`
                : `${status.broker_health.broker} not connected: ${status.broker_health.reason}`}
            </p>
          )}
        </Section>

        {/* Broker (only meaningful once mode touches one — paper mode never contacts it) */}
        {brokers.length > 1 && status.user_is_admin && (
          <Section title="Broker">
            <div className="flex gap-1">
              {brokers.map((name) => (
                <button
                  key={name}
                  disabled={busy}
                  onClick={() => run(
                    () => execution.setBroker(name),
                    () => `Broker set to ${name}. Any live confirmation was cleared.`,
                  )}
                  className={`px-2 py-1 text-[10px] rounded capitalize transition-colors ${
                    status.broker === name
                      ? "bg-brand-accent text-white"
                      : "border border-term-border text-term-dim hover:text-term-text"
                  }`}
                >
                  {name}
                </button>
              ))}
            </div>
            <p className="text-[10px] text-term-dim leading-relaxed mt-1.5">
              Only used by "Broker paper" and "Live" mode. Switching brokers clears any
              active live-confirmation window.
            </p>
          </Section>
        )}

        {/* Live arming */}
        {isLive && status.live_enabled_on_instance && !status.live_confirmed && (
          <Section title="Arm live execution">
            <p className="text-[10px] text-term-muted leading-relaxed mb-1.5">
              Type the phrase exactly to arm live orders for 30 minutes.
            </p>
            <div className="flex gap-1">
              <input
                value={confirmPhrase}
                onChange={(e) => setConfirmPhrase(e.target.value)}
                placeholder="TRADE LIVE MONEY"
                className="flex-1 min-w-0 bg-term-raised border border-term-down rounded px-2 py-1
                           text-[10px] font-mono text-term-text placeholder:text-term-dim
                           focus:outline-none"
              />
              <button
                disabled={busy}
                onClick={() =>
                  run(
                    () => execution.confirmLive({ phrase: confirmPhrase, minutes: 30 }),
                    () => "Live execution armed for 30 minutes.",
                  ).then(() => setConfirmPhrase(""))
                }
                className="px-2 py-1 text-[10px] rounded bg-term-down text-white font-semibold"
              >
                Arm
              </button>
            </div>
          </Section>
        )}

        {/* Kill switch */}
        <Section title="Kill switch">
          {killed ? (
            <button
              disabled={busy}
              onClick={() => run(() => execution.disengageKillSwitch(), () => "Trading re-enabled.")}
              className="btn-term text-[10px] w-full"
            >
              Disengage and allow trading
            </button>
          ) : (
            <button
              disabled={busy}
              onClick={() =>
                run(
                  () => execution.killSwitch({ reason: "manual" }),
                  (r) =>
                    `Flattened. ${r.orders_cancelled} order(s) cancelled, ` +
                    `${r.positions_closed.length} position(s) closed.`,
                )
              }
              className="w-full px-2 py-2 text-[11px] rounded bg-term-down/20 border border-term-down
                         text-term-down font-bold hover:bg-term-down/30 transition-colors"
            >
              FLATTEN EVERYTHING &amp; STOP
            </button>
          )}
          <p className="text-[10px] text-term-dim leading-relaxed mt-1">
            Cancels all working orders, closes all positions, and blocks new orders until
            explicitly disengaged. Survives a server restart.
          </p>
        </Section>

        {message && (
          <div className="mx-3 mb-2 p-2 rounded border border-term-info/40 bg-term-info/10">
            <p className="text-[10px] text-term-info leading-relaxed">{message}</p>
          </div>
        )}
        {error && (
          <div className="mx-3 mb-2 p-2 rounded border border-term-down/40 bg-term-down/10">
            <p className="text-[10px] text-term-down leading-relaxed">{error}</p>
          </div>
        )}

        {/* Orders */}
        <Section title={`Orders (${orders.length})`}>
          {orders.length === 0 && (
            <p className="text-[10px] text-term-dim leading-relaxed">
              No orders yet. Refused orders appear here too, with the rule that stopped them.
            </p>
          )}
          {orders.map((o) => (
            <div key={o.id} className="mb-1.5 pl-2 border-l border-term-border">
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-[11px] font-mono text-term-text">
                  <span className={o.side === "buy" ? "text-term-up" : "text-term-down"}>
                    {o.side}
                  </span>{" "}
                  {formatNumber(o.quantity, 6)} {o.symbol}
                </span>
                <span
                  className={`text-[9px] uppercase px-1 rounded ${
                    o.allowed
                      ? "bg-term-up/20 text-term-up"
                      : "bg-term-down/20 text-term-down"
                  }`}
                >
                  {o.allowed ? o.status : "refused"}
                </span>
              </div>
              <div className="text-[10px] text-term-dim font-mono">
                {o.mode} · stop {formatPrice(o.stop_price)}
              </div>
              {o.reason && (
                <p className="text-[10px] text-term-down leading-relaxed">{o.reason}</p>
              )}
            </div>
          ))}
        </Section>

        {/* Rules */}
        <Section title="Guardrails">
          <button
            onClick={() => setShowRules((v) => !v)}
            className="text-[10px] text-term-muted hover:text-term-text mb-1"
          >
            {showRules ? "Hide" : "Show"} the rules that block orders
          </button>
          {showRules && status.guardrail_rules && (
            <>
              <ul className="space-y-0.5 mb-2">
                {status.guardrail_rules.always_blocking.map((rule, i) => (
                  <li key={i} className="text-[10px] text-term-muted leading-relaxed flex gap-1.5">
                    <span className="text-term-down shrink-0">✕</span>
                    <span>{rule}</span>
                  </li>
                ))}
              </ul>
              <p className="text-[10px] uppercase tracking-wide text-term-dim mb-0.5">
                Your limits
              </p>
              <p className="text-[9px] text-term-dim leading-relaxed mb-1.5">
                You can tighten any of these below the platform default. They can never be
                loosened — even by an admin — from here.
              </p>
              {limitsError && (
                <p className="text-[9px] text-term-down mb-1">{limitsError}</p>
              )}
              {limitDrafts && platformDefaults ? (
                <>
                  <div className="space-y-1 mb-1.5">
                    {Object.entries(limitDrafts).map(([key, value]) => (
                      <div key={key} className="flex items-center justify-between gap-2">
                        <span
                          className="text-[9px] text-term-dim truncate"
                          title={`Platform default: ${platformDefaults[key]}`}
                        >
                          {key.replace(/_/g, " ")}
                        </span>
                        <input
                          type="number"
                          step="any"
                          value={value}
                          onChange={(e) => {
                            const next = parseFloat(e.target.value);
                            setLimitDrafts((prev) => ({
                              ...prev,
                              [key]: Number.isNaN(next) ? prev[key] : next,
                            }));
                          }}
                          className="w-20 bg-term-bg border border-term-border rounded px-1 py-0.5
                                     text-[9px] font-mono text-term-text text-right"
                        />
                      </div>
                    ))}
                  </div>
                  <div className="flex gap-1.5">
                    <button
                      onClick={saveLimits}
                      disabled={busy}
                      className="flex-1 py-1 text-[10px] rounded bg-term-info/20 text-term-info
                                 hover:bg-term-info/30 disabled:opacity-50"
                    >
                      Save
                    </button>
                    <button
                      onClick={resetLimits}
                      disabled={busy}
                      className="flex-1 py-1 text-[10px] rounded bg-term-bg border border-term-border
                                 text-term-muted hover:text-term-text disabled:opacity-50"
                    >
                      Reset to defaults
                    </button>
                  </div>
                </>
              ) : (
                <p className="text-[9px] text-term-dim">Loading limits…</p>
              )}
            </>
          )}
        </Section>
      </div>
    </div>
  );
}

function Section({ title, children }) {
  return (
    <div className="px-3 py-2 border-b border-term-border">
      <p className="text-[10px] uppercase tracking-wide text-term-dim mb-1.5">{title}</p>
      {children}
    </div>
  );
}
