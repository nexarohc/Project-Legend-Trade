import { useEffect, useState } from "react";
import { auth } from "../lib/tradingApi.js";
import { isSignedIn } from "../lib/auth.js";

/**
 * Two-factor authentication for the trading-terminal account.
 *
 * The setup flow is deliberately three steps, not one: generate a secret,
 * confirm a real code was actually produced from it, only then flip MFA on
 * and hand over recovery codes. Skipping the confirm step would let someone
 * "enable" MFA with a secret their authenticator app never actually received,
 * locking them out on the next sign-in.
 *
 * Recovery codes are shown exactly once, in the confirm response — the
 * backend never returns them again after this, the same way a password reset
 * can't recover the old password.
 */
export default function MfaSettings() {
  const [status, setStatus] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  // Setup-in-progress state — cleared once confirmed or cancelled.
  const [pending, setPending] = useState(null); // { secret, provisioning_uri }
  const [setupCode, setSetupCode] = useState("");
  const [recoveryCodes, setRecoveryCodes] = useState(null);

  const [disabling, setDisabling] = useState(false);
  const [disablePassword, setDisablePassword] = useState("");

  const refresh = () => auth.mfaStatus().then(setStatus).catch((e) => setError(e.message));

  useEffect(() => {
    if (isSignedIn()) refresh();
  }, []);

  if (!isSignedIn()) return null;
  if (!status) return null;

  const startSetup = async () => {
    setBusy(true);
    setError(null);
    try {
      setPending(await auth.mfaSetup());
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const confirmSetup = async (event) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await auth.mfaConfirm(setupCode.trim());
      setRecoveryCodes(result.recovery_codes);
      setPending(null);
      setSetupCode("");
      await refresh();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const submitDisable = async (event) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await auth.mfaDisable(disablePassword);
      setDisabling(false);
      setDisablePassword("");
      await refresh();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section>
      <h2 className="text-lg font-semibold mb-3">Two-factor authentication</h2>

      {error && (
        <div className="mb-3 p-3 rounded-xl bg-red-950/40 border border-red-900 text-red-300 text-sm">
          {error}
        </div>
      )}

      {/* Just confirmed — show the recovery codes once, and only once. */}
      {recoveryCodes && (
        <div className="mb-3 bg-brand-panel rounded-xl px-4 py-3 space-y-2">
          <p className="text-sm font-medium">MFA is on. Save these recovery codes now.</p>
          <p className="text-xs text-slate-400">
            Each works once, in place of a code, if you lose access to your authenticator.
            They will not be shown again.
          </p>
          <div className="grid grid-cols-2 gap-1.5 font-mono text-xs bg-black/30 rounded-lg p-3">
            {recoveryCodes.map((code) => (
              <span key={code}>{code}</span>
            ))}
          </div>
          <button
            onClick={() => setRecoveryCodes(null)}
            className="text-xs px-3 py-1.5 rounded-full bg-slate-800 text-slate-300"
          >
            I've saved these — done
          </button>
        </div>
      )}

      {/* Setup in progress: show the secret, wait for a confirming code. */}
      {!recoveryCodes && pending && (
        <div className="mb-3 bg-brand-panel rounded-xl px-4 py-3 space-y-2">
          <p className="text-sm font-medium">Add this to your authenticator app</p>
          <p className="text-xs text-slate-400">
            Scan isn't available here — enter the secret below manually, or paste the
            full URI if your app accepts one.
          </p>
          <div className="font-mono text-xs bg-black/30 rounded-lg p-3 break-all">
            {pending.secret}
          </div>
          <details className="text-xs text-slate-500">
            <summary className="cursor-pointer">Show the full otpauth:// URI</summary>
            <div className="font-mono break-all mt-1">{pending.provisioning_uri}</div>
          </details>

          <form onSubmit={confirmSetup} className="flex gap-2 pt-1">
            <input
              value={setupCode}
              onChange={(e) => setSetupCode(e.target.value)}
              placeholder="6-digit code"
              autoComplete="one-time-code"
              className="flex-1 min-w-0 bg-black/30 border border-slate-700 rounded-lg px-3 py-1.5 text-sm"
            />
            <button
              type="submit"
              disabled={busy || !setupCode.trim()}
              className="text-xs px-3 py-1.5 rounded-full bg-emerald-900 text-emerald-300 shrink-0"
            >
              {busy ? "Confirming…" : "Confirm"}
            </button>
          </form>
          <button
            onClick={() => { setPending(null); setSetupCode(""); setError(null); }}
            className="text-xs text-slate-500 hover:text-slate-300"
          >
            Cancel
          </button>
        </div>
      )}

      {/* Steady state: enabled or not, nothing else in flight. */}
      {!recoveryCodes && !pending && (
        <div className="flex items-center justify-between bg-brand-panel rounded-xl px-4 py-3">
          <div>
            <span className="block">
              {status.enabled ? "Enabled" : "Not enabled"}
            </span>
            {status.enabled && (
              <span className="text-xs text-slate-500">
                {status.recovery_codes_remaining} recovery code
                {status.recovery_codes_remaining === 1 ? "" : "s"} remaining
              </span>
            )}
          </div>

          {status.enabled ? (
            <button
              onClick={() => setDisabling((v) => !v)}
              className="text-xs px-3 py-1.5 rounded-full bg-slate-800 text-slate-400"
            >
              Disable
            </button>
          ) : (
            <button
              onClick={startSetup}
              disabled={busy}
              className="text-xs px-3 py-1.5 rounded-full bg-emerald-900 text-emerald-300"
            >
              {busy ? "Working…" : "Enable"}
            </button>
          )}
        </div>
      )}

      {disabling && (
        <form onSubmit={submitDisable} className="mt-2 bg-brand-panel rounded-xl px-4 py-3 space-y-2">
          <p className="text-xs text-slate-400">
            Confirm your password to turn MFA off.
          </p>
          <div className="flex gap-2">
            <input
              type="password"
              value={disablePassword}
              onChange={(e) => setDisablePassword(e.target.value)}
              autoComplete="current-password"
              placeholder="Password"
              className="flex-1 min-w-0 bg-black/30 border border-slate-700 rounded-lg px-3 py-1.5 text-sm"
            />
            <button
              type="submit"
              disabled={busy || !disablePassword}
              className="text-xs px-3 py-1.5 rounded-full bg-red-950 text-red-300 shrink-0"
            >
              Disable
            </button>
          </div>
        </form>
      )}
    </section>
  );
}
