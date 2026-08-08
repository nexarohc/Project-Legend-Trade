/**
 * Token storage and session management.
 *
 * Tokens live in localStorage. That is a deliberate trade-off, not an
 * oversight: httpOnly cookies would resist XSS better, but the frontend is
 * served from a different origin than the API, so a cookie-based session needs
 * CORS credentials and CSRF protection to be got right rather than merely
 * configured. Given the access token is short-lived (30 minutes) and the app
 * renders no third-party content, the practical exposure is small. If this ever
 * serves untrusted content, move to httpOnly cookies with CSRF tokens.
 *
 * A single refresh promise is shared across callers, so ten concurrent 401s
 * trigger one refresh rather than ten competing ones.
 */

const ACCESS_KEY = "legend.access_token";
const REFRESH_KEY = "legend.refresh_token";
const USER_KEY = "legend.user";

let refreshInFlight = null;
const listeners = new Set();

function notify() {
  listeners.forEach((fn) => {
    try {
      fn(getUser());
    } catch (error) {
      console.error("auth listener failed", error);
    }
  });
}

export function onAuthChange(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getAccessToken() {
  try {
    return localStorage.getItem(ACCESS_KEY);
  } catch {
    return null;   // private browsing / storage disabled
  }
}

export function getRefreshToken() {
  try {
    return localStorage.getItem(REFRESH_KEY);
  } catch {
    return null;
  }
}

export function getUser() {
  try {
    const raw = localStorage.getItem(USER_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function isSignedIn() {
  return Boolean(getAccessToken());
}

export function storeSession({ access_token, refresh_token, user }) {
  try {
    localStorage.setItem(ACCESS_KEY, access_token);
    localStorage.setItem(REFRESH_KEY, refresh_token);
    if (user) localStorage.setItem(USER_KEY, JSON.stringify(user));
  } catch (error) {
    console.error("could not persist the session", error);
  }
  notify();
}

export function clearSession() {
  try {
    localStorage.removeItem(ACCESS_KEY);
    localStorage.removeItem(REFRESH_KEY);
    localStorage.removeItem(USER_KEY);
  } catch {
    /* nothing to clear */
  }
  refreshInFlight = null;
  notify();
}

/**
 * Exchange the refresh token for a new pair.
 *
 * Concurrent callers share one in-flight request: without this, a page that
 * fires several requests at once would send several refreshes, and each new
 * pair would invalidate the last.
 */
export function refreshSession(baseUrl) {
  if (refreshInFlight) return refreshInFlight;

  const token = getRefreshToken();
  if (!token) return Promise.reject(new Error("No refresh token stored."));

  refreshInFlight = fetch(`${baseUrl}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: token }),
  })
    .then(async (response) => {
      if (!response.ok) {
        // The refresh token is expired or revoked — the session is over.
        clearSession();
        throw new Error("Session expired. Sign in again.");
      }
      const data = await response.json();
      storeSession(data);
      return data.access_token;
    })
    .finally(() => {
      refreshInFlight = null;
    });

  return refreshInFlight;
}
