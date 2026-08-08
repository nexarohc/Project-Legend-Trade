import { clearSession, getAccessToken, refreshSession } from "./auth.js";

export const BASE_URL = "http://127.0.0.1:8000";
const WS_BASE = "ws://127.0.0.1:8000/market/ws";

/**
 * The WebSocket URL, carrying the access token.
 *
 * Browsers cannot set headers on a WebSocket handshake, so the token has to
 * travel as a query parameter. Access tokens are short-lived precisely because
 * this puts them somewhere marginally more visible than a header.
 */
export function wsUrl() {
  const token = getAccessToken();
  return token ? `${WS_BASE}?token=${encodeURIComponent(token)}` : WS_BASE;
}

/**
 * Every call surfaces the backend's own `detail` message rather than a bare
 * status code. The API is careful to explain failures ("POLYGON_API_KEY is not
 * set", "Binance returned 451"), and swallowing that here would throw away the
 * most useful part of the response.
 */
async function send(path, options, token) {
  const headers = { "Content-Type": "application/json", ...(options.headers ?? {}) };
  if (token) headers.Authorization = `Bearer ${token}`;
  return fetch(`${BASE_URL}${path}`, { ...options, headers });
}

async function request(path, options = {}) {
  let response = await send(path, options, getAccessToken());

  // A 401 on a request that carried a token means the access token expired.
  // Refresh once and retry; if that fails the session is genuinely over.
  if (response.status === 401 && getAccessToken() && !path.startsWith("/auth/")) {
    try {
      const fresh = await refreshSession(BASE_URL);
      response = await send(path, options, fresh);
    } catch {
      clearSession();
    }
  }

  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }

  if (!response.ok) {
    const detail = data?.detail ?? text ?? response.statusText;
    const error = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    error.status = response.status;
    throw error;
  }
  return data;
}

const query = (params) => {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") search.append(key, value);
  });
  const string = search.toString();
  return string ? `?${string}` : "";
};

const post = (path, body) =>
  request(path, { method: "POST", body: JSON.stringify(body ?? {}) });

export const auth = {
  status: () => request("/auth/status"),
  register: (body) => post("/auth/register", body),
  login: (body) => post("/auth/login", body),
  me: () => request("/auth/me"),
  changePassword: (body) => post("/auth/password", body),
  forgotPassword: (email) => post("/auth/password/forgot", { email }),
  resetPassword: (token, newPassword) => post("/auth/password/reset", { token, new_password: newPassword }),
  verifyEmail: (token) => post("/auth/email/verify", { token }),
  resendVerification: () => post("/auth/email/resend"),
  logout: (everywhere = false) => post(`/auth/logout?everywhere=${everywhere}`),
  webhookUrl: () => request("/auth/webhook-url"),
  rotateWebhookUrl: () => post("/auth/webhook-url/rotate"),
  mfaStatus: () => request("/auth/mfa/status"),
  mfaSetup: () => post("/auth/mfa/setup"),
  mfaConfirm: (code) => post("/auth/mfa/confirm", { code }),
  mfaVerify: (mfaToken, code) => post("/auth/mfa/verify", { mfa_token: mfaToken, code }),
  mfaDisable: (password) => post("/auth/mfa/disable", { password }),

  exportMyData: () => request("/auth/me/export"),
  deleteMyAccount: (password, confirm) => post("/auth/me/delete", { password, confirm }),

  // Admin-only: 403 for everyone else, so the UI hides these entirely.
  adminListUsers: (query = "") =>
    request(`/auth/admin/users${query ? `?query=${encodeURIComponent(query)}` : ""}`),
  adminSetActive: (userId, active) => post(`/auth/admin/users/${userId}/active`, { active }),
  adminUnlock: (userId) => post(`/auth/admin/users/${userId}/unlock`),
  adminResetMfa: (email) => post("/auth/admin/mfa/reset", { email }),
};

export const scanner = {
  fields: () => request("/scanner/fields"),
  run: (body) => post("/scanner", body),
};

export const calendar = {
  events: (start, end, importance) => request(`/calendar${query({ start, end, importance })}`),
  upcoming: (days = 14) => request(`/calendar/upcoming${query({ days })}`),
};

