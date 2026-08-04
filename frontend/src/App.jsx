import { useEffect, useState } from "react";
import Landing from "./components/Landing.jsx";
import SignIn from "./components/SignIn.jsx";
import TopBar from "./components/TopBar.jsx";
import AccountPage from "./components/AccountPage.jsx";
import TradingTerminal from "./components/terminal/TradingTerminal.jsx";
import { auth } from "./lib/tradingApi.js";
import { clearSession, getUser, isSignedIn } from "./lib/auth.js";

/**
 * Three screens, in one order: landing → sign-in → terminal.
 *
 * The landing page is the only thing an anonymous visitor sees, and it is shown
 * from local state rather than a router: there are three screens and two of
 * them must not be deep-linkable anyway, so a router would be more machinery
 * than the problem has. A returning signed-in visitor skips straight past it,
 * because someone with a live session came here to look at the market, not to
 * read marketing copy.
 */
export default function App() {
  // null = still asking the server whether authentication is enforced.
  const [authState, setAuthState] = useState(null);
  const [user, setUser] = useState(() => getUser());
  const [view, setView] = useState("terminal");
  const [showLanding, setShowLanding] = useState(() => !isSignedIn());
  const [verifyBanner, setVerifyBanner] = useState(null);

  // A verification link can be clicked whether or not this browser is currently
  // signed in, so this runs at the top level rather than inside SignIn — the
  // banner then renders over whichever screen ends up showing.
  useEffect(() => {
    const token = new URLSearchParams(window.location.search).get("verify_token");
    if (!token) return;
    window.history.replaceState({}, "", window.location.pathname);
    auth
      .verifyEmail(token)
      .then((r) => setVerifyBanner({ tone: "ok", text: `${r.email} is now verified.` }))
      .catch((e) => setVerifyBanner({ tone: "error", text: e.message }));
  }, []);

  // A password-reset link has to land on the sign-in form, not the landing page,
  // or the token in the URL is thrown away by the first click.
  useEffect(() => {
    if (new URLSearchParams(window.location.search).get("reset_token")) {
      setShowLanding(false);
    }
  }, []);

  // Ask the backend whether a session is needed. Failing open here would show
  // the terminal to someone who should be signing in, so an unreachable backend
  // is treated as "authentication required".
  useEffect(() => {
    auth
      .status()
      .then(setAuthState)
      .catch(() => setAuthState({ auth_required: true, unreachable: true }));
  }, []);

  const signOut = async () => {
    try {
      await auth.logout(false);
    } catch {
      /* the local session is cleared regardless of what the server says */
    }
    clearSession();
    setUser(null);
    setShowLanding(true);
  };

  const signedIn = (payload) => {
    setUser(payload);
    setShowLanding(false);
    setView("terminal");
  };

  const banner = verifyBanner && (
    <div
      className={`px-3 py-1.5 text-xs text-center ${
        verifyBanner.tone === "ok" ? "bg-term-up text-white" : "bg-term-down text-white"
      }`}
    >
      {verifyBanner.text}
      <button onClick={() => setVerifyBanner(null)} className="ml-3 underline">
        Dismiss
      </button>
    </div>
  );

  // The landing page needs no backend, so it renders before the auth check
  // resolves — a visitor on a slow connection sees the page, not a spinner.
  if (showLanding) {
    return (
      <>
        {banner}
        <Landing onGetStarted={() => setShowLanding(false)} />
      </>
    );
  }

  if (authState === null) {
    return (
      <>
        {banner}
        <div className="h-screen flex items-center justify-center bg-term-bg">
          <span className="text-xs text-term-muted">Connecting…</span>
        </div>
      </>
    );
  }

  if (authState.auth_required && !isSignedIn()) {
    return (
      <div className="h-screen flex flex-col bg-term-bg">
        {banner}
        <div className="flex-1 min-h-0">
          <SignIn onSignedIn={signedIn} onBack={() => setShowLanding(true)} />
        </div>
      </div>
    );
  }

  return (
    <div className="h-screen flex flex-col bg-term-bg">
      {banner}
      <TopBar
        view={view}
        onNavigate={setView}
        user={authState.auth_required ? user : null}
        onSignOut={signOut}
        onHome={() => setShowLanding(true)}
      />
      {/* The terminal scrolls each panel independently, so it must not sit
          inside the page's scroll container — a scrollbar wrapped around a live
          chart makes panning fight the page. */}
      {view === "terminal" ? (
        <div className="flex-1 min-h-0">
          <TradingTerminal />
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto">
          <AccountPage />
        </div>
      )}
    </div>
  );
}
