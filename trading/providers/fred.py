"""FRED (Federal Reserve Bank of St. Louis) — economic release calendar and
series data. Free registration at https://fred.stlouisfed.org/docs/api/api_key.html;
no cost, no card required.

This is not a `MarketDataProvider` — it has nothing to do with candles or
quotes. It answers a different question: *when* does a scheduled economic
release land, and what did a given series actually print. `trading/econcalendar.py`
turns the raw responses this module returns into the platform's own
`EconomicEvent` shape; this module only ever talks to FRED's REST API.
"""
from __future__ import annotations

import logging

import httpx

from trading.models import MarketDataError

logger = logging.getLogger("legend.trading.fred")

BASE_URL = "https://api.stlouisfed.org/fred"


class FredClient:
    name = "fred"

    def __init__(self, api_key: str = ""):
        self.api_key = api_key

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _get(self, path: str, params: dict) -> dict:
        if not self.api_key:
            raise MarketDataError("FRED_API_KEY is not set")
        query = dict(params)
        query["api_key"] = self.api_key
        query["file_type"] = "json"
        try:
            response = httpx.get(f"{BASE_URL}{path}", params=query, timeout=20)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            raise MarketDataError(f"FRED {path} unreachable: {exc}") from exc

    def releases(self) -> list[dict]:
        """Every economic release FRED tracks (name + release_id)."""
        data = self._get("/releases", {"limit": 1000})
        return data.get("releases", [])

    def release_dates(self, start: str, end: str) -> list[dict]:
        """Every scheduled release date — past and future — in [start, end]
        (YYYY-MM-DD). This *is* the calendar: each row is
        {release_id, release_name, date}.
        """
        data = self._get("/releases/dates", {
            "realtime_start": start,
            "realtime_end": end,
            "include_release_dates_with_no_data": "true",
            "sort_order": "asc",
            "limit": 1000,
        })
        return data.get("release_dates", [])

    def series_observations(self, series_id: str, limit: int = 10) -> list[dict]:
        """Most recent observations for a series (e.g. `CPIAUCSL`), newest first."""
        data = self._get("/series/observations", {
            "series_id": series_id,
            "sort_order": "desc",
            "limit": limit,
        })
        return data.get("observations", [])
