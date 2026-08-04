"""Authentication dependencies shared by every protected route.

The central rule: **the user id comes from the verified token, never from the
request body or a query parameter.** Every trading query is filtered by that
id, so no request can reach another account's data by guessing an integer.

Auth enforcement has three modes, chosen by `LEGEND_AUTH_REQUIRED`:

* `always`   — every protected route needs a token.
* `never`    — no route needs one (single-user desktop use).
* `auto`     — the default: required unless the server is bound to loopback.

`auto` exists because this is both a desktop app and a deployable service.
Forcing a login screen on a localhost desktop app is friction with no security
benefit, while allowing anonymous access on a public interface is a hole. The
bind address distinguishes the two cases better than a flag someone will forget
to flip, and the choice is logged at startup either way so it is never a
surprise.
"""
from __future__ import annotations

import datetime as dt
import logging

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import settings
from app.security import (
    TokenError,
    decode_token,
    is_loopback,
    resolve_secret_key,
    utc_epoch,
)
from database.db import get_session
from database.models import User

logger = logging.getLogger("legend.auth")

# auto_error=False so a missing header reaches our own handler, which can decide
# whether anonymous access is permitted rather than always returning 403.
bearer_scheme = HTTPBearer(auto_error=False)

# The single-user identity used when authentication is disabled.
LOCAL_USER_EMAIL = "local@localhost"


def auth_required() -> bool:
    """Whether a token is needed, resolved from config and the bind address."""
    mode = (settings.auth_required or "auto").strip().lower()
    if mode in ("always", "true", "1", "yes"):
        return True
    if mode in ("never", "false", "0", "no"):
        return False
    return not is_loopback(settings.host)


def secret_key() -> str:
    return resolve_secret_key(settings.secret_key)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_or_create_local_user(session: Session) -> User:
    """The implicit account used when auth is disabled.

    Everything is still written against a real user row, so turning auth on
    later does not orphan data created before it.
    """
    import secrets

    from app.security import hash_password

    user = session.query(User).filter(User.email == LOCAL_USER_EMAIL).first()
    if user:
        return user

    user = User(
        email=LOCAL_USER_EMAIL,
        display_name="Local User",
        # Unusable by design: this account exists to own rows, not to log in.
        # A random secret means it cannot be signed into even if auth is
        # switched on later without the row being cleaned up.
        password_hash=hash_password(secrets.token_urlsafe(48)),
        webhook_token=secrets.token_urlsafe(32),
        is_admin=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    logger.info("created the implicit local account for unauthenticated use")
    return user


def resolve_user_from_token(token: str, session: Session) -> User:
    """Verify an access token and load its user, or raise 401."""
    try:
        payload = decode_token(token, secret_key(), expected_type="access")
    except TokenError as exc:
        raise _unauthorized(str(exc)) from exc

    user = session.get(User, payload.subject)
    if user is None:
        raise _unauthorized("The account for this token no longer exists.")
    if not user.is_active:
        raise _unauthorized("This account is disabled.")

    # Tokens issued before the cutoff are refused. This is what makes a password
    # change or "sign out everywhere" revoke tokens that are still unexpired.
    if user.tokens_valid_from and payload.issued_at < utc_epoch(user.tokens_valid_from):
        raise _unauthorized("This session was ended. Sign in again.")

    return user


def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: Session = Depends(get_session),
) -> User:
    """The authenticated user for this request.

    Returns the implicit local account when authentication is disabled, so
    downstream code always has a real user to scope queries by and never has to
    handle a `None` user.
    """
    if not auth_required():
        if credentials and credentials.credentials:
            # A token was supplied even though it isn't required — honour it, so
            # a browser session still maps to the right account on localhost.
            try:
                return resolve_user_from_token(credentials.credentials, session)
            except HTTPException:
                pass
        return get_or_create_local_user(session)

    if credentials is None or not credentials.credentials:
        raise _unauthorized("Not authenticated. Send an Authorization: Bearer <token> header.")

    return resolve_user_from_token(credentials.credentials, session)


def current_admin(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required.")
    return user


async def websocket_user(websocket, session: Session) -> User | None:
    """Authenticate a WebSocket connection.

    Browsers cannot set headers on a WebSocket handshake, so the token travels
    as a query parameter. That places it in server logs and referrers, which is
    why access tokens are short-lived (30 minutes) and refresh tokens are never
    accepted here.
    """
    if not auth_required():
        return get_or_create_local_user(session)

    token = websocket.query_params.get("token")
    if not token:
        return None
    try:
        return resolve_user_from_token(token, session)
    except HTTPException:
        return None


def record_login_success(session: Session, user: User) -> None:
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = dt.datetime.utcnow()
    session.commit()


# After this many consecutive failures the account is locked for a while. This
# blunts online password guessing even when the attacker rotates source IPs,
# which is what makes it a useful complement to per-IP rate limiting.
MAX_FAILED_LOGINS = 8
LOCKOUT_MINUTES = 15


def record_login_failure(session: Session, user: User) -> None:
    user.failed_login_count = (user.failed_login_count or 0) + 1
    if user.failed_login_count >= MAX_FAILED_LOGINS:
        user.locked_until = dt.datetime.utcnow() + dt.timedelta(minutes=LOCKOUT_MINUTES)
        logger.warning("account %s locked after %d failed logins", user.email, user.failed_login_count)
    session.commit()


def account_is_locked(user: User) -> bool:
    return bool(user.locked_until and user.locked_until > dt.datetime.utcnow())
