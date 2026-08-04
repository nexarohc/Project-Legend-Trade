"""Smoke tests for the API surface itself, as distinct from any one router.

The per-feature routers each have their own file; what this one asserts is that
the app boots, that liveness does not depend on a database or a provider, and
that the router set is exactly the trading platform's — no assistant endpoints
survived the extraction from the original repo.
"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(session):  # noqa: ARG001  (session fixture creates the schema first)
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_no_assistant_endpoints_survive():
    """The assistant's routes must be gone, not merely unlinked from the UI.

    A leftover /chat or /permissions would still be reachable by anyone who knows
    the path even with no button pointing at it, so this asserts against the
    app's own route table rather than against the frontend.
    """
    from app.main import app

    paths = {getattr(route, "path", "") for route in app.routes}
    for gone in ("/chat", "/permissions", "/voice", "/auth/google"):
        assert not any(path.startswith(gone) for path in paths), f"{gone} is still routed"
