"""Authentication, authorisation and per-user data isolation.

The isolation tests are the important ones. Auth that lets you log in but still
serves another account's positions is not auth, so every user-scoped resource is
checked from a second account: it must be invisible in lists and indistinguishable
from missing when addressed directly by id.
"""
import base64
import datetime as dt
import json
import time

import pytest
from fastapi.testclient import TestClient

from app.mfa import totp
from app.security import (
    TokenError,
    create_token,
    decode_token,
    hash_password,
    password_problems,
    resolve_secret_key,
    verify_password,
)
from tests.test_trading_api import StubProvider

SECRET = "test-signing-secret-not-a-real-one"
STRONG_PASSWORD = "correct horse battery staple"


# --- password hashing --------------------------------------------------------

def test_password_roundtrip():
    stored = hash_password(STRONG_PASSWORD)
    assert verify_password(STRONG_PASSWORD, stored)
    assert not verify_password("wrong password entirely", stored)


def test_hash_is_salted_so_equal_passwords_differ():
    """Two accounts with the same password must not share a hash."""
    assert hash_password(STRONG_PASSWORD) != hash_password(STRONG_PASSWORD)


def test_password_is_not_recoverable_from_the_hash():
    stored = hash_password(STRONG_PASSWORD)
    assert STRONG_PASSWORD not in stored
    assert "correct" not in stored.lower()


def test_hash_records_its_own_parameters():
    """Parameters travel with the hash so the cost can be raised later."""
    stored = hash_password(STRONG_PASSWORD)
    scheme, n, r, p, _salt, _digest = stored.split("$")
    assert scheme == "scrypt"
    assert int(n) >= 2 ** 14 and int(r) >= 8 and int(p) >= 1


def test_malformed_hash_fails_closed():
    """A corrupt hash must return False, never raise into the request path."""
    for bad in ("", "garbage", "scrypt$notanumber$8$1$aa$bb", "bcrypt$1$2$3$aa$bb", "$$$$$"):
        assert verify_password(STRONG_PASSWORD, bad) is False


def test_weak_passwords_are_rejected():
    assert password_problems("short")
    assert password_problems("password123")
    assert password_problems("aaaaaaaaaaaa")   # too few distinct characters
    assert password_problems(STRONG_PASSWORD) == []


# --- token signing -----------------------------------------------------------

def test_token_roundtrip():
    payload = decode_token(create_token(7, SECRET, "access"), SECRET, "access")
    assert payload.subject == 7
    assert payload.token_type == "access"


def test_token_signed_with_another_secret_is_rejected():
    token = create_token(7, SECRET)
    with pytest.raises(TokenError, match="signature"):
        decode_token(token, "a-completely-different-secret")


def test_tampering_with_the_payload_is_detected():
    """Re-encoding the payload to claim another user must fail the signature."""
    token = create_token(7, SECRET)
    header, payload_b64, signature = token.split(".")

    payload = json.loads(base64.urlsafe_b64decode(payload_b64 + "=="))
    payload["sub"] = "1"           # try to become user 1
    forged = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    ).decode().rstrip("=")

    with pytest.raises(TokenError):
        decode_token(f"{header}.{forged}.{signature}", SECRET)


def test_alg_none_token_is_rejected():
    """The classic JWT bypass: claim 'none' and send no signature."""
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "none", "typ": "JWT"}).encode()
    ).decode().rstrip("=")
    payload = base64.urlsafe_b64encode(
        json.dumps({
            "sub": "1", "type": "access",
            "iat": int(time.time()), "exp": int(time.time()) + 3600,
        }).encode()
    ).decode().rstrip("=")

    with pytest.raises(TokenError):
        decode_token(f"{header}.{payload}.", SECRET)


def test_expired_token_is_rejected():
    with pytest.raises(TokenError, match="expired"):
        decode_token(create_token(7, SECRET, "access", ttl=-120), SECRET)


def test_refresh_token_cannot_be_used_as_an_access_token():
    refresh = create_token(7, SECRET, "refresh")
    with pytest.raises(TokenError, match="Expected a access token"):
        decode_token(refresh, SECRET, expected_type="access")


@pytest.mark.parametrize("malformed", ["", "a.b", "not-a-token", "a.b.c.d", "..."])
def test_malformed_tokens_are_rejected(malformed):
    with pytest.raises(TokenError):
        decode_token(malformed, SECRET)


def test_secret_key_is_generated_and_persisted(tmp_path):
    """A generated secret must be stable across restarts, or everyone gets logged out."""
    first = resolve_secret_key("", tmp_path)
    second = resolve_secret_key("", tmp_path)
    assert first == second
    assert len(first) >= 32


def test_insecure_placeholder_secrets_are_replaced(tmp_path):
    generated = resolve_secret_key("change-me", tmp_path)
    assert generated != "change-me"


# --- authenticated API -------------------------------------------------------

@pytest.fixture()
def auth_client(monkeypatch, session):
    """A TestClient with authentication switched on and a stub data provider."""
    stub = StubProvider()
    import trading.market as market_module
    import trading.providers as providers_module

    monkeypatch.setattr(providers_module, "get_provider", lambda name=None: stub)
    monkeypatch.setattr(providers_module, "resolve_provider_for", lambda s, explicit=None: stub)
    monkeypatch.setattr(market_module, "get_provider", lambda name=None: stub)
    monkeypatch.setattr(market_module, "resolve_provider_for", lambda s, explicit=None: stub)
    market_module.market_service.invalidate()

    from app.config import settings

    monkeypatch.setattr(settings, "auth_required", "always")
    monkeypatch.setattr(settings, "allow_signup", True)

    # Rate limiters are process-global; a previous test's attempts would
    # otherwise leak in and cause spurious 429s here.
    from app.ratelimit import (
        compute_limiter,
        email_verify_limiter,
        login_limiter,
        mfa_limiter,
        password_reset_limiter,
        webhook_limiter,
    )

    login_limiter.reset()
    webhook_limiter.reset()
    compute_limiter.reset()
    mfa_limiter.reset()
    password_reset_limiter.reset()
    email_verify_limiter.reset()

    from app.main import app

    with TestClient(app) as client:
        yield client

    market_module.market_service.invalidate()


def _register(client, email: str, password: str = STRONG_PASSWORD) -> dict:
    response = client.post("/auth/register", json={"email": email, "password": password})
    assert response.status_code == 201, response.text
    return response.json()