export const execution = {
  status: () => request("/execution/status"),
  setMode: (mode) => post("/execution/mode", { mode }),
  brokers: () => request("/execution/brokers"),
  setBroker: (broker) => post("/execution/broker", { broker }),
  confirmLive: (body) => post("/execution/confirm-live", body),
  submitOrder: (body) => post("/execution/orders", body),
  orders: (limit = 50) => request(`/execution/orders${query({ limit })}`),
  reconcile: () => post("/execution/reconcile"),
  killSwitch: (body) => post("/execution/kill-switch", body),
  disengageKillSwitch: () => request("/execution/kill-switch", { method: "DELETE" }),
  rules: () => request("/execution/rules"),
  limits: () => request("/execution/limits"),
  setLimits: (overrides) => post("/execution/limits", overrides),
  resetLimits: () => request("/execution/limits", { method: "DELETE" }),
};

export const optionsPricing = {
  price: (body) => post("/options/price", body),
  impliedVolatility: (body) => post("/options/implied-volatility", body),
  historicalVolatility: (symbol, timeframe = "1d", window = 252) =>
    request(`/options/historical-volatility${query({ symbol, timeframe, window })}`),
  strategies: () => request("/options/strategies"),
  payoff: (body) => post("/options/payoff", body),
};

export const trading = {
  // --- market data ---------------------------------------------------------
  providers: () => request("/market/providers"),
  markets: () => request("/market/markets"),
  resolve: (symbol) => request(`/market/resolve${query({ symbol })}`),
  candles: (symbol, timeframe, limit = 500, provider) =>
    request(`/market/candles${query({ symbol, timeframe, limit, provider })}`),
  quote: (symbol, provider) => request(`/market/quote${query({ symbol, provider })}`),
  quotes: (symbols, provider) =>
    request(`/market/quotes${query({ symbols: symbols.join(","), provider })}`),
  search: (searchQuery, provider) =>
    request(`/market/search${query({ query: searchQuery, provider })}`),

  // --- analysis ------------------------------------------------------------
  analyze: (body) => post("/analysis", body),
  narrate: (body) => post("/analysis/narrate", body),
  research: (body) => post("/analysis/research", body),
  concepts: () => request("/analysis/concepts"),
  concept: (name, audience = "intermediate") =>
    request(`/analysis/concepts/${name}${query({ audience })}`),
  feedback: (body) => post("/analysis/feedback", body),
  calibration: (symbol) => request(`/analysis/calibration${query({ symbol })}`),
  lessons: () => request("/analysis/lessons"),

  // --- strategy ------------------------------------------------------------
  buildStrategy: (body) => post("/strategy/build", body),
  studyStrategy: (body) => post("/strategy/study", body),
  pine: (body) => post("/strategy/pine", body),
  saveStrategy: (body) => post("/strategy/save", body),
  savedStrategies: () => request("/strategy/saved"),
  deleteStrategy: (id) => request(`/strategy/saved/${id}`, { method: "DELETE" }),
  leaderboard: () => request("/strategy/leaderboard"),
  features: () => request("/strategy/features"),

  // --- watchlist, alerts, paper trading -----------------------------------
  watchlist: (withQuotes = true) =>
    request(`/trading/watchlist${query({ with_quotes: withQuotes })}`),
  addToWatchlist: (body) => post("/trading/watchlist", body),
  removeFromWatchlist: (id) => request(`/trading/watchlist/${id}`, { method: "DELETE" }),

  alerts: (activeOnly = false) => request(`/trading/alerts${query({ active_only: activeOnly })}`),
  createAlert: (body) => post("/trading/alerts", body),
  deleteAlert: (id) => request(`/trading/alerts/${id}`, { method: "DELETE" }),
  checkAlerts: () => post("/trading/alerts/check"),

  positions: (status = "open") => request(`/trading/paper/positions${query({ status })}`),
  openPosition: (body) => post("/trading/paper/open", body),
  closePosition: (id, body) => post(`/trading/paper/close/${id}`, body),
  syncPositions: () => post("/trading/paper/sync"),
  paperPerformance: () => request("/trading/paper/performance"),

  webhookHistory: () => request("/trading/webhook/history"),
};

export const TIMEFRAMES = ["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"];

/** Format a price with a sensible number of decimals for its magnitude. */
export function formatPrice(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const abs = Math.abs(value);
  if (abs >= 1000) return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (abs >= 1) return value.toFixed(2);
  if (abs >= 0.01) return value.toFixed(4);
  return value.toFixed(8);
}

export function formatPercent(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}%`;
}

export function formatNumber(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toLocaleString(undefined, { maximumFractionDigits: digits });
}
