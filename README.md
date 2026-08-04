# Legend Trade

An institutional-grade trading terminal: real-time charts across every listed
market, a deterministic analysis pipeline that shows its evidence, strategy
generation with an adversarial audit, and an execution layer whose only job is
to refuse orders that shouldn't be sent.

- [`docs/PROJECT_STATE.md`](./docs/PROJECT_STATE.md) — **start here when
  resuming work**: current state, decisions and why, known gaps, next actions
- [`docs/TRADING_TERMINAL.md`](./docs/TRADING_TERMINAL.md) — market coverage,
  analysis pipeline, backtester, execution, full API reference
- [`PROJECT_SPEC.md`](./PROJECT_SPEC.md) — architecture, tech stack, folder
  structure, roadmap
- [`docs/DEVELOPMENT_WORKFLOW.md`](./docs/DEVELOPMENT_WORKFLOW.md) — how this
  was built, one module at a time

## What it does

It charts every listed market — US stocks of any market cap (including micro
caps like `NASDAQ:SERV`), ETFs, indices, crypto, forex, futures, commodities and
international listings — and **works with no API key at all**: crypto streams
tick-by-tick from Binance or Coinbase, and everything else comes from Yahoo
Finance, with automatic failover to a keyed provider when one is configured.

The design rule it is built around: **describe the market before predicting it,
and show the evidence for every conclusion.** Market context, structure, price
action, volume, indicators, Smart Money Concepts and key levels are all computed
and explained before the probability engine runs; probabilities combine base
rates measured on the symbol's own history with weighted, individually-explained
factors; and the setup builder returns an explicit "no trade" with a reason when
the evidence does not support one.

Strategies described in plain English are parsed into an executable spec,
backtested with realistic costs and next-bar fills, validated with Monte Carlo,
walk-forward and regime tests, then put through a 15-check audit that returns
`APPROVED` / `REVISE` / `REJECT` with reasoning. Orders route through a guardrail
layer whose only job is to say no: no entry without a protective stop, nothing
from a strategy the audit rejected, and a kill switch that flattens everything.
Paper trading is the default and needs no broker; live trading is off unless a
server-side flag, an explicit mode switch and a typed confirmation phrase all
line up.

Options are priced with Black-Scholes and a Cox-Ross-Rubinstein binomial tree,
with full Greeks, implied volatility by bisection, and payoff diagrams for six
named multi-leg strategies.

Accounts, per-user data isolation, MFA, and Redis-backed rate limiting are built
in. Authentication is enforced automatically whenever the server is bound to
anything other than loopback, so a localhost run stays frictionless while a
deployed one is protected by default.

## Running it

**Backend** (Python 3.11+):
```
pip install -r backend/requirements-server.txt
python backend/run.py           # serves http://127.0.0.1:8000
```
Copy `.env.example` to `.env` to configure market-data providers, SMTP, Redis
and broker credentials. None of them are required to boot.

**Frontend** (Node 20+):
```
cd frontend && npm install
npm run dev                     # Vite dev server on :5173
```

Or `docker compose up --build` for a self-contained deployment on :8000.

**Tests**: `python -m pytest tests/` from the repo root. They run against
generated series and stub providers, so they need no network access and no keys.

## Status

Verified end-to-end in this repo against live market data: charts render 500
real candles and update tick-by-tick over the WebSocket, the full analysis
pipeline runs on live prices, and strategies backtest and audit correctly. The
test suite covers the engines, authentication and execution — including the
backtester's no-look-ahead guarantees, per-user data isolation, and every
guardrail that can refuse an order.

Live broker execution exists but is **off by default and gated four independent
ways**. No order has ever been sent to a real broker from this repo, because
that needs your own credentials — so the Alpaca, Tradier and IBKR adapters are
covered by tests against recorded wire formats, not against live accounts. Read
the limitations section of [`docs/TRADING_TERMINAL.md`](./docs/TRADING_TERMINAL.md)
before deploying this anywhere near real money.

## Relationship to Project-DEX

This codebase was extracted from `Project-DEX`, which held both a desktop AI
assistant and this trading platform in one repository. The two shared a database
and a FastAPI app but no application code, which made the split mechanical: the
assistant's packages, routers, models and frontend were removed here, and the
trading platform is now the whole of this repo.

Two consequences worth knowing:

- Environment variables previously spelled `DEX_*` are now `LEGEND_*`. The old
  names still resolve, so an existing `.env` keeps working. The single exception
  is `LEGEND_ENABLE_LIVE_TRADING`, which deliberately does **not** honour its
  old name — see the comment in `trading/execution/guardrails.py`.
- The database defaults moved from `~/.dex/dex.db` to
  `~/.legend-trade/legend.db`. Point `LEGEND_DB_PATH` at the old file to keep
  existing accounts and history.

## Layout

```
backend/    FastAPI app: routers, auth, rate limiting, observability
trading/    the platform itself — providers, analysis, strategy, execution
database/   SQLAlchemy models, engine, migrations
frontend/   React + Vite + Tailwind: landing page and trading terminal
tests/      pytest suite, no network required
docs/       state, decisions, API reference, workflow
```
