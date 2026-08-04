import { useEffect, useState } from "react";
import { auth } from "../lib/tradingApi.js";
import { storeSession } from "../lib/auth.js";
import logo from "../assets/logo.svg";

/**
 * Sign-in and first-run account setup.
 *
 * Which mode is shown comes from the server (`/auth/status`), not from a guess:
 * a fresh instance with no accounts shows "create your account", an instance
 * with accounts shows sign-in, and registration is only offered afterwards if
 * the server allows additional signups.
 */
export default function SignIn({ onSignedIn, onBack }) {
  const [status, setStatus] = useState(null);
  const [mode, setMode] = useState("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [info, setInfo] = useState(null);

  // Set only when the password step succeeds but the account has MFA enabled —
  // the login is not complete until this token is exchanged for real ones.
  const [mfaToken, setMfaToken] = useState(null);
  const [mfaCode, setMfaCode] = useState("");

  // Reset-password mode is reached via the link mailed by /auth/password/forgot,
  // which points back here with ?reset_token=... rather than a separate route.
  const [resetToken, setResetToken] = useState(null);
  const [newPassword, setNewPassword] = useState("");

  useEffect(() => {
    const fromLink = new URLSearchParams(window.location.search).get("reset_token");
    if (fromLink) {
      setResetToken(fromLink);
      setMode("reset");
      window.history.replaceState({}, "", window.location.pathname);
    }
  }, []);

  useEffect(() => {
    auth
      .status()
      .then((data) => {
        setStatus(data);
        // No accounts yet — this is a first run, so go straight to setup.
        // A reset link takes priority over both — it isn't a fresh install.
        setMode((current) => (current === "reset" ? current : data.has_accounts ? "login" : "register"));
      })
      .catch((e) => setError(`Cannot reach the backend: ${e.message}`));
  }, []);

  const submit = async (event) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const payload =
        mode === "register"
          ? await auth.register({ email, password, display_name: displayName })
          : await auth.login({ email, password });

      if (payload.mfa_required) {
        // Password was correct, but the login isn't finished — switch to the
        // code-entry step rather than storing a session that doesn't exist yet.
        setMfaToken(payload.mfa_token);
        return;
      }
      storeSession(payload);
      onSignedIn(payload.user);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const submitMfa = async (event) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const payload = await auth.mfaVerify(mfaToken, mfaCode.trim());
      storeSession(payload);
      onSignedIn(payload.user);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const submitForgot = async (event) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setInfo(null);
    try {
      const payload = await auth.forgotPassword(email);
      setInfo(payload.note);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const submitReset = async (event) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setInfo(null);
    try {
      const payload = await auth.resetPassword(resetToken, newPassword);
      setInfo(`${payload.note} You can sign in below.`);
      setMode("login");
      setResetToken(null);
      setNewPassword("");
      setPassword("");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const firstRun = status && !status.has_accounts;

  if (mfaToken) {
    return (
      <div className="h-full flex items-center justify-center bg-term-bg p-6">
        <div className="w-full max-w-sm">
          <Brand onBack={onBack} />

          <div className="bg-term-panel border border-term-border rounded-lg p-5">
            <h1 className="text-sm font-semibold text-term-text mb-1">Enter your code</h1>
            <p className="text-[11px] text-term-muted leading-relaxed mb-4">
              Open your authenticator app and enter the 6-digit code, or use one of your
              recovery codes if the device isn't available.
            </p>

            <form onSubmit={submitMfa} className="space-y-3">
              <Field
                label="Code"
                value={mfaCode}
                onChange={setMfaCode}
                placeholder="123456"
                autoComplete="one-time-code"
                autoFocus
                required
              />

              {error && (
                <div className="p-2 rounded border border-term-down/40 bg-term-down/10">
                  <p className="text-[11px] text-term-down leading-relaxed">{error}</p>
                </div>
              )}

              <button
                type="submit"
                disabled={busy || !mfaCode.trim()}
                className="w-full btn-term-primary py-2 text-xs"
              >
                {busy ? "Verifying…" : "Verify"}
              </button>
            </form>

            <button
              onClick={() => {
                setMfaToken(null);
                setMfaCode("");
                setError(null);
              }}
              className="w-full text-[11px] text-term-muted hover:text-term-text mt-3 transition-colors"
            >
              Back to sign in
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (mode === "forgot" || mode === "reset") {
    return (
      <div className="h-full flex items-center justify-center bg-term-bg p-6">
        <div className="w-full max-w-sm">
          <Brand onBack={onBack} />

          <div className="bg-term-panel border border-term-border rounded-lg p-5">
            <h1 className="text-sm font-semibold text-term-text mb-1">
              {mode === "forgot" ? "Reset your password" : "Choose a new password"}
            </h1>
            <p className="text-[11px] text-term-muted leading-relaxed mb-4">
              {mode === "forgot"
                ? "Enter your account email and, if it exists, we'll send a reset link."
                : "This link is valid for 15 minutes and works once."}
            </p>

            <form onSubmit={mode === "forgot" ? submitForgot : submitReset} className="space-y-3">
              {mode === "forgot" ? (
                <Field
                  label="Email"
                  type="email"
                  value={email}
                  onChange={setEmail}
                  required
                  autoComplete="username"
                  autoFocus
                />
              ) : (
                <Field
                  label="New password"
                  type="password"
                  value={newPassword}
                  onChange={setNewPassword}
                  required
                  autoComplete="new-password"
                  hint="At least 10 characters. A passphrase is fine."
                  autoFocus
                />
              )}

              {info && (
                <div className="p-2 rounded border border-brand-accent/40 bg-brand-accent/10">
                  <p className="text-[11px] text-term-text leading-relaxed">{info}</p>
                </div>
              )}
              {error && (
                <div className="p-2 rounded border border-term-down/40 bg-term-down/10">
                  <p className="text-[11px] text-term-down leading-relaxed">{error}</p>
                </div>
              )}

              <button
                type="submit"
                disabled={busy || (mode === "forgot" ? !email : !newPassword)}
                className="w-full btn-term-primary py-2 text-xs"
              >
                {busy ? "Working…" : mode === "forgot" ? "Send reset link" : "Reset password"}
              </button>
            </form>

            <button
              onClick={() => {
                setMode("login");
                setResetToken(null);
                setError(null);
                setInfo(null);
              }}
              className="w-full text-[11px] text-term-muted hover:text-term-text mt-3 transition-colors"
            >
              Back to sign in
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full flex items-center justify-center bg-term-bg p-6">
      <div className="w-full max-w-sm">
        <Brand onBack={onBack} />

        <div className="bg-term-panel border border-term-border rounded-lg p-5">
          <h1 className="text-sm font-semibold text-term-text mb-1">
            {firstRun ? "Create your account" : mode === "register" ? "Create an account" : "Sign in"}
          </h1>
          <p className="text-[11px] text-term-muted leading-relaxed mb-4">
            {firstRun
              ? "This is the first account on this instance, so it will be the administrator."
              : mode === "register"
                ? "Your watchlist, alerts, positions and strategies are private to your account."
                : "Signed-in sessions keep your watchlist, positions and strategies separate."}
          </p>

          <form onSubmit={submit} className="space-y-3">
            {mode === "register" && (
              <Field
                label="Display name"
                value={displayName}
                onChange={setDisplayName}
                placeholder="optional"
                autoComplete="name"
              />
            )}
            <Field
              label="Email"
              type="email"
              value={email}
              onChange={setEmail}
              required
              autoComplete="username"
            />
            <Field
              label="Password"
              type="password"
              value={password}
              onChange={setPassword}
              required
              autoComplete={mode === "register" ? "new-password" : "current-password"}
              hint={mode === "register" ? "At least 10 characters. A passphrase is fine." : null}
            />

            {info && (
              <div className="p-2 rounded border border-brand-accent/40 bg-brand-accent/10">
                <p className="text-[11px] text-term-text leading-relaxed">{info}</p>
              </div>
            )}
            {error && (
              <div className="p-2 rounded border border-term-down/40 bg-term-down/10">
                <p className="text-[11px] text-term-down leading-relaxed">{error}</p>
              </div>
            )}

            <button
              type="submit"
              disabled={busy || !email || !password}
              className="w-full btn-term-primary py-2 text-xs"
            >
              {busy ? "Working…" : mode === "register" ? "Create account" : "Sign in"}
            </button>
          </form>

          {mode === "login" && (
            <button
              onClick={() => {
                setMode("forgot");
                setError(null);
                setInfo(null);
              }}
              className="w-full text-[11px] text-term-muted hover:text-term-text mt-3 transition-colors"
            >
              Forgot password?
            </button>
          )}

          {status?.signup_allowed && status?.has_accounts && (
            <button
              onClick={() => {
                setMode(mode === "register" ? "login" : "register");
                setError(null);
                setInfo(null);
              }}
              className="w-full text-[11px] text-term-muted hover:text-term-text mt-3 transition-colors"
            >
              {mode === "register" ? "Have an account? Sign in" : "Create a new account"}
            </button>
          )}
        </div>

        {status && (
          <p className="text-[10px] text-term-dim leading-relaxed mt-3 text-center">
            {status.note}
          </p>
        )}
      </div>
    </div>
  );
}

function Brand({ onBack }) {
  return (
    <div className="mb-6 text-center">
      <button
        onClick={onBack}
        disabled={!onBack}
        className="inline-flex items-center gap-2 disabled:cursor-default group"
        title={onBack ? "Back to the home page" : undefined}
      >
        <img src={logo} alt="" className="h-7 w-7 rounded" />
        <span className="font-display text-lg font-semibold text-term-text tracking-tight
                         group-enabled:group-hover:text-white transition-colors">
          Legend Trade
        </span>
      </button>
    </div>
  );
}

function Field({ label, hint, value, onChange, ...props }) {
  return (
    <label className="block">
      <span className="text-[10px] uppercase tracking-wide text-term-dim">{label}</span>
      <input
        {...props}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full mt-1 bg-term-raised border border-term-border rounded px-2 py-1.5
                   text-xs text-term-text placeholder:text-term-dim
                   focus:outline-none focus:border-brand-accent"
      />
      {hint && <span className="text-[10px] text-term-dim mt-0.5 block">{hint}</span>}
    </label>
  );
}