def _auth(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def test_register_returns_tokens_and_user(auth_client):
    tokens = _register(auth_client, "first@example.com")
    assert tokens["access_token"] and tokens["refresh_token"]
    assert tokens["user"]["email"] == "first@example.com"
    # The first account administers the instance.
    assert tokens["user"]["is_admin"] is True


def test_register_rejects_a_weak_password(auth_client):
    response = auth_client.post("/auth/register", json={"email": "a@example.com", "password": "short"})
    assert response.status_code == 422


def test_register_rejects_a_duplicate_email(auth_client):
    _register(auth_client, "dupe@example.com")
    response = auth_client.post(
        "/auth/register", json={"email": "dupe@example.com", "password": STRONG_PASSWORD}
    )
    assert response.status_code == 409


def test_login_succeeds_with_correct_credentials(auth_client):
    _register(auth_client, "login@example.com")
    response = auth_client.post(
        "/auth/login", json={"email": "login@example.com", "password": STRONG_PASSWORD}
    )
    assert response.status_code == 200
    assert response.json()["access_token"]


def test_login_failure_does_not_reveal_whether_the_account_exists(auth_client):
    """Different messages here would be a free account-enumeration oracle."""
    _register(auth_client, "known@example.com")

    wrong_password = auth_client.post(
        "/auth/login", json={"email": "known@example.com", "password": "wrong-password-here"}
    )
    unknown_account = auth_client.post(
        "/auth/login", json={"email": "nobody@example.com", "password": "wrong-password-here"}
    )

    assert wrong_password.status_code == unknown_account.status_code == 401
    assert wrong_password.json()["detail"] == unknown_account.json()["detail"]


def test_protected_endpoint_requires_a_token(auth_client):
    assert auth_client.get("/trading/watchlist").status_code == 401


def test_protected_endpoint_rejects_a_forged_token(auth_client):
    forged = create_token(1, "not-the-server-secret")
    response = auth_client.get(
        "/trading/watchlist", headers={"Authorization": f"Bearer {forged}"}
    )
    assert response.status_code == 401


def test_me_returns_the_token_holder(auth_client):
    tokens = _register(auth_client, "me@example.com")
    payload = auth_client.get("/auth/me", headers=_auth(tokens)).json()
    assert payload["email"] == "me@example.com"


def test_refresh_issues_a_new_access_token(auth_client):
    tokens = _register(auth_client, "refresh@example.com")
    response = auth_client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert response.status_code == 200
    assert response.json()["access_token"]


def test_access_token_cannot_be_used_to_refresh(auth_client):
    tokens = _register(auth_client, "swap@example.com")
    response = auth_client.post("/auth/refresh", json={"refresh_token": tokens["access_token"]})
    assert response.status_code == 401


def test_password_change_invalidates_existing_tokens(auth_client):
    tokens = _register(auth_client, "rotate@example.com")
    headers = _auth(tokens)
    assert auth_client.get("/auth/me", headers=headers).status_code == 200

    changed = auth_client.post("/auth/password", headers=headers, json={
        "current_password": STRONG_PASSWORD,
        "new_password": "an entirely different passphrase",
    })
    assert changed.status_code == 200

    # The old token must stop working, or a password change protects nothing.
    assert auth_client.get("/auth/me", headers=headers).status_code == 401


def test_logout_everywhere_revokes_issued_tokens(auth_client):
    tokens = _register(auth_client, "logout@example.com")
    headers = _auth(tokens)

    assert auth_client.post("/auth/logout?everywhere=true", headers=headers).status_code == 200
    assert auth_client.get("/auth/me", headers=headers).status_code == 401


def test_auth_status_reports_configuration(auth_client):
    payload = auth_client.get("/auth/status").json()
    assert payload["auth_required"] is True
    assert "note" in payload


# --- per-user data isolation --------------------------------------------------

@pytest.fixture()
def two_users(auth_client):
    alice = _register(auth_client, "alice@example.com")
    bob = _register(auth_client, "bob@example.com")
    return auth_client, _auth(alice), _auth(bob)


def test_watchlists_are_isolated(two_users):
    client, alice, bob = two_users

    created = client.post("/trading/watchlist", json={"symbol": "BTCUSDT"}, headers=alice).json()

    alice_items = client.get("/trading/watchlist?with_quotes=false", headers=alice).json()["items"]
    bob_items = client.get("/trading/watchlist?with_quotes=false", headers=bob).json()["items"]

    assert any(i["symbol"] == "BTCUSDT" for i in alice_items)
    assert bob_items == []

    # Addressing the row directly by id must look like it does not exist.
    assert client.delete(f"/trading/watchlist/{created['id']}", headers=bob).status_code == 404
    assert client.delete(f"/trading/watchlist/{created['id']}", headers=alice).status_code == 200


def test_two_users_can_watch_the_same_symbol(two_users):
    """Watchlist symbols are unique per user, not globally."""
    client, alice, bob = two_users
    assert client.post("/trading/watchlist", json={"symbol": "ETHUSDT"}, headers=alice).json()["added"]
    assert client.post("/trading/watchlist", json={"symbol": "ETHUSDT"}, headers=bob).json()["added"]


def test_alerts_are_isolated(two_users):
    client, alice, bob = two_users
    created = client.post(
        "/trading/alerts", json={"symbol": "BTCUSDT", "condition": "above", "price": 1.0},
        headers=alice,
    ).json()

    assert client.get("/trading/alerts", headers=bob).json()["alerts"] == []
    assert client.delete(f"/trading/alerts/{created['id']}", headers=bob).status_code == 404
    assert client.delete(f"/trading/alerts/{created['id']}", headers=alice).status_code == 200


def test_paper_positions_are_isolated(two_users):
    client, alice, bob = two_users
    quote = client.get("/market/quote", params={"symbol": "BTCUSDT"}, headers=alice).json()

    opened = client.post("/trading/paper/open", json={
        "symbol": "BTCUSDT", "direction": "long", "stop_price": quote["price"] * 0.95,
    }, headers=alice).json()

    assert client.get("/trading/paper/positions", headers=bob).json()["positions"] == []

    # Bob must not be able to close Alice's position.
    assert client.post(f"/trading/paper/close/{opened['id']}", json={}, headers=bob).status_code == 404
    assert client.post(f"/trading/paper/close/{opened['id']}", json={}, headers=alice).status_code == 200


def test_saved_strategies_are_isolated(two_users):
    client, alice, bob = two_users
    spec = client.post(
        "/strategy/build", json={"description": "Buy breakout with volume"}, headers=alice
    ).json()["spec"]

    saved = client.post("/strategy/save", json={"spec": spec, "name": "Alice's"}, headers=alice).json()

    assert client.get("/strategy/saved", headers=bob).json()["strategies"] == []
    assert client.get(f"/strategy/saved/{saved['id']}", headers=bob).status_code == 404
    assert client.delete(f"/strategy/saved/{saved['id']}", headers=bob).status_code == 404
    assert client.get(f"/strategy/saved/{saved['id']}", headers=alice).status_code == 200


def test_analyses_are_isolated(two_users):
    client, alice, bob = two_users
    client.post("/analysis", json={
        "symbol": "BTCUSDT", "timeframe": "1h", "bars": 300,
        "include_mtf": False, "include_chart_data": False,
    }, headers=alice)

    alice_lessons = client.get("/analysis/lessons", headers=alice).json()
    bob_lessons = client.get("/analysis/lessons", headers=bob).json()

    assert bob_lessons["analyses_graded"] == 0
    assert bob_lessons["backtests_run"] == 0
    assert alice_lessons["feedback_entries"] == 0   # none rated yet, but scoped to Alice


# --- webhook security ---------------------------------------------------------

def test_each_user_gets_a_distinct_webhook_url(two_users):
    client, alice, bob = two_users
    alice_path = client.get("/auth/webhook-url", headers=alice).json()["path"]
    bob_path = client.get("/auth/webhook-url", headers=bob).json()["path"]
    assert alice_path != bob_path
    assert len(alice_path.rsplit("/", 1)[-1]) >= 20


def test_webhook_records_against_the_url_owner(two_users):
    client, alice, bob = two_users
    alice_path = client.get("/auth/webhook-url", headers=alice).json()["path"]

    assert client.post(alice_path, content="LONG BTCUSDT @ 63000").json()["received"] is True

    assert client.get("/trading/webhook/history", headers=alice).json()["webhooks"]
    assert client.get("/trading/webhook/history", headers=bob).json()["webhooks"] == []


def test_webhook_rejects_an_unknown_url(auth_client):
    assert auth_client.post(
        "/trading/webhook/tradingview/definitely-not-a-real-token", content="LONG BTC"
    ).status_code == 404


def test_rotating_the_webhook_url_invalidates_the_old_one(auth_client):
    tokens = _register(auth_client, "rotate-hook@example.com")
    headers = _auth(tokens)

    old_path = auth_client.get("/auth/webhook-url", headers=headers).json()["path"]
    new_path = auth_client.post("/auth/webhook-url/rotate", headers=headers).json()["path"]

    assert old_path != new_path
    assert auth_client.post(old_path, content="LONG BTC").status_code == 404
    assert auth_client.post(new_path, content="LONG BTC").status_code == 200


# --- multi-factor authentication ----------------------------------------------

def _enable_mfa(client, headers) -> dict:
    """Set up and confirm MFA, returning the setup secret plus the response
    from /mfa/confirm (which carries the one-time recovery codes)."""
    setup = client.post("/auth/mfa/setup", headers=headers).json()
    code = totp(setup["secret"])
    confirmed = client.post("/auth/mfa/confirm", json={"code": code}, headers=headers)
    assert confirmed.status_code == 200, confirmed.text
    return {"secret": setup["secret"], **confirmed.json()}


def test_mfa_setup_returns_a_secret_and_provisioning_uri(auth_client):
    tokens = _register(auth_client, "mfa-setup@example.com")
    response = auth_client.post("/auth/mfa/setup", headers=_auth(tokens))
    payload = response.json()
    assert len(payload["secret"]) >= 16
    assert payload["provisioning_uri"].startswith("otpauth://totp/")


def test_mfa_confirm_rejects_a_wrong_code(auth_client):
    tokens = _register(auth_client, "mfa-wrong-confirm@example.com")
    headers = _auth(tokens)
    auth_client.post("/auth/mfa/setup", headers=headers)
    response = auth_client.post("/auth/mfa/confirm", json={"code": "000000"}, headers=headers)
    assert response.status_code == 400


def test_mfa_confirm_enables_it_and_returns_ten_recovery_codes(auth_client):
    tokens = _register(auth_client, "mfa-confirm@example.com")
    result = _enable_mfa(auth_client, _auth(tokens))
    assert result["enabled"] is True
    assert len(result["recovery_codes"]) == 10
    assert len(set(result["recovery_codes"])) == 10  # all distinct

    me = auth_client.get("/auth/me", headers=_auth(tokens)).json()
    assert me["mfa_enabled"] is True


def test_login_with_mfa_enabled_returns_a_challenge_not_tokens(auth_client):
    email = "mfa-login@example.com"
    tokens = _register(auth_client, email)
    _enable_mfa(auth_client, _auth(tokens))

    response = auth_client.post("/auth/login", json={"email": email, "password": STRONG_PASSWORD})
    payload = response.json()
    assert payload["mfa_required"] is True
    assert "mfa_token" in payload
    assert "access_token" not in payload


def test_mfa_verify_completes_login_with_a_correct_code(auth_client):
    email = "mfa-verify@example.com"
    tokens = _register(auth_client, email)
    enabled = _enable_mfa(auth_client, _auth(tokens))

    challenge = auth_client.post(
        "/auth/login", json={"email": email, "password": STRONG_PASSWORD}
    ).json()
    code = totp(enabled["secret"])
    response = auth_client.post(
        "/auth/mfa/verify", json={"mfa_token": challenge["mfa_token"], "code": code}
    )
    assert response.status_code == 200
    verified = response.json()
    assert verified["access_token"] and verified["refresh_token"]


def test_mfa_verify_rejects_a_wrong_code(auth_client):
    email = "mfa-verify-wrong@example.com"
    tokens = _register(auth_client, email)
    _enable_mfa(auth_client, _auth(tokens))

    challenge = auth_client.post(
        "/auth/login", json={"email": email, "password": STRONG_PASSWORD}
    ).json()
    response = auth_client.post(
        "/auth/mfa/verify", json={"mfa_token": challenge["mfa_token"], "code": "000000"}
    )
    assert response.status_code == 401


def test_mfa_token_cannot_be_used_as_an_access_token(auth_client):
    """Type isolation: an mfa-scoped token must be rejected by endpoints that
    require a real access token, the same guarantee refresh tokens already get."""
    email = "mfa-type-confusion@example.com"
    tokens = _register(auth_client, email)
    _enable_mfa(auth_client, _auth(tokens))

    challenge = auth_client.post(
        "/auth/login", json={"email": email, "password": STRONG_PASSWORD}
    ).json()
    response = auth_client.get(
        "/auth/me", headers={"Authorization": f"Bearer {challenge['mfa_token']}"}
    )
    assert response.status_code == 401


def test_an_access_token_cannot_be_used_as_an_mfa_token(auth_client):
    """The reverse direction of the same guarantee."""
    email = "mfa-reverse-confusion@example.com"
    tokens = _register(auth_client, email)
    _enable_mfa(auth_client, _auth(tokens))

    response = auth_client.post(
        "/auth/mfa/verify", json={"mfa_token": tokens["access_token"], "code": "000000"}
    )
    assert response.status_code == 401


def test_recovery_code_completes_login_and_is_single_use(auth_client):
    email = "mfa-recovery@example.com"
    tokens = _register(auth_client, email)
    enabled = _enable_mfa(auth_client, _auth(tokens))
    recovery_code = enabled["recovery_codes"][0]

    challenge = auth_client.post(
        "/auth/login", json={"email": email, "password": STRONG_PASSWORD}
    ).json()
    first_use = auth_client.post(
        "/auth/mfa/verify", json={"mfa_token": challenge["mfa_token"], "code": recovery_code}
    )
    assert first_use.status_code == 200

    # A second login attempt with the SAME recovery code must fail — one use each.
    second_challenge = auth_client.post(
        "/auth/login", json={"email": email, "password": STRONG_PASSWORD}
    ).json()
    second_use = auth_client.post(
        "/auth/mfa/verify", json={"mfa_token": second_challenge["mfa_token"], "code": recovery_code}
    )
    assert second_use.status_code == 401


def test_mfa_status_reports_remaining_recovery_codes(auth_client):
    tokens = _register(auth_client, "mfa-status@example.com")
    headers = _auth(tokens)
    enabled = _enable_mfa(auth_client, headers)

    status_before = auth_client.get("/auth/mfa/status", headers=headers).json()
    assert status_before["enabled"] is True
    assert status_before["recovery_codes_remaining"] == 10

    challenge = auth_client.post(
        "/auth/login", json={"email": "mfa-status@example.com", "password": STRONG_PASSWORD}
    ).json()
    auth_client.post(
        "/auth/mfa/verify",
        json={"mfa_token": challenge["mfa_token"], "code": enabled["recovery_codes"][0]},
    )

    # Fetch a fresh access token via the login that just succeeded, since the
    # original `headers` token is still perfectly valid too (MFA doesn't
    # revoke pre-existing sessions) — either works; using the original here.
    status_after = auth_client.get("/auth/mfa/status", headers=headers).json()
    assert status_after["recovery_codes_remaining"] == 9


def test_mfa_disable_requires_the_correct_password(auth_client):
    tokens = _register(auth_client, "mfa-disable-wrong@example.com")
    headers = _auth(tokens)
    _enable_mfa(auth_client, headers)

    response = auth_client.post(
        "/auth/mfa/disable", json={"password": "not the password"}, headers=headers
    )
    assert response.status_code == 401

    status = auth_client.get("/auth/mfa/status", headers=headers).json()
    assert status["enabled"] is True  # unchanged


def test_mfa_disable_turns_it_off(auth_client):
    email = "mfa-disable@example.com"
    tokens = _register(auth_client, email)
    headers = _auth(tokens)
    _enable_mfa(auth_client, headers)

    response = auth_client.post(
        "/auth/mfa/disable", json={"password": STRONG_PASSWORD}, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["disabled"] is True

    # And a plain login now succeeds without any MFA challenge.
    login = auth_client.post("/auth/login", json={"email": email, "password": STRONG_PASSWORD})
    assert login.status_code == 200
    assert "access_token" in login.json()


def test_admin_can_reset_a_locked_out_users_mfa(auth_client):
    # The first registered account on a fresh instance is always admin.
    admin_tokens = _register(auth_client, "admin-mfa-reset@example.com")
    admin_headers = _auth(admin_tokens)

    locked_email = "locked-out@example.com"
    locked_tokens = _register(auth_client, locked_email)
    _enable_mfa(auth_client, _auth(locked_tokens))

    # Confirms the setup: without the reset, this account is genuinely
    # locked behind a code only the (now-lost) authenticator can produce.
    login = auth_client.post("/auth/login", json={"email": locked_email, "password": STRONG_PASSWORD})
    assert login.status_code == 200
    assert "mfa_token" in login.json()

    response = auth_client.post(
        "/auth/admin/mfa/reset", json={"email": locked_email}, headers=admin_headers
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["reset"] is True
    assert payload["mfa_was_enabled"] is True

    # Now a plain login succeeds with no MFA challenge at all.
    login_after = auth_client.post(
        "/auth/login", json={"email": locked_email, "password": STRONG_PASSWORD}
    )
    assert login_after.status_code == 200
    assert "access_token" in login_after.json()

    status = auth_client.get("/auth/mfa/status", headers=_auth(locked_tokens)).json()
    assert status["enabled"] is False


def test_admin_mfa_reset_is_refused_to_a_non_admin(auth_client):
    _register(auth_client, "admin2-mfa-reset@example.com")  # first account, is admin
    member_tokens = _register(auth_client, "member-mfa-reset@example.com")
    target_tokens = _register(auth_client, "target-mfa-reset@example.com")
    _enable_mfa(auth_client, _auth(target_tokens))

    response = auth_client.post(
        "/auth/admin/mfa/reset",
        json={"email": "target-mfa-reset@example.com"},
        headers=_auth(member_tokens),
    )
    assert response.status_code == 403

    status = auth_client.get("/auth/mfa/status", headers=_auth(target_tokens)).json()
    assert status["enabled"] is True  # unchanged


def test_admin_mfa_reset_404s_for_an_unknown_email(auth_client):
    admin_tokens = _register(auth_client, "admin3-mfa-reset@example.com")
    response = auth_client.post(
        "/auth/admin/mfa/reset",
        json={"email": "nobody-here@example.com"},
        headers=_auth(admin_tokens),
    )
    assert response.status_code == 404


def test_admin_mfa_reset_is_a_no_op_result_when_mfa_was_never_enabled(auth_client):
    admin_tokens = _register(auth_client, "admin4-mfa-reset@example.com")
    _register(auth_client, "never-enabled-mfa@example.com")  # no _enable_mfa call

    response = auth_client.post(
        "/auth/admin/mfa/reset",
        json={"email": "never-enabled-mfa@example.com"},
        headers=_auth(admin_tokens),
    )
    assert response.status_code == 200
    assert response.json()["mfa_was_enabled"] is False


def test_mfa_setup_refuses_to_run_again_while_already_enabled(auth_client):
    tokens = _register(auth_client, "mfa-double-setup@example.com")
    headers = _auth(tokens)
    _enable_mfa(auth_client, headers)

    response = auth_client.post("/auth/mfa/setup", headers=headers)
    assert response.status_code == 400


def test_login_success_is_not_recorded_until_mfa_completes(auth_client):
    """A stolen password alone must not look like a completed sign-in in the
    account's own history."""
    email = "mfa-timing@example.com"
    tokens = _register(auth_client, email)
    headers = _auth(tokens)
    _enable_mfa(auth_client, headers)

    before = auth_client.get("/auth/me", headers=headers).json()["last_login_at"]

    # Password-only step: must not bump last_login_at.
    auth_client.post("/auth/login", json={"email": email, "password": STRONG_PASSWORD})
    after_password_only = auth_client.get("/auth/me", headers=headers).json()["last_login_at"]
    assert after_password_only == before


# --- rate limiting ------------------------------------------------------------

def test_repeated_login_attempts_are_rate_limited(auth_client):
    _register(auth_client, "bruteforce@example.com")

    statuses = [
        auth_client.post(
            "/auth/login",
            json={"email": "bruteforce@example.com", "password": f"guess-number-{i}"},
        ).status_code
        for i in range(25)
    ]
    # Guessing must be throttled rather than answered indefinitely.
    assert 429 in statuses


def test_repeated_mfa_verify_attempts_are_rate_limited(auth_client):
    """A 6-digit code is a 1-in-a-million guess per attempt — the endpoint
    that checks it needs its own throttle, same as the password does."""
    email = "mfa-bruteforce@example.com"
    tokens = _register(auth_client, email)
    _enable_mfa(auth_client, _auth(tokens))

    challenge = auth_client.post(
        "/auth/login", json={"email": email, "password": STRONG_PASSWORD}
    ).json()

    statuses = [
        auth_client.post(
            "/auth/mfa/verify",
            json={"mfa_token": challenge["mfa_token"], "code": f"{i:06d}"},
        ).status_code
        for i in range(25)
    ]
    assert 429 in statuses


def test_rate_limiter_refills_over_time():
    from app.ratelimit import RateLimiter

    limiter = RateLimiter(capacity=2, refill_per_second=1000.0, name="test")
    assert limiter.check("client")[0] is True
    assert limiter.check("client")[0] is True
    assert limiter.check("client")[0] is False

    time.sleep(0.01)   # at 1000/s this refills the bucket immediately
    assert limiter.check("client")[0] is True


def test_rate_limiter_separates_clients():
    from app.ratelimit import RateLimiter

    limiter = RateLimiter(capacity=1, refill_per_second=0.001, name="test")
    assert limiter.check("client-a")[0] is True
    assert limiter.check("client-a")[0] is False
    # A different caller must have its own budget.
    assert limiter.check("client-b")[0] is True


# --- password reset -----------------------------------------------------------

def _capture_reset_link(monkeypatch) -> list:
    """Stub the mailer and capture what would have been sent, since the API
    never returns the reset token directly (the enumeration-oracle guard
    applies here just as much as at /login)."""
    import app.routers.users as users_module

    sent = []

    def fake_send_mail(to, subject, body):
        sent.append({"to": to, "subject": subject, "body": body})
        return True

    monkeypatch.setattr(users_module, "send_mail", fake_send_mail)
    return sent


def _extract_token(sent: list) -> str:
    """Registration now sends its own verification email, so a reset token is
    no longer reliably `sent[0]` — search every captured message for the most
    recent one carrying a reset_token."""
    import re

    for item in reversed(sent):
        match = re.search(r"reset_token=([\w\-\.]+)", item["body"])
        if match:
            return match.group(1)
    raise AssertionError(f"no reset_token found in mailed messages: {sent!r}")


def test_forgot_password_returns_the_same_response_whether_or_not_the_account_exists(auth_client):
    known = auth_client.post("/auth/password/forgot", json={"email": "not-registered@example.com"})
    _register(auth_client, "exists@example.com")
    unknown = auth_client.post("/auth/password/forgot", json={"email": "exists@example.com"})

    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()


def test_forgot_password_sends_a_reset_link_for_a_real_account(auth_client, monkeypatch):
    sent = _capture_reset_link(monkeypatch)
    _register(auth_client, "reset-me@example.com")

    response = auth_client.post("/auth/password/forgot", json={"email": "reset-me@example.com"})
    assert response.status_code == 200
    assert len(sent) == 2  # registration's own verification email, then the reset link
    reset_mail = sent[-1]
    assert reset_mail["to"] == "reset-me@example.com"
    assert "reset_token=" in reset_mail["body"]


def test_forgot_password_sends_nothing_for_an_unregistered_address(auth_client, monkeypatch):
    sent = _capture_reset_link(monkeypatch)
    auth_client.post("/auth/password/forgot", json={"email": "nobody-here@example.com"})
    assert sent == []


def test_reset_password_with_a_valid_token_changes_the_password_and_signs_out_sessions(
    auth_client, monkeypatch,
):
    sent = _capture_reset_link(monkeypatch)
    email = "full-reset@example.com"
    tokens = _register(auth_client, email)
    headers = _auth(tokens)
    assert auth_client.get("/auth/me", headers=headers).status_code == 200

    auth_client.post("/auth/password/forgot", json={"email": email})
    reset_token = _extract_token(sent)

    response = auth_client.post(
        "/auth/password/reset",
        json={"token": reset_token, "new_password": "an entirely different passphrase"},
    )
    assert response.status_code == 200
    assert response.json()["reset"] is True

    # The old session must not survive a reset any more than a manual change.
    assert auth_client.get("/auth/me", headers=headers).status_code == 401

    # And the new password actually works.
    login = auth_client.post("/auth/login", json={
        "email": email, "password": "an entirely different passphrase",
    })
    assert login.status_code == 200


def test_reset_password_token_is_single_use(auth_client, monkeypatch):
    sent = _capture_reset_link(monkeypatch)
    email = "single-use@example.com"
    _register(auth_client, email)

    auth_client.post("/auth/password/forgot", json={"email": email})
    reset_token = _extract_token(sent)

    first = auth_client.post("/auth/password/reset", json={
        "token": reset_token, "new_password": "first new passphrase here",
    })
    assert first.status_code == 200

    replay = auth_client.post("/auth/password/reset", json={
        "token": reset_token, "new_password": "second new passphrase here",
    })
    assert replay.status_code == 400


def test_reset_password_rejects_an_older_token_once_a_newer_one_is_requested(auth_client, monkeypatch):
    sent = _capture_reset_link(monkeypatch)
    email = "superseded@example.com"
    _register(auth_client, email)

    auth_client.post("/auth/password/forgot", json={"email": email})
    old_token = _extract_token(sent)

    time.sleep(1.1)  # tokens carry second-resolution issued-at timestamps
    auth_client.post("/auth/password/forgot", json={"email": email})
    assert len(sent) == 3  # registration's verification email, then both reset links

    response = auth_client.post("/auth/password/reset", json={
        "token": old_token, "new_password": "irrelevant passphrase here",
    })
    assert response.status_code == 400


def test_reset_password_rejects_a_malformed_token(auth_client):
    response = auth_client.post("/auth/password/reset", json={
        "token": "not-a-real-token", "new_password": "irrelevant passphrase here",
    })
    assert response.status_code == 400


def test_reset_password_rejects_a_token_of_the_wrong_type(auth_client):
    """A refresh or access token must not double as a password-reset token —
    the same type-confusion guard MFA already relies on."""
    tokens = _register(auth_client, "wrong-type@example.com")

    response = auth_client.post("/auth/password/reset", json={
        "token": tokens["access_token"], "new_password": "irrelevant passphrase here",
    })
    assert response.status_code == 400


def test_reset_password_enforces_password_strength(auth_client, monkeypatch):
    sent = _capture_reset_link(monkeypatch)
    email = "weak-reset@example.com"
    _register(auth_client, email)

    auth_client.post("/auth/password/forgot", json={"email": email})
    reset_token = _extract_token(sent)

    response = auth_client.post("/auth/password/reset", json={
        "token": reset_token, "new_password": "short",
    })
    assert response.status_code == 422


def test_forgot_password_is_rate_limited(auth_client, monkeypatch):
    _capture_reset_link(monkeypatch)
    statuses = [
        auth_client.post("/auth/password/forgot", json={"email": "spam@example.com"}).status_code
        for _ in range(10)
    ]
    assert 429 in statuses


# --- email verification --------------------------------------------------------

def _extract_verify_token(sent: list) -> str:
    import re

    for item in reversed(sent):
        match = re.search(r"verify_token=([\w\-\.]+)", item["body"])
        if match:
            return match.group(1)
    raise AssertionError(f"no verify_token found in mailed messages: {sent!r}")


def test_registration_sends_a_verification_email(auth_client, monkeypatch):
    sent = _capture_reset_link(monkeypatch)
    _register(auth_client, "fresh@example.com")

    assert len(sent) == 1
    assert sent[0]["to"] == "fresh@example.com"
    assert sent[0]["subject"] == "Verify your Legend Trade email address"
    assert "verify_token=" in sent[0]["body"]


def test_new_account_starts_unverified(auth_client, monkeypatch):
    _capture_reset_link(monkeypatch)
    tokens = _register(auth_client, "unverified@example.com")
    assert tokens["user"]["email_verified"] is False

    me = auth_client.get("/auth/me", headers=_auth(tokens)).json()
    assert me["email_verified"] is False


def test_verify_email_with_a_valid_token_marks_the_account_verified(auth_client, monkeypatch):
    sent = _capture_reset_link(monkeypatch)
    tokens = _register(auth_client, "verify-me@example.com")
    verify_token = _extract_verify_token(sent)

    response = auth_client.post("/auth/email/verify", json={"token": verify_token})
    assert response.status_code == 200
    assert response.json() == {"verified": True, "email": "verify-me@example.com"}

    me = auth_client.get("/auth/me", headers=_auth(tokens)).json()
    assert me["email_verified"] is True


def test_verify_email_is_idempotent(auth_client, monkeypatch):
    sent = _capture_reset_link(monkeypatch)
    _register(auth_client, "twice@example.com")
    verify_token = _extract_verify_token(sent)

    first = auth_client.post("/auth/email/verify", json={"token": verify_token})
    second = auth_client.post("/auth/email/verify", json={"token": verify_token})
    assert first.status_code == second.status_code == 200


def test_verify_email_rejects_a_malformed_token(auth_client):
    response = auth_client.post("/auth/email/verify", json={"token": "not-a-real-token"})
    assert response.status_code == 400


def test_verify_email_rejects_a_token_of_the_wrong_type(auth_client, monkeypatch):
    _capture_reset_link(monkeypatch)
    tokens = _register(auth_client, "wrong-type-verify@example.com")

    response = auth_client.post(
        "/auth/email/verify", json={"token": tokens["access_token"]},
    )
    assert response.status_code == 400


def test_resend_verification_requires_authentication(auth_client):
    response = auth_client.post("/auth/email/resend")
    assert response.status_code == 401


def test_resend_verification_sends_a_fresh_link(auth_client, monkeypatch):
    sent = _capture_reset_link(monkeypatch)
    tokens = _register(auth_client, "resend-me@example.com")
    assert len(sent) == 1  # the one sent at registration

    response = auth_client.post("/auth/email/resend", headers=_auth(tokens))
    assert response.status_code == 200
    assert response.json()["sent"] is True
    assert len(sent) == 2


def test_resend_verification_does_nothing_once_already_verified(auth_client, monkeypatch):
    sent = _capture_reset_link(monkeypatch)
    tokens = _register(auth_client, "already-verified@example.com")
    verify_token = _extract_verify_token(sent)
    auth_client.post("/auth/email/verify", json={"token": verify_token})

    response = auth_client.post("/auth/email/resend", headers=_auth(tokens))
    assert response.status_code == 200
    assert response.json()["sent"] is False
    assert len(sent) == 1  # no second email went out


def test_resend_verification_is_rate_limited(auth_client, monkeypatch):
    _capture_reset_link(monkeypatch)
    tokens = _register(auth_client, "resend-spam@example.com")
    headers = _auth(tokens)

    statuses = [
        auth_client.post("/auth/email/resend", headers=headers).status_code
        for _ in range(10)
    ]
    assert 429 in statuses


# --- admin user management ------------------------------------------------------
#
# An operator of a published instance needs to see accounts and disable an
# abusive one without hand-editing SQLite. The guardrails matter more than the
# feature: locking yourself out of your own instance has no in-app recovery.

def test_admin_can_list_users_without_leaking_secrets(auth_client):
    admin_tokens = _register(auth_client, "listadmin@example.com")
    _register(auth_client, "listmember@example.com")

    response = auth_client.get("/auth/admin/users", headers=_auth(admin_tokens))
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 2

    emails = {u["email"] for u in payload["users"]}
    assert emails == {"listadmin@example.com", "listmember@example.com"}

    # An admin has no legitimate use for another account's secrets, and
    # returning them would turn one stolen admin session into a full compromise.
    for user in payload["users"]:
        for secret in ("password_hash", "mfa_secret", "webhook_token"):
            assert secret not in user


def test_listing_users_is_refused_to_a_non_admin(auth_client):
    _register(auth_client, "la2@example.com")
    member = _register(auth_client, "lm2@example.com")
    assert auth_client.get("/auth/admin/users", headers=_auth(member)).status_code == 403


def test_admin_can_filter_users_by_email(auth_client):
    admin_tokens = _register(auth_client, "filteradmin@example.com")
    _register(auth_client, "needle@example.com")
    _register(auth_client, "haystack@example.com")

    response = auth_client.get("/auth/admin/users?query=needle", headers=_auth(admin_tokens))
    assert [u["email"] for u in response.json()["users"]] == ["needle@example.com"]


def test_admin_can_deactivate_an_account_and_its_tokens_stop_working(auth_client):
    admin_tokens = _register(auth_client, "deactadmin@example.com")
    member = _register(auth_client, "abusive@example.com")
    member_id = member["user"]["id"]

    # The member's token works before deactivation.
    assert auth_client.get("/auth/me", headers=_auth(member)).status_code == 200

    response = auth_client.post(
        f"/auth/admin/users/{member_id}/active",
        json={"active": False}, headers=_auth(admin_tokens),
    )
    assert response.status_code == 200
    assert response.json()["is_active"] is False

    # Already-issued tokens must stop working immediately, not at next sign-in.
    assert auth_client.get("/auth/me", headers=_auth(member)).status_code == 401
    login = auth_client.post(
        "/auth/login", json={"email": "abusive@example.com", "password": STRONG_PASSWORD}
    )
    assert login.status_code == 401


def test_admin_can_reactivate_an_account(auth_client):
    admin_tokens = _register(auth_client, "reactadmin@example.com")
    member = _register(auth_client, "reactivate-me@example.com")
    member_id = member["user"]["id"]

    auth_client.post(f"/auth/admin/users/{member_id}/active",
                     json={"active": False}, headers=_auth(admin_tokens))
    auth_client.post(f"/auth/admin/users/{member_id}/active",
                     json={"active": True}, headers=_auth(admin_tokens))

    login = auth_client.post(
        "/auth/login", json={"email": "reactivate-me@example.com", "password": STRONG_PASSWORD}
    )
    assert login.status_code == 200


def test_an_admin_cannot_deactivate_themselves(auth_client):
    """The obvious misclick, and it has no in-app recovery."""
    admin_tokens = _register(auth_client, "selfdeact@example.com")
    admin_id = admin_tokens["user"]["id"]

    response = auth_client.post(f"/auth/admin/users/{admin_id}/active",
                                json={"active": False}, headers=_auth(admin_tokens))
    assert response.status_code == 400
    assert "your own account" in response.json()["detail"]

    # Still works afterwards.
    assert auth_client.get("/auth/me", headers=_auth(admin_tokens)).status_code == 200


def test_active_admin_count_backs_the_last_admin_invariant(session):
    """The endpoint's last-admin guard is unreachable today (any caller past
    the self-check is another active admin), so the helper it depends on is
    tested directly rather than through a scenario that cannot occur. It
    becomes live the moment admin promotion/demotion is added."""
    from app.routers.users import _active_admin_count
    from database.db import SessionLocal
    from database.models import User

    db = SessionLocal()
    try:
        db.add(User(email="count-a@example.com", display_name="A", password_hash="x",
                    webhook_token="t1", tokens_valid_from=dt.datetime.utcnow(),
                    is_admin=True, is_active=True))
        db.add(User(email="count-b@example.com", display_name="B", password_hash="x",
                    webhook_token="t2", tokens_valid_from=dt.datetime.utcnow(),
                    is_admin=True, is_active=False))   # inactive admin must not count
        db.add(User(email="count-c@example.com", display_name="C", password_hash="x",
                    webhook_token="t3", tokens_valid_from=dt.datetime.utcnow(),
                    is_admin=False, is_active=True))   # non-admin must not count
        db.commit()

        assert _active_admin_count(db) == 1
        only_admin = db.query(User).filter(User.email == "count-a@example.com").first()
        assert _active_admin_count(db, excluding=only_admin.id) == 0
    finally:
        db.close()


def test_admin_can_clear_a_login_lockout(auth_client):
    admin_tokens = _register(auth_client, "unlockadmin@example.com")
    member = _register(auth_client, "lockedout@example.com")
    member_id = member["user"]["id"]

    for _ in range(9):
        auth_client.post("/auth/login",
                         json={"email": "lockedout@example.com", "password": "wrong-password"})

    listed = auth_client.get("/auth/admin/users?query=lockedout",
                             headers=_auth(admin_tokens)).json()["users"][0]
    assert listed["locked"] is True

    response = auth_client.post(f"/auth/admin/users/{member_id}/unlock",
                                headers=_auth(admin_tokens))
    assert response.status_code == 200
    assert response.json()["was_locked"] is True

    # The nine deliberate failures also spent the per-IP login budget; this
    # test is about the account lockout, not the rate limiter.
    from app.ratelimit import login_limiter
    login_limiter.reset()

    login = auth_client.post(
        "/auth/login", json={"email": "lockedout@example.com", "password": STRONG_PASSWORD}
    )
    assert login.status_code == 200


def test_admin_actions_404_for_an_unknown_user(auth_client):
    admin_tokens = _register(auth_client, "admin404@example.com")
    assert auth_client.post("/auth/admin/users/9999/active", json={"active": False},
                            headers=_auth(admin_tokens)).status_code == 404
    assert auth_client.post("/auth/admin/users/9999/unlock",
                            headers=_auth(admin_tokens)).status_code == 404


def test_deactivation_is_refused_to_a_non_admin(auth_client):
    _register(auth_client, "na-admin@example.com")
    member = _register(auth_client, "na-member@example.com")
    victim = _register(auth_client, "na-victim@example.com")

    response = auth_client.post(f"/auth/admin/users/{victim['user']['id']}/active",
                                json={"active": False}, headers=_auth(member))
    assert response.status_code == 403


# --- data export and account deletion ----------------------------------------
#
# These back a legal promise, not a feature request: the privacy policy says a
# user can obtain and erase their data. The dangerous failure is silent — an
# export that omits a table, or a deletion that leaves rows behind, both look
# like success. So the seeding map below is asserted to cover `_USER_OWNED`
# exactly: add a table to the export without seeding it here and this file
# fails, rather than the omission shipping unnoticed.

def _seed_every_user_table(db, user_id: int) -> dict:
    """One row in every table the export and the deletion walk."""
    from database.models import (
        AnalysisFeedback,
        AnalysisRecord,
        BacktestRecord,
        ExecutionState,
        MfaRecoveryCode,
        OrderRecord,
        PaperPosition,
        PriceAlert,
        StrategyRecord,
        TradingWebhook,
        WatchlistItem,
    )

    rows = {
        "mfa_recovery_codes": MfaRecoveryCode(user_id=user_id, code_hash="hashed-recovery-code"),
        "analyses": AnalysisRecord(user_id=user_id, symbol="BTCUSDT", timeframe="1h",
                                   price_at_analysis=100.0),
        "strategies": StrategyRecord(user_id=user_id, name="mine", spec_json="{}"),
        "backtests": BacktestRecord(user_id=user_id, strategy_name="mine"),
        "paper_positions": PaperPosition(user_id=user_id, symbol="BTCUSDT", direction="long",
                                         quantity=1.0, entry_price=100.0, stop_price=98.0),
        "alerts": PriceAlert(user_id=user_id, symbol="BTCUSDT", condition="above", price=1.0),
        "watchlist": WatchlistItem(user_id=user_id, symbol="BTCUSDT"),
        "webhooks": TradingWebhook(user_id=user_id, raw_body="{}"),
        "feedback": AnalysisFeedback(user_id=user_id, rating="useful"),
        "orders": OrderRecord(user_id=user_id, mode="paper", symbol="BTCUSDT", side="buy",
                              quantity=1.0),
        "execution_state": ExecutionState(user_id=user_id),
    }
    for row in rows.values():
        db.add(row)
    db.commit()
    return rows


def test_the_seed_helper_covers_every_exported_table(session):
    """The guard that keeps every test below honest as the schema grows.

    Without this, adding a table to `_USER_OWNED` and forgetting to seed it here
    would leave the export and deletion tests passing on a table that was never
    populated — the exact blind spot they exist to close.
    """
    from app.routers.users import _USER_OWNED

    seeded = _seed_every_user_table(session, user_id=4242)
    assert set(seeded) == {label for label, _ in _USER_OWNED}


def test_export_requires_authentication(auth_client):
    assert auth_client.get("/auth/me/export").status_code == 401


def test_export_returns_every_user_owned_table(auth_client, session):
    from app.routers.users import _USER_OWNED

    tokens = _register(auth_client, "exporter@example.com")
    _seed_every_user_table(session, tokens["user"]["id"])

    body = auth_client.get("/auth/me/export", headers=_auth(tokens)).json()

    assert body["account"]["email"] == "exporter@example.com"
    assert set(body["data"]) == {label for label, _ in _USER_OWNED}
    # Every table seeded with exactly one row must come back with exactly one.
    assert body["counts"] == {label: 1 for label, _ in _USER_OWNED}


def test_export_never_discloses_credential_material(auth_client, session):
    """A subject access request must not become an offline attack on the account.

    The export is generated from the table columns, so a credential column added
    later would be swept in automatically. This asserts against the serialised
    body rather than against named fields, so it catches that.
    """
    from app import mfa
    from database.models import User

    tokens = _register(auth_client, "secrets@example.com")
    user = session.query(User).filter(User.email == "secrets@example.com").first()
    secret = mfa.generate_secret()
    user.mfa_secret = secret
    user.mfa_enabled = True
    session.commit()
    password_hash = user.password_hash
    _seed_every_user_table(session, user.id)

    response = auth_client.get("/auth/me/export", headers=_auth(tokens))
    raw = response.text

    assert password_hash not in raw
    assert secret not in raw
    assert "hashed-recovery-code" not in raw

    body = response.json()
    # The account block never carries credential fields at all.
    assert "password_hash" not in body["account"]
    assert "mfa_secret" not in body["account"]
    # The recovery-code row is still reported — the user is entitled to know the
    # codes exist and when they were used — but the hash itself is withheld.
    code_row = body["data"]["mfa_recovery_codes"][0]
    assert "used_at" in code_row
    assert code_row["code_hash"] == "[withheld — see not_included]"
    # It should still say the second factor exists — that fact is the user's.
    assert body["account"]["mfa_enabled"] is True


def test_export_is_scoped_to_the_caller(auth_client, session):
    _register(auth_client, "export-admin@example.com")
    alice = _register(auth_client, "export-alice@example.com")
    bob = _register(auth_client, "export-bob@example.com")
    _seed_every_user_table(session, bob["user"]["id"])

    body = auth_client.get("/auth/me/export", headers=_auth(alice)).json()

    assert body["account"]["email"] == "export-alice@example.com"
    assert all(count == 0 for count in body["counts"].values())


def test_delete_requires_the_exact_confirmation_phrase(auth_client):
    _register(auth_client, "del-admin@example.com")
    member = _register(auth_client, "del-typo@example.com")

    for wrong in ("", "delete my account", "DELETE MY ACCOUNT ", "yes"):
        response = auth_client.post("/auth/me/delete", headers=_auth(member),
                                    json={"password": STRONG_PASSWORD, "confirm": wrong})
        assert response.status_code == 400, wrong

    assert auth_client.get("/auth/me", headers=_auth(member)).status_code == 200


def test_delete_requires_the_current_password(auth_client):
    _register(auth_client, "del-admin2@example.com")
    member = _register(auth_client, "del-wrongpw@example.com")

    response = auth_client.post(
        "/auth/me/delete", headers=_auth(member),
        json={"password": "not the password", "confirm": "DELETE MY ACCOUNT"})

    assert response.status_code == 401
    assert auth_client.get("/auth/me", headers=_auth(member)).status_code == 200


def test_delete_requires_authentication(auth_client):
    response = auth_client.post(
        "/auth/me/delete", json={"password": STRONG_PASSWORD, "confirm": "DELETE MY ACCOUNT"})
    assert response.status_code == 401


def test_the_only_administrator_cannot_delete_themselves(auth_client):
    """Otherwise the instance is left with nobody able to administer it."""
    admin = _register(auth_client, "sole-admin@example.com")
    _register(auth_client, "sole-member@example.com")   # a member is not a replacement

    response = auth_client.post(
        "/auth/me/delete", headers=_auth(admin),
        json={"password": STRONG_PASSWORD, "confirm": "DELETE MY ACCOUNT"})

    assert response.status_code == 409
    assert auth_client.get("/auth/me", headers=_auth(admin)).status_code == 200


def test_delete_removes_the_account_and_every_row_keyed_to_it(auth_client, session):
    from app.routers.users import _USER_OWNED
    from database.models import User

    _register(auth_client, "erase-admin@example.com")
    member = _register(auth_client, "erase-me@example.com")
    user_id = member["user"]["id"]
    _seed_every_user_table(session, user_id)

    response = auth_client.post(
        "/auth/me/delete", headers=_auth(member),
        json={"password": STRONG_PASSWORD, "confirm": "DELETE MY ACCOUNT"})

    assert response.status_code == 200, response.text
    assert response.json()["rows_removed"] == {label: 1 for label, _ in _USER_OWNED}

    session.expire_all()
    assert session.query(User).filter(User.id == user_id).first() is None
    for label, model in _USER_OWNED:
        remaining = session.query(model).filter(model.user_id == user_id).count()
        assert remaining == 0, f"{label} survived the deletion"


def test_a_deleted_accounts_tokens_stop_working(auth_client):
    _register(auth_client, "revoke-admin@example.com")
    member = _register(auth_client, "revoke-me@example.com")

    auth_client.post("/auth/me/delete", headers=_auth(member),
                     json={"password": STRONG_PASSWORD, "confirm": "DELETE MY ACCOUNT"})

    assert auth_client.get("/auth/me", headers=_auth(member)).status_code == 401
    login = auth_client.post("/auth/login",
                             json={"email": "revoke-me@example.com", "password": STRONG_PASSWORD})
    assert login.status_code == 401


def test_deleting_one_account_leaves_another_untouched(auth_client, session):
    from app.routers.users import _USER_OWNED

    _register(auth_client, "pair-admin@example.com")
    alice = _register(auth_client, "pair-alice@example.com")
    bob = _register(auth_client, "pair-bob@example.com")
    _seed_every_user_table(session, alice["user"]["id"])
    _seed_every_user_table(session, bob["user"]["id"])

    assert auth_client.post(
        "/auth/me/delete", headers=_auth(alice),
        json={"password": STRONG_PASSWORD, "confirm": "DELETE MY ACCOUNT"}).status_code == 200

    session.expire_all()
    for label, model in _USER_OWNED:
        kept = session.query(model).filter(model.user_id == bob["user"]["id"]).count()
        assert kept == 1, f"deleting alice removed bob's {label}"
    assert auth_client.get("/auth/me", headers=_auth(bob)).status_code == 200
