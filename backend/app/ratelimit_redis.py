"""Redis-backed token buckets, so rate limits hold across workers.

The in-process limiter in `ratelimit.py` keeps one bucket per process, so N
workers means N times the intended limit. This module moves the bucket into
Redis where every worker shares it. It is **opt-in** via `REDIS_URL` — left
unset, nothing here runs and the in-process limiter behaves exactly as
before, which is the right default for the single-instance deployment this
platform targets.

Two decisions worth not re-litigating:

1. **The refill arithmetic runs inside Redis as a Lua script, not as
   read-modify-write from Python.** Two workers checking the same bucket at
   the same moment would otherwise both read the same token count and both
   spend it — the exact race this module exists to close. `EVAL` runs
   atomically on the server, so the read, the refill and the decrement are
   one indivisible step. The script also takes its clock from Redis's own
   `TIME` rather than each caller's, so workers with slightly skewed clocks
   still agree on how much a bucket has refilled.

2. **A Redis outage degrades to the in-process limiter, rather than failing
   open or closed.** Failing closed would turn a cache outage into a total
   outage — every request 429s, a self-inflicted denial of service far worse
   than the problem. Failing fully open would drop rate limiting entirely at
   exactly the moment infrastructure is already unhappy. Falling back to
   in-process is strictly better than both: limits still apply per worker,
   which is precisely the protection this deployment would have had without
   Redis configured at all. The degradation is logged once on the way down
   and once on the way back up, not per request, so an outage doesn't also
   flood the logs.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("legend.ratelimit.redis")

# KEYS[1] = bucket key
# ARGV    = capacity, refill_per_second, cost
#
# Returns {allowed, retry_after_as_string}. retry_after comes back as a
# string because Redis converts Lua numbers to integers on the way out,
# which would silently truncate a sub-second wait to zero.
_BUCKET_SCRIPT = """
local key      = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill   = tonumber(ARGV[2])
local cost     = tonumber(ARGV[3])

local t   = redis.call('TIME')
local now = tonumber(t[1]) + (tonumber(t[2]) / 1000000)

local data   = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(data[1])
local ts     = tonumber(data[2])

if tokens == nil or ts == nil then
  tokens = capacity
  ts = now
end

local elapsed = now - ts
if elapsed < 0 then
  elapsed = 0
end
tokens = math.min(capacity, tokens + (elapsed * refill))

local allowed = 0
local retry_after = 0

if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
elseif refill > 0 then
  retry_after = (cost - tokens) / refill
else
  retry_after = 60
end

redis.call('HSET', key, 'tokens', tokens, 'ts', now)

-- A bucket that has had time to refill completely carries no state worth
-- keeping; expiring it is the Redis equivalent of the in-process sweep.
local ttl = 3600
if refill > 0 then
  ttl = math.ceil(capacity / refill) + 60
end
redis.call('EXPIRE', key, ttl)

return {allowed, tostring(retry_after)}
"""


class RedisBackend:
    """Shared token buckets in Redis. One instance is shared by every limiter."""

    def __init__(self, client, namespace: str = "dex:rl"):
        self._client = client
        self._namespace = namespace
        self._script = client.register_script(_BUCKET_SCRIPT)
        self._degraded = False

    def _key(self, limiter: str, key: str) -> str:
        return f"{self._namespace}:{limiter}:{key}"

    def _note_failure(self, exc: Exception) -> None:
        if not self._degraded:
            self._degraded = True
            logger.warning(
                "Redis rate limiting unavailable (%s); falling back to in-process buckets. "
                "Limits now apply per worker rather than across the fleet.", exc,
            )

    def _note_recovery(self) -> None:
        if self._degraded:
            self._degraded = False
            logger.info("Redis rate limiting recovered; buckets are shared across workers again.")

    def check(self, limiter: str, key: str, capacity: int, refill_per_second: float,
              cost: float) -> tuple[bool, float] | None:
        """Consume `cost` tokens from the shared bucket.

        Returns (allowed, retry_after), or None when Redis could not be
        reached — the caller then falls back to its in-process bucket.
        """
        try:
            allowed, retry_after = self._script(
                keys=[self._key(limiter, key)],
                args=[capacity, refill_per_second, cost],
            )
        except Exception as exc:  # noqa: BLE001 - any Redis failure degrades the same way
            self._note_failure(exc)
            return None

        self._note_recovery()
        return bool(allowed), float(retry_after)

    def reset(self, limiter: str, key: str | None = None) -> None:
        try:
            if key is not None:
                self._client.delete(self._key(limiter, key))
                return
            pattern = f"{self._namespace}:{limiter}:*"
            for found in self._client.scan_iter(match=pattern, count=500):
                self._client.delete(found)
        except Exception as exc:  # noqa: BLE001
            self._note_failure(exc)


def connect(url: str) -> RedisBackend | None:
    """Build a backend from a `REDIS_URL`, or return None if unreachable.

    Called once at startup. A bad URL or an unreachable server is logged and
    returns None rather than raising — a broken cache should not stop the
    server from booting, it should leave rate limiting in-process.
    """
    try:
        import redis  # imported lazily so the dependency stays optional
    except ImportError:
        logger.warning(
            "REDIS_URL is set but the 'redis' package is not installed; "
            "rate limiting stays in-process. Install it with: pip install redis"
        )
        return None

    try:
        client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=2,
                                      socket_connect_timeout=2)
        client.ping()
        backend = RedisBackend(client)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Could not reach Redis at %s (%s); rate limiting stays in-process.", url, exc,
        )
        return None

    logger.info("rate limiting is Redis-backed (%s) — buckets are shared across workers", url)
    return backend
