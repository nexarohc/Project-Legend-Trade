"""Calendar API endpoints. The FRED client is stubbed at the calendar-service
level, same isolation the scanner tests use for market data — no network.
"""
import pytest

from tests.test_trading_api import client  # noqa: F401 - shared fixture
from trading.econcalendar import EconomicEvent


@pytest.fixture()
def stub_calendar(monkeypatch):
    import app.routers.calendar as calendar_router

    events = [
        EconomicEvent(release_id="1", name="Employment Situation", date="2026-08-02", importance="high"),
        EconomicEvent(release_id="2", name="Some Regional Survey", date="2026-08-03", importance="low"),
    ]

    class StubService:
        def events(self, start, end):
            return [e for e in events if start <= e.date <= end]

        def upcoming(self, days=14):
            return events

    monkeypatch.setattr(calendar_router, "calendar_service", StubService())
    return events


def test_events_endpoint_returns_events_in_range(client, stub_calendar):
    payload = client.get("/calendar", params={"start": "2026-08-01", "end": "2026-08-31"}).json()
    assert payload["count"] == 2
    assert {e["release_id"] for e in payload["events"]} == {"1", "2"}


def test_events_endpoint_filters_by_importance(client, stub_calendar):
    payload = client.get(
        "/calendar", params={"start": "2026-08-01", "end": "2026-08-31", "importance": "high"},
    ).json()
    assert payload["count"] == 1
    assert payload["events"][0]["release_id"] == "1"


def test_events_endpoint_rejects_an_invalid_date(client):
    response = client.get("/calendar", params={"start": "not-a-date", "end": "2026-08-31"})
    assert response.status_code == 400


def test_events_endpoint_rejects_end_before_start(client):
    response = client.get("/calendar", params={"start": "2026-08-31", "end": "2026-08-01"})
    assert response.status_code == 400


def test_events_endpoint_rejects_an_invalid_importance(client):
    response = client.get(
        "/calendar", params={"start": "2026-08-01", "end": "2026-08-31", "importance": "critical"},
    )
    assert response.status_code == 422


def test_upcoming_endpoint_returns_events(client, stub_calendar):
    payload = client.get("/calendar/upcoming", params={"days": 7}).json()
    assert payload["count"] == 2


def test_upcoming_endpoint_rejects_an_out_of_range_days(client):
    response = client.get("/calendar/upcoming", params={"days": 999})
    assert response.status_code == 422
