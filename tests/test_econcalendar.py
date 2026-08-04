"""Pure calendar logic (no network) plus the caching service, stubbed against
a fake FRED client so no test ever reaches the real API.
"""
from trading.econcalendar import (
    EconomicCalendarService,
    build_events,
    classify_importance,
    events_between,
    high_impact_within,
)


def test_classify_importance_matches_known_high_impact_releases():
    assert classify_importance("Employment Situation") == "high"
    assert classify_importance("Gross Domestic Product") == "high"
    assert classify_importance("Consumer Price Index") == "high"
    assert classify_importance("Personal Income and Outlays") == "high"


def test_classify_importance_is_case_insensitive():
    assert classify_importance("employment situation") == "high"


def test_classify_importance_defaults_to_low_for_unknown_releases():
    assert classify_importance("Some Regional Fed Survey Nobody Tracks") == "low"


def test_build_events_parses_and_sorts():
    raw = [
        {"release_id": "10", "release_name": "Employment Situation", "date": "2026-08-07"},
        {"release_id": "11", "release_name": "Gross Domestic Product", "date": "2026-07-30"},
    ]
    events = build_events(raw)
    assert [e.date for e in events] == ["2026-07-30", "2026-08-07"]
    assert events[0].importance == "high"


def test_build_events_deduplicates_identical_release_and_date():
    raw = [
        {"release_id": "10", "release_name": "Employment Situation", "date": "2026-08-07"},
        {"release_id": "10", "release_name": "Employment Situation", "date": "2026-08-07"},
    ]
    assert len(build_events(raw)) == 1


def test_build_events_skips_incomplete_rows():
    raw = [{"release_id": "10", "release_name": "", "date": "2026-08-07"}]
    assert build_events(raw) == []


def test_events_between_is_inclusive():
    raw = [
        {"release_id": "1", "release_name": "A", "date": "2026-08-01"},
        {"release_id": "2", "release_name": "B", "date": "2026-08-05"},
        {"release_id": "3", "release_name": "C", "date": "2026-08-10"},
    ]
    events = build_events(raw)
    filtered = events_between(events, "2026-08-01", "2026-08-05")
    assert {e.release_id for e in filtered} == {"1", "2"}


def test_high_impact_within_filters_by_importance_and_window():
    raw = [
        {"release_id": "1", "release_name": "Employment Situation", "date": "2026-08-02"},
        {"release_id": "2", "release_name": "Some Regional Survey", "date": "2026-08-02"},
        {"release_id": "3", "release_name": "Gross Domestic Product", "date": "2026-09-01"},
    ]
    events = build_events(raw)
    found = high_impact_within(events, "2026-08-01", days=3)
    assert [e.release_id for e in found] == ["1"]


class FakeFredClient:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def release_dates(self, start, end):
        self.calls.append((start, end))
        return self.rows


def test_calendar_service_caches_between_calls_within_ttl():
    client = FakeFredClient([
        {"release_id": "1", "release_name": "Employment Situation", "date": "2026-08-02"},
    ])
    service = EconomicCalendarService(client=client, ttl_seconds=3600)

    first = service.events("2026-08-01", "2026-08-31")
    second = service.events("2026-08-01", "2026-08-05")

    assert len(client.calls) == 1  # second call served from cache
    assert len(first) == 1
    assert len(second) == 1  # narrower window, filtered from the cached set


def test_calendar_service_refetches_after_invalidate():
    client = FakeFredClient([
        {"release_id": "1", "release_name": "Employment Situation", "date": "2026-08-02"},
    ])
    service = EconomicCalendarService(client=client, ttl_seconds=3600)

    service.events("2026-08-01", "2026-08-31")
    service.invalidate()
    service.events("2026-08-01", "2026-08-31")

    assert len(client.calls) == 2


def test_calendar_service_refetches_after_ttl_expires():
    client = FakeFredClient([
        {"release_id": "1", "release_name": "Employment Situation", "date": "2026-08-02"},
    ])
    service = EconomicCalendarService(client=client, ttl_seconds=-1)  # already expired

    service.events("2026-08-01", "2026-08-31")
    service.events("2026-08-01", "2026-08-31")

    assert len(client.calls) == 2


def test_calendar_service_upcoming_uses_todays_date():
    import datetime as dt

    client = FakeFredClient([])
    service = EconomicCalendarService(client=client, ttl_seconds=3600)
    service.upcoming(days=7)

    start, end = client.calls[0]
    today = dt.date.today()
    assert start == today.isoformat()
    assert end == (today + dt.timedelta(days=7)).isoformat()
