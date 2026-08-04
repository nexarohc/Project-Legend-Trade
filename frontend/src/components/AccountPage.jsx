import EmailVerification from "./EmailVerification.jsx";
import MfaSettings from "./MfaSettings.jsx";
import AdminUsers from "./AdminUsers.jsx";

/**
 * Everything about the account, and nothing about the market.
 *
 * The three children each fetch their own state and each decide for themselves
 * whether they have anything to show — `AdminUsers` renders nothing at all for
 * a non-admin, so this page is the same component tree for every user and the
 * server's answer is what differs.
 */
export default function AccountPage() {
  return (
    <div className="max-w-2xl mx-auto px-6 py-8 space-y-8">
      <header>
        <h1 className="font-display text-xl font-semibold text-term-text">Account</h1>
        <p className="text-xs text-term-muted mt-1 leading-relaxed">
          Your watchlist, positions, strategies and risk limits are private to this
          account. Broker credentials are stored server-side and are never returned
          to the browser.
        </p>
      </header>

      <EmailVerification />
      <MfaSettings />
      <AdminUsers />
    </div>
  );
}
