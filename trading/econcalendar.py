"""Economic calendar: pure event modelling plus a caching service over FRED.

Two things worth knowing before touching this file:

1. **Importance is a heuristic, not something FRED provides.** FRED's release
   catalogue has no impact tier — `classify_importance` matches on well-known
   release-name keywords (employment, GDP, CPI, PCE, ...) and defaults
   everything else to `"low"`. It is hand-maintained, not exhaustive, and
   should be read as "probably market-moving," not authoritative. **Do not
   present this as an official importance rating** — say "estimated" wherever
   it's shown.
2. **Dates are day-level, not time-of-day.** FRED's `releases/dates` endpoint
   gives a calendar date, not a release hour (most US macro data prints at
   8:30am ET, but that convention lives in institutional knowledge, not in
   this API response). A "within 48 hours" check here really means "within
   the next two calendar days" — close enough to flag the risk, not precise
   enough to time an entry around.

FOMC rate decisions are **not** included — they are not part of FRED's
`releases` catalogue, and fabricating dates for them would be exactly the
kind of invented data this platform refuses to produce elsewhere.
"""
from __future__ import annotations

import datetime as dt
import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger("legend.trading.econcalendar")

# Keyword -> importance. Checked in order, case-insensitively, against the
# release name; the first match wins. Add to this list as gaps are found —
# it will never be complete, and that's stated in the module docstring.
_IMPORTANCE_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("employment situation", "high"),
    ("nonfarm", "high"),
    ("gross domestic product", "high"),
    ("consumer price index", "high"),
    ("personal income", "high"),  # carries the PCE price index, the Fed's preferred gauge
    ("producer price index", "medium"),
    ("retail sales", "medium"),
    ("industrial production", "medium"),
    ("housing starts", "medium"),
    ("new residential construction", "medium"),
    ("consumer sentiment", "medium"),
    ("ism", "medium"),
)


def classify_importance(release_name: str) -> str:
    lowered = release_name.lower()
    for keyword, level in _IMPORTANCE_KEYWORDS:
        if keyword in lowered:
            return level
    return "low"


@dataclass
class EconomicEvent:
    release_id: str
    name: str
    date: str            # YYYY-MM-DD, day-level per FRED (see module docstring)
    importance: str       # "high" | "medium" | "low" — estimated, see classify_importance

    def to_dict(self) -> dict:
        return {
            "release_id": self.release_id,
            "name": self.name,
            "date": self.date,
            "importance": self.importance,
        }


def build_events(raw_release_dates: list[dict]) -> list[EconomicEvent]:
    """Turn FRED's raw `release_dates` rows into sorted, deduplicated events.

    Pure transform — no network, no clock reads — so it's trivially testable
    against a fixture and reusable regardless of how the raw rows were cached.
    """
    seen: set[tuple[str, str]] = set()
    events: list[EconomicEvent] = []
    for row in raw_release_dates:
        release_id = str(row.get("release_id", ""))
        name = str(row.get("release_name", "")).strip()
        date = str(row.get("date", ""))
        if not (release_id and name and date):
            continue
        key = (release_id, date)
        if key in seen:
            continue
        seen.add(key)
        events.append(EconomicEvent(
            release_id=release_id, name=name, date=date,
            importance=classify_importance(name),
        ))
    events.sort(key=lambda e: (e.date, e.name))
    return events


def events_between(events: list[EconomicEvent], start: str, end: str) -> list[EconomicEvent]:
    """`start`/`end` are inclusive YYYY-MM-DD strings — plain string comparison
    is correct here since ISO dates sort lexicographically."""
    return [e for e in events if start <= e.date <= end]


def high_impact_within(events: list[EconomicEvent], from_date: str, days: int) -> list[EconomicEvent]:
    """High-importance events landing in the `days` calendar days from `from_date`."""
    horizon = (dt.date.fromisoformat(from_date) + dt.timedelta(days=days)).isoformat()
    return [
        e for e in events_between(events, from_date, horizon)
        if e.importance == "high"
    ]


@dataclass
class _CacheEntry:
    events: list[EconomicEvent]
    fetched_at: float


class EconomicCalendarService:
    """Caching facade over `FredClient`, mirroring `MarketService`'s pattern:
    fetch once, serve repeated requests from memory until the TTL lapses.
    Release schedules change rarely, so a long TTL is correct here, not lazy.
    """

    def __init__(self, client=None, ttl_seconds: int = 6 * 3600):
        self._client = client
        self._ttl = ttl_seconds
        self._cache: _CacheEntry | None = None

    def _client_or_raise(self):
        if self._client is None:
            from trading.providers.fred import FredClient

            import os
            self._client = FredClient(api_key=os.environ.get("FRED_API_KEY", ""))
        return self._client

    def _refresh(self, start: str, end: str) -> list[EconomicEvent]:
        client = self._client_or_raise()
        raw = client.release_dates(start, end)
        return build_events(raw)

    def events(self, start: str, end: str, force: bool = False) -> list[EconomicEvent]:
        now = time.time()
        if (
            force
            or self._cache is None
            or now - self._cache.fetched_at > self._ttl
        ):
            fetched = self._refresh(start, end)
            self._cache = _CacheEntry(events=fetched, fetched_at=now)
            return fetched
        return events_between(self._cache.events, start, end)

    def upcoming(self, days: int = 14) -> list[EconomicEvent]:
        today = dt.date.today()
        start = today.isoformat()
        end = (today + dt.timedelta(days=days)).isoformat()
        return self.events(start, end)

    def invalidate(self) -> None:
        self._cache = None


# One shared instance, same reasoning as `market.market_service`: repeated
# calendar checks across requests should not re-hit FRED every time.
calendar_service = EconomicCalendarService()
