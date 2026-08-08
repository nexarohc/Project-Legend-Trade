"""Rate limiting: a token bucket per client key.

Buckets refill continuously rather than resetting on a boundary, which avoids
the burst-at-the-turn-of-the-window behaviour of naive fixed-window counters.

**Scope, stated plainly:** by default this is per-process and in-memory. It
protects a single-instance deployment, which is what this platform targets.
Behind several workers or replicas each process would keep its own counters,
so the effective limit multiplies by the worker count.

Set `REDIS_URL` to close that gap: the buckets move into Redis and every
worker shares them (see `ratelimit_redis.py`, which also explains why a Redis
outage degrades back to these in-process buckets rather than failing open or
closed). Left unset, nothing changes and this stays exactly as it was.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

from fastapi import HTTPException, Request, status

logger = logging.getLogger("legend.ratelimit")

# Set by `use_redis()` at startup when REDIS_URL is configured. While None,
# every limiter uses its own in-process buckets, which is the default.
_shared_backend = None


def use_redis(backend) -> None:
    """Point every limiter at a shared Redis backend (or None to go back to
    in-process buckets). Called once from the app's lifespan."""
    global _shared_backend
    _shared_backend = backend


@dataclass
class _Bucket:
    tokens: float
    last_refill: float


@dataclass
class RateLimiter:
    """Token bucket keyed by client identity."""

    capacity: int
    refill_per_second: float
    name: str = "default"
    _buckets: dict[str, _Bucket] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _last_sweep: float = field(default_factory=time.monotonic)

    # Buckets for idle clients are dropped periodically so a long-running
    # process cannot accumulate one entry per IP that ever connected.
    SWEEP_INTERVAL = 300.0

    def check(self, key: str, cost: float = 1.0) -> tuple[bool, float]:
        """Consume `cost` tokens. Returns (allowed, seconds until retry).

        Uses the shared Redis bucket when one is configured, falling back to
        the in-process bucket below if Redis cannot be reached — see
        `ratelimit_redis.py` for why that beats failing open or closed.
        """
        if _shared_backend is not None:
            shared = _shared_backend.check(
                self.name, key, self.capacity, self.refill_per_second, cost
            )
            if shared is not None:
                return shared

        now = time.monotonic()

        with self._lock:
            self._maybe_sweep(now)

            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(tokens=float(self.capacity), last_refill=now)
                self._buckets[key] = bucket

            elapsed = now - bucket.last_refill
            bucket.tokens = min(self.capacity, bucket.tokens + elapsed * self.refill_per_second)
            bucket.last_refill = now

            if bucket.tokens >= cost:
                bucket.tokens -= cost
                return True, 0.0

            deficit = cost - bucket.tokens
            return False, deficit / self.refill_per_second if self.refill_per_second else 60.0

    def _maybe_sweep(self, now: float) -> None:
        if now - self._last_sweep < self.SWEEP_INTERVAL:
            return
        self._last_sweep = now
        # A bucket that has had time to refill completely carries no state worth
        # keeping, so it can be dropped safely.
        idle_after = self.capacity / self.refill_per_second if self.refill_per_second else 3600
        stale = [k for k, b in self._buckets.items() if now - b.last_refill > idle_after]
        for key in stale:
            del self._buckets[key]

    def reset(self, key: str | None = None) -> None:
        if _shared_backend is not None:
            _shared_backend.reset(self.name, key)
        with self._lock:
            if key is None:
                self._buckets.clear()
            else:
                self._buckets.pop(key, None)

    def enforce(self, request: Request, key: str | None = None, cost: float = 1.0) -> None:
        """Raise HTTP 429 when the caller is over its limit."""
        identity = key or client_key(request)
        allowed, retry_after = self.check(identity, cost)
        if allowed:
            return

        logger.info("rate limit '%s' hit by %s", self.name, identity)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Too many requests. Try again in {retry_after:.0f} seconds."
            ),
            headers={"Retry-After": str(max(1, int(retry_after)))},
        )


def client_key(request: Request) -> str:
    """Identify the caller for rate-limiting purposes.

    `X-Forwarded-For` is only trusted when the deployment declares it is behind
    a proxy. Trusting it unconditionally would let any client spoof a fresh
    identity per request and bypass the limit entirely.
    """
    from app.config import settings

    if settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()

    return request.client.host if request.client else "unknown"


# Login and registration are the endpoints worth guessing against, so they get
# the tightest budget: 10 attempts, refilling at one every 30 seconds.
login_limiter = RateLimiter(capacity=10, refill_per_second=1 / 30, name="login")

# A 6-digit TOTP code is a 1-in-a-million guess per attempt, which sounds safe
# until an attacker gets to make a million attempts. Same budget as login.
mfa_limiter = RateLimiter(capacity=10, refill_per_second=1 / 30, name="mfa")

# Webhooks arrive from TradingView at alert frequency, not user frequency.
webhook_limiter = RateLimiter(capacity=60, refill_per_second=1.0, name="webhook")

# Analysis and backtests are CPU-bound; this stops one client saturating a core.
compute_limiter = RateLimiter(capacity=30, refill_per_second=0.5, name="compute")

# Password reset is keyed by IP rather than account (the request itself doesn't
# prove account ownership) and touches the mail-sending path, so it gets a
# tighter budget than login's guess-resistance already provides elsewhere.
password_reset_limiter = RateLimiter(capacity=6, refill_per_second=1 / 60, name="password_reset")

# Resending a verification email is authenticated (unlike password reset), so
# the risk is a signed-in user spamming their own inbox by mashing "resend"
# rather than an enumeration attack — a looser budget than password reset is
# fine, but it still shouldn't be unlimited.
email_verify_limiter = RateLimiter(capacity=5, refill_per_second=1 / 60, name="email_verify")
