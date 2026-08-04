import asyncio
import contextlib
import importlib
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from app.config import settings
from app.observability import configure_logging, metrics, route_template
from app.routers import (
    analysis,
    calendar,
    execution,
    market,
    options,
    scanner,
    strategy,
    trading,
    users,
)
from database.db import engine, init_db
from database.migrations import run_migrations

configure_logging(settings.log_format, settings.log_level)
logger = logging.getLogger("legend.backend")



@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Re-applied here as well as at import: uvicorn installs its own handlers
    # on its loggers *after* this module is imported, so configuring once at
    # import time leaves its startup and access lines in the old format.
    configure_logging(settings.log_format, settings.log_level)

    init_db()
    # Bring pre-existing databases up to the current schema. Idempotent, so it
    # is safe on every start; without it a database created before accounts
    # existed would have trading tables with no user_id column.
    run_migrations(engine)

    from app.dependencies import auth_required
    from app.security import is_loopback

    if auth_required():
        logger.info("authentication is ENFORCED on this instance")
    else:
        logger.warning(
            "authentication is DISABLED (host=%s). This is intended for local desktop use. "
            "Bind to a non-loopback address or set LEGEND_AUTH_REQUIRED=always before exposing "
            "this server.",
            settings.host,
        )
        if not is_loopback(settings.host):
            logger.error(
                "LEGEND_AUTH_REQUIRED is set to 'never' while bound to %s — this server is "
                "reachable off-host with no authentication.",
                settings.host,
            )

    if settings.redis_url:
        from app import ratelimit, ratelimit_redis

        ratelimit.use_redis(ratelimit_redis.connect(settings.redis_url))
    else:
        logger.info(
            "rate limiting is in-process (no REDIS_URL); limits apply per worker, "
            "which is correct for a single-instance deployment"
        )

    reconcile_task = None
    if settings.execution_reconcile_interval_seconds > 0:
        from trading.execution.scheduler import reconcile_loop

        reconcile_task = asyncio.create_task(
            reconcile_loop(settings.execution_reconcile_interval_seconds)
        )
    else:
        logger.info("scheduled broker reconcile disabled (EXECUTION_RECONCILE_INTERVAL_SECONDS=0)")

    logger.info("Legend Trade backend ready")
    yield

    if reconcile_task is not None:
        reconcile_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reconcile_task


app = FastAPI(
    title="Legend Trade API",
    version="1.0.0",
    lifespan=lifespan,
    description=(
        "Real-time market data, institutional chart analysis, options pricing, strategy "
        "generation, backtesting with an adversarial audit, Pine Script export, and a "
        "guardrailed execution layer."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def record_metrics(request, call_next):
    """Time every request and count it by route template.

    The template — not the raw path — keeps cardinality bounded; see
    `observability.route_template`. A failing request is still recorded,
    under status 500, because an endpoint that only errors would otherwise
    vanish from the metrics entirely at exactly the moment it matters.
    """
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        metrics.record_request(
            request.method, route_template(request), 500, time.perf_counter() - started
        )
        raise

    metrics.record_request(
        request.method, route_template(request), response.status_code,
        time.perf_counter() - started,
    )
    if response.status_code == 429:
        metrics.record_rate_limited(route_template(request))
    return response


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/metrics")
def prometheus_metrics(request: Request):
    """Prometheus exposition format.

    **Admin-only whenever authentication is enforced.** Metrics leak
    operational shape — which endpoints exist, how much traffic each takes,
    when the instance restarted — so this is not public on a deployed
    instance. It reuses the same bearer-token auth as everything else rather
    than inventing a second mechanism, so a scraper is configured with an
    admin token. On a loopback desktop run authentication is off entirely and
    so is this gate, which is the same trade the rest of the API makes.

    Set `METRICS_ENABLED=false` to remove the endpoint's output altogether.
    """
    if not settings.metrics_enabled:
        raise HTTPException(status_code=404, detail="Metrics are disabled on this instance.")

    from app.dependencies import auth_required, resolve_user_from_token
    from database.db import SessionLocal

    if auth_required():
        # Resolved by hand rather than via Depends() so the gate can be
        # skipped entirely on a loopback desktop run, where authentication is
        # off for every other endpoint too.
        header = request.headers.get("authorization") or ""
        if not header.lower().startswith("bearer "):
            raise HTTPException(
                status_code=401,
                detail="Metrics require an admin bearer token on an authenticated instance.",
            )
        session = SessionLocal()
        try:
            user = resolve_user_from_token(header.split(" ", 1)[1].strip(), session)
            if not user.is_admin:
                raise HTTPException(status_code=403, detail="Admin access required.")
        finally:
            session.close()

    return PlainTextResponse(metrics.render_prometheus(), media_type="text/plain; version=0.0.4")


# --- trading terminal: always available -------------------------------------
app.include_router(market.router)
app.include_router(options.router)
app.include_router(analysis.router)
app.include_router(strategy.router)
app.include_router(trading.router)
app.include_router(execution.router)
app.include_router(scanner.router)
app.include_router(calendar.router)
app.include_router(users.router)

logger.info(
    "mounted routers: market, options, analysis, strategy, trading, execution, "
    "scanner, calendar, auth"
)
