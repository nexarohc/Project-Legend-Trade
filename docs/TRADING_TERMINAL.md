# Legend Trade

An institutional-style market analysis platform: real-time charts, deterministic
chart analysis, strategy generation, backtesting with an adversarial audit, and
Pine Script v6 export.

The design rule the whole system is built around: **describe the market before
predicting it, and show the evidence for every conclusion.** There is no code
path that emits a directional call without the reasoning that produced it.

---

## Table of contents

1. [What it does](#what-it-does)
2. [Quick start](#quick-start)
3. [Market coverage](#market-coverage)
4. [Data providers](#data-providers)
5. [The analysis pipeline](#the-analysis-pipeline)
6. [Market scanner](#market-scanner)
7. [Strategy builder and backtester](#strategy-builder-and-backtester)
8. [Pine Script generation](#pine-script-generation)
9. [Paper trading, alerts and webhooks](#paper-trading-alerts-and-webhooks)
10. [Broker execution and the guardrails](#broker-execution-and-the-guardrails)
11. [The learning engine](#the-learning-engine)
12. [Authentication](#authentication)
13. [API reference](#api-reference)
14. [Architecture](#architecture)
15. [Deployment](#deployment)
16. [Honest limitations](#honest-limitations)

---

## What it does

| Capability | Status |
|---|---|
| Live candlestick charts with zoom, pan, crosshair, multi-timeframe | Working, tick-by-tick on crypto; stocks/forex/indices stream tick-by-tick too with a free **Twelve Data** key (verified end-to-end — Finnhub's free tier cannot, Polygon's free tier is EOD-only, see limitations), honestly badged `LIVE`/`DELAYED` either way |
| Market structure: HH/HL/LH/LL, BOS, CHOCH, EQH/EQL, liquidity sweeps | Working |
| Smart Money Concepts: order blocks, breakers, FVGs, liquidity pools, premium/discount, OTE, inducement | Working |
| Wyckoff-style phase classification (accumulation / distribution / expansion / compression) | Working |
| Wyckoff event labelling: spring, upthrust, test, sign of strength/weakness, last point of support/supply | Working |
| 15+ indicators, each with a written interpretation | Working |
| Volume profile (POC, VAH, VAL), divergence, estimated delta | Working |
| Key levels: S/R, supply/demand, period highs and lows, round numbers, fibs, trendlines | Working |
| Probability engine with empirical base rates measured on the symbol's own history | Working |
| Trade setups with entry, stop, three targets, R:R, invalidation | Working |
| Risk engine: volatility, liquidity, gap, correlation, position sizing | Working |
| Options pricing: Black-Scholes/binomial fair value, Greeks, implied vol, multi-leg payoff diagrams | Working (verified vs. textbook reference values); no live option chain — see limitations |
| Natural-language strategy builder | Working (documented vocabulary + optional LLM fallback) |
| Event-driven backtester with realistic costs | Working |
| Monte Carlo, walk-forward, regime and parameter-stability testing | Working |
| Strategy audit returning APPROVED / REVISE / REJECT | Working |
| Pine Script v6 generation (strategy + indicator) | Working |
| Paper trading, price alerts, TradingView webhook receiver | Working |
| Calibration tracking — scores past calls against what price actually did | Working |
| LLM narrative and research mode | Optional; needs an API key |
| Authentication, per-user data isolation, rate limiting | Working |
| Two-factor authentication (TOTP, optional, per-account) | Working |
| Self-service password reset (email link, SMTP or logged) | Working |
| Email verification (sent at registration, resendable, informational only) | Working |
| Broker execution (Alpaca, Tradier, Interactive Brokers) with mandatory-stop guardrails and a kill switch | Working; live mode off by default, gated four ways — see [limitations](#honest-limitations). Tradier and IBKR are unverified against a real account; IBKR also needs a separately-running gateway, not just an API key. |
| Market scanner: filter a symbol list by measurable trend/volatility/indicator conditions | Working |
| Economic calendar (FRED-backed), wired into risk review's `news_risk` | Working; needs `FRED_API_KEY` (free) |

---

## Quick start

### Docker (fastest)

```bash
cp .env.example .env      # optional — it runs with no keys at all
docker compose up --build
```

Open <http://localhost:8000/docs> for the API, or run the UI dev server below.

### Local

```bash
# Backend
pip install -r backend/requirements-server.txt
cd backend && python -m uvicorn app.main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend && npm install && npm run dev
```

Open <http://localhost:5173> and click **Terminal**.

**No API key is required.** Crypto streams live from Binance (or Coinbase where
Binance is geo-blocked), and equities, indices, FX and futures come from Yahoo
Finance.

### Keyboard shortcuts

| Key | Action |
|---|---|
| `A` | Run analysis |
| `N` | Analysis with an LLM narrative |
| `S` / `R` / `I` / `E` / `C` / `L` | Strategy / Research / Analysis / Execution / Scanner / Calendar panel |
| `1`–`8` | Switch timeframe |
| `/` | Focus the symbol box |
| `?` | Shortcut help |

---

## Market coverage

Every listed market is chartable. There is no fixed whitelist of tickers —
symbol lookup queries the provider's live index, so a company that IPO'd this
morning is chartable this afternoon.

| Market | Examples | Keyless? |
|---|---|---|
| **US stocks — all caps** | `AAPL`, `NVDA`, `PLTR`, `SERV`, `IONQ`, `RGTI`, `LUNR`, `ACHR` | Yes (Yahoo) |
| **ETFs** | `SPY`, `QQQ`, `IWM`, `ARKK`, `SOXL` | Yes |
| **Indices** | `^IXIC`, `^GSPC`, `^RUT`, `^VIX`, `^N225` | Yes |
| **Crypto** | `BTCUSDT`, `ETHUSDT`, `SOLUSDT`, `BTC-USD` | Yes (streaming) |
| **Forex** | `EURUSD`, `GBPUSD`, `USDJPY` | Yes |
| **Futures** | `ES=F`, `NQ=F`, `RTY=F`, `CL=F`, `GC=F` | Yes |
| **Commodities** | `GC=F`, `SI=F`, `CL=F`, `NG=F` | Yes |
| **International** | `SHOP.TO`, `BARC.L`, `SAP.DE`, `7203.T`, `0700.HK` | Yes |
| **Options** | `O:SPY241220C00500000` | No — needs Polygon |

### Small and micro caps

Small caps are ordinary listings to every provider here — nothing special is
needed to chart `NASDAQ:SERV` (Serve Robotics). What *is* different is the
market behaviour, and the analysis reflects it: thin volume and wide spreads
trigger the liquidity-risk check, and overnight gaps trigger the gap-risk check,
both of which reduce the position size the risk engine recommends.

### Symbol formats

All of these resolve to the same instrument:

```
SERV            NASDAQ:SERV      NASDAQ: SERV      serv
BRK.B           BRK-B            NYSE:BRK.B
^RUT            ES=F             EURUSD            EURUSD=X
BTCUSDT         BTC-USD          BINANCE:BTCUSDT
```

Search accepts company names too — typing "serve robotics" in the watchlist
finds `SERV`. Check any symbol's interpretation with:

```bash
curl "http://localhost:8000/market/resolve?symbol=NASDAQ:SERV"
curl "http://localhost:8000/market/markets"     # the full catalogue
```

### The Markets browser

A dedicated **Markets** tab (next to Watch/Positions/Alerts, in the left panel)
for the exact drill-down this catalogue supports: pick a category (US Stocks,
ETFs, Indices, Crypto, Forex, Futures, Commodities, Options, International),
optionally an exchange within it (NASDAQ, NYSE, AMEX, OTC for US stocks), then
search or type a ticker to chart it — "US Stocks → NASDAQ → LASR" as an
explicit path, not just a flat search box.

Two ways to land on a symbol from there:

- **Live search**, scoped to the category's asset class, backed by
  `GET /market/search` — the same provider-index lookup the watchlist already
  uses. A company that IPO'd this morning is findable by name, in whichever
  category it belongs to.
- **Direct resolve**, backed by `GET /market/resolve` — type a bare ticker
  (`LASR`) or a qualified one (`NASDAQ:LASR`) and see exactly how it's
  interpreted (ticker, exchange, asset class, which provider will serve it)
  before charting it. Selecting an exchange chip first qualifies a bare ticker
  automatically, so choosing NASDAQ and typing `LASR` resolves the same as
  typing `NASDAQ:LASR` directly.

Resolve exists as a distinct, always-available path rather than a fallback
bolted onto search, because live search depends on a provider's index
responding — and Yahoo, the keyless default, rate-limits per IP and can take
over ten seconds to fail under load. Resolution is pure symbol parsing with no
network call, so it works even when search is degraded or entirely down; there
is deliberately no bundled ticker list behind either path; both reach the same
live provider index `/market/markets` describes.

---

## Data providers

| Provider | Covers | Key | Streaming |
|---|---|---|---|
| **Binance** | Crypto | No | WebSocket, tick-by-tick |
| **Coinbase** | Crypto | No | WebSocket, tick-by-tick |
| **Yahoo Finance** | Stocks, ETFs, indices, FX, futures, crypto | No | Polled |
| **Polygon.io** | Stocks, options, indices, FX, crypto | Yes | WebSocket |
| **Twelve Data** | Stocks, FX, indices, ETFs, commodities | Yes | WebSocket |
| **Finnhub** | Stocks, FX, crypto | Yes | WebSocket |
| **Alpha Vantage** | Stocks, ETFs, FX, crypto | Yes | Polled |

Selection is automatic per symbol, and **failures fall through the chain** — if
Yahoo rate-limits and you have a Polygon key, the request is served by Polygon
without the chart noticing. Force one provider with `MARKET_DATA_PROVIDER=polygon`
or per-request with `?provider=polygon`.

Adding a provider means implementing four methods in
`trading/providers/base.py` and registering it. Nothing in the analysis,
backtesting or UI layers changes.

> **On Yahoo Finance:** it is an undocumented endpoint that rate-limits per IP.
> It is the right default because it makes the entire listed universe work with
> zero setup, but for production equity work configure Polygon or Twelve Data —
> the platform prefers a keyed provider automatically.

---

## The analysis pipeline

Ordering is enforced in code, not by convention. `trading/analysis.py` computes
and explains the market **before** the probability engine is allowed to run, and
the trade setup is derived from the probability output rather than the reverse.

```
1  Market context     trend, strength, momentum, volatility, liquidity,
                      regime, HTF/LTF bias, institutional bias
2  Market structure    swings, BOS, CHOCH, equal highs/lows, sweeps,
                      internal vs external, phase, Wyckoff events
3  Price action        15 candlestick patterns + failed breakouts, scored
4  Volume              trend, spikes, divergence, pressure, profile/POC/VAH/VAL
5  Indicators          EMA, SMA, VWAP, ATR, RSI, MACD, ADX, Bollinger,
                      Ichimoku, CCI, MFI, Stoch RSI, OBV, SuperTrend
6  Smart Money         order blocks, breakers, mitigation, FVGs, liquidity
                      pools, premium/discount, OTE, inducement
7  Key levels          S/R, period highs/lows, round numbers, fibs, trendlines
8  Summary             the readable synthesis
--- only now does anything predictive run ---
9  Probability         six scenarios, each with weighted, explained factors
10 Trade setup         entry, stop, TP1-3, R:R, confidence, invalidation
11 Risk review         volatility, liquidity, gap, correlation, sizing
```

### How probabilities are produced

Two components, both inspectable:

1. **Empirical base rates.** The engine scans the symbol's own history for bars
   in a comparable regime (same trend direction, same ADX bucket) and counts
   what actually happened over the next N bars. It reports the sample size and
   downgrades its own confidence when that sample is thin.
2. **Weighted evidence.** Structure, momentum, volume, SMC and levels each
   contribute signed weights, and every factor carries a sentence explaining it.

Both are returned in full. A probability with no visible factors is a bug.

### Wyckoff events

`trading/wyckoff.py` labels the discrete events inside a range, not just its
phase: **spring** and **upthrust** (a liquidity sweep at the edge of a recently
contained range), **test** (a low-volume return to that level that holds),
**sign of strength / sign of weakness** (a breakout of the range on above-
average volume), and **last point of support / supply** (the first pullback
after the breakout that holds the old boundary as the new one).

The range each event is checked against is computed **locally in time**, from
the 40 bars immediately before that event, not from the tail of the whole
series. That distinction is deliberate and load-bearing: a spring found on bar
60 must still be recognisable as one after fifty more bars of the resulting
uptrend have printed, by which point the *current* phase has already flipped
away from accumulation. Gating events off today's snapshot would silently
discard the sequence this feature exists to find.

Two guards keep the range read honest, both enforced before a sweep is allowed
to qualify as a spring or upthrust at all:

- **Containment.** The 40-bar window's (high − low) / midpoint must stay under
  20% — a level swept mid-trend is not a range edge.
- **Directional efficiency.** `|net change| / (high − low)` over that same
  window must stay under 40%. Containment alone is not sufficient: a market
  drifting slowly and steadily can look "contained" over any single 40-bar
  slice even while trending hard zoomed out. A real range oscillates back near
  where it started (low efficiency); a trend spends most of its range on net
  travel in one direction (high efficiency). `tests/test_trading_wyckoff.py`
  asserts a steady trend produces zero events, and separately disables the
  efficiency check to prove the guard is what's catching it — the test failed
  before the guard existed.

### When there is no trade

The setup builder refuses to produce a setup when the dominant scenario is
"range" or "false breakout", when scenario confidence is under 35%, when long
and short odds are within 5 points, or when the nearest sensible target is under
1.5R. It returns the specific reason instead. A platform that always finds a
setup is a platform that finds bad ones.

### Correlation across open positions

When a signed-in request analyzes a symbol, the risk review measures Pearson
correlation of bar-to-bar percentage returns against up to 5 of that account's
other **open** paper positions (each fetched at the same timeframe). Returns,
not price levels — two instruments that both happened to drift upward over the
window would otherwise show a spurious high correlation that has nothing to do
with how they actually move together. Pairs are matched by timestamp, and a
pair with fewer than 30 overlapping bars is reported as insufficient data
rather than given a number that isn't meaningful. `correlation_risk` becomes
`high`/`moderate`/`low` once there is something to measure, and stays
`unmodelled` — honestly, not guessed — when there are no open positions to
compare against.

### Economic calendar

Every analysis checks for high-importance scheduled data releases in the next
2 days, sourced from [FRED](https://fred.stlouisfed.org) (the St. Louis Fed) —
free registration, no cost. `news_risk` becomes `elevated` when one is found,
`low` when the window is clear, and stays `unmodelled` — honestly — when
`FRED_API_KEY` isn't set, the same three-state pattern correlation uses.

**Importance is estimated, not official.** FRED's release catalogue carries no
impact tier of its own; `trading/econcalendar.py`'s `classify_importance`
keyword-matches release names (employment, GDP, CPI, PCE → high; PPI, retail
sales, industrial production → medium; everything else → low). It's shown as
"estimated importance" everywhere in the UI, never as an official rating, and
it does not cover FOMC rate decisions — those aren't part of FRED's `releases`
catalogue, and inventing dates for them isn't something this platform does.

Browse the calendar directly in the terminal's **Calendar** panel (`L`), or:

```bash
curl "localhost:8000/calendar/upcoming?days=14"
curl "localhost:8000/calendar?start=2026-08-01&end=2026-08-31&importance=high"
```

---

## Market scanner

Filters a symbol list — your watchlist, or any list you type — down to the ones
that currently pass every condition you set. There is **no bundled ticker
list**, the same rule that governs symbol lookup elsewhere: no keyless provider
exposes a screener API, so pretending to scan "every NASDAQ stock" would mean
silently maintaining a static universe. A scan runs over symbols you supply.

Filters compose over the same measurements the full analysis pipeline produces
(`trading/regime.py`, `trading/indicators.py`), so a scan result can never
disagree with what a full analysis of that symbol would show:

| Field | Meaning |
|---|---|
| `price`, `volume`, `change_percent` | Latest bar |
| `rsi`, `adx`, `atr_percent`, `macd_hist` | Indicator values |
| `trend` | `bullish` / `bearish` / `ranging` (string, `eq` only) |
| `trend_strength`, `volatility` | Regime classification |

Operators: `gt`, `gte`, `lt`, `lte`, `eq`, `between` (needs `value2`). A field
with no measurable value for a symbol — too little history, a failed fetch —
never matches; it fails closed rather than silently passing, the same rule the
guardrails use. One symbol failing to fetch does not abort the scan; it's
reported separately under `failed` with the reason. Capped at 50 symbols per
request (`MAX_SCAN_SYMBOLS` in `trading/scanner.py`) to bound provider load.

```bash
curl -X POST localhost:8000/scanner -H 'Content-Type: application/json' -d '{
  "symbols": ["BTCUSDT", "ETHUSDT"],
  "timeframe": "1h",
  "filters": [{"field": "trend", "op": "eq", "value": "bullish"},
              {"field": "rsi", "op": "between", "value": 40, "value2": 70}]
}'
```

---

## Strategy builder and backtester

### Natural language → strategy

```bash
curl -X POST localhost:8000/strategy/build -H 'Content-Type: application/json' -d '{
  "description": "Buy breakout with increasing volume on the 4h, 1.5 ATR stop, 3R target, risk 1%",
  "symbol": "BTCUSDT"
}'
```

The parser is deterministic over a documented vocabulary (setups, filters, risk,
management, sessions) and reports **both** what it understood and what it
ignored. If it recognises nothing, an LLM can draft the spec instead — clearly
labelled, because that output is an interpretation rather than a parse.

### The execution model

These guarantees are asserted directly in `tests/test_trading_strategy.py`:

- **Signals evaluate on closed bars and fill at the next bar's open.** This is
  the single most important defence against look-ahead bias.
- **When one bar spans both stop and target, the stop is assumed to fill
  first.** OHLCV cannot tell you the intrabar sequence; assuming the favourable
  one inflates every result built on it.
- **Gaps through the stop fill at the open**, not at the stop price.
- **Costs are charged on every fill,** including each partial take-profit.
- **Position size derives from the stop distance,** so risk stays constant.
- **One position at a time.**

### Validation

- **Monte Carlo** — bootstrap and shuffle resampling; reports the 90% confidence
  interval, drawdown percentiles and probability of ruin.
- **Walk-forward** — sliding in-sample/out-of-sample folds with an efficiency ratio.
- **Regime testing** — slices history by trend and volatility and reports each
  bucket separately, so a bull-market-only strategy cannot hide in an average.
- **Parameter stability** — re-runs with the stop and targets scaled ±10–20%.

### The audit

Fifteen checks, adversarial by design: look-ahead bias, sample size, cost
realism, overfitting (trades per parameter), stop quality, risk per trade,
drawdown, profit factor, outlier dependency, data span, directional balance,
walk-forward, regime dependency, parameter stability and Monte Carlo risk.

Each returns a severity, the evidence, and a recommendation. The verdict —
`APPROVED` / `REVISE` / `REJECT` — is derived from the checks, not from the
headline return, so a strategy that made 400% on four trades with no costs gets
rejected exactly as it should.

**Approval is not a prediction of profit.** It means the backtest is believable.

---

## Pine Script generation

Generates commented Pine v6 strategies and indicators from the same spec that
drives the backtester, so the two cannot silently diverge.

Deliberate translation choices:

- Breakout lookbacks compile to `ta.highest(high, n)[1]` — without the `[1]` the
  current bar sits inside its own window and the condition can never fire.
- `calc_on_every_tick=false` and `process_orders_on_close=false` match the
  backtester's bar-close evaluation and next-bar fills.
- `request.security(..., lookahead=barmerge.lookahead_off)` so the
  higher-timeframe filter does not repaint.
- Commission and slippage are declared from the spec's own cost settings.
- Scale-out exits each carry the stop; no duplicate full-size stop order.

```bash
curl -X POST localhost:8000/strategy/pine -H 'Content-Type: application/json' \
  -d '{"description": "Buy breakout with volume, 1.5 ATR stop, 3R target", "kind": "strategy"}'
```

---

## Paper trading, alerts and webhooks

Internal paper trading is the **default** execution path and needs no broker at
all. Positions are sized from the stop distance, marked to live prices, and
closed automatically when a stop or target is reached
(`POST /trading/paper/sync`, stop checked first). Routing orders to a real
broker is covered in [the next section](#broker-execution-and-the-guardrails).

The TradingView webhook receiver at `POST /trading/webhook/tradingview` accepts
both JSON and the plain-text format the generated Pine emits. **It records
alerts and never places orders** — it is a public endpoint, and wiring one
straight to order placement is how a leaked URL drains an account. This stays
true now that broker execution exists: there is no path from a webhook to an
order, by design.

---

## Broker execution and the guardrails

Orders can be routed to a real broker — Alpaca, Tradier or Interactive
Brokers, switchable with `POST /execution/broker` (paper and live share one
API per broker, so the paper path exercises exactly the code the live path
will). The whole design of this subsystem is built around one assumption:
**the expensive failure is an order that should not have been sent.**
Everything below optimises for refusing rather than for convenience.

**Tradier is unverified against a real account.** Its adapter
(`trading/execution/tradier.py`) was implemented from Tradier's documented API
shape — including the `otoco`/`oto` indexed-field format used to attach a
protective stop atomically, since Tradier has no Alpaca-style single-field
bracket — and is covered by tests that stub `httpx.request`, which proves the
adapter's own logic is internally consistent but cannot prove Tradier's real
responses match. Verify a few round trips against `TRADIER_PAPER_TOKEN` in
sandbox mode before ever setting `TRADIER_LIVE_*`. It is also **long-only**:
Tradier distinguishes closing a long (`sell`) from opening a short
(`sell_short`), a distinction this platform's order model doesn't carry yet,
so `close_position` refuses outright on a short rather than risk sending the
wrong side.

**Interactive Brokers is unverified more strongly than Tradier, and is
architecturally different, not just less certain.** Its adapter
(`trading/execution/ibkr.py`) was reconstructed from web search summaries and
community client libraries — IBKR's own API reference pages returned `403
Forbidden` when fetched directly during development, unlike Tradier's, which
was read straight from its documented shape. More importantly: **IBKR has no
bearer-token REST API.** It requires a separately-running "Client Portal
Gateway" process that you authenticate by logging into in a browser
(`IBKR_GATEWAY_URL`, default `https://localhost:5000/v1/api`) — an API key
alone cannot substitute for that step. The gateway session also times out
after roughly 5-6 minutes idle; nothing in this app pings it faster than the
scheduled reconcile (`EXECUTION_RECONCILE_INTERVAL_SECONDS`, default
300s — right at the edge), so lower that interval if you actually use this
broker. Orders are placed by numeric contract id rather than ticker symbol,
resolved via a symbol search that picks the first equity match — untested
against a real, possibly ambiguous result. Unlike Tradier, IBKR **does**
support shorting, so `close_position` sends whichever side flattens the
position rather than refusing.

### Three modes

| Mode | What it does | Money at risk |
|---|---|---|
| `paper` (default) | Internal simulator, no broker connection | None |
| `broker_paper` | Broker's paper endpoint, real order lifecycle | None |
| `live` | Broker's live endpoint | **Real** |

### Reaching live mode takes five independent acts on a multi-user instance

No single mistake gets to real money:

0. **Being the instance administrator.** `POST /execution/mode` to anything
   but `paper`, `POST /execution/broker`, and `POST /execution/confirm-live`
   all return `403` for any account other than the first registered one —
   broker credentials are shared across the whole instance (see below), so a
   published, multi-user deployment cannot let every account touch them. A
   single-operator desktop instance is unaffected, since the implicit local
   account is always admin.
1. **A server-side flag.** `LEGEND_ENABLE_LIVE_TRADING=true` in the environment,
   plus a restart. No API request, database row or UI click can set it — that
   asymmetry is the point.
2. **An explicit mode switch** to `live` (`POST /execution/mode`).
3. **A typed confirmation:** the exact phrase `TRADE LIVE MONEY`, which arms
   live orders for a bounded window (default 30 minutes, max 240) and then
   expires on its own. A checkbox gets clicked reflexively; a phrase does not.
   Switching broker (`POST /execution/broker`) clears this window — arming
   live orders is a decision about one broker's credentials, not something
   that should silently carry over to a newly selected one.
4. **Separate credentials per mode.** `ALPACA_LIVE_KEY_ID`/`SECRET_KEY` and
   `TRADIER_LIVE_TOKEN`/`ACCOUNT_ID` are distinct variable names from their
   paper counterparts, so a copy-paste slip cannot silently point live keys at
   what you believe is a paper account.

The UI makes live mode visually unmistakable — a red banner and a red border
around the entire panel — because the most dangerous state this app can be in
is one where you believe you are on paper.

### Rules that always refuse an order

Implemented in one file, `trading/execution/guardrails.py`, so every reason an
order can be blocked is readable in one place rather than scattered across call
sites. `GET /execution/rules` returns this list live, with current thresholds.

- The kill switch is engaged.
- Live mode without the server-side instance flag.
- Live mode without a typed, unexpired confirmation.
- **An entry order with no protective stop.** The rule most likely to be
  resented and the one most worth keeping — "I'll watch it" is not a risk
  control.
- A stop placed on the wrong side of price (it would trigger instantly, and
  almost always means the direction is confused).
- A stop closer than 0.1% (inside noise) or further than 25% (risks far more
  than intended).
- An order attributed to a strategy whose audit verdict is not `APPROVED`, or
  that has never been audited. Routing capital into a rejected strategy would
  make the audit decorative.
- A **live** order from a strategy with fewer than 20 closed paper trades. An
  approved backtest is not a track record.
- Any size check whose inputs are unavailable — see fail-closed below.

Default caps, all configurable per instance: 10% of equity per position, 30%
total exposure, $25,000 per order, 10 open positions, 20 orders per hour. The
order-rate cap is a runaway-loop guard as much as a discipline one.

### Per-user risk limits

`GET /execution/limits` returns your effective limits (platform defaults,
tightened by anything you've overridden) alongside the platform defaults for
reference. `POST /execution/limits` accepts any subset of `RiskLimits`'
fields and merges them into your existing overrides; `DELETE
/execution/limits` clears them, reverting to the platform defaults.

**Overrides can only tighten a limit, never loosen it — for every account,
including the admin's.** `max_position_percent`, `max_total_exposure_percent`,
`max_order_notional`, `max_open_positions` and `max_orders_per_hour` may only
be set at or below the platform default; `min_stop_distance_percent` may only
be set at or above it (narrowing the allowed stop-distance window from the
other side); `max_stop_distance_percent` follows the same "at most the
default" rule. A request that would loosen any field is refused with the
specific field and both values in the error — nothing is written. There is
no path, admin or otherwise, to raise a limit above the platform default at
runtime; that's a deliberate choice (see decision 28 in
`docs/PROJECT_STATE.md`) — if a default is ever wrong, the fix is to change
`RiskLimits` in code, not to add a way around it from a request. The
terminal's Execution panel exposes this as editable fields under
"Guardrails → Your limits," with Save and Reset-to-defaults.

### Fail closed

A check that cannot be evaluated **refuses**. If account equity cannot be read
from the broker, position size cannot be verified, so the order is rejected
rather than sized blind. A guardrail that silently passes when its input is
missing is not a guardrail.

### Refusals explain themselves

A blocked order returns HTTP 200 with `submitted: false` and the full decision —
every rule that fired, the number that broke it, and what to do about it. A
refusal is a normal, expected outcome, not an error, and it is persisted
alongside accepted orders. A guardrail that fires without explaining itself
trains people to route around it.

### The kill switch

`POST /execution/kill-switch` cancels every working order, flattens every
position, and blocks new orders until explicitly disengaged. It sets the
blocking flag **before** it starts flattening, so nothing can slip out during
the unwind, and it disarms any live confirmation on the way. The state is
persisted, so it survives a server restart — a kill switch that forgets after a
crash is worse than none.

### Order flow and reconciliation

The submit path is fixed:

```
assemble context → run guardrails → persist the attempt → transmit → persist the result
```

The attempt is written **before** anything is sent, so a crash between the
guardrail pass and the broker call still leaves a record that an order was about
to go out.

Entries go out as bracket/OTO orders so the protective stop is attached at the
broker and rides with the entry — there is never a window where a filled
position sits unprotected because a second request had not landed yet.

`POST /execution/reconcile` resolves toward the **broker**. Local `OrderRecord`
rows are a log of intent; the broker's orders and positions are the truth.
Nothing infers a fill from having sent an order, and an unrecognised broker
status maps to `unknown` rather than being guessed at — guessing a terminal
state for a working order would make reconciliation drop a real position.

This also now runs on a schedule, not just on demand: a background loop
(`trading/execution/scheduler.py`, started from `main.py`'s lifespan)
reconciles every account whose mode is `broker_paper` or `live` every
`EXECUTION_RECONCILE_INTERVAL_SECONDS` (default 300s, disable with `0`).
Internal paper accounts are skipped — they have no broker to reconcile
against. One account's failure (bad credentials, broker outage) is logged
and skipped without stopping the rest of the pass or killing the loop.
`POST /execution/reconcile` still works on demand regardless of the
schedule.

### Credentials

Read from the environment only, **never from the database**. Storing per-user
broker keys would need real encryption at rest, and no vetted crypto library is
available here. Environment-only means broker access is an instance-level
capability rather than a per-user one — a limitation stated plainly rather than
papered over with weak encryption.

| Broker | Paper | Live |
|---|---|---|
| Alpaca | `ALPACA_PAPER_KEY_ID`, `ALPACA_PAPER_SECRET_KEY` | `ALPACA_LIVE_KEY_ID`, `ALPACA_LIVE_SECRET_KEY` |
| Tradier | `TRADIER_PAPER_TOKEN`, `TRADIER_PAPER_ACCOUNT_ID` | `TRADIER_LIVE_TOKEN`, `TRADIER_LIVE_ACCOUNT_ID` |
| Interactive Brokers | `IBKR_PAPER_ACCOUNT_ID` (+ `IBKR_GATEWAY_URL`, `IBKR_VERIFY_SSL`) | `IBKR_LIVE_ACCOUNT_ID` |

IBKR's row is not like the other two: there is no key/secret pair to set and
be done. `IBKR_GATEWAY_URL` (default `https://localhost:5000/v1/api`) points
at a Client Portal Gateway process you run and log into yourself in a
browser — the account-id variables only tell this adapter which account to
address once that session exists, they do not create it. `IBKR_VERIFY_SSL`
defaults to `false` because the gateway's default install uses a
self-signed certificate; set it `true` only if you've put a real one in
front of it.

---

## The learning engine

"Learning" here means calibration, not model training. Every analysis is stored
with the call it made; once the forward horizon has elapsed, the engine fetches
what price actually did and grades it.

```bash
curl -X POST localhost:8000/analysis/score-pending
curl localhost:8000/analysis/calibration
```

The calibration report breaks accuracy down by scenario and by confidence band —
and says so explicitly when high-confidence calls are *not* outperforming
low-confidence ones, which is the only way to know whether the confidence score
means anything on your data.

---

## Authentication

Enforcement is decided by `LEGEND_AUTH_REQUIRED`:

| Mode | Behaviour |
|---|---|
| `auto` (default) | Required unless the server is bound to loopback |
| `always` | Always required |
| `never` | Never required (single-user desktop) |

`auto` exists because this is both a desktop app and a deployable service.
Forcing a login on a localhost desktop app is friction with no security benefit;
allowing anonymous access on a public interface is a hole. The bind address
distinguishes the two better than a flag someone forgets to flip — and the
choice is logged at startup either way, so it is never a surprise.

The first account created is the administrator. Further signups need
`LEGEND_ALLOW_SIGNUP=true`, so a self-hosted instance doesn't quietly become a
public service.

### What it does

- **Passwords**: `hashlib.scrypt` (n=2¹⁵, r=8, p=1), per-password salt, parameters
  stored with each hash so the cost can be raised later without invalidating
  anyone. Constant-time comparison.
- **Tokens**: HS256, 30-minute access and 14-day refresh. The verifier pins the
  algorithm rather than trusting the token's own `alg` header, so `alg: none`
  and algorithm-substitution attacks fail. A refresh token cannot be replayed as
  an access token.
- **Revocation**: a password change or "sign out everywhere" moves a server-side
  cutoff, so tokens that have not yet expired stop working. Logout that only
  clears the browser's copy is not logout.
- **Isolation**: every trading table carries `user_id`, and the id comes from the
  verified token — never from the request. A row belonging to another account is
  indistinguishable from one that does not exist (404, not 403), so ids cannot
  be probed.
- **Brute force**: per-IP token-bucket rate limiting on login and registration,
  plus account lockout after 8 consecutive failures. Sign-in failures return one
  message for every cause, so the endpoint is not an account-enumeration oracle.
  Buckets are in-process by default; set `REDIS_URL` to share them across
  workers (see "Rate limiting across workers" below).
- **Webhooks**: each account gets a secret URL
  (`/trading/webhook/tradingview/<token>`), compared in constant time and
  rotatable without touching the password. TradingView cannot send headers, so
  the secret has to be in the path — treat the URL as a credential.

### Admin account management

The first registered account administers the instance. An **Accounts** panel
appears in Settings for admins only (it renders nothing for everyone else,
and the endpoints behind it return 403 regardless — the server is the
boundary, the hidden UI is just courtesy).

| Endpoint | Purpose |
|---|---|
| `GET /auth/admin/users` | List accounts, optionally filtered by email |
| `POST /auth/admin/users/{id}/active` | Disable or re-enable an account |
| `POST /auth/admin/users/{id}/unlock` | Clear a failed-login lockout early |
| `POST /auth/admin/mfa/reset` | Reset MFA for someone locked out of it |

Disabling takes effect **immediately** — `is_active` is checked on every
token, so existing sessions stop working on the next request rather than at
next sign-in. An admin cannot deactivate their own account; that misclick has
no in-app recovery.

The listing deliberately never includes `password_hash`, `mfa_secret` or
`webhook_token`. An administrator has no legitimate use for another account's
credentials, and returning them would turn one stolen admin session into a
compromise of every account on the instance.

There is no way to edit another account's email or password from here, and no
promotion/demotion of admins — the first account is the admin, and changing
that is a deliberate database operation.

### Logging and metrics

`LOG_FORMAT=json` switches every log line to a single parseable JSON object —
including uvicorn's own startup and access lines, which need explicit handling
because uvicorn installs its own handlers with `propagate=False`. Anything a
caller attaches via `logger.info(..., extra={...})` becomes a real field
rather than being interpolated into the message string, so it stays
queryable. `LOG_LEVEL` sets the threshold.

`GET /metrics` serves Prometheus exposition format: request counts by method,
route and status; a latency histogram; rate-limit refusals; and process
uptime. There is no new dependency — the format is plain text.

**It is admin-only whenever authentication is enforced**, because metrics
leak operational shape (which endpoints exist, how much traffic each takes,
when the instance last restarted). Point a scraper at it with an admin bearer
token:

```yaml
scrape_configs:
  - job_name: legend-trade
    authorization:
      credentials: <an admin access token>
    static_configs:
      - targets: ["your-host:8000"]
```

Set `METRICS_ENABLED=false` to remove it entirely. On a loopback desktop run
authentication is off and so is this gate, matching every other endpoint.

Labels use the **route template** (`/execution/orders`), never the raw path,
so cardinality stays bounded no matter how much traffic arrives; unmatched
paths collapse into one `<unmatched>` bucket rather than letting random URLs
create unbounded series.

### Rate limiting across workers

By default the token buckets live in each process's memory. That is correct
for the single-instance deployment this platform targets, but behind several
workers each process keeps its own counters, so a limit of 10 becomes 10 per
worker.

Set `REDIS_URL` (e.g. `redis://localhost:6379/0`) and the buckets move into
Redis, shared by every worker. The refill arithmetic runs as a Lua script
inside Redis so the read-refill-decrement is atomic — doing it as
read-modify-write from Python would leave the exact race this is meant to
close, since two workers could read the same token count and both spend it.
The script reads Redis's own `TIME` rather than the caller's clock, so
workers with a little NTP skew still agree on how much a bucket has refilled.

**A Redis outage degrades to the in-process buckets rather than failing open
or closed.** Failing closed would turn a cache outage into a total outage;
failing open would drop brute-force protection exactly when infrastructure is
already unhappy. Falling back means limits still apply per worker — the same
protection the deployment would have had without Redis configured at all. The
degradation is logged once on the way down and once on recovery, not per
request.

Measured, not assumed: two limiters standing in for two workers allowed 6 of
6 requests with in-process buckets (each getting a full budget) versus 3 of 6
Redis-backed (one shared budget).

### Two-factor authentication (TOTP)

Optional, per-account, off by default. RFC 6238 TOTP over stdlib HMAC-SHA1 —
the same reasoning as the hand-rolled JWT: rolling six functions in-house
avoids a dependency for what the standard library already provides, and every
mainstream authenticator app (Google Authenticator, Authy, 1Password) only
speaks SHA1 TOTP, so that's the only choice that actually works with one.

**Enrolment is three steps, not one**, precisely so a secret can't be
"enabled" without ever reaching a real authenticator:

1. `POST /auth/mfa/setup` generates a secret and returns it (plus an
   `otpauth://` URI) — pending, not yet active.
2. `POST /auth/mfa/confirm` with a real generated code turns MFA on and
   returns ten recovery codes **in plaintext, exactly once**. They're stored
   hashed, like a password, from that point on — losing them means losing that
   fallback, not losing account access, since the authenticator itself still
   works.
3. From then on, `POST /auth/login` returns `{"mfa_required": true, "mfa_token": "..."}`
   instead of tokens. That token is scoped to `type=mfa`, expires in 5 minutes,
   and is rejected by every endpoint that requires a real access token — the
   same type-confusion guarantee that already stops a refresh token from being
   replayed as an access token, extended to cover this third type.
   `POST /auth/mfa/verify` exchanges it plus a code (TOTP or a recovery code)
   for the real token pair.

**A password alone is not a completed login once MFA is on.** `last_login_at`
and the failed-attempt counter only update after `/auth/mfa/verify` succeeds —
otherwise a stolen password would show up as a successful sign-in in the
account's own history before the second factor was ever checked. The MFA
verify endpoint gets its own rate limit and shares the same account-lockout
threshold as the password step, since a 6-digit code is exactly the kind of
thing worth throttling.

**Where the secret lives, stated plainly:** unlike a password, a TOTP secret
cannot be hashed at rest — verifying a future code requires reproducing it,
which is only possible in symmetric form. It's stored in the database as-is,
the same tradeoff already made for the per-user webhook token, and for the
same reason broker credentials in `trading/execution/` stay in the
environment rather than an encrypted column: there's no vetted crypto library
here to build that layer safely. Recovery codes don't have this problem —
they're compared, never reproduced — so they're hashed exactly like a password.

**Locked out of both the authenticator and the recovery codes?** The instance
admin can clear an account's MFA with `POST /auth/admin/mfa/reset`, taking
just the locked-out account's email. It wipes the secret, the confirmation
timestamp and every stored recovery-code hash, so the next sign-in needs only
the password again.

This is deliberately an admin action rather than something password reset
does on its own. Letting a reset also clear MFA would mean anyone who can
read a reset email — a compromised mailbox, an intercepted link — could strip
the second factor off an account they otherwise couldn't touch, which is the
exact attack MFA exists to prevent. A second factor a first-factor recovery
flow can remove isn't a second factor. Each reset is logged at WARNING with
both the target account and the admin who performed it.

### Password reset

Self-service, via email — `POST /auth/password/forgot` with just an email
address, then `POST /auth/password/reset` with the token from the link plus a
new password. Two things worth knowing:

- **The response is identical whether or not the address has an account**,
  for the same reason `/auth/login` never says which half of a bad credential
  pair was wrong — a "yes, we found that account" branch is a free enumeration
  oracle.
- **A reset link works once.** The token's signature and 15-minute expiry
  prove it came from this server and hasn't timed out, but not that it hasn't
  already been spent — `User.password_reset_requested_at` is stamped with the
  token's own issued-at time when it's created, and `/reset` requires an exact
  match, clearing the column on success. Requesting a second link invalidates
  the first automatically, since the timestamp gets overwritten.

**Delivery has no mail-sending SaaS wired in.** Set `SMTP_HOST` (plus
`SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_USE_TLS`) and
`app/mailer.py` sends over plain `smtplib`. Leave it unset and the link is
logged instead of emailed — which is genuinely usable, not a stub, on a
self-hosted single-operator instance where the operator already has log
access and no actual email step is required to reset their own password.

### Email verification

A verification link is sent automatically at registration (same delivery
path as password reset — SMTP or logged) and can be re-sent with `POST
/auth/email/resend` if it's missed or expires (24-hour window). Confirming it
hits `POST /auth/email/verify` with the token from the link.

**This gates nothing.** An unverified account can sign in, trade paper, run
analysis — every feature works identically either way. The point is knowing
an address is real once real strangers can register, not restricting what
they can do before proving it. It also needs no single-use protection the
way a reset token does: verifying twice is a harmless no-op, since nothing
about the account's security state changes.

### Notes and trade-offs, stated plainly

- **Tokens are stored in `localStorage`**, not httpOnly cookies. The app is
  served cross-origin in development and runs in Electron in production, where
  cookie handling is inconsistent. Access tokens are short-lived and the app
  renders no third-party content, so the practical exposure is small — but if
  you ever serve untrusted content from this origin, move to httpOnly cookies
  with CSRF protection.
- **The WebSocket token travels as a query parameter**, because browsers cannot
  set headers on a WebSocket handshake. That puts it in server logs, which is
  part of why access tokens expire in 30 minutes.
- **Rate limiting is in-process.** It protects a single instance. Behind several
  workers each keeps its own counters, so the effective limit multiplies — move
  the buckets to Redis before running at that scale.
- **The signing secret is generated and stored on disk** when unset, so restarts
  don't sign everyone out. That's right for a desktop app and wrong for a fleet:
  set `LEGEND_SECRET_KEY` explicitly for multi-instance deployments, or tokens from
  one instance will be rejected by the others.

### Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /auth/status` | Whether auth is on, and whether any account exists |
| `POST /auth/register` | Create an account (first one is admin) |
| `POST /auth/login` | Exchange credentials for tokens |
| `POST /auth/refresh` | New token pair from a refresh token |
| `GET /auth/me` | The current account |
| `GET /auth/me/export` | Everything stored about this account, as JSON. Credential material (password hash, TOTP secret, recovery-code hashes) is reported as present but withheld |
| `POST /auth/me/delete` | Erase the account and every row keyed to it. Requires the password and the exact phrase `DELETE MY ACCOUNT`; refused for the last remaining admin. Irreversible |
| `POST /auth/password` | Change password; revokes all sessions |
| `POST /auth/password/forgot` | Request a reset link (always a generic response) |
| `POST /auth/password/reset` | Complete a reset with the token from the link |
| `POST /auth/email/verify` | Confirm an email address from its verification link |
| `POST /auth/email/resend` | Send a fresh verification link (auth required) |
| `POST /auth/logout?everywhere=` | Sign out, optionally revoking issued tokens |
| `GET /auth/webhook-url` | This account's secret webhook URL |
| `POST /auth/webhook-url/rotate` | Rotate it |
| `POST /auth/mfa/setup` | Generate a pending TOTP secret |
| `POST /auth/mfa/confirm` | Verify a code, enable MFA, get recovery codes once |
| `POST /auth/mfa/verify` | Exchange an `mfa_token` + code for real tokens |
| `POST /auth/mfa/disable` | Turn MFA off (requires the current password) |
| `GET /auth/mfa/status` | Whether MFA is on, and recovery codes remaining |
| `POST /auth/admin/mfa/reset` | **Admin only.** Clear another account's MFA and recovery codes, for a user locked out of both |

## API reference

### Market data
| Endpoint | Purpose |
|---|---|
| `GET /market/markets` | Every market, exchange and symbol format |
| `GET /market/resolve?symbol=` | How a symbol is interpreted |
| `GET /market/providers` | Provider status and which have keys |
| `GET /market/candles` | OHLCV history |
| `GET /market/quote`, `/market/quotes` | Live quotes |
| `GET /market/search?query=` | Symbol search by ticker or company name |
| `GET /market/multi-timeframe` | Several timeframes at once |
| `WS /market/ws` | Live candle stream |

### Scanner
| Endpoint | Purpose |
|---|---|
| `POST /scanner` | Filter a symbol list by measurable conditions |
| `GET /scanner/fields` | Available fields and operators |

### Calendar
| Endpoint | Purpose |
|---|---|
| `GET /calendar` | Scheduled releases in a date range, optional importance filter |
| `GET /calendar/upcoming` | Releases from today through N days ahead |

### Analysis
| Endpoint | Purpose |
|---|---|
| `POST /analysis` | The full 20-section report |
| `POST /analysis/narrate` | Report plus an LLM walkthrough |
| `POST /analysis/research` | Grounded Q&A |
| `GET /analysis/concepts` | Concept glossary (works offline) |
| `POST /analysis/feedback` | Rate an analysis |
| `GET /analysis/calibration` | Measured accuracy of past calls |

### Strategy
| Endpoint | Purpose |
|---|---|
| `POST /strategy/build` | Natural language → spec |
| `POST /strategy/study` | Backtest + validation + audit |
| `POST /strategy/pine` | Pine Script v6 |
| `POST /strategy/save`, `GET /strategy/saved` | Persistence |
| `GET /strategy/features` | Rule vocabulary |

### Trading
| Endpoint | Purpose |
|---|---|
| `GET/POST/DELETE /trading/watchlist` | Watchlist |
| `GET/POST /trading/alerts`, `POST /trading/alerts/check` | Alerts |
| `POST /trading/paper/open`, `/close/{id}`, `/sync` | Paper positions |
| `GET /trading/paper/performance` | Paper results |
| `POST /trading/webhook/tradingview` | Webhook receiver |

### Execution

| Endpoint | Purpose |
|---|---|
| `GET /execution/status` | Mode, kill switch, live arming, limits, broker health |
| `POST /execution/mode` | Switch `paper` / `broker_paper` / `live` |
| `GET /execution/brokers` | Which broker adapters exist (`alpaca`, `tradier`, `ibkr`) |
| `POST /execution/broker` | Switch which broker `broker_paper`/`live` routes to |
| `POST /execution/confirm-live` | Arm live orders with the typed phrase |
| `POST /execution/orders` | Submit through the guardrails |
| `GET /execution/orders` | Order history, including refusals and their reasons |
| `POST /execution/reconcile` | Refresh state from the broker (authoritative); also runs on a schedule |
| `POST /execution/kill-switch` | Flatten everything, block new orders |
| `DELETE /execution/kill-switch` | Re-enable trading |
| `GET /execution/rules` | Every guardrail rule and its current threshold |
| `GET /execution/limits` | Your effective limits plus the platform defaults |
| `POST /execution/limits` | Tighten your own limits (never loosen — see decision 28) |
| `DELETE /execution/limits` | Reset your limits to the platform defaults |

### Options pricing

| Endpoint | Purpose |
|---|---|
| `POST /options/price` | Fair value + Greeks for one contract (Black-Scholes or binomial) |
| `POST /options/implied-volatility` | Solve for the volatility an observed price implies |
| `GET /options/historical-volatility` | Annualized volatility from the underlying's real prices |
| `GET /options/strategies` | Named multi-leg strategies this can build and price for you |
| `POST /options/payoff` | P/L at expiration for explicit legs or a named strategy |

### WebSocket protocol

```jsonc
// client → server
{"action": "subscribe",   "symbol": "BTCUSDT", "timeframe": "1m"}
{"action": "unsubscribe", "symbol": "BTCUSDT", "timeframe": "1m"}

// server → client
{"type": "snapshot", "symbol": "BTCUSDT", "live": true, "candles": [...]}  // seeded history
{"type": "candle",   "symbol": "BTCUSDT", "candle": {...}}                // every update
```

One upstream exchange connection is shared across every client watching the same
symbol, and the client reconnects with backoff and re-subscribes automatically.

`snapshot.live` is `true` when the `candle` frames that follow come from a real
upstream push (Binance/Coinbase for crypto, or a keyed vendor's socket for
stocks/forex/indices once one is configured) and `false` when `StreamHub` is
polling REST every ~15s and relaying it over this same connection instead. Both
arrive as identical `candle` frames — this field is the only way a client
knows the difference, which is why the terminal's stream badge reads this
rather than just "the socket is connected."

---

## Architecture

```
trading/
├── models.py            Candle, Series, Timeframe, AssetClass
├── markets.py           market catalogue + symbol resolution
├── providers/           binance, coinbase, yahoo, polygon, twelvedata,
│                        finnhub, alphavantage — behind one interface
├── market.py            caching + WebSocket fan-out hub
├── indicators.py        pure-Python indicators, None-padded
├── structure.py         swings, BOS/CHOCH, sweeps, phase
├── patterns.py          candlestick and price-action patterns
├── volume.py            profile, POC/VAH/VAL, divergence, delta proxy
├── smc.py               order blocks, FVGs, liquidity, premium/discount, OTE
├── levels.py            S/R, period levels, fibs, trendlines
├── regime.py            market context (step 1 of the pipeline)
├── probability.py       base rates + weighted evidence
├── setups.py            trade plan construction
├── risk.py              position sizing and risk assessment
├── analysis.py          the ordered pipeline
├── strategy/            spec, NL parser, features, engine, backtest,
│                        metrics, montecarlo, walkforward, audit
├── pine/                Pine Script v6 generation
├── learning.py          calibration store
└── narrative.py         LLM layer + concept glossary
```

The analysis layers never touch the network. They take a `Series` and return
findings — which is what makes them testable and the backtester honest.

**Dependencies:** the analysis stack is pure Python (no numpy or pandas), so it
imports anywhere the backend runs and the test suite needs no scientific stack.

---

## Deployment

```bash
docker compose up --build -d           # SQLite, no keys needed
docker compose --profile scale up -d   # adds Postgres + Redis
```

Before exposing this to the internet:

1. Set real API keys in `.env` and never commit it.
2. Put a TLS-terminating reverse proxy in front of it.
3. Restrict `CORS_ORIGINS` to your actual origin.
4. Authentication turns on automatically when you bind to a non-loopback
   address. Set `LEGEND_SECRET_KEY` explicitly, and leave `LEGEND_ALLOW_SIGNUP=false`
   after creating your account.
5. Set `LEGEND_TRUST_PROXY_HEADERS=true` **only** behind a proxy you control, so
   rate limiting keys on the real client IP.
6. If you run more than one worker, set `REDIS_URL` — otherwise each worker
   keeps its own rate-limit buckets and the effective limit multiplies by the
   worker count.

### Tests

```bash
python -m pytest tests/ -v     # 645 tests (632 without Redis — those skip)
```

---

## Honest limitations

Things this platform does **not** do, stated plainly:

- **Live execution is possible but deliberately hard to reach, through Alpaca
  or Tradier.** The default is internal paper trading with no broker at all.
  Live requires a server-side environment flag plus a restart, an explicit
  mode switch, a typed confirmation phrase, and separate credentials.
  **Tradier's adapter has not been exercised against a real Tradier account
  from this repo** — its request/response handling is covered by tests that
  stub the HTTP layer, which proves internal consistency, not that it matches
  Tradier's actual wire format. Verify sandbox round trips before going live
  with it. It is also long-only — shorting isn't implemented. Interactive
  Brokers and others are not implemented at all — the `BrokerAdapter`
  interface is there, the adapters are not.
- **Broker credentials are instance-level, not per-user.** They come from the
  environment only. A multi-user deployment cannot give each account its own
  broker, because storing per-user keys needs encryption at rest that this
  codebase does not have. Because of that, **`broker_paper` and `live` modes,
  switching the broker, and confirming live orders are restricted to the
  instance administrator** (the first registered account) — every other
  account is limited to internal `paper` mode, so a multi-user deployment
  never has strangers sharing one broker connection. A single-operator
  desktop instance is unaffected: the implicit local account is always admin.
- **MFA is optional and off by default.** Accounts are email plus password,
  with TOTP available as an opt-in second factor. Self-service password
  reset and email verification have both shipped (see above); verification
  is informational and anti-abuse only, and does not gate login. Losing both
  the authenticator and every recovery code is recoverable by the instance
  admin via `POST /auth/admin/mfa/reset` — deliberately not by password
  reset, which would let anyone who can read a reset email strip the second
  factor off an account. That endpoint is API-only: there is no admin or
  user-management screen in the UI to drive it from.
- **Password-reset email needs SMTP credentials you supply.** No mail-sending
  SaaS is wired in; without `SMTP_HOST` set, the reset link is logged rather
  than emailed, which only works for an operator with log access to their own
  instance.
- **Delta is estimated, not measured.** Real order-flow delta needs tick or
  order-book data that free OHLCV feeds do not carry. The estimate is derived
  from close-position-within-range and is labelled as an estimate everywhere it
  appears.
- **The economic calendar's importance is estimated, not official.** FRED has
  no impact-tier field; `trading/econcalendar.py` classifies releases by a
  small, hand-maintained keyword list. Without `FRED_API_KEY` set, news risk
  still reports as `unmodelled` rather than guessed at. FOMC rate decisions
  are not covered — they aren't in FRED's release catalogue.
- **Correlation is measured only against your own open paper positions**
  (up to 5, by Pearson correlation of bar-to-bar returns) — it cannot see a
  live broker's book, positions on another account, or anything closed. With
  no open positions, or too few overlapping bars to measure, it is reported as
  `unmodelled` rather than guessed at.
- **Options pricing has no live option chain.** `/options` (see the
  "Options pricing" section below) computes real Black-Scholes/binomial fair
  value, Greeks, implied volatility and multi-leg payoff diagrams for a
  contract you specify — but there is no strikes/bid-ask/open-interest chain
  to browse, because none of this platform's free market-data vendors serve
  that (the same constraint discovered verifying equity streaming — see
  "Honest limitations" above). Volatility is typed in or estimated from the
  underlying's own real historical prices, never faked. No assignment
  modelling.
- **Streaming without any API key is crypto-only.** Binance/Coinbase are
  keyless and stream tick-by-tick. Stocks, ETFs, indices and forex *can*
  stream too — `StreamHub` automatically prefers a real WebSocket over
  polling for any provider that has one (Polygon, Twelve Data, Finnhub all
  implement `stream_candles`) — but the three keyed vendors are not
  interchangeable on their free tiers, and this was verified by direct
  testing rather than assumed:
  - **Twelve Data's free tier works end-to-end.** `/time_series` (candle
    history) returns real data and the WebSocket streams live per-tick
    prices with no paid plan — confirmed with a real key against
    `api.twelvedata.com` and `wss://ws.twelvedata.com`, and confirmed in
    this app's own terminal (AAPL chart loads, badge shows `TWELVEDATA
    ● LIVE`, price updates without a page refresh). Set
    `TWELVEDATA_API_KEY` to enable it.
  - **Finnhub's free tier does not.** `/quote` returns real data, but
    `/stock/candle` — the historical-OHLC endpoint the terminal needs to
    seed a chart before streaming can start — returns `403 Forbidden`. A
    free Finnhub key alone cannot produce a working live equity chart,
    even though its WebSocket itself would otherwise be reachable.
  - **Polygon's free tier is EOD/15-min-delayed only** (5 calls/min,
    explicitly a "development sandbox" per its own docs) — no real-time
    access, keyed or not, below its paid tiers.

  Given `NON_CRYPTO_PREFERENCE = (polygon, twelvedata, finnhub, alphavantage,
  yahoo)`, setting only `TWELVEDATA_API_KEY` is sufficient — Polygon isn't
  configured so it's skipped, and Twelve Data is picked next. Without a
  working keyed stream, those markets poll every ~15 seconds instead, and
  the terminal's stream badge says `DELAYED` rather than `LIVE` so this is
  never ambiguous in the UI. **Futures have no streaming path at all**,
  keyed or not — none of the three adapters implement it, and genuinely
  free real-time futures data doesn't really exist as a category.
- **TradingView Strategy Tester results will differ slightly** from the local
  backtester, because TradingView makes different intrabar fill assumptions.
- **Backtests are not predictions.** Every audit says so, and the Monte Carlo
  section exists specifically to show how wide the range of outcomes really is.

Nothing here is financial advice.
