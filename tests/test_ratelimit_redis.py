"""Redis-backed rate limiting.

These tests run against a **real Redis server** when one is reachable at
`LEGEND_TEST_REDIS_URL` (default `redis://localhost:6379/15` — database 15, kept
away from anything real), and skip otherwise so the suite still passes with
no network access, which is the rule for the rest of this suite.

The interesting property being proven is the one the whole module exists for:
two independent `RateLimiter` objects — standing in for two worker processes
— must share a single budget, not get one each. The fallback path (Redis
unreachable mid-flight) is tested separately with a deliberately broken
client, since that failure cannot be induced against a healthy server.
"""
import os

import pytest

from app.ratelimit import RateLimiter, use_redis

REDIS_URL = os.environ.get("LEGEND_TEST_REDIS_URL", "redis://localhost:6379/15")


def _backend_or_skip():
    from app import ratelimit_redis

    backend = ratelimit_redis.connect(REDIS_URL)
    if backend is None:
        pytest.skip(f"no Redis reachable at {REDIS_URL}")
    return backend


@pytest.fixture()
def redis_backend():
    """Point every limiter at a real Redis, and put it back afterwards."""
    backend = _backend_or_skip()
    use_redis(backend)
    yield backend
    use_redis(None)


def test_allows_up_to_capacity_then_refuses(redis_backend):
    limiter = RateLimiter(capacity=3, refill_per_second=0.0001, name="test_capacity")
    limiter.reset()

    assert limiter.check("client-a")[0] is True
    assert limiter.check("client-a")[0] is True
    assert limiter.check("client-a")[0] is True

    allowed, retry_after = limiter.check("client-a")
    assert allowed is False
    assert retry_after > 0


def test_two_limiters_share_one_budget(redis_backend):
    """The whole point: two workers must not each get the full capacity."""
    worker_one = RateLimiter(capacity=3, refill_per_second=0.0001, name="test_shared")
    worker_two = RateLimiter(capacity=3, refill_per_second=0.0001, name="test_shared")
    worker_one.reset()

    assert worker_one.check("same-client")[0] is True
    assert worker_two.check("same-client")[0] is True
    assert worker_one.check("same-client")[0] is True

    # Three spent between them — the fourth is refused regardless of which
    # "worker" asks. In-process buckets would have allowed six.
    assert worker_two.check("same-client")[0] is False
    assert worker_one.check("same-client")[0] is False


def test_different_clients_have_separate_buckets(redis_backend):
    limiter = RateLimiter(capacity=2, refill_per_second=0.0001, name="test_isolation")
    limiter.reset()

    assert limiter.check("client-x")[0] is True
    assert limiter.check("client-x")[0] is True
    assert limiter.check("client-x")[0] is False

    # A different client is unaffected by the first one's exhaustion.
    assert limiter.check("client-y")[0] is True


def test_different_limiters_do_not_collide(redis_backend):
    """Two limiters with different names must not share a bucket even for
    the same client key — login and compute budgets are separate things."""
    login = RateLimiter(capacity=1, refill_per_second=0.0001, name="test_login_ns")
    compute = RateLimiter(capacity=1, refill_per_second=0.0001, name="test_compute_ns")
    login.reset()
    compute.reset()

    assert login.check("same-ip")[0] is True
    assert login.check("same-ip")[0] is False
    assert compute.check("same-ip")[0] is True  # its own budget


def test_refill_returns_tokens_over_time(redis_backend):
    import time

    # Refills fast enough to recover within the test's patience.
    limiter = RateLimiter(capacity=1, refill_per_second=20.0, name="test_refill")
    limiter.reset()

    assert limiter.check("refill-client")[0] is True
    assert limiter.check("refill-client")[0] is False

    time.sleep(0.2)  # 20/s * 0.2s = ~4 tokens, capped at capacity 1
    assert limiter.check("refill-client")[0] is True


def test_reset_clears_a_single_key(redis_backend):
    limiter = RateLimiter(capacity=1, refill_per_second=0.0001, name="test_reset_one")
    limiter.reset()

    assert limiter.check("client-1")[0] is True
    assert limiter.check("client-1")[0] is False

    limiter.reset("client-1")
    assert limiter.check("client-1")[0] is True


def test_reset_without_a_key_clears_every_client(redis_backend):
    limiter = RateLimiter(capacity=1, refill_per_second=0.0001, name="test_reset_all")
    limiter.reset()

    limiter.check("a")
    limiter.check("b")
    assert limiter.check("a")[0] is False
    assert limiter.check("b")[0] is False

    limiter.reset()
    assert limiter.check("a")[0] is True
    assert limiter.check("b")[0] is True


def test_retry_after_reflects_the_refill_rate(redis_backend):
    limiter = RateLimiter(capacity=1, refill_per_second=0.5, name="test_retry_after")
    limiter.reset()

    limiter.check("client-r")
    allowed, retry_after = limiter.check("client-r")
    assert allowed is False
    # One token at 0.5/s is ~2 seconds away; sub-second precision must survive
    # the round trip through Redis rather than truncating to an integer.
    assert 1.0 < retry_after <= 2.5


# --- degradation, without a healthy server ------------------------------------

class _BrokenClient:
    """Stands in for a Redis that has gone away mid-flight."""

    def register_script(self, _script):
        def _fail(*args, **kwargs):
            raise ConnectionError("redis is gone")
        return _fail

    def delete(self, *args, **kwargs):
        raise ConnectionError("redis is gone")

    def scan_iter(self, *args, **kwargs):
        raise ConnectionError("redis is gone")


def test_check_falls_back_to_in_process_when_redis_fails():
    """A Redis outage must not fail open (no limiting) or closed (total
    outage) — it degrades to the in-process bucket."""
    from app.ratelimit_redis import RedisBackend

    use_redis(RedisBackend(_BrokenClient()))
    try:
        limiter = RateLimiter(capacity=2, refill_per_second=0.0001, name="test_degraded")

        # Still limited — by the in-process bucket, not silently unlimited.
        assert limiter.check("client-d")[0] is True
        assert limiter.check("client-d")[0] is True
        assert limiter.check("client-d")[0] is False
    finally:
        use_redis(None)


def test_backend_reports_none_rather_than_raising_on_failure():
    from app.ratelimit_redis import RedisBackend

    backend = RedisBackend(_BrokenClient())
    assert backend.check("any", "key", 10, 1.0, 1.0) is None
    backend.reset("any", "key")  # must not raise either


def test_degradation_is_logged_once_not_per_request(caplog):
    from app.ratelimit_redis import RedisBackend

    backend = RedisBackend(_BrokenClient())
    with caplog.at_level("WARNING", logger="dex.ratelimit.redis"):
        for _ in range(5):
            backend.check("any", "key", 10, 1.0, 1.0)

    warnings = [r for r in caplog.records if "falling back to in-process" in r.message]
    assert len(warnings) == 1, "an outage should not flood the logs"


def test_connect_returns_none_for_an_unreachable_server():
    from app import ratelimit_redis

    # Port 1 is reserved and nothing listens there.
    assert ratelimit_redis.connect("redis://localhost:1/0") is None


def test_unset_backend_uses_in_process_buckets():
    """The default path, with no Redis configured at all."""
    use_redis(None)
    limiter = RateLimiter(capacity=1, refill_per_second=0.0001, name="test_no_redis")
    assert limiter.check("client-n")[0] is True
    assert limiter.check("client-n")[0] is False
