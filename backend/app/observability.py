"""Structured logging and metrics.

Two things a published instance needs that a desktop one does not: logs a
machine can parse, and numbers you can alert on. Both are **opt-in and
dependency-free**, in keeping with the rest of this codebase — the Prometheus
exposition format is a few lines of text, so emitting it by hand costs less
than taking on `prometheus-client`.

**Metric labels use the route *template*, never the raw path.** Labelling by
raw path is the classic way to melt a metrics backend: `/execution/orders/1`,
`/execution/orders/2` and so on become distinct time series, so cardinality
grows without bound with traffic. `/execution/orders/{order_id}` stays one
series no matter how many orders exist. `_route_template` resolves the
matched route and falls back to `"<unmatched>"` — a single bucket — rather
than leaking the raw path for 404s, which an attacker could otherwise use to
inflate cardinality deliberately by requesting random URLs.

Latency is a histogram with fixed buckets rather than an average, because an
average hides exactly the thing you need to see: an endpoint at 30 ms mean
with a 4 s p99 looks healthy and is not.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import defaultdict

logger = logging.getLogger("legend.observability")

# Seconds. Chosen to straddle what this app actually does: sub-10 ms cache
# hits, ~50 ms analysis runs, and the multi-second provider calls that are
# the interesting tail.
LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

# Standard logging.LogRecord attributes, so the JSON formatter can tell them
# apart from extras a caller attached via logger.info(..., extra={...}).
_STANDARD_RECORD_FIELDS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
) | {"asctime", "message", "taskName"}


class JsonFormatter(logging.Formatter):
    """One JSON object per line, so a log shipper can parse it without regexes."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
                  + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        # Anything passed via extra={...} rides along as a real field rather
        # than being interpolated into the message string.
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_FIELDS and not key.startswith("_"):
                try:
                    json.dumps(value)
                    payload[key] = value
                except (TypeError, ValueError):
                    payload[key] = repr(value)

        return json.dumps(payload, default=str)


# Uvicorn attaches its own handlers to these and sets propagate=False, so a
# root-logger config alone leaves its startup and access lines as plain text —
# which would make `LOG_FORMAT=json` only half true, and a log shipper would
# choke on the unparseable minority.
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


def configure_logging(fmt: str, level: str = "INFO") -> None:
    """Install either the human format or the JSON one, including on uvicorn."""
    handler = logging.StreamHandler()
    if fmt.lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )

    root = logging.getLogger()
    # Replace rather than add, so re-configuring cannot double every line.
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Hand uvicorn's loggers back to the root handler above.
    for name in _UVICORN_LOGGERS:
        uvicorn_logger = logging.getLogger(name)
        for existing in list(uvicorn_logger.handlers):
            uvicorn_logger.removeHandler(existing)
        uvicorn_logger.propagate = True


class Metrics:
    """A tiny counter/histogram registry rendered in Prometheus text format.

    Deliberately not a general-purpose metrics library: it holds exactly what
    this app reports, guarded by one lock, with no background threads.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._requests: dict[tuple[str, str, int], int] = defaultdict(int)
        self._latency_buckets: dict[tuple[str, str], list[int]] = {}
        self._latency_sum: dict[tuple[str, str], float] = defaultdict(float)
        self._latency_count: dict[tuple[str, str], int] = defaultdict(int)
        self._rate_limited: dict[str, int] = defaultdict(int)
        self._started_at = time.time()

    def record_request(self, method: str, route: str, status_code: int, seconds: float) -> None:
        key = (method, route)
        with self._lock:
            self._requests[(method, route, status_code)] += 1

            buckets = self._latency_buckets.get(key)
            if buckets is None:
                buckets = [0] * len(LATENCY_BUCKETS)
                self._latency_buckets[key] = buckets
            for i, edge in enumerate(LATENCY_BUCKETS):
                if seconds <= edge:
                    buckets[i] += 1
            self._latency_sum[key] += seconds
            self._latency_count[key] += 1

    def record_rate_limited(self, limiter: str) -> None:
        with self._lock:
            self._rate_limited[limiter] += 1

    def snapshot(self) -> dict:
        """The same numbers as a plain dict, for tests and JSON consumers."""
        with self._lock:
            return {
                "uptime_seconds": round(time.time() - self._started_at, 3),
                "requests": {
                    f"{method} {route} {status}": count
                    for (method, route, status), count in self._requests.items()
                },
                "request_total": sum(self._requests.values()),
                "rate_limited": dict(self._rate_limited),
                "latency_count": {
                    f"{method} {route}": count
                    for (method, route), count in self._latency_count.items()
                },
            }

    def render_prometheus(self) -> str:
        lines: list[str] = []
        with self._lock:
            lines.append("# HELP legend_uptime_seconds Seconds since this process started.")
            lines.append("# TYPE legend_uptime_seconds gauge")
            lines.append(f"legend_uptime_seconds {time.time() - self._started_at:.3f}")

            lines.append("# HELP legend_http_requests_total HTTP requests by method, route and status.")
            lines.append("# TYPE legend_http_requests_total counter")
            for (method, route, status), count in sorted(self._requests.items()):
                lines.append(
                    f'legend_http_requests_total{{method="{_escape(method)}",'
                    f'route="{_escape(route)}",status="{status}"}} {count}'
                )

            lines.append("# HELP legend_http_request_duration_seconds Request latency.")
            lines.append("# TYPE legend_http_request_duration_seconds histogram")
            for (method, route), buckets in sorted(self._latency_buckets.items()):
                labels = f'method="{_escape(method)}",route="{_escape(route)}"'
                for edge, count in zip(LATENCY_BUCKETS, buckets):
                    lines.append(
                        f'legend_http_request_duration_seconds_bucket{{{labels},le="{edge}"}} {count}'
                    )
                total = self._latency_count[(method, route)]
                # +Inf must equal the observation count, or the histogram is
                # malformed and every quantile computed from it is wrong.
                lines.append(
                    f'legend_http_request_duration_seconds_bucket{{{labels},le="+Inf"}} {total}'
                )
                lines.append(
                    f'legend_http_request_duration_seconds_sum{{{labels}}} '
                    f'{self._latency_sum[(method, route)]:.6f}'
                )
                lines.append(f'legend_http_request_duration_seconds_count{{{labels}}} {total}')

            lines.append("# HELP legend_rate_limited_total Requests refused by a rate limiter.")
            lines.append("# TYPE legend_rate_limited_total counter")
            for limiter, count in sorted(self._rate_limited.items()):
                lines.append(f'legend_rate_limited_total{{limiter="{_escape(limiter)}"}} {count}')

        return "\n".join(lines) + "\n"

    def reset(self) -> None:
        """Test hook — never called by the app."""
        with self._lock:
            self._requests.clear()
            self._latency_buckets.clear()
            self._latency_sum.clear()
            self._latency_count.clear()
            self._rate_limited.clear()


def _escape(value: str) -> str:
    """Prometheus label values escape backslash, quote and newline."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def route_template(request) -> str:
    """The matched route's path template, or a single bucket for no match.

    Returning the raw path for unmatched requests would let anyone inflate
    metric cardinality without bound by requesting random URLs.
    """
    route = request.scope.get("route")
    path_format = getattr(route, "path_format", None) or getattr(route, "path", None)
    if path_format:
        return path_format
    return "<unmatched>"


metrics = Metrics()
