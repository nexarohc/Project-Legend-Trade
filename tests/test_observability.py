"""Structured logging and the /metrics endpoint.

The properties worth pinning here are the ones that are quietly expensive to
get wrong: histogram buckets that don't add up make every quantile computed
from them wrong, and route labels taken from the raw path let anyone grow
cardinality without bound by requesting random URLs.
"""
import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.observability import (
    LATENCY_BUCKETS,
    JsonFormatter,
    Metrics,
    configure_logging,
)
from tests.test_trading_api import StubProvider


# --- JSON log formatting -------------------------------------------------------

def _record(**kwargs) -> logging.LogRecord:
    record = logging.LogRecord(
        name="dex.test", level=logging.INFO, pathname="x.py", lineno=1,
        msg=kwargs.pop("msg", "hello %s"), args=kwargs.pop("args", ("world",)),
        exc_info=None,
    )
    for key, value in kwargs.items():
        setattr(record, key, value)
    return record


def test_json_formatter_emits_one_parseable_object():
    payload = json.loads(JsonFormatter().format(_record()))
    assert payload["level"] == "INFO"
    assert payload["logger"] == "dex.test"
    assert payload["message"] == "hello world"
    assert payload["ts"].endswith("Z")


def test_json_formatter_promotes_extras_to_fields():
    """A value passed via extra={...} should be a real field, not smuggled
    into the message string where nothing can query it."""
    payload = json.loads(JsonFormatter().format(_record(symbol="BTCUSDT", latency_ms=12)))
    assert payload["symbol"] == "BTCUSDT"
    assert payload["latency_ms"] == 12


def test_json_formatter_survives_an_unserialisable_extra():
    payload = json.loads(JsonFormatter().format(_record(thing=object())))
    assert "thing" in payload  # repr'd rather than blowing up the log line


def test_json_formatter_includes_the_traceback_on_an_exception():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys
        record = _record()
        record.exc_info = sys.exc_info()
        payload = json.loads(JsonFormatter().format(record))
    assert "ValueError: boom" in payload["exception"]


def test_configure_logging_replaces_handlers_rather_than_stacking():
    root = logging.getLogger()
    try:
        configure_logging("json")
        first = len(root.handlers)
        configure_logging("json")
        assert len(root.handlers) == first, "re-configuring must not double every log line"
    finally:
        configure_logging("text")


# --- metrics ------------------------------------------------------------------

def test_counts_requests_by_method_route_and_status():
    m = Metrics()
    m.record_request("GET", "/market/candles", 200, 0.01)
    m.record_request("GET", "/market/candles", 200, 0.02)
    m.record_request("GET", "/market/candles", 502, 0.03)

    snapshot = m.snapshot()
    assert snapshot["requests"]["GET /market/candles 200"] == 2
    assert snapshot["requests"]["GET /market/candles 502"] == 1
    assert snapshot["request_total"] == 3


def test_histogram_buckets_are_cumulative_and_end_at_the_observation_count():
    """A Prometheus histogram is cumulative: each bucket counts everything at
    or below its edge, and +Inf must equal the total. Getting this wrong makes
    every quantile derived from it wrong."""
    m = Metrics()
    for seconds in (0.001, 0.03, 0.3, 7.0):
        m.record_request("GET", "/x", 200, seconds)

    text = m.render_prometheus()
    buckets = {}
    for line in text.splitlines():
        if line.startswith("dex_http_request_duration_seconds_bucket"):
            edge = line.split('le="')[1].split('"')[0]
            buckets[edge] = int(line.rsplit(" ", 1)[1])

    ordered = [buckets[str(edge)] for edge in LATENCY_BUCKETS]
    assert ordered == sorted(ordered), "buckets must be non-decreasing"
    assert buckets["+Inf"] == 4, "+Inf must equal the number of observations"
    assert buckets["0.005"] == 1  # only the 1 ms request
    assert buckets["0.05"] == 2   # 1 ms and 30 ms


def test_histogram_sum_and_count_match_what_was_recorded():
    m = Metrics()
    m.record_request("GET", "/y", 200, 0.5)
    m.record_request("GET", "/y", 200, 1.5)

    text = m.render_prometheus()
    total = next(l for l in text.splitlines() if "duration_seconds_sum" in l)
    count = next(l for l in text.splitlines() if "duration_seconds_count" in l)
    assert abs(float(total.rsplit(" ", 1)[1]) - 2.0) < 1e-6
    assert int(count.rsplit(" ", 1)[1]) == 2


def test_rate_limited_requests_are_counted_separately():
    m = Metrics()
    m.record_rate_limited("/auth/login")
    m.record_rate_limited("/auth/login")
    assert m.snapshot()["rate_limited"]["/auth/login"] == 2
    assert 'dex_rate_limited_total{limiter="/auth/login"} 2' in m.render_prometheus()


def test_label_values_are_escaped():
    m = Metrics()
    m.record_request('GE"T', "/a\\b", 200, 0.01)
    text = m.render_prometheus()
    assert '\\"' in text and "\\\\" in text


def test_uptime_is_reported():
    assert Metrics().snapshot()["uptime_seconds"] >= 0
    assert "dex_uptime_seconds" in Metrics().render_prometheus()


# --- the endpoint, through the real app ----------------------------------------

@pytest.fixture()
def client(monkeypatch, session):
    stub = StubProvider()
    import trading.market as market_module
    import trading.providers as providers_module

    monkeypatch.setattr(providers_module, "get_provider", lambda name=None: stub)
    monkeypatch.setattr(providers_module, "resolve_provider_for", lambda s, explicit=None: stub)
    monkeypatch.setattr(market_module, "get_provider", lambda name=None: stub)
    monkeypatch.setattr(market_module, "resolve_provider_for", lambda s, explicit=None: stub)
    market_module.market_service.invalidate()

    from app.observability import metrics

    metrics.reset()

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
    market_module.market_service.invalidate()


def test_metrics_endpoint_serves_prometheus_text(client):
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert "# TYPE dex_http_requests_total counter" in response.text


def test_requests_are_recorded_by_route_template_not_raw_path(client):
    """The cardinality guarantee: a path with an id in it must collapse to one
    series, or metric cardinality grows without bound with traffic."""
    client.get("/health")
    client.get("/health")

    text = client.get("/metrics").text
    assert 'route="/health"' in text


def test_unmatched_paths_collapse_into_a_single_bucket(client):
    """Otherwise anyone could inflate cardinality by requesting random URLs."""
    for i in range(5):
        client.get(f"/definitely-not-a-route-{i}")

    text = client.get("/metrics").text
    assert 'route="<unmatched>"' in text
    for i in range(5):
        assert f"definitely-not-a-route-{i}" not in text


def test_metrics_can_be_disabled(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "metrics_enabled", False)
    assert client.get("/metrics").status_code == 404
