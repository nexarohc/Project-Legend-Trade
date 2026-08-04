import { useEffect, useState } from "react";
import { auth } from "../lib/tradingApi.js";
import { isSignedIn } from "../lib/auth.js";

/**
 * Prompts an unverified account to confirm its email. Renders nothing once
 * verified, or when signed out — this is account housekeeping, not something
 * that should compete for attention on every screen.
 */
export default function EmailVerification() {
  const [me, setMe] = useState(null);
  const [sent, setSent] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (isSignedIn()) auth.me().then(setMe).catch(() => {});
  }, []);

  if (!isSignedIn() || !me || me.email_verified) return null;

  const resend = async () => {
    setBusy(true);
    setError(null);
    setSent(null);
    try {
      const result = await auth.resendVerification();
      setSent(result.note);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="bg-brand-panel rounded-xl px-4 py-3">
      <h2 className="text-lg font-semibold mb-1">Email verification</h2>
      <p className="text-sm text-slate-400 mb-3">
        {me.email} hasn't been confirmed yet. Check your inbox for the link sent at
        registration, or send a new one.
      </p>
      {sent && <p className="text-xs text-emerald-400 mb-2">{sent}</p>}
      {error && <p className="text-xs text-red-400 mb-2">{error}</p>}
      <button
        onClick={resend}
        disabled={busy}
        className="text-xs px-3 py-1.5 rounded-full bg-slate-800 text-slate-300 hover:text-white transition"
      >
        {busy ? "Sending…" : "Resend verification email"}
      </button>
    </section>
  );
}
