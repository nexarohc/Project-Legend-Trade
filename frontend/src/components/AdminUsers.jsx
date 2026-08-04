import { useCallback, useEffect, useState } from "react";
import { auth } from "../lib/tradingApi.js";

/**
 * Admin-only account management.
 *
 * Renders nothing at all for a non-admin — the endpoints behind it return 403
 * regardless, so this is presentation, not the security boundary. The server
 * is the boundary; hiding the panel just avoids showing controls that would
 * only ever fail.
 *
 * Deliberately narrow: see accounts, disable an abusive one, clear a lockout,
 * and reset MFA for someone who lost both their authenticator and every
 * recovery code. It is not a general CRUD screen — there is no way to edit
 * another account's email, password or secrets from here, because an
 * administrator has no legitimate need for them and the endpoint never
 * returns them.
 */
export default function AdminUsers() {
  const [users, setUsers] = useState(null);
  const [query, setQuery] = useState("");
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);
  const [forbidden, setForbidden] = useState(false);

  const load = useCallback(async (search = "") => {
    try {
      const result = await auth.adminListUsers(search);
      setUsers(result.users);
      setError(null);
    } catch (e) {
      // 403 simply means "not an admin" — hide the panel rather than
      // showing an error for a feature this account was never offered.
      if (String(e.message).includes("Admin access required")) {
        setForbidden(true);
      } else {
        setError(e.message);
      }
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  if (forbidden) return null;

  const act = async (fn, message) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await fn();
      setNotice(message);
      await load(query);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="mt-8">
      <h2 className="text-sm font-semibold mb-1">Accounts</h2>
      <p className="text-xs text-gray-500 dark:text-gray-400 mb-3">
        Instance administration. Disabling an account takes effect immediately —
        its existing sessions stop working on the next request, not at next sign-in.
      </p>

      <div className="flex gap-2 mb-3">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && load(query)}
          placeholder="Filter by email…"
          className="flex-1 px-2 py-1 text-xs rounded border border-gray-300 dark:border-gray-700
                     bg-white dark:bg-gray-900"
        />
        <button
          onClick={() => load(query)}
          className="px-3 py-1 text-xs rounded bg-gray-200 dark:bg-gray-700"
        >
          Search
        </button>
      </div>

      {error && <p className="text-xs text-red-600 mb-2">{error}</p>}
      {notice && <p className="text-xs text-green-600 mb-2">{notice}</p>}

      {users === null ? (
        <p className="text-xs text-gray-500">Loading accounts…</p>
      ) : users.length === 0 ? (
        <p className="text-xs text-gray-500">No accounts match.</p>
      ) : (
        <div className="space-y-2">
          {users.map((user) => (
            <div
              key={user.id}
              className={`rounded border p-2 text-xs ${
                user.is_active
                  ? "border-gray-300 dark:border-gray-700"
                  : "border-red-400 bg-red-50 dark:bg-red-950/30"
              }`}
            >
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0">
                  <div className="font-medium truncate">{user.email}</div>
                  <div className="text-gray-500 dark:text-gray-400 flex flex-wrap gap-x-2">
                    {user.is_admin && <span title="Instance administrator">admin</span>}
                    {!user.is_active && <span className="text-red-600">disabled</span>}
                    {user.locked && <span className="text-amber-600">locked</span>}
                    <span>{user.mfa_enabled ? "MFA on" : "MFA off"}</span>
                    <span>{user.email_verified ? "verified" : "unverified"}</span>
                  </div>
                </div>

                <div className="flex gap-1 shrink-0">
                  {user.locked && (
                    <button
                      disabled={busy}
                      onClick={() =>
                        act(() => auth.adminUnlock(user.id), `Lockout cleared for ${user.email}.`)
                      }
                      className="px-2 py-1 rounded bg-amber-100 dark:bg-amber-900/40 text-amber-800
                                 dark:text-amber-200 disabled:opacity-50"
                    >
                      Unlock
                    </button>
                  )}
                  {user.mfa_enabled && (
                    <button
                      disabled={busy}
                      onClick={() =>
                        act(
                          () => auth.adminResetMfa(user.email),
                          `MFA reset for ${user.email}. They can now sign in with their password alone.`,
                        )
                      }
                      className="px-2 py-1 rounded bg-gray-200 dark:bg-gray-700 disabled:opacity-50"
                      title="For someone who lost both their authenticator and every recovery code"
                    >
                      Reset MFA
                    </button>
                  )}
                  <button
                    disabled={busy}
                    onClick={() =>
                      act(
                        () => auth.adminSetActive(user.id, !user.is_active),
                        `${user.email} ${user.is_active ? "disabled" : "re-enabled"}.`,
                      )
                    }
                    className={`px-2 py-1 rounded disabled:opacity-50 ${
                      user.is_active
                        ? "bg-red-100 dark:bg-red-900/40 text-red-700 dark:text-red-300"
                        : "bg-green-100 dark:bg-green-900/40 text-green-700 dark:text-green-300"
                    }`}
                  >
                    {user.is_active ? "Disable" : "Enable"}
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
