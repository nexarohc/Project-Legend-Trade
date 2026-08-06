import { useState } from "react";
import { auth } from "../lib/tradingApi.js";
import { clearSession, isSignedIn } from "../lib/auth.js";

const CONFIRM_PHRASE = "DELETE MY ACCOUNT";

/**
 * Subject access and erasure, as buttons rather than as an email address.
 *
 * The privacy policy promises a user can obtain and delete their data. Until
 * this existed, honouring that meant an operator running queries by hand, which
 * is a promise that quietly stops being kept the moment there is more than a
 * trickle of requests.
 *
 * Deletion deliberately costs more than a click: the password is re-entered
 * even though the session is already authenticated, and the phrase is typed in
 * full. Both are there because the action cannot be undone — there is no soft
 * delete and no recovery window behind it.
 */
export default function DataAndPrivacy() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [note, setNote] = useState(null);
  const [armed, setArmed] = useState(false);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");

  if (!isSignedIn()) return null;

  const download = async () => {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const data = await auth.exportMyData();
      // Built and revoked in the browser: sending the export back out to a file
      // service to produce a download would undo the point of the export.
      const url = URL.createObjectURL(
        new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }),
      );
      const link = document.createElement("a");
      link.href = url;
      link.download = `legend-trade-export-${new Date().toISOString().slice(0, 10)}.json`;
      link.click();
      URL.revokeObjectURL(url);
      const total = Object.values(data.counts || {}).reduce((sum, n) => sum + n, 0);
      setNote(`Downloaded — ${total} record${total === 1 ? "" : "s"} across ${
        Object.keys(data.counts || {}).length} tables.`);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const destroy = async () => {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      await auth.deleteMyAccount(password, confirm);
      // The tokens now point at a user row that no longer exists. Clearing the
      // session locally avoids a screen full of 401s on the way out.
      clearSession();
      window.location.reload();
    } catch (e) {
      setError(e.message);
      setBusy(false);
    }
  };

  return (
    <section className="bg-brand-panel rounded-xl px-4 py-3">
      <h2 className="text-lg font-semibold mb-1">Your data</h2>
      <p className="text-sm text-slate-400 mb-3">
        Download everything this instance holds about you, or remove it entirely.
        The export excludes your password hash and two-factor secret — handing
        those back would turn one stolen session into a lasting compromise.
      </p>

      {note && <p className="text-xs text-emerald-400 mb-2">{note}</p>}
      {error && <p className="text-xs text-red-400 mb-2">{error}</p>}

      <button
        onClick={download}
        disabled={busy}
        className="text-xs px-3 py-1.5 rounded-full bg-slate-800 text-slate-300 hover:text-white transition"
      >
        {busy ? "Working…" : "Download my data (JSON)"}
      </button>

      <div className="mt-5 pt-4 border-t border-slate-800">
        <h3 className="text-sm font-semibold text-red-400 mb-1">Delete this account</h3>
        <p className="text-xs text-slate-500 mb-3 leading-relaxed">
          Permanent. Your watchlist, alerts, analyses, strategies, backtests,
          simulated positions and order history are erased along with the account.
          There is no undo and no recovery window. Export first if you want a copy.
        </p>

        {!armed ? (
          <button
            onClick={() => setArmed(true)}
            className="text-xs px-3 py-1.5 rounded-full border border-red-900 text-red-400 hover:bg-red-950/40 transition"
          >
            Delete my account…
          </button>
        ) : (
          <div className="space-y-2 max-w-sm">
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Your password"
              autoComplete="current-password"
              className="w-full bg-slate-900 border border-slate-800 rounded px-2 py-1.5 text-xs text-slate-200"
            />
            <input
              type="text"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              placeholder={`Type ${CONFIRM_PHRASE}`}
              className="w-full bg-slate-900 border border-slate-800 rounded px-2 py-1.5 text-xs text-slate-200 font-mono"
            />
            <div className="flex gap-2">
              <button
                onClick={destroy}
                disabled={busy || confirm !== CONFIRM_PHRASE || !password}
                className="text-xs px-3 py-1.5 rounded-full bg-red-900/70 text-red-100 disabled:opacity-40 disabled:cursor-not-allowed hover:bg-red-900 transition"
              >
                {busy ? "Deleting…" : "Delete permanently"}
              </button>
              <button
                onClick={() => {
                  setArmed(false);
                  setPassword("");
                  setConfirm("");
                  setError(null);
                }}
                className="text-xs px-3 py-1.5 rounded-full text-slate-400 hover:text-slate-200 transition"
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
