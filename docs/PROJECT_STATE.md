# Project state and plan

**Read this first when resuming work.** It is the handoff document for the
trading terminal: what is built, what was decided and why, what is deliberately
absent, and what comes next.

Last updated: 2026-08-04, after Wyckoff event labelling, position correlation,
the random-walk backtest proof, TOTP two-factor authentication, the Markets
browser UI, the market scanner, self-service password reset, the Tradier
broker adapter, the economic calendar, the admin-only gate on
broker-connected execution modes, email verification, the honest
`LIVE`/`DELAYED` stream badge, real-time equity streaming (Twelve Data)
verified working end-to-end, per-user risk-limit overrides, scheduled
broker reconciliation, an options pricing engine (Black-Scholes, binomial,
Greeks, payoff diagrams), a third broker adapter (Interactive Brokers),
admin-only MFA recovery, Redis-backed rate limiting, a generic additive
migration sweep, a stream-room leak found by load testing and fixed, and
structured logging plus an admin-gated metrics endpoint, admin account
management, and **the extraction of this platform out of `Project-DEX` into its
own repository with a real landing page** (see decision 37).

---

## Where things stand

Repo: [`nexarohc/Project-Legend-Trade`](https://github.com/nexarohc/Project-Legend-Trade)
Branch: `claude/institutional-ai-trading-platform-pib2l4`
PR: [#1](https://github.com/nexarohc/Project-Legend-Trade/pull/1) — **draft**, base is `main`

Everything before the extraction commit was built in
[`nexarohc/Project-DEX`](https://github.com/nexarohc/Project-DEX) on the same
branch name, where it shared a repository with a desktop AI assistant. The
commit hashes in the table below are from that repo; the code arrived here as a
single extraction commit. `Project-DEX` is deliberately left untouched — the
split was done copy-first and is reversible.

| Commit | Contents | CI |
|---|---|---|
| `f2f66c3` | Trading terminal: data, analysis, backtest, Pine, UI | All green |
| `da89f9c` | Authentication, per-user isolation, rate limiting | All green on `3f68ad9` |
| `9f91505` | Broker execution: adapter, guardrails, engine, API, UI | Verify on resume |
| `719f0cc` | Random-walk backtest honesty proof (ported from the parallel PR #2) | Verify on resume |
| `fbe8fee` | Wyckoff event labelling (`trading/wyckoff.py`), wired into the UI | Verify on resume |
| `418384c` | Correlation across open positions (`risk.compute_position_correlation`) | Verify on resume |
| `a4fceab` | TOTP two-factor authentication (`backend/app/mfa.py`), full setup/login/disable UI | Verify on resume |
| `4e2f8c4` | Markets browser UI (category → exchange → search/resolve → chart) | Verify on resume |
| `a20e515` | Market scanner (`trading/scanner.py` + `/scanner` API + `ScannerPanel` UI) | All green |
| `5746969` | Self-service password reset (`/auth/password/forgot`, `/reset`, `app/mailer.py`) | All green |
| `9e4ed58` | Tradier broker adapter, `POST /execution/broker`, broker selector UI | Verify on resume |
| `7c0cb8a` | Economic calendar (`trading/econcalendar.py` + `/calendar` API + Calendar panel), wired into `news_risk` | Verify on resume |
| `0eef78e` | Broker-connected execution modes restricted to the instance admin | Verify on resume |
| `5e181a1` | Email verification (`/auth/email/verify`, `/auth/email/resend`), informational only | Verify on resume |
| `602e524` | Honest `LIVE`/`DELAYED` stream badge (`snapshot.live`) | Verify on resume |
| `a6f13c3` | Docs correction: tested a real Finnhub free-tier key and found it does NOT enable working equity streaming (candle history 403s) | Verify on resume |
| `759e805` | Real-time equity streaming actually working: Twelve Data free tier tested and verified end-to-end (candles + WebSocket + live browser check); Finnhub and Polygon free tiers confirmed dead ends | Verify on resume |
| `db5f658` | Per-user risk limit overrides (`/execution/limits`, tighten-only) and scheduled broker reconcile (`trading/execution/scheduler.py`, background loop) | Verify on resume |
| `6513ee5` | Options pricing engine: Black-Scholes, binomial (American), implied vol, historical vol, multi-leg payoff diagrams (`trading/options.py`, `/options` API, `OptionsPanel.jsx`) — closes the "no greeks" gap, no live option chain (see decision 29) | Verify on resume |
| `179963d` | Interactive Brokers adapter (`trading/execution/ibkr.py`), a third `POST /execution/broker` option — unverified more strongly than Tradier's and architecturally different (Client Portal Gateway, not a bearer token; see decision 30) | Verify on resume |
| `c9309fe` | Admin-only MFA recovery (`POST /auth/admin/mfa/reset`) — closes the "losing both the authenticator and the recovery codes needs database access" gap; deliberately not folded into password reset (see decision 31) | Verify on resume |
| `5dd6f89` | Redis-backed rate limiting (`app/ratelimit_redis.py`, opt-in via `REDIS_URL`) so buckets are shared across workers instead of multiplying by worker count; degrades to in-process on a Redis outage (see decision 32). CI now runs a Redis service so this path is actually exercised | Verify on resume |
| `1f7a4c9` | Generic additive migration sweep (`_add_missing_columns`) so a model column with no hand-written migration block can no longer silently break existing databases; Alembic evaluated and deliberately not adopted (see decision 33) | Verify on resume |
| `9b2e1d4` | **Stream-room leak fixed** — the WebSocket disconnect path unsubscribed without the provider it subscribed with, leaking an upstream room per client (bug 5, decision 34). Found by a 150-client load test: 50 rooms leaked before, 0 after | Verify on resume |
| `4e77b12` | Structured JSON logging (`LOG_FORMAT=json`, uvicorn included) and an admin-gated Prometheus `/metrics` endpoint with a route-template cardinality guard (`app/observability.py`, decision 35) | Verify on resume |
| `(DEX)` | Admin account management: list/disable/enable/unlock users plus MFA reset, with an Accounts panel that is invisible to non-admins (decision 36) | All green |
| _this one_ | **Extraction into `Project-Legend-Trade`** — assistant routers, models, tests and frontend removed; `DEX_*` settings renamed to `LEGEND_*` with back-compat aliases (one deliberate exception); a real landing page, three loaded typefaces, and two layout bugs fixed (decision 37) | This PR |

630 tests are collected here; 622 pass and 8 skip with no Redis reachable — the
Redis-backed rate-limit tests skip rather than fail. None require network access.
The count dropped from 645 in `Project-DEX` because the assistant's orchestrator,
permissions and memory tests left with the code they covered.

### Verified working against live data

- Charts render 500 real candles and update tick-by-tick over WebSocket
  (confirmed in a real browser, not asserted from unit tests).
- The full analysis pipeline runs on live prices in ~50 ms.
- A mediocre strategy was correctly **REJECTED** by the audit with specific
  reasons — the engine does not rubber-stamp.
- Full auth flow: first-run setup → sign in → authenticated WebSocket → sign out
  re-gates the app.
- Docker image builds, container starts, `/health` responds (verified in CI).
- Execution panel against a live backend with `LEGEND_ENABLE_LIVE_TRADING=true`:
  paper is the default, the live banner is absent on paper and unmissable on
  live, live orders are refused until the phrase is typed, and the kill switch
  banner appears — checked in a real browser with zero console errors.
- The full spring → test → sign-of-strength → last-point-of-support sequence,
  and its mirror (upthrust → test → sign-of-weakness → last-point-of-supply),
  detected in the correct order against a hand-built fixture; a steady trend
  produces zero false springs, verified both directly and by disabling the
  guard that prevents it and watching the false positives reappear.
- The full TOTP lifecycle in a real browser: register → Settings → enable MFA
  → confirm with a code computed from the displayed secret → ten recovery
  codes shown once → sign out → sign back in → MFA challenge screen (not
  tokens) → verify with a fresh code → signed in. Zero console errors besides
  a pre-existing, unrelated missing favicon.
- Position correlation measured correctly end to end through the real API:
  opening a paper position then analyzing a different symbol surfaces a
  measured `correlation_risk` and per-symbol evidence; analyzing the *same*
  symbol as the open position correctly stays `unmodelled` rather than
  correlating a series with itself.
- The Markets browser in a real browser, twice: (1) US Stocks → NASDAQ →
  typed `LASR` → live search hit the sandbox's known Yahoo rate-limit (a real
  ~14s failure, not simulated) → the resolve fallback correctly took over,
  showed `NASDAQ:LASR`, and charted it; (2) Crypto → an example chip clicked
  directly → charted instantly with no search step at all. Confirmed selecting
  an exchange chip actually qualifies a bare ticker before resolving (`NASDAQ`
  + `LASR` → resolves as `NASDAQ:LASR`, not a bare, exchange-less ticker) —
  caught and fixed after the first pass resolved without the exchange prefix.
- The scanner in a real browser: loaded the watchlist, ran the default
  `trend == bullish` filter against `BTCUSDT`/`ETHUSDT`, and got back
  "RESULTS — 2 OF 2 MATCHED" with the correct prices and the evidence line
  `trend == bullish — measured 'bullish'` for each. Zero console errors besides
  the pre-existing favicon 404. (First verification attempt reported a false
  failure — the test script checked for title-case "Symbols"/"Filters" text,
  but the panel's CSS `uppercase` class transforms the actual rendered text
  the browser exposes, so the check needed to be case-insensitive. Not a bug
  in the app.)
- Password reset, full round trip in a real browser against a fresh database
  with `LEGEND_AUTH_REQUIRED=always`: registered an account, signed out, clicked
  "Forgot password?", submitted the email, got the generic "if that address
  has an account…" confirmation, pulled the reset link from the backend log
  (SMTP deliberately left unconfigured for this run), opened it, set a new
  password, was told every session was signed out, signed in with the *new*
  password successfully, and confirmed the *old* password was then rejected
  with the standard generic "Incorrect email or password" — proving the reset
  actually replaced the hash rather than merely appearing to.
- The broker selector in a real browser: the Execution panel lists both
  `alpaca` and `tradier`, clicking `tradier` switches `/execution/status`'s
  `broker` field and shows "Broker set to tradier. Any live confirmation was
  cleared." Zero console errors besides the pre-existing favicon 404. The
  Tradier *adapter itself* is unverified against a live account — see
  decision 22 — this only confirms the broker-selection plumbing works, which
  is everything that can be checked without real Tradier credentials.
- The economic calendar, both paths, in a real browser: (1) with no
  `FRED_API_KEY` configured, the panel shows "FRED_API_KEY is not set" plus a
  link to free registration, not a blank or misleading state; (2) with a
  stubbed calendar service standing in for a real FRED response, the panel
  correctly rendered four events with the right importance badges (two high,
  one medium, one low). Zero console errors besides the pre-existing favicon
  404 in either case.
- The admin-only broker gate, two accounts, one real browser session: the
  first (admin) account's Execution panel let it switch to `broker_paper`
  and showed the broker selector; the second (non-admin) account on the same
  instance saw the button disabled with the "limited to the instance
  administrator" explanation and the broker selector hidden entirely.
- The admin Accounts panel in a real browser against a live server with
  `LEGEND_AUTH_REQUIRED=always`, both roles: the admin saw both accounts and
  disabled one (the row turning red with an Enable button, and the account's
  existing sessions refused on the next request); the **non-admin saw no
  Accounts section at all and none of the other account's details**. The
  self-deactivation guard was checked the way an operator would actually hit
  it — clicking Disable on your own row — and surfaced "You cannot deactivate
  your own account." while leaving the session signed in. Only console error
  was the pre-existing favicon 404.
- Structured logging and metrics against a real running server with
  `LEGEND_AUTH_REQUIRED=always`. JSON logging: **every** line parsed as JSON,
  11 of 11 — including uvicorn's own access lines, which needed an explicit
  fix (see decision 35) because uvicorn installs its own handlers with
  `propagate=False` and a root-logger config alone left them as plain text.
  The metrics gate: anonymous → 401 with no metrics in the body, non-admin →
  403, admin → 200 with real counters. The cardinality guard was tested
  adversarially — 40 requests to distinct random URLs collapsed to a single
  `route="<unmatched>"` label rather than creating 40 time series — and the
  histogram was checked for well-formedness (cumulative buckets, `+Inf`
  equal to the observation count, matching `_sum`/`_count`).
- Stream-hub load testing against a real running server, which found a real
  bug rather than confirming a guess: 150 concurrent WebSocket clients across
  25 symbols with mixed providers, disconnected abruptly the way a closing
  tab does. **50 upstream rooms leaked before the fix, 0 after** — verified
  by reverting the fix and re-running as a control, which reproduced exactly
  the predicted split (the 100 clients using an explicit provider leaked into
  50 rooms; the 50 using none cleaned up correctly). Fan-out was confirmed in
  the same run: 200 clients collapsed to 60 shared rooms, 50 clients on one
  symbol shared exactly one upstream, and a room stayed open until its last
  subscriber left.
- Migration drift, by simulating the exact mistake it guards against:
  appended three columns to the `users` model with no hand-written migration
  block (a nullable string, a `NOT NULL` boolean, a `NOT NULL` integer), ran
  the migration, and all three were added automatically with the pre-existing
  row intact and correctly defaulted; a second run was a no-op. Separately, a
  hand-built pre-authentication database (12 columns, no MFA/reset/verify)
  was brought fully up to the model's current 17. The refusal path was
  checked too: a `NOT NULL DATETIME` with no default on a table with rows is
  skipped with an error rather than backfilled with a fabricated timestamp.
- Redis-backed rate limiting against a **real Redis server**, end to end
  through the HTTP path rather than only at the bucket level: with
  `REDIS_URL` set, `/auth/login` allowed exactly 10 attempts then returned
  429, and the bucket was visible in Redis (`legend:rl:login:127.0.0.1`) with
  its remaining tokens. The control experiment is the one that matters:
  two `RateLimiter` instances standing in for two workers allowed **6 of 6**
  requests in-process (each getting its own full budget — the actual bug)
  versus **3 of 6** Redis-backed (one shared budget — the fix). The outage
  path was verified by killing Redis out from under the running server: it
  stayed up (not fail-closed), still limited at 10 via the in-process
  fallback (not fail-open), logged the degradation exactly once across 13
  requests, and logged recovery automatically when Redis came back. With
  Redis stopped the Redis-dependent tests skip rather than fail, so the
  suite still passes with no network access.
- Admin MFA recovery, full round trip against a live server with
  `LEGEND_AUTH_REQUIRED=always` and a fresh database: registered an admin and a
  second account, enrolled real TOTP on the second (confirmed with a code
  computed from the returned secret, ten recovery codes issued), confirmed
  its login then returned an `mfa_token` challenge rather than tokens —
  i.e. genuinely locked out — then called `POST /auth/admin/mfa/reset` with
  the admin's bearer token and watched the same login return a real
  `access_token` with `mfa_enabled: false`. API-only; there is no admin
  screen in the UI to drive this from, which is why it was verified by curl
  against a running server rather than in a browser.
- Email verification, full round trip in a real browser: registered an
  account, confirmed the Settings page showed "hasn't been confirmed yet"
  with a resend button, pulled the verification link from the backend log
  (SMTP unconfigured), visited it on the same origin as the active session
  (a first attempt visited it on `localhost` while the session lived on
  `127.0.0.1` — different origins, different `localStorage`, so the session
  looked lost; not a bug, a sandbox artifact of testing two hostnames for one
  server), got "is now verified," and confirmed the Settings notice
  disappeared live without needing to sign out and back in.
- The stream badge, both markets, in a real browser: BTCUSDT (crypto, no key
  needed) showed genuine `LIVE`; AAPL (with a stubbed provider standing in
  for the sandbox's real Yahoo rate-limiting, so this was tested against the
  actual `snapshot.live` logic rather than blocked by an unrelated network
  issue) showed `DELAYED` with a tooltip naming the three env vars that
  enable real streaming for that market. First run showed both symbols as
  `DELAYED` because the stub over-matched every symbol including crypto —
  caught and fixed by scoping it to the one stock symbol under test.

---

## Decisions worth not re-litigating

These were deliberate. Changing them is fine, but do it knowingly.

1. **Describe before predicting.** `trading/analysis.py` computes and explains
   market context, structure, price action, volume, indicators, SMC and levels
   *before* the probability engine may run. Enforced by call order in code.
   Every probability carries weighted, individually-explained factors.

2. **The backtester is pessimistic on purpose.** Next-bar-open fills;
   stop-before-target when one bar spans both; gaps fill at the open; costs on
   every fill including partials; size derived from stop distance. Each is
   asserted directly in `tests/test_trading_strategy.py`. **Do not "fix" these
   to improve backtest results** — they are the reason the results mean anything.

3. **The audit's verdict derives from its checks, not from the return.** A
   strategy that made 400% on four trades with no costs gets REJECTED. Approval
   means the backtest is *believable*, not that it will be profitable.

4. **Live execution is gated four independent ways** and defaults off: a
   server-side env flag needing a restart, an explicit mode switch, a typed
   phrase that expires, and separately-named live credentials. The webhook
   receiver still records and never executes — it is a public endpoint whose
   URL gets pasted into third-party config, and there is deliberately no path
   from it to an order.

9. **Guardrails fail closed and live in one file.** A check whose inputs are
   unavailable refuses the order rather than passing. Every refusal reason is
   in `trading/execution/guardrails.py` so the full list is readable at once.
   **Do not add a "skip guardrails" flag** — the refusal path is the product.

10. **Broker credentials come from the environment, never the database.**
    Per-user broker keys would need encryption at rest, and there is no vetted
    crypto library here. Instance-level access is the honest trade.

5. **Auth mode `auto`.** Required unless bound to loopback. Desktop use stays
   frictionless; a deployed instance is protected by default.

6. **404, not 403, for another account's rows.** So ids cannot be probed.

7. **Pure-Python analysis stack.** No numpy/pandas, so it imports anywhere and
   the test suite needs no scientific stack.

8. **No bundled ticker list.** Symbol lookup hits the provider's live index, so
   a company that IPO'd this morning is chartable this afternoon.

11. **Wyckoff events are judged against a locally-computed range, not the
    global one in `StructureReport`.** A spring found on bar 60 must still be
    recognisable after fifty more bars of the resulting uptrend, by which point
    `structure.range_high`/`phase` have already moved on. `trading/wyckoff.py`
    recomputes the range from the 40 bars before each sweep instead of trusting
    the series-end snapshot. **Do not "simplify" this back to using
    `structure.range_high` directly** — that was the first version, and it
    silently failed to detect any event whose confirmation arrived after the
    market had already broken out.

12. **Containment alone does not prove a range.** A market drifting slowly and
    steadily can look "contained" over any single 40-bar window even while
    trending hard zoomed out — `tests/test_trading_wyckoff.py` caught this on
    `trending_series` before the fix. `MAX_DIRECTIONAL_EFFICIENCY` in
    `trading/wyckoff.py` additionally requires that net travel stay under 40%
    of the window's range; a real range oscillates back near where it started,
    a trend doesn't. **Do not drop this guard to "loosen" spring/upthrust
    detection** — it's what keeps a slow trend from producing false springs.

13. **Correlation is computed on returns, matched by timestamp, not on price
    levels or by index.** Two instruments that both drifted upward over the
    window would otherwise show a spurious high correlation. A pair with
    fewer than 30 overlapping bars is reported as insufficient data rather
    than given a number — **do not lower `min_overlap` to "fill in" more
    pairs**, a correlation from 5 overlapping bars is noise with a decimal
    point.

14. **Which symbols to correlate against is a database question, not a
    `trading/` question.** `trading/risk.py` and `trading/analysis.py` stay
    network- and DB-free (`compute_position_correlation` takes candles the
    caller already fetched); the query for the account's open positions lives
    only in `backend/app/routers/analysis.py`, capped at 5 to bound the extra
    provider fetches one analysis request can trigger.

15. **The MFA token type extends the existing token-type-confusion guard
    rather than sidestepping it.** `security.py`'s `TOKEN_TYPES` grew from
    `("access", "refresh")` to include `"mfa"`; `decode_token`'s
    `expected_type` check (already what stops a refresh token being replayed
    as an access token) covers the new type automatically. **Do not build a
    separate, ad-hoc token for the MFA-pending state** — that would mean
    re-deriving the algorithm-pinning and expiry checks `decode_token` already
    gets right.

16. **A password alone is not a completed login once MFA is on.**
    `record_login_success` (which resets the failed-attempt counter and stamps
    `last_login_at`) is called from `/auth/mfa/verify`, not from `/auth/login`,
    for an MFA-enabled account. Calling it at the password step would make a
    stolen password look like a completed sign-in in the account's own
    history before the second factor was ever checked.

17. **The TOTP secret is stored in the database as-is, not hashed** — the
    same tradeoff as the webhook token, for the same reason broker credentials
    stay in the environment rather than an encrypted column: verifying a
    future code requires reproducing the secret, which only symmetric storage
    allows, and there's no vetted crypto library here to encrypt it safely.
    Recovery codes don't have this problem (compared, never reproduced), so
    they're hashed exactly like a password. **Do not "fix" this by hashing
    the TOTP secret** — that would make MFA unable to verify anything.

18. **Resolve is a separate, always-on path in the Markets browser, not a
    fallback bolted onto search.** `/market/resolve` is pure symbol parsing —
    no network call — while `/market/search` depends on a provider's live
    index responding, and Yahoo (the keyless default) can take upwards of ten
    seconds to fail under this sandbox's rate limiting, confirmed directly
    (~14s to a real 502). Keeping resolve as its own button rather than an
    automatic retry-after-search-fails means a symbol you already know how to
    spell never has to wait on search at all.

19. **The scanner filters a caller-supplied symbol list; it does not enumerate
    a market.** Same rule as `markets.py`: no keyless provider exposes a
    screener API, so "scan every NASDAQ stock" would mean silently maintaining
    a static universe. `run_scan` reuses `regime.build_context` and
    `indicators.compute_all` directly, so a scan result can never disagree
    with a full analysis of the same symbol. **Do not add a bundled ticker
    list to make this feel more like a "real" screener** — that's exactly the
    dishonesty this platform avoids everywhere else. Note also: stock/equity
    symbols route through the test fixture's stubbed provider correctly, but
    `provider_chain_for`'s crypto branch calls `healthy_crypto_provider()`
    first, which does its own raw network probe and bypasses the monkeypatched
    `get_provider` — a pre-existing test-harness quirk, not something this
    feature introduced. Use stock symbols, not crypto, in any new test that
    stubs the provider layer.

20. **A password-reset token is made single-use by a stored timestamp, not a
    revocation table.** `User.password_reset_requested_at` is stamped with the
    token's own `iat` when `/auth/password/forgot` issues it; `/auth/password/reset`
    requires the two to match exactly and clears the column on success. A
    signature and expiry alone would make the token merely *valid*, not
    *unspent* — this is the same problem `tokens_valid_from` solves for
    sessions, reused rather than duplicated with a new mechanism. Requesting a
    second reset overwrites the timestamp, so only the newest link ever works
    — **do not "fix" this to allow both links to work**, that would mean an
    attacker who triggers extra reset emails keeps a valid link alive
    indefinitely instead of the legitimate owner's latest one winning.

21. **There is no mail-sending SaaS wired in for password reset.** `app/mailer.py`
    sends over plain stdlib `smtplib` when `SMTP_HOST` is configured and
    otherwise logs the reset link. That is a deliberate stopping point, not a
    placeholder to fill in later: adding SendGrid/SES/etc. would be a vendor
    and billing decision, the same category as a payment processor, not a
    code change this repo should make unprompted. An operator who wants real
    email sets their own SMTP credentials (Gmail app password, a mail
    relay, whatever they already have) exactly like they set Alpaca or Polygon
    keys — instance-level configuration, not a business decision made on
    their behalf.

22. **Tradier's adapter is real code, not a stub, but it is unverified against
    a live account — stated loudly rather than quietly shipped as equivalent
    to Alpaca's.** Two things specifically were implemented from Tradier's
    documented API shape without a live response to check them against: the
    balances endpoint's nesting (margin vs. cash vs. pdt sub-objects carry
    buying power under different keys — `trading/execution/tradier.py`'s
    `get_account` falls back through all three, then to `total_cash`, rather
    than assuming one shape) and the `otoco`/`oto` indexed-field format used
    to attach a stop atomically (`symbol[0]`, `side[0]`, `symbol[1]`, ...).
    Every unit test in `tests/test_tradier.py` stubs `httpx.request` and so
    proves the adapter's own request-building and parsing are internally
    consistent — it cannot prove Tradier's real API matches these shapes.
    **Run a real round trip against `TRADIER_PAPER_TOKEN` in sandbox mode
    before ever setting `TRADIER_LIVE_*`.** Also: this adapter is **long-only**
    — Tradier distinguishes `sell` (closing a long) from `sell_short` (opening
    a short), a distinction `OrderSide` doesn't carry, so `close_position`
    refuses outright on a short rather than risk mismapping the side. Do not
    "fix" that by guessing at a mapping; extending `OrderSide` to carry
    position-direction is the real fix, and it touches the shared interface
    every broker and the guardrails use.

23. **The economic calendar's importance ranking is a hand-maintained
    heuristic, not something FRED provides.** FRED's release catalogue has no
    impact tier; `trading/econcalendar.py`'s `classify_importance` matches
    release names against a small, explicitly incomplete keyword list
    (employment, GDP, CPI, PCE → high; PPI, retail sales, industrial
    production → medium; everything else → low) and every place it's shown
    says "estimated," never presents it as official. **FOMC rate decisions are
    deliberately not included** — they aren't part of FRED's `releases`
    catalogue, and inventing dates for them would be exactly the kind of
    fabricated data this platform refuses everywhere else. Dates are also
    day-level, not time-of-day, because that's the granularity FRED's
    `releases/dates` endpoint actually returns.

24. **The calendar fetch lives in `analyze_symbol`, not the backend router —
    unlike the correlation query.** Correlation needs the account's open
    positions, a database question that only the router can answer;
    `upcoming_high_impact_events` needs no user-specific data at all, so it's
    fetched the same way HTF/LTF context is: directly inside `analyze_symbol`
    via `econcalendar.calendar_service`, with `trading/risk.py`'s
    `assess_risk` staying just as network-free as it already was for
    correlation. **Do not move this fetch into the router** on the mistaken
    assumption it must mirror correlation exactly — the two enhancements have
    different dependencies, and the code should reflect that.

25. **Broker-connected execution modes are admin-only, enforced server-side.**
    The user confirmed the intent to publish this as a real multi-user app;
    broker credentials being instance-level (decision "Broker credentials come
    from the environment, never the database" above) means every account
    sharing one broker connection is not an acceptable multi-user default.
    `backend/app/routers/execution.py`'s `_require_admin_for_shared_broker`
    rejects `POST /execution/mode` (to anything but `paper`), `POST
    /execution/broker`, and `POST /execution/confirm-live` with 403 for any
    non-admin account; `GET /execution/status` returns `user_is_admin` so the
    UI can disable those controls up front rather than only failing on
    submit. The first registered account is always admin (existing behavior
    from registration), and the implicit local account used when auth is
    disabled is always admin too, so a single-operator desktop instance sees
    no change. **This is a stopgap, not a full answer** — it makes "public
    users get paper only" true today, but real per-user broker access still
    needs per-user encrypted credentials (see the gaps list) if that's ever
    wanted for a public deployment.

26. **Email verification has no single-use mechanism, unlike password reset,
    because it doesn't need one.** A password-reset token changes account
    state (the password) and must not be replayable, so it's checked against
    a stored timestamp. Verifying an email twice does nothing — the second
    call is a harmless no-op — so `/auth/email/verify` only checks the
    token's own signature and expiry, same as an access token. **Do not add
    single-use tracking here "for consistency" with password reset** — the
    two tokens solve different problems and only one of them needs it.
    Also: verification is sent automatically at registration and available
    via `/auth/email/resend`, but it **gates nothing** — an unverified
    account can still sign in, trade paper, and use every feature. It exists
    so a published instance knows an address is real, not as a login wall.

27. **The stream badge reads `snapshot.live`, not WebSocket connection
    status, because those are different facts.** The socket being open only
    means the client can receive frames — it says nothing about whether the
    upstream source is a real push or `StreamHub` polling REST every ~15s and
    relaying it over the same connection. Both look identical as `candle`
    frames, so before this, a stock with no equity API key configured showed
    the exact same green pulsing `LIVE` badge as tick-by-tick crypto — a real
    misrepresentation, found while checking whether equity streaming
    (`trading/providers/{polygon,twelvedata,finnhub}.py` all already
    implement `stream_candles`) was actually reachable end-to-end.
    `backend/app/routers/market.py`'s snapshot handler now looks up
    `get_provider(history.provider).supports_streaming` and sends it as
    `snapshot.live`, failing closed to `False` on an unrecognised provider
    name rather than raising — the only way that path triggers in practice is
    a test stub whose name isn't a registered factory, not a real deployment.
    **Do not derive this from `stream.status` instead** — that only ever
    tells you the socket is connected, never what's actually behind it.

28. **Per-user risk-limit overrides can only tighten the platform default,
    never loosen it — for every account, including the admin's.** The
    alternative (letting an admin raise their own cap) would make the
    guardrail layer's stated job — "the only place allowed to refuse an
    order" (`guardrails.py`'s module docstring) — conditional on who's
    calling, which defeats the point of having one, uniformly-enforced set
    of rules. If the platform defaults are ever genuinely wrong, the fix is
    to change `RiskLimits`' defaults in code (visible in a diff, covered by
    tests), not to add a way to raise them from a request at runtime.
    `guardrails.validate_limit_overrides` enforces this per-field: a "max"
    field (e.g. `max_position_percent`) may only be set at or below the
    default; a "min" field (`min_stop_distance_percent`) may only be set at
    or above it. **Do not add an admin-only path to loosen these** — that
    reintroduces exactly the hole this decision closes.

29. **Options pricing has no live option chain, on purpose.** The same
    lesson from equity streaming (decision 27, and the Finnhub/Polygon
    free-tier tests before it) applies here: none of this platform's free
    market-data vendors serve real-time strikes, bid/ask, open interest or
    IV. Rather than fake a chain with stale or synthetic numbers — which
    would misrepresent real data the way this project consistently refuses
    to elsewhere — `trading/options.py` only prices a contract you specify.
    Volatility is either typed in explicitly or estimated from the
    underlying's own real historical closes (`historical_volatility`); there
    is no invented "implied volatility" presented as if it came from a real
    market. **Do not add a chain-display endpoint that returns synthetic or
    stale strikes/IV to make the UI look more complete** — an honest
    calculator beats a fake chain.

30. **The Interactive Brokers adapter (`trading/execution/ibkr.py`) is real
    code, but it's unverified more strongly than Tradier's, and it's
    architecturally different, not just less certain.** Tradier's wire
    format came from reading IBKR's — sorry, Tradier's — own documented API
    reference directly. IBKR's own API reference pages returned `403
    Forbidden` when fetched during development, so this adapter's request/
    response shapes were reconstructed from web search summaries and
    community client libraries instead. Treat every field name and status
    string in it as meaningfully less certain than Tradier's, and read the
    actual JSON a sandbox round trip returns before trusting it.

    More importantly, **IBKR has no bearer-token REST API at all** — Alpaca
    and Tradier both take an API key and a base URL; IBKR requires a
    separately-running "Client Portal Gateway" process that you authenticate
    by opening in a browser and logging into, which this adapter cannot do
    for you. `IBKR_GATEWAY_URL`/`IBKR_PAPER_ACCOUNT_ID`/`IBKR_LIVE_ACCOUNT_ID`
    only select which account this adapter's requests address — they do not
    establish the session itself. Two more real (not just unverified)
    consequences of that: the gateway session times out after roughly 5-6
    minutes of inactivity, and nothing in this app pings it faster than the
    scheduled broker reconcile (300s default — right at the edge; lower
    `EXECUTION_RECONCILE_INTERVAL_SECONDS` if you actually use this broker);
    and orders are placed by numeric contract id, not ticker symbol, so
    `submit_order` resolves one via `/iserver/secdef/search` and picks the
    first equity match — ambiguous for dual-listed or multi-class tickers,
    never exercised against a real search response.

    One capability is genuinely better than Tradier here, not just
    different: **IBKR is not long-only.** It supports shorting natively, so
    `close_position` sends whichever side flattens the position instead of
    refusing on a short the way Tradier's adapter does.

    A speculative `/iserver/accounts` "registration" call some community
    clients describe was deliberately left out rather than included
    unverified — see the module docstring for why guessing at a step that
    might not even be necessary was worse than not having it.

31. **MFA recovery is an admin action, deliberately not something password
    reset can do.** The tempting shortcut for "I lost my authenticator and
    my recovery codes" is to let a successful password reset also clear MFA
    — one flow, no operator involvement. That would be a real downgrade:
    anyone who can read a reset email (a compromised mailbox, an intercepted
    link) could then strip the second factor off an account they otherwise
    couldn't touch, which is exactly the attack MFA is there to stop. A
    second factor that a first-factor recovery flow can remove is not a
    second factor. So `POST /auth/admin/mfa/reset` requires an
    authenticated admin instead — same reasoning as decision 25's admin gate
    on broker-connected execution modes: some actions should need a human
    with instance-level authority, not just proof of email access. The reset
    is logged at WARNING with both the target and the acting admin, because
    "who cleared this account's MFA and when" is exactly the question worth
    being able to answer later. **Do not add an MFA bypass to
    `/auth/password/reset` for convenience.**

32. **A Redis outage degrades rate limiting to in-process buckets — not open,
    not closed.** The two obvious options are both worse. Failing closed
    (refuse everything when the bucket store is unreachable) turns a cache
    outage into a total outage: every request 429s, a self-inflicted denial
    of service triggered by exactly the infrastructure wobble you'd most
    want the app to ride out. Failing open (skip the limit entirely) drops
    brute-force protection at the moment things are already going wrong.
    Falling back to the in-process bucket is strictly better than both: the
    limit still applies per worker, which is precisely the protection the
    deployment would have had with no Redis configured at all — so the
    degraded state is a known-good configuration, not an unknown one. The
    transition is logged once on the way down and once on recovery rather
    than per request; a 13-request outage produced exactly one warning line
    when this was verified against a real server. **Do not "simplify" this
    by letting Redis errors propagate** — that's the fail-closed behaviour,
    and it means one unreachable cache takes the whole API down.

    Also worth not re-litigating: the bucket arithmetic lives in a Lua script
    executed by Redis, not in Python around `GET`/`SET`. Two workers checking
    the same bucket concurrently would otherwise both read the same token
    count and both spend it — the exact race this feature exists to close, so
    implementing it non-atomically would leave the original bug in place
    while appearing to fix it. The script also reads Redis's `TIME` instead
    of each worker's own clock, so hosts with a little NTP skew still agree
    on how much a bucket has refilled.

33. **Schema migrations stay hand-rolled, with a generic sweep behind them —
    Alembic was evaluated and rejected for now.** Every schema change this
    project has needed has been an additive column add, which SQLite does
    directly. Adopting Alembic would add a dependency, a versions directory
    and migration-ordering rules, and would carry real risk to existing
    databases, in exchange for machinery whose payoff only arrives with the
    first destructive change. That change hasn't come.

    What *was* worth fixing is the failure mode the hand-rolled approach
    actually has: it is manual, so the next person to add a column must also
    remember to write a migration block, and forgetting is silent. A fresh
    install builds the column via `create_all` and passes every test, while
    existing installs break on the next query. `_add_missing_columns` closes
    that by comparing each table against its model and adding whatever is
    missing — so the mistake becomes impossible rather than merely tested
    for. Verified by simulating exactly that mistake: three columns appended
    to the model with no migration block, all three added automatically,
    existing rows intact, second run a no-op.

    The sweep **refuses** rather than guesses in one case: a `NOT NULL`
    column with no usable default on a table that already has rows. There is
    no honest value to write there — inventing one silently fabricates data
    that was never true — so it logs an error and skips, leaving the operator
    to write an explicit step. **Do not "fix" that by making it fall back to
    an empty string or epoch timestamp for every type.** The type-based
    fallbacks it does use (0 for numbers and booleans, '' for text) are
    deliberately limited to cases where the value is unambiguously a
    placeholder, not a plausible-looking lie.

    The original guidance is unchanged and still the right trigger: **when a
    genuinely destructive change is needed — dropping columns, changing
    types, splitting tables — switch to Alembic rather than growing this
    module.**

34. **Anything keyed by a tuple must be released with the same tuple — and
    load testing is how that gets caught.** `StreamHub` keys rooms by
    `(provider, symbol, timeframe)`. The WebSocket handler acquired rooms
    with the provider and released them without it, so the release silently
    addressed a room that never existed. Nothing raised, nothing logged, and
    every unit test passed — room count is not something a single-client test
    looks at. It surfaced only under 150 concurrent clients disconnecting the
    way real ones do.

    Two rules follow. First, **the disconnect path is the primary path, not
    the exception**: browsers close tabs far more often than they send a
    polite unsubscribe, so cleanup-on-disconnect deserves the same scrutiny
    as the happy path rather than a `finally` block written from memory.
    Second, when acquisition takes parameters that form an identity, **store
    them with the handle** instead of reconstructing them at release time —
    the handler now keeps `provider` beside each subscription's queue and
    task precisely so the release cannot drift from the acquire.

    `GET /market/streams` is what turned the suspicion into a measurement, by
    reporting live rooms and their subscriber counts. **Do not remove it as
    "just diagnostics".**

35. **Metrics are admin-only, labelled by route template, and dependency-free.**
    Three decisions in one module, each with a failure mode worth naming.

    *Admin-only.* `GET /metrics` exposes which endpoints exist, how much
    traffic each takes and when the process restarted — a useful map for
    someone probing the instance. It is gated behind the existing bearer-token
    auth whenever authentication is enforced, rather than a second parallel
    mechanism: a scraper is configured with an admin token. On a loopback
    desktop run the gate is off, exactly as it is for every other endpoint.
    **Do not make it public "because Prometheus needs it"** — Prometheus
    supports bearer tokens.

    *Route template, never raw path.* Labelling by raw path is the standard
    way to melt a metrics backend: `/execution/orders/1` and
    `/execution/orders/2` become separate time series, so cardinality grows
    with traffic forever. Unmatched paths collapse to a single
    `<unmatched>` bucket rather than echoing the raw path, because otherwise
    anyone could inflate cardinality deliberately by requesting random URLs —
    verified adversarially with 40 distinct random paths, which produced one
    label, not forty.

    *No dependency.* The Prometheus exposition format is a few lines of text.
    Hand-writing it is less cost than `prometheus-client`, and matches the
    hand-rolled TOTP, JWT and rate limiter elsewhere. The one thing to get
    right is that a histogram is **cumulative** and its `+Inf` bucket must
    equal the observation count — a malformed histogram makes every quantile
    computed from it silently wrong, so that invariant is asserted directly
    in `tests/test_observability.py`.

    One implementation trap worth remembering: **uvicorn installs its own
    handlers on `uvicorn`, `uvicorn.error` and `uvicorn.access` with
    `propagate=False`**, and does it *after* the app module is imported. A
    root-logger config alone therefore leaves its startup and access lines as
    plain text, which would make `LOG_FORMAT=json` only mostly true and
    choke a log shipper on the unparseable minority. `configure_logging`
    clears those handlers and re-enables propagation, and is called again
    from the lifespan so it runs after uvicorn has set itself up.

36. **The admin panel never returns another account's secrets, and its
    "last admin" guard is documented as unreachable rather than presented as
    working protection.** Two separate points.

    *Secrets.* `GET /auth/admin/users` deliberately omits `password_hash`,
    `mfa_secret` and `webhook_token`. An administrator has no legitimate use
    for another account's credentials, and an endpoint that returns them
    converts one stolen admin session into a compromise of every account on
    the instance. The list is for *deciding* (who is locked out, who is
    abusive), not for impersonating. **Do not add secrets to this payload
    for debugging convenience.**

    *The unreachable guard.* `admin_set_active` refuses to deactivate the
    last active administrator. Writing the test for it revealed the branch
    cannot fire: any caller that gets past the self-check is, by definition,
    an active admin who is not the target, so at least one active admin
    always remains. It is kept — because the moment an admin promotion or
    demotion endpoint exists the invariant stops being free, and a guard
    written during the incident is written too late — but it is labelled
    unreachable in the code and backed by a direct unit test of
    `_active_admin_count` rather than by an end-to-end test pretending to
    exercise a scenario that cannot occur. **Dead code that looks like
    protection is worse than no code, so if this is ever removed, remove the
    comment claiming it protects anything too.**

37. **Renamed settings keep their old names — except the one that arms real
    money.** The extraction renamed every `DEX_*` environment variable to
    `LEGEND_*`. All of them still resolve under the old spelling, because an
    operator's existing `.env` being silently ignored after a rename is the
    same class of failure the alias list was built to prevent: you set a
    security setting the documented way and get nothing.

    `LEGEND_ENABLE_LIVE_TRADING` is the single exception and does **not**
    answer to `DEX_ENABLE_LIVE_TRADING`. It is the instance switch that makes
    live mode reachable at all, and honouring a leftover variable from a
    differently-named product would arm real money without anyone deciding to.
    Everywhere else, accepting the old name is the safe direction; here,
    ignoring it is. Re-setting it costs five seconds and is a deliberate act,
    which is exactly what arming live trading should be. **If someone
    "fixes the inconsistency" by adding the alias, they have removed a safety
    property, not tidied one.**

    The same asymmetry governs the database path: `DEX_DB_PATH` still works,
    because pointing at the wrong database loses data rather than risking it.

38. **The landing page is state, not a route, and the terminal is the only
    screen with a fixed-height shell.** Three screens (landing → sign-in →
    terminal), two of which must not be deep-linkable, is less problem than a
    router is machinery. But the first version got the layout inverted: `#root`
    carried `height: 100%`, which the terminal needs so each panel scrolls
    independently, and the landing page inherited it and became unscrollable —
    its whole body trapped in an inner container. Anchor links, browser scroll
    restoration and the mobile URL bar all key off the *document* scroller.

    `#root` is now `min-height: 100%` and any screen wanting the fixed shell
    asks for `h-screen` itself. **A fixed-height app shell is a terminal
    affordance, not a global one — do not push it back up to the root.**

39. **`flex-1` on a tab strip clips instead of scrolling, and clipped tabs are
    missing features.** Seven side-panel tabs across a 26rem column silently
    cut off the last two; Execute was unreachable by mouse entirely and worked
    only because a keyboard shortcut happened to exist. Nothing errored and
    nothing looked obviously broken — it read as a design choice. Both tab
    strips now scroll with `overflow-x-auto` and non-shrinking buttons.
    **Anywhere a horizontal list of controls can exceed its container, it must
    scroll; a control that is merely invisible is a control that is gone.**

---

## Known gaps, in the order I'd tackle them

### 1. More brokers
Alpaca, Tradier and now Interactive Brokers all shipped (see
`trading/execution/`), switchable via `POST /execution/broker`. **Tradier is
unverified against a real account** — see decision 22 below before ever
pointing it at `TRADIER_LIVE_*`. **IBKR is unverified more strongly still,
and architecturally different, not just less certain** — see decision 30:
its wire format was reconstructed from web search summaries (IBKR's own API
docs 403'd when fetched directly), and unlike Alpaca/Tradier it requires a
separately-running, browser-authenticated Client Portal Gateway rather than
a bearer token. Run a real sandbox round trip and read the actual responses
before trusting either of these against live money.

**Per-user risk limits — shipped.** `ExecutionState.limits_json` already
existed but nothing wrote to it; now `GET/POST/DELETE /execution/limits`
let any user tighten their own `RiskLimits` fields below the platform
default (`trading/execution/guardrails.validate_limit_overrides`). Overrides
can only ever move *toward* stricter — never looser, not even for an admin —
so this path can't be used to weaken what the guardrails already enforce for
everyone. `ExecutionPanel.jsx`'s Guardrails section has editable inputs plus
Save/Reset, browser-verified: tightened a value, saved, hard-reloaded the
page to confirm it persisted server-side, then reset back to the default.
Also verified the UI surfaces a rejection cleanly when a loosening attempt
is submitted.

**Scheduled broker reconcile — shipped.** `trading/execution/scheduler.py`
runs `reconcile_all_once()` on a fixed interval (`EXECUTION_RECONCILE_INTERVAL_SECONDS`,
default 300s) via an `asyncio` background task started in `main.py`'s
lifespan and cancelled on shutdown — no new dependency (no APScheduler/Celery)
since a single interval loop is all this needs. Only accounts whose mode is
`broker_paper`/`live` are touched; internal paper accounts have no broker to
reconcile against. One account's failure (bad credentials, broker outage) is
caught, logged, and skipped — it never stops the rest of the pass or kills
the loop. Set the interval to `0` to disable it entirely; `POST
/execution/reconcile` still works on demand either way.

**Per-user broker credentials, if this is ever meant to let the public trade
real money through their own accounts.** Right now broker access is
instance-level and admin-only (decision 25) — the right call for "publish
this, public users get paper only," which is what was decided. If the plan
ever changes to "public users connect their own broker," that needs real
encryption at rest for per-user secrets, which this codebase does not have
and would need to be built (or a vetted library added) deliberately, not
bolted on. Do not store per-user broker keys in plaintext to get there faster.

### 2. Equity streaming — DONE, running on Twelve Data
The plumbing was already real — `StreamHub` in `trading/market.py`
automatically prefers a real WebSocket over polling for any provider whose
`supports_streaming` is true, and `resolve_provider_for` already prefers a
configured keyed vendor (Polygon, Twelve Data, Finnhub — in that order) over
keyless Yahoo. What was missing was a vendor whose *free* tier actually
supports both pieces the terminal needs: historical candles to seed the
chart, and a live WebSocket to keep it moving.

All three keyed vendors were tested directly (curl/websocket against the
vendor's own API, bypassing the app) before trusting any of them:

- **Twelve Data — works end-to-end on the free tier.** `/time_series`
  returned real AAPL candles (HTTP 200) and `wss://ws.twelvedata.com`
  streamed live per-tick prices after a `subscribe` message, no paid plan.
  Wired in via `TWELVEDATA_API_KEY`; verified in this app's own terminal —
  AAPL chart loads, badge reads `TWELVEDATA ● LIVE`, price updates without
  a manual refresh. **This is the first API key in this project verified
  to make a real difference in the running app**, not just accepted by an
  endpoint.
- **Finnhub — does not.** `/quote` returns real data but `/stock/candle`
  (the history endpoint) returns `403 Forbidden` on the free tier, so a
  chart never loads even though the WebSocket itself would be reachable.
  This corrected an earlier, untested claim in this file that Finnhub's
  free tier was "confirmed" — it had not actually been tested when that
  was written.
- **Polygon — does not.** Free tier is 5 calls/min, end-of-day and 15-min-
  delayed data only, described by Polygon itself as a "development
  sandbox." No real-time access at any price point below its paid tiers.

Net effect: with `TWELVEDATA_API_KEY` set, stocks, ETFs, indices and forex
now stream live in this app for free, same as crypto. The stream badge
(`snapshot.live` over the WS protocol, see decision 27) still honestly
shows `DELAYED` for any symbol/provider combination where a real push isn't
flowing, so nothing is silently misrepresented. **Futures have no
streaming path at all, keyed or not** — none of the three adapters
implement it, and free real-time futures data doesn't really exist as a
category; that would need its own paid, licensed feed.

`TWELVEDATA_API_KEY` is set in the operator's local `.env` (gitignored, not
committed) — a fresh clone or deployment needs its own key from
twelvedata.com to get the same result; without it, equities silently and
correctly fall back to Yahoo polling.

### 3. Deeper analysis
Wyckoff event labelling shipped (`trading/wyckoff.py`: spring, upthrust, test,
sign of strength/weakness, last point of support/supply — folded into the
`market_structure` section of the analysis response). Correlation across open
positions also shipped (`risk.compute_position_correlation`: Pearson
correlation of returns against up to 5 of the account's other open paper
positions, wired through `analyze_symbol` -> `analyze` -> `risk.assess_risk`).
Market scanner also shipped (`trading/scanner.py`: filter fields for price,
volume, change, RSI, ADX, ATR%, trend, trend strength, volatility, MACD
histogram, wired through `/scanner` and the terminal's Scanner panel). Economic
calendar also shipped (`trading/econcalendar.py`, FRED-backed: `news_risk` in
`risk.assess_risk` is now `elevated`/`low`/`unmodelled` rather than always
`unmodelled`, driven by whether a high-importance release lands within 2 days
— see decision 23).

**Options pricing and Greeks — shipped** (`trading/options.py`,
`backend/app/routers/options.py`, `OptionsPanel.jsx`, closing the "price-only,
no greeks" gap): exact closed-form Black-Scholes for European contracts, a
Cox-Ross-Rubinstein binomial tree for American early exercise, an implied-
volatility solver, and multi-leg payoff diagrams (bull/bear spreads,
straddles, strangles, iron condors, butterflies) auto-priced via
Black-Scholes. Verified against Hull's textbook reference values (call
4.76 / put 0.81 for the classic S=42, K=40, T=0.5, r=10%, sigma=20% example)
and against known identities — put-call parity, American >= European puts,
call/put gamma and vega equality — not just self-consistency. 46 pure-math
tests plus 17 API tests; browser-verified (a real iron condor priced and
plotted with the correct plateau P/L shape, correct max profit = net
credit).

**Deliberately no live option chain.** Strikes/OI/bid-ask/real IV need a
market-data feed none of this platform's free vendors serve — confirmed the
hard way while verifying equity streaming just before this (Finnhub/Polygon
free tiers gate real-time data). Volatility is either typed in directly or
estimated from the underlying's own real historical prices
(`historical_volatility`) — never faked. See decision 29 below.

### 4. Hardening
**Redis-backed rate limiting — shipped.** `backend/app/ratelimit_redis.py`
moves the token buckets into Redis so every worker shares one budget instead
of each getting its own. Opt-in via `REDIS_URL`; unset, the in-process
limiter behaves exactly as before, which stays the right default for the
single-instance deployment this targets. The refill arithmetic runs as a Lua
script inside Redis so the read-refill-decrement is atomic across workers,
and takes its clock from Redis's own `TIME` so skewed worker clocks still
agree. A Redis outage degrades to the in-process buckets — see decision 32
for why that beats failing open or closed. Verified against a real Redis
server, including the outage path (see the verified-working list above).

**Migration drift closed by construction — Alembic deliberately not
adopted.** `database/migrations.py` now runs a generic additive sweep after
its explicit steps: it compares every table against its SQLAlchemy model and
adds any column the model declares but the database lacks. The failure mode
this removes is quiet and expensive — add a column to `models.py`, forget the
matching block, and a *fresh* install works perfectly while every *existing*
one starts erroring, so tests written against a new database all pass. The
sweep is conservative: it refuses to invent a value for a `NOT NULL` column
with no usable default once rows exist, logging loudly instead of silently
writing data that was never true (see decision 33).

Alembic was evaluated and **not** adopted. Every schema change so far has
been an additive column add, which SQLite handles directly and the sweep now
handles automatically; swapping frameworks would add a dependency, a
versions directory and real risk to existing databases for a payoff that
only arrives with the first destructive change. The original guidance stands
unchanged: **when a destructive change is genuinely needed — dropping
columns, changing types, splitting tables — switch to Alembic rather than
growing this module.**

**Stream hub load testing — done, and it found a real bug.** 150 concurrent
WebSocket clients across 25 symbols with mixed providers, disconnected
abruptly the way a closing tab does, leaked 50 upstream rooms — see bug 5
below and decision 34. Fixed, re-run to zero leaks, and pinned by
`tests/test_stream_hub.py`. Fan-out was confirmed correct at the same time:
50 clients on one symbol share exactly one upstream, and a room stays open
until its genuinely last subscriber leaves.

**Structured logging and metrics — shipped, closing this section.**
`backend/app/observability.py`: `LOG_FORMAT=json` emits one parseable object
per line (including uvicorn's own access lines — see below), and
`GET /metrics` serves Prometheus exposition format. Both are dependency-free;
the exposition format is a few lines of text, so hand-writing it cost less
than taking on `prometheus-client`, consistent with the hand-rolled TOTP, JWT
and rate limiter. Metrics are **admin-only whenever authentication is
enforced** — they leak operational shape — reusing the existing bearer-token
auth rather than inventing a parallel mechanism (decision 35).

### 5. Auth follow-ups
MFA (TOTP) shipped — `backend/app/mfa.py`, optional and off by default, see
`docs/TRADING_TERMINAL.md`'s "Two-factor authentication" section for the full
enrolment/login flow. Self-service password reset also shipped (`POST
/auth/password/forgot` + `/auth/password/reset`, `app/mailer.py`). Email
verification also shipped (`POST /auth/email/verify` + `/auth/email/resend`,
sent automatically at registration, see decision 26 below) — it's
informational and anti-abuse only, not a login gate.

**MFA recovery no longer needs database access.** `POST /auth/admin/mfa/reset`
(admin-only, takes the locked-out account's email) clears `mfa_enabled`,
`mfa_secret`, `mfa_confirmed_at` and every stored recovery-code hash, so an
account that lost both its authenticator and all ten recovery codes can be
recovered by the instance operator instead of by hand-editing SQLite. It is
the first real caller of the `current_admin` dependency in
`backend/app/dependencies.py`, which existed but was dead code until now.
See decision 31 for why this is admin-only rather than folded into password
reset.

**Admin account management — shipped, closing this section.**
`GET /auth/admin/users`, `POST /auth/admin/users/{id}/active` and
`/unlock`, surfaced as an Accounts panel in Settings
(`frontend/src/components/AdminUsers.jsx`) that renders nothing at all for a
non-admin. The operator can now see accounts, disable an abusive one, clear a
login lockout and reset a locked-out user's MFA without hand-editing SQLite.
Deliberately narrow — no editing another account's email, password or
secrets, and the list never returns `password_hash`, `mfa_secret` or
`webhook_token` (decision 36). Deactivation takes effect on the next request,
not at next sign-in, because `is_active` is already checked by
`resolve_user_from_token`.

---

## Environment gotchas that cost time before

- **Binance returns HTTP 451** from many regions and from cloud IPs. The
  platform fails over to Coinbase automatically. Not a bug.
- **Yahoo Finance rate-limits shared cloud IPs** hard (429) and may stay blocked
  for a while. It worked from this sandbox initially, then got throttled. On a
  residential IP it is generally fine. For production equity data, set
  `POLYGON_API_KEY` or `TWELVEDATA_API_KEY` — the platform prefers a keyed
  provider automatically and only falls back to Yahoo.
- **`curl` to `api.github.com` is blocked** from the sandbox; use the GitHub MCP
  tools instead. A monitor polling it will report "unreachable" forever and tell
  you nothing.
- **System PyJWT is broken here** (its `cryptography` rust bindings panic). This
  is why token signing uses stdlib HMAC.
- **Docker is unavailable in the sandbox** — the image can only be verified in CI.

---

## Five bugs already found and fixed — don't reintroduce them

1. **WebSocket snapshot symbol mismatch.** The snapshot frame echoed the
   provider-normalised symbol (`BTC-USD`) while candle frames echoed the
   requested one (`BTCUSDT`), so the client dropped every snapshot and charts
   showed a single candle. Only surfaces with a provider that renames symbols.

2. **Chart re-seeding.** A chart rebuilt after its snapshot arrived was never
   re-seeded, leaving an empty series fed only by live ticks. Fixed with a
   `chartEpoch` counter that data effects depend on.

3. **Token revocation broken off-UTC.** Comparing naive UTC datetimes via
   `.timestamp()` makes Python interpret them as *local* time, so revocation
   worked on a UTC host and silently failed everywhere else. Use
   `app.security.utc_epoch()`.

5. **The WebSocket disconnect path leaked a stream room per client.**
   `market_stream`'s handler subscribed with a provider but, in its `finally`
   cleanup, called `stream_hub.unsubscribe(...)` *without* one.
   `StreamHub._key()` includes the provider, so the cleanup addressed a
   different key, found no room, and returned — leaving the real room open
   with a phantom subscriber and its upstream task running forever. The
   explicit `unsubscribe` action passed the provider correctly, so only the
   *disconnect* path leaked, which is the common one: browsers close tabs,
   they rarely send an unsubscribe frame. Found by load testing, not by
   reading: 150 concurrent clients leaked 50 rooms before the fix and zero
   after. The handler now stores the provider alongside each subscription's
   queue and task. Pinned by `tests/test_stream_hub.py`.

4. **CI installed a hand-maintained package list** duplicated in the workflow
   instead of reading `backend/requirements-server.txt`. Adding a dependency to
   requirements did not reach CI, so the API tests errored at import while every
   requirements file was correct and the suite passed locally. Keep CI installing
   from the requirements file — do not reintroduce an inline package list.

Related: **`LEGEND_AUTH_REQUIRED` was silently ignored** because pydantic-settings
had no env prefix — asking for auth produced none. Auth settings now accept both
prefixed and bare names via `AliasChoices`. Any new security setting must do the
same.

---

## Before publishing this as a real app/website

The user confirmed the intent to publish this for real external users, and
confirmed public users get paper trading only — real broker execution stays
admin-only (enforced server-side, decision 25). Given that, the remaining
gap between "runs correctly" and "safe to put in front of strangers" is
**not code this repo can finish alone**:

- **Privacy policy and terms of service.** Real users' emails and trading
  activity will be collected. Not something to draft without the operator's
  input on what's actually true about the deployment.
- **Whether giving the public trade setups/probabilities needs any
  regulatory consideration** varies by jurisdiction and is a legal question,
  not a code question — get real advice before treating it as settled either
  way.
- **Infrastructure**: a domain, TLS termination, a paid market-data key
  (Yahoo's keyless rate limit will not survive real traffic), SMTP for
  password reset emails, backups, monitoring. See `docs/TRADING_TERMINAL.md`'s
  "Deployment" section for the technical checklist (`LEGEND_SECRET_KEY`,
  `LEGEND_ALLOW_SIGNUP=false` after the admin account exists, `CORS_ORIGINS`,
  `LEGEND_TRUST_PROXY_HEADERS` only behind a real proxy).
- **If "app" means an app-store submission** (iOS/Android/Electron
  distribution), each store has its own review process for financial apps,
  separate from anything in this codebase.

None of these are blocked on more code from this session — they're
operator/business decisions that need to happen before or alongside further
building, not after.

## Immediate next actions on resume

1. Check CI on the branch head after the execution commit.
2. Run the terminal locally and confirm Yahoo serves equities from your own IP —
   chart `NASDAQ:SERV`. This is the one thing never verified outside CI.
3. Connect an Alpaca **paper** key pair and place a real order through
   `broker_paper` mode. Every guardrail is unit-tested and the UI is browser-
   verified, but no order has been sent to Alpaca itself from here — that needs
   credentials. Do this before ever setting `LEGEND_ENABLE_LIVE_TRADING`.
4. Decide whether to mark PR #1 ready for review, or keep stacking on the branch.
5. Then pick from the gaps above.

## Running it

```bash
# Backend (trading only — no desktop-automation dependencies)
pip install -r backend/requirements-server.txt
cd backend && python -m uvicorn app.main:app --reload --port 8000

# Frontend
cd frontend && npm install && npm run dev   # http://localhost:5173 → "Terminal"

# Everything in one container
docker compose up --build                   # http://localhost:8000

# Tests
python -m pytest tests/ -q                  # 472 tests, no network needed
```

Full documentation: [`TRADING_TERMINAL.md`](./TRADING_TERMINAL.md).
