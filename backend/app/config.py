from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve .env by absolute path (repo root = two levels up from this file:
# backend/app/config.py -> backend/ -> repo root) so the key loads no matter
# which directory the backend is launched from.
ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ENV_FILE), extra="ignore")

    # --- Narrative generation -------------------------------------------------
    # Optional. trading/narrative.py turns a completed analysis into prose; with
    # neither key set it falls back to a deterministic template, so no feature
    # depends on a model being reachable. Nothing else in the platform calls an
    # LLM — signals, probabilities and risk decisions are all arithmetic.
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-flash-latest"

    # --- Market data providers ---------------------------------------------
    # Binance and Coinbase need no key; the platform probes them in order and
    # uses whichever answers, so charts are live on a fresh install. The rest
    # unlock equities, FX, indices and options.
    market_data_provider: str = "auto"
    polygon_api_key: str = ""
    twelvedata_api_key: str = ""
    finnhub_api_key: str = ""

    # --- Authentication -----------------------------------------------------
    # These accept a LEGEND_-prefixed name as well as the bare one. The prefix
    # is what the docs use and what people reach for, and a security setting
    # that is silently ignored because the variable was spelled the documented
    # way is the worst possible failure: you ask for authentication and get
    # none. SECRET_KEY especially is too generic a name to rely on alone.
    #
    # DEX_ is still accepted everywhere LEGEND_ is. This codebase was extracted
    # from a repo that used that prefix, and an operator's existing .env going
    # quietly ignored after a rename is the same silent-failure mode the aliases
    # exist to prevent. It is a compatibility shim, not the documented spelling.

    # Signing secret for access/refresh tokens. Left empty, one is generated and
    # persisted to ~/.legend-trade/secret_key so restarts don't sign everyone out. Set it
    # explicitly for any deployment running more than one instance.
    secret_key: str = Field(
        default="", validation_alias=AliasChoices("LEGEND_SECRET_KEY", "DEX_SECRET_KEY", "SECRET_KEY")
    )

    # always | never | auto. "auto" requires authentication unless the server is
    # bound to loopback, so a desktop app stays frictionless while anything
    # listening on a real interface is protected by default.
    auth_required: str = Field(
        default="auto", validation_alias=AliasChoices("LEGEND_AUTH_REQUIRED", "DEX_AUTH_REQUIRED", "AUTH_REQUIRED")
    )

    # Whether accounts beyond the first can be created. The first account is
    # always allowed so a fresh install can be set up.
    allow_signup: bool = Field(
        default=False, validation_alias=AliasChoices("LEGEND_ALLOW_SIGNUP", "DEX_ALLOW_SIGNUP", "ALLOW_SIGNUP")
    )

    # Only honour X-Forwarded-For when actually behind a reverse proxy —
    # otherwise any client could spoof its identity to defeat rate limiting.
    trust_proxy_headers: bool = Field(
        default=False,
        validation_alias=AliasChoices("LEGEND_TRUST_PROXY_HEADERS", "DEX_TRUST_PROXY_HEADERS", "TRUST_PROXY_HEADERS"),
    )

    host: str = "127.0.0.1"
    port: int = 8000
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # --- Transactional email --------------------------------------------------
    # Used only for password-reset links. Left unset, no mail-sending SaaS is
    # wired in here — that would be its own credential and billing decision —
    # so the reset link is logged instead, which still works for a self-hosted,
    # single-operator instance where the operator has log access.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = "no-reply@localhost"
    smtp_use_tls: bool = True

    # Where the frontend is served, so a password-reset email can link back to it.
    frontend_url: str = "http://localhost:5173"

    # --- Observability --------------------------------------------------------
    # "text" (default) is the human-readable format. Set "json" on a deployed
    # instance so a log shipper can parse lines without regexes.
    log_format: str = Field(default="text", validation_alias=AliasChoices("LEGEND_LOG_FORMAT", "LOG_FORMAT", "DEX_LOG_FORMAT"))
    log_level: str = Field(default="INFO", validation_alias=AliasChoices("LEGEND_LOG_LEVEL", "LOG_LEVEL", "DEX_LOG_LEVEL"))

    # Whether GET /metrics is served at all. It is admin-only when
    # authentication is enforced; see the endpoint's docstring.
    metrics_enabled: bool = Field(
        default=True, validation_alias=AliasChoices("LEGEND_METRICS_ENABLED", "METRICS_ENABLED", "DEX_METRICS_ENABLED")
    )

    # --- Rate limiting --------------------------------------------------------
    # Left empty, rate-limit buckets live in each worker's memory, so the
    # effective limit multiplies by the worker count. Set this (e.g.
    # redis://localhost:6379/0) to share buckets across every worker. A Redis
    # outage degrades back to in-process buckets rather than failing open or
    # closed — see backend/app/ratelimit_redis.py.
    redis_url: str = Field(default="", validation_alias=AliasChoices("LEGEND_REDIS_URL", "REDIS_URL", "DEX_REDIS_URL"))

    # --- Execution ------------------------------------------------------------
    # How often broker-connected accounts (broker_paper/live) get reconciled
    # in the background, so order/position state stays fresh without anyone
    # calling POST /execution/reconcile by hand. Set to 0 to disable the loop
    # entirely (reconcile stays available on demand either way).
    execution_reconcile_interval_seconds: int = Field(
        default=300, validation_alias=AliasChoices("EXECUTION_RECONCILE_INTERVAL_SECONDS")
    )


settings = Settings()
