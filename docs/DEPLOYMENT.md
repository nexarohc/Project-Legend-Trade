# Deployment

Putting Legend Trade in front of real users. Everything here was run against
this codebase; where a command has not been executed end to end in a production
environment, it says so.

Read [`TRADING_TERMINAL.md`](./TRADING_TERMINAL.md)'s limitations section and
the root [`PRIVACY.md`](../PRIVACY.md) and [`TERMS.md`](../TERMS.md) before
launch. The legal drafts have unfilled placeholders and open questions that are
not code problems.

---

## 1. The pre-flight checklist

Nothing below is optional for a public instance.

| | Setting | Why |
|---|---|---|
| ☐ | `LEGEND_SECRET_KEY` set explicitly | Left unset, a random key is generated and saved to `~/.legend-trade/secret_key`. Fine for one instance; behind two, tokens issued by one are rejected by the other and users are randomly signed out. |
| ☐ | `LEGEND_ALLOW_SIGNUP=false` **after** creating the admin account | The first account is always allowed so a fresh install can be set up. Leaving signup open makes your instance a free public service. |
| ☐ | `CORS_ORIGINS` set to your real domain | The default allows `localhost:5173`. |
| ☐ | `LEGEND_TRUST_PROXY_HEADERS=true` **only** behind a proxy you control | Trusting `X-Forwarded-For` while directly exposed lets any client spoof a fresh identity per request and walk straight through rate limiting. |
| ☐ | `HOST=0.0.0.0` | Required to be reachable. This also switches authentication on under the default `auto` mode — that is the intended interaction, not a side effect. |
| ☐ | `TWELVEDATA_API_KEY` set | See [§5](#5-market-data-you-need-a-key). |
| ☐ | SMTP configured | See [§6](#6-email). Without it, password-reset links go to the server log. |
| ☐ | Backups scheduled and **a restore tested** | See [§4](#4-backups). |
| ☐ | TLS terminating in front of the app | See [§2](#2-tls-and-the-reverse-proxy). |

Verify authentication is actually on before letting anyone in:

```bash
curl -s https://your-domain/auth/status
# {"auth_required":true, ...}   <- must be true
```

If that says `false`, stop. The terminal is open to the world.

## 2. TLS and the reverse proxy

The app speaks plain HTTP and does not terminate TLS. Put a reverse proxy in
front of it. Caddy is the shortest path because it obtains and renews
certificates without configuration:

```caddy
# /etc/caddy/Caddyfile
your-domain.com {
    encode gzip

    # The chart stream is a WebSocket. Caddy proxies upgrades natively, but the
    # timeout matters: a market stream is idle between ticks on a slow symbol
    # and a short read timeout will disconnect it repeatedly.
    reverse_proxy 127.0.0.1:8000 {
        transport http {
            read_timeout 300s
        }
    }
}
```

nginx equivalent, where the upgrade headers must be explicit:

```nginx
server {
    listen 443 ssl http2;
    server_name your-domain.com;

    ssl_certificate     /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;

        # Without these two, the WebSocket handshake fails and charts fall back
        # to polling — which the UI will honestly badge DELAYED, so the symptom
        # is "why is nothing live" rather than an error.
        proxy_set_header Upgrade    $http_upgrade;
        proxy_set_header Connection "upgrade";

        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_read_timeout 300s;
    }
}
```

Set `LEGEND_TRUST_PROXY_HEADERS=true` **only once this is in place.** The proxy
must overwrite `X-Forwarded-For` rather than append to a client-supplied one —
both configurations above do.

## 3. Running the app

Docker is the shortest path and builds the frontend into the image:

```bash
docker compose up --build -d
```

Without Docker, run it under a supervisor so it restarts. systemd:

```ini
# /etc/systemd/system/legend-trade.service
[Unit]
Description=Legend Trade
After=network.target

[Service]
Type=exec
User=legend
WorkingDirectory=/opt/legend-trade
EnvironmentFile=/opt/legend-trade/.env
ExecStart=/opt/legend-trade/venv/bin/python -m uvicorn app.main:app \
          --host 0.0.0.0 --port 8000 --app-dir backend
Restart=always
RestartSec=5

# The app needs to write only its own database directory.
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/home/legend/.legend-trade

[Install]
WantedBy=multi-user.target
```

> Not yet run on a real production host. The unit is written against this
> repo's actual entry point and paths, but treat the hardening directives as a
> starting point to test rather than a guarantee.

### Workers, and the one thing that breaks with more than one

More than one uvicorn worker is fine **only if** you also set `REDIS_URL`.
Without it, rate-limit buckets live in each worker's memory, so the effective
limit multiplies by the worker count — four workers turns a 10-attempt login
limit into 40. A Redis outage degrades back to in-process buckets rather than
failing open or closed.

SQLite also tolerates concurrent writers poorly. For a single-operator
instance, one worker plus a proxy is the honest recommendation.

## 4. Backups

**A `cp` of a live SQLite file is not a backup.** SQLite writes in pages, and a
copy taken mid-transaction can open cleanly and be corrupt in the middle — which
you discover on the day you need it.

`scripts/backup.py` uses SQLite's online-backup API, which snapshots a database
that is actively being written to, then verifies the result with
`PRAGMA integrity_check` before keeping it.

```bash
python scripts/backup.py --dest /var/backups/legend --keep 14
```

Verified working: writes a snapshot, verifies it, prunes to the newest N, and
exits non-zero if the integrity check fails. Two runs in the same second get
distinct filenames rather than silently overwriting.

Daily via cron, relying on the failure mail:

```cron
MAILTO=you@example.com
17 3 * * * cd /opt/legend-trade && /opt/legend-trade/venv/bin/python scripts/backup.py --dest /var/backups/legend --keep 14
```

**Then test a restore.** A backup you have never restored is a hypothesis:

```bash
python scripts/backup.py --verify-only /var/backups/legend/legend-<stamp>.db
# stop the app, then:
cp /var/backups/legend/legend-<stamp>.db ~/.legend-trade/legend.db
```

Copy backups off the machine. A snapshot sitting on the disk that dies with the
server is not a backup either.

## 5. Market data: you need a key

**Measured, not assumed:** on a host whose IP Yahoo is rate-limiting, *every*
non-crypto symbol fails — equities, indices, forex and futures alike, with
`Yahoo Finance is rate-limiting this network`. Yahoo limits per IP, and cloud
hosts, shared IPs and VPNs are exactly the addresses most likely to be limited.

- **Crypto** needs no key, ever. Binance and Coinbase stream tick-by-tick.
- **Everything else**: set `TWELVEDATA_API_KEY`. Its free tier is the one
  verified to serve both historical candles and a live WebSocket. Polygon's free
  tier is end-of-day and 15-minute-delayed only; Finnhub's free tier returns 403
  on the candle endpoint the charts need.

Note that a free Twelve Data plan does not cover every exchange. `NASDAQ:SERV`
works; `PSX:SERV` returns "available starting with the Pro or Venture plan".
The interface surfaces the vendor's own message, so the failure is legible.

Check the data licence before showing this to the public — a personal-use tier
usually does not permit redistribution.

## 6. Email

Without SMTP, password-reset links are written to the server log. That works
for a single operator with log access; for real users it means **the operator
can read every reset link**, and there is no self-service recovery.

```
SMTP_HOST=smtp.your-provider.com
SMTP_PORT=587
SMTP_USERNAME=...
SMTP_PASSWORD=...
SMTP_FROM=no-reply@your-domain.com
SMTP_USE_TLS=true
FRONTEND_URL=https://your-domain.com
```

`FRONTEND_URL` is what reset and verification links point at. Get it wrong and
every link in every email is broken.

## 7. Monitoring

`GET /metrics` serves Prometheus exposition format. It is **admin-only whenever
authentication is enforced**, because metrics leak operational shape — which
endpoints exist and how much traffic each takes. Scrape it with an admin bearer
token, or set `METRICS_ENABLED=false` to remove it.

```yaml
scrape_configs:
  - job_name: legend-trade
    metrics_path: /metrics
    authorization:
      credentials: <admin access token>
    static_configs:
      - targets: ["your-domain.com"]
```

Exposed: `legend_uptime_seconds`, `legend_http_requests_total`,
`legend_http_request_duration_seconds` (a histogram), and
`legend_rate_limited_total`. Request labels use the *route template*, not the
raw path, so `/market/candles` stays one series instead of one per symbol —
unmatched paths collapse to `<unmatched>`.

Worth alerting on:

- `legend_rate_limited_total{limiter="/auth/login"}` climbing — someone is
  guessing passwords.
- 5xx rate on `/market/*` — a data provider is down or your key hit its quota.
- `legend_uptime_seconds` resetting repeatedly — the app is crash-looping.

Set `LOG_FORMAT=json` so a log shipper can parse lines without regexes. This
covers uvicorn's own access lines too.

## 8. Connecting a broker

Ordinary accounts get internal simulation and never touch a broker. This
section is for the administrator enabling `broker_paper`.

**Nothing in this codebase has ever placed an order with a real broker.** The
Alpaca, Tradier and IBKR adapters are tested against recorded wire formats, not
live accounts. Alpaca is the shortest path to changing that, because its paper
and live APIs are identical apart from the base URL and which key pair you
supply — so a paper test is genuine evidence about live behaviour.

### Step 1 — get paper keys

Create a free Alpaca account, switch to **Paper Trading**, and generate an API
key pair. Put them in `.env` (which is gitignored — never commit them):

```
ALPACA_PAPER_KEY_ID=...
ALPACA_PAPER_SECRET_KEY=...
```

Paper and live read *different* variable names on purpose. Sharing one pair
makes it far too easy to point live keys at what you believe is the sandbox.

### Step 2 — preflight

```bash
python scripts/preflight.py
```

This checks the whole chain and **transmits no order**: credentials present,
broker reachable, account state (equity, buying power, whether the broker has
halted the account), and a representative order run through the real guardrail
rules to show exactly which would refuse it. It exits non-zero unless the chain
is clear, so it works as a deployment gate.

With nothing configured it names the missing variables rather than saying
"not configured":

```
1. Credentials
[ FAIL ] alpaca has no credentials for broker_paper mode
[ warn ]   ALPACA_PAPER_KEY_ID: MISSING
[ warn ]   ALPACA_PAPER_SECRET_KEY: MISSING
```

Size the sample order to something you would actually place — the guardrails
are proportional to equity, and the default 10% position cap refuses more
first orders than anything else:

```bash
python scripts/preflight.py --symbol AAPL --quantity 5 --price 200
```

> Verified against stub and unconfigured adapters, including the refusal paths
> (undersized account, broker-halted account). **Not yet run against real Alpaca
> credentials** — that is the step this exists to make safe, and it needs your
> keys.

### Step 3 — place the first order from the UI

Preflight deliberately cannot place it. Open the **Execute** panel, confirm the
mode reads `broker_paper`, and place one small order with a protective stop.
Then check it appears in your Alpaca dashboard — that round trip is the only
thing that proves the adapter works.

Do **not** set `LEGEND_ENABLE_LIVE_TRADING` until a paper order has completed
that round trip.

## 9. Before you let anyone in

1. `curl https://your-domain/auth/status` returns `auth_required: true`.
2. Create the admin account, then set `LEGEND_ALLOW_SIGNUP=false` and restart.
3. Confirm the chart streams (badge reads `LIVE`, not `DELAYED`) — if it is
   always `DELAYED`, the WebSocket upgrade is not passing through the proxy.
4. Trigger a password reset and confirm the email arrives with a working link.
5. Run a backup and restore it into a throwaway copy.
6. Fill in every `{{PLACEHOLDER}}` in `PRIVACY.md` and `TERMS.md` (below), and
   get the regulatory question in both of them answered by a real lawyer.

Item 6 is the one that is not a technical task and is the one most likely to be
skipped. Whether providing trade setups and probabilities to the public is a
regulated activity depends on your jurisdiction, and a disclaimer does not
settle it.

### Rendering the policy documents

`PRIVACY.md` and `TERMS.md` in the repository are templates. Rather than editing
them by hand in two places, put the answers in one file and render:

```bash
python scripts/legal.py --init      # writes legal/answers.json — fill it in
python scripts/legal.py             # renders legal/PRIVACY.md and legal/TERMS.md
python scripts/legal.py --check     # exit 1 if anything is unanswered
```

There are nine values. Seven are decisions you can make now — legal entity name,
service URL, privacy and support addresses, minimum age, software licence, and
the last-updated date (blank means today). Two are not:
`GOVERNING_LAW_JURISDICTION` and `LIABILITY_CAP` need a lawyer, and the script
says so rather than accepting whatever a template found online suggests.

Nothing renders until every value is answered, and answers that are blank, still
in braces, or say `TBD` are refused — a policy with a visible hole in it is
better than one that merely looks finished. Every problem is reported in a single
pass so filling nine values takes one round trip, not nine.

`--check` writes nothing and is the gate to put in a deployment pipeline: no
launch with an unfilled policy.

Output lands in `legal/`, which is gitignored — the answers and the rendered
documents belong to one deployment, while the templates belong to the repository.
Serve the rendered files, not the templates.

The rendered documents keep their **"not yet reviewed by a lawyer"** banner.
Filling in a legal name is not legal review. Remove that banner by hand once it
has stopped being true, and not before.
