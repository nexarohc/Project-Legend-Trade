# Legend Trade — Project Specification

Legend Trade is a trading terminal: real-time charts across every listed market,
a deterministic analysis pipeline that shows its evidence, strategy generation
validated by an adversarial audit, options pricing, and a guardrailed execution
layer that connects to real brokers.

This document is the architecture spec. For the running state of the project —
what is built, what is verified, what is knowingly missing — read
[`docs/PROJECT_STATE.md`](./docs/PROJECT_STATE.md), which is the authoritative
document and is updated with every change. For the API and the analysis
internals, read [`docs/TRADING_TERMINAL.md`](./docs/TRADING_TERMINAL.md).

## 1. Feature scope

- Real-time and historical price data across stocks (any market cap), ETFs,
  indices, crypto, forex, futures, commodities and international listings
- Streaming charts over WebSocket, with an explicit live-vs-polling indicator
- A deterministic analysis pipeline: market context, structure, price action,
  volume, indicators, Smart Money Concepts, key levels
- Probability estimation from base rates measured on the symbol's own history,
  combined with weighted and individually-explained factors
- Trade setup construction that returns an explicit "no trade" with a reason
- Strategy building from plain English, backtesting with realistic costs and
  next-bar fills, Monte Carlo, walk-forward, regime tests, and a 15-check audit
- Pine Script v6 export
- Options: Black-Scholes and CRR binomial pricing, Greeks, implied volatility,
  payoff diagrams for named multi-leg strategies
- A market scanner over the full analysis feature set
- An economic calendar (FRED-backed) wired into the risk engine
- Guardrailed execution against Alpaca, Tradier and Interactive Brokers, with
  paper trading as the default
- Accounts, per-user data isolation, MFA, email verification, admin user
  management, and Redis-backed rate limiting

Deliberately out of scope: anything that makes a trading decision by asking a
language model. Signals, probabilities and risk decisions are arithmetic on
price data, reproducible from the same inputs. An LLM is used in exactly one
place — turning a completed analysis into prose — and it degrades to a
deterministic template when no key is set.

## 2. Technology stack

| Layer | Choice | Notes |
|---|---|---|
| Frontend | React 18 + Vite 5 + Tailwind 3 | no component library, no state library |
| Charts | lightweight-charts 4 | the same engine TradingView ships |
| Backend | FastAPI + uvicorn | REST plus a WebSocket stream hub |
| Persistence | SQLite via SQLAlchemy | hand-rolled additive migrations, no Alembic |
| Rate limiting | in-process token buckets, Redis-backed when configured | a Redis outage degrades to in-process, not open or closed |
| Observability | hand-rolled Prometheus exposition + JSON logging | no `prometheus-client` dependency |
| Market data | Binance, Coinbase, Yahoo, Twelve Data, Polygon, Finnhub, Alpha Vantage, FRED | probed in order; works with no key at all |
| Brokers | Alpaca, Tradier, Interactive Brokers | behind one adapter interface |

## 3. Folder structure

```
Legend-Trade/
├── frontend/     React + Vite + Tailwind: landing page and trading terminal
├── backend/      FastAPI app: routers, auth, rate limiting, observability
├── trading/      the platform itself
│   ├── providers/   market data sources behind one interface
│   ├── analysis/    indicators, structure, SMC, volume, levels
│   ├── strategy/    parser, backtester, Monte Carlo, walk-forward, audit
│   └── execution/   broker adapters, guardrails, engine, scheduler
├── database/     SQLAlchemy models, engine, migrations
├── tests/        pytest suite — no network, no keys required
└── docs/         state, decisions, API reference, workflow
```

## 4. Architecture overview

```
┌──────────────┐   REST + WebSocket   ┌───────────────┐
│  frontend/   │ <------------------> │   backend/    │
│ React + Vite │                      │   FastAPI     │
└──────────────┘                      └───────┬───────┘
                                              │
                              ┌───────────────▼───────────────┐
                              │           trading/            │
                              │                               │
                    ┌─────────┴────────┬──────────┬───────────┴─────────┐
                    ▼                  ▼          ▼                     ▼
               providers/         analysis/   strategy/            execution/
            (market data)       (evidence)  (backtest+audit)   (guardrails+brokers)
```

Data flows one way: providers produce candles, analysis describes them, strategy
tests hypotheses against them, and execution acts — but only after guardrails
agree. `trading/execution/guardrails.py` is the single place in the codebase
allowed to refuse an order, so every reason an order can be rejected is visible
in one file rather than scattered across call sites.

## 5. Dependencies

- Node: `react`, `react-dom`, `vite`, `tailwindcss`, `lightweight-charts`
- Python: `fastapi`, `uvicorn`, `sqlalchemy`, `pydantic-settings`, `httpx`,
  `websockets`, `numpy`
- Optional: `redis` (shared rate-limit buckets), `anthropic` (narrative prose)

Versions are pinned in `frontend/package.json` and
`backend/requirements-server.txt`.

## 6. Design rules

These are the rules the codebase is actually held to, not aspirations.

1. **Describe before predicting.** Every conclusion the platform reaches is
   accompanied by the evidence that produced it. A probability with no visible
   factors is not shippable.
2. **"No trade" is a valid answer.** The setup builder returns it with a reason
   whenever the evidence does not support a position. A tool that always finds a
   trade is a tool that is lying.
3. **Guardrails fail closed.** Any check that cannot be evaluated refuses. A
   guardrail that silently passes when its input is missing is not a guardrail.
4. **Live trading is opt-in three times over.** A server-side environment flag,
   an explicit mode selection, and a typed confirmation phrase. No single
   mistake reaches real money.
5. **Per-user overrides may only tighten.** Risk limits can be made stricter by
   a user but never looser, not even by an admin.
6. **Degrade to a known-good configuration.** Where a dependency can fail, the
   fallback is a working configuration rather than fail-open or fail-closed —
   a Redis outage drops back to in-process rate-limit buckets.
7. **Nothing unverified is described as verified.** The docs distinguish what
   has been run against live data from what has only been tested against
   recorded wire formats. The broker adapters are in the second category.

## 7. History

This repository was extracted from `Project-DEX`, which contained both a desktop
AI assistant and this trading platform. They shared a database and a FastAPI app
but no application code, so the split removed the assistant's packages, routers,
models and frontend without touching the trading platform's logic. See the
"Relationship to Project-DEX" section of the [`README.md`](./README.md) for the
migration notes that matter to an existing deployment.
