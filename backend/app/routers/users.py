"""Account endpoints: register, sign in, refresh, profile, password change and
reset.

Behaviours worth calling out because they look like bugs but are deliberate:

* **Sign-in failures never say whether the address exists.** Distinguishing
  "no such account" from "wrong password" hands an attacker a free account
  enumeration oracle. `/password/forgot` returns the same generic response
  for the same reason.
* **Registration is closed once an account exists**, unless `LEGEND_ALLOW_SIGNUP`
  is on. A self-hosted terminal exposed to the internet with open registration
  is a public service nobody intended to run.
* **A password-reset token is made single-use via `User.password_reset_requested_at`,
  not a revocation table.** The token's own signature and expiry prove it was
  issued by this server and hasn't timed out, but not that it hasn't already
  been spent — `/password/reset` additionally requires the stored timestamp to
  match the token's `iat` exactly, and clears it on use. A newer request
  overwrites the timestamp, which is also why only the most recent reset link
  for an account ever works.
* **Email verification has no single-use trick, unlike password reset.**
  Verifying twice does nothing dangerous, so `/email/verify` just checks the
  token's signature and expiry and stamps `email_verified_at` — there's no
  state to invalidate on reuse. It also doesn't gate login or any feature;
  it exists so a published, multi-user instance knows an address is real,
  not to lock unverified accounts out.
"""
from __future__ import annotations

import datetime as dt
import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app import mfa
from app.config import settings
from app.dependencies import (
    MAX_FAILED_LOGINS,
    account_is_locked,
    auth_required,
    current_admin,
    current_user,
    record_login_failure,
    record_login_success,
    secret_key,
)
from app.mailer import send_mail
from app.ratelimit import (
    client_key,
    email_verify_limiter,
    login_limiter,
    mfa_limiter,
    password_reset_limiter,
)
from app.security import (
    ACCESS_TOKEN_TTL,
    MFA_TOKEN_TTL,
    TokenError,
    create_token,
    decode_token,
    hash_password,
    password_problems,
    revocation_cutoff,
    utc_epoch,
    verify_password,
)
from database.db import get_session
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
    User,
    WatchlistItem,
)

logger = logging.getLogger("legend.users")
router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1)
    display_name: str = ""


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=1)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=1)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=1)


class VerifyEmailRequest(BaseModel):
    token: str = Field(..., min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: dict


class MfaChallengeResponse(BaseModel):
    """What /auth/login returns instead of tokens when MFA is enabled.

    `mfa_token` is scoped to `type=mfa` and is rejected by every endpoint that
    requires an access token — it proves the password was correct, nothing
    more, and expires in 5 minutes.
    """
    mfa_required: bool = True
    mfa_token: str
    expires_in: int


class MfaVerifyRequest(BaseModel):
    mfa_token: str = Field(..., min_length=1)
    code: str = Field(..., min_length=1)


class MfaConfirmRequest(BaseModel):
    code: str = Field(..., min_length=1)


class MfaDisableRequest(BaseModel):
    password: str = Field(..., min_length=1)


class AdminMfaResetRequest(BaseModel):
    email: EmailStr


class AdminSetActiveRequest(BaseModel):
    active: bool


def _user_payload(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "is_admin": user.is_admin,
        "mfa_enabled": user.mfa_enabled,
        "email_verified": user.email_verified_at is not None,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }


def _send_verification_email(user: User) -> None:
    """Best-effort — a registration or resend must not fail because the mail
    step did. Same fire-and-forget posture as the password-reset link."""
    token = create_token(user.id, secret_key(), "email_verify")
    link = f"{settings.frontend_url.rstrip('/')}/?verify_token={token}"
    body = (
        f"Confirm this is your email address to finish setting up your Legend Trade account:\n\n{link}\n\n"
        "This link is valid for 24 hours. If you didn't create this account, ignore it."
    )
    if not send_mail(user.email, "Verify your Legend Trade email address", body):
        logger.info("verification link for user %s logged above (no SMTP configured)", user.id)


def _issue_tokens(user: User) -> TokenResponse:
    key = secret_key()
    return TokenResponse(
        access_token=create_token(user.id, key, "access"),
        refresh_token=create_token(user.id, key, "refresh"),
        expires_in=ACCESS_TOKEN_TTL,
        user=_user_payload(user),
    )


@router.get("/status")
def auth_status(session: Session = Depends(get_session)):
    """Whether authentication is on, and whether an account exists yet.

    The UI calls this before rendering, so it knows whether to show a login
    screen, a first-run setup screen, or go straight to the terminal.
    """
    user_count = session.query(User).filter(User.email != "local@localhost").count()
    return {
        "auth_required": auth_required(),
        "signup_allowed": settings.allow_signup or user_count == 0,
        "has_accounts": user_count > 0,
        "mode": settings.auth_required,
        "note": (
            "Authentication is enforced because the server is not bound to loopback."
            if auth_required() and settings.auth_required.lower() == "auto"
            else "Authentication is disabled for local use; bind to a non-loopback "
                 "address or set LEGEND_AUTH_REQUIRED=always to enforce it."
            if not auth_required()
            else "Authentication is enforced by configuration."
        ),
    }


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(request: RegisterRequest, http_request: Request,
             session: Session = Depends(get_session)):
    """Create an account.

    The first account is always permitted so a fresh install can be set up;
    after that, registration requires `LEGEND_ALLOW_SIGNUP=true`.
    """
    login_limiter.enforce(http_request)

    real_users = session.query(User).filter(User.email != "local@localhost").count()
    if real_users > 0 and not settings.allow_signup:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Registration is closed. Set LEGEND_ALLOW_SIGNUP=true to allow additional "
                "accounts on this instance."
            ),
        )

    email = request.email.lower().strip()
    if session.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="An account with that email already exists.")

    problems = password_problems(request.password)
    if problems:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=" ".join(problems))

    user = User(
        email=email,
        display_name=(request.display_name or email.split("@")[0]).strip(),
        password_hash=hash_password(request.password),
        webhook_token=secrets.token_urlsafe(32),
        # The first account administers the instance.
        is_admin=real_users == 0,
    )
    session.add(user)
    session.commit()
    session.refresh(user)

    logger.info("registered account %s (admin=%s)", user.email, user.is_admin)
    try:
        _send_verification_email(user)
    except Exception as exc:  # noqa: BLE001 - verification email is an enhancement, not a requirement
        logger.info("verification email failed for %s: %s", user.email, exc)
    return _issue_tokens(user)


@router.post("/login")
def login(request: LoginRequest, http_request: Request,
          session: Session = Depends(get_session)):
    """Sign in. Returns tokens directly, or an `mfa_token` when the account
    has MFA enabled — the login is not complete until that is exchanged via
    `/auth/mfa/verify`."""
    login_limiter.enforce(http_request)

    email = request.email.lower().strip()
    user = session.query(User).filter(User.email == email).first()

    # One message for every failure mode, so a caller learns nothing about
    # which addresses are registered.
    generic_failure = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Incorrect email or password.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if user is None:
        # Hash anyway so a missing account does not return measurably faster
        # than a wrong password.
        verify_password(request.password, hash_password("timing-equalizer"))
        raise generic_failure

    if account_is_locked(user):
        remaining = int((user.locked_until - dt.datetime.utcnow()).total_seconds() / 60) + 1
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Account locked after {MAX_FAILED_LOGINS} failed attempts. "
                f"Try again in {remaining} minute(s)."
            ),
        )

    if not user.is_active:
        raise generic_failure

    if not verify_password(request.password, user.password_hash):
        record_login_failure(session, user)
        raise generic_failure

    if user.mfa_enabled:
        # Correct password, but not a completed login yet. `record_login_success`
        # (which resets the failure counter and stamps last_login_at) waits for
        # /auth/mfa/verify — otherwise a stolen password alone would show up as
        # a successful sign-in in the account's own history.
        token = create_token(user.id, secret_key(), "mfa")
        return MfaChallengeResponse(mfa_token=token, expires_in=MFA_TOKEN_TTL)

    record_login_success(session, user)
    return _issue_tokens(user)


def _consume_recovery_code(session: Session, user: User, code: str) -> bool:
    """Try `code` against this user's unused recovery codes, consuming it on a match.

    A linear scan over (at most 10) hashed codes, each compared with the same
    constant-time scrypt verification a password gets — low cardinality, so
    the cost is negligible, and reusing `verify_password` means a recovery
    code gets exactly the same non-recoverable-at-rest treatment as one.
    """
    candidates = (
        session.query(MfaRecoveryCode)
        .filter(MfaRecoveryCode.user_id == user.id, MfaRecoveryCode.used_at.is_(None))
        .all()
    )
    for candidate in candidates:
        if verify_password(code, candidate.code_hash):
            candidate.used_at = dt.datetime.utcnow()
            session.commit()
            return True
    return False


@router.post("/mfa/verify", response_model=TokenResponse)
def verify_mfa(request: MfaVerifyRequest, http_request: Request,
              session: Session = Depends(get_session)):
    """Exchange an `mfa_token` plus a TOTP or recovery code for real tokens."""
    mfa_limiter.enforce(http_request)

    try:
        payload = decode_token(request.mfa_token, secret_key(), expected_type="mfa")
    except TokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    generic_failure = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect code.")

    user = session.get(User, payload.subject)
    if user is None or not user.is_active or not user.mfa_enabled or not user.mfa_secret:
        raise generic_failure

    if account_is_locked(user):
        remaining = int((user.locked_until - dt.datetime.utcnow()).total_seconds() / 60) + 1
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Account locked after {MAX_FAILED_LOGINS} failed attempts. "
                f"Try again in {remaining} minute(s)."
            ),
        )

    if mfa.verify_totp(user.mfa_secret, request.code) or _consume_recovery_code(session, user, request.code):
        record_login_success(session, user)
        return _issue_tokens(user)

    record_login_failure(session, user)
    raise generic_failure


@router.post("/mfa/setup")
def setup_mfa(user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Generate a pending TOTP secret. MFA is not active until /mfa/confirm
    verifies a real code against it."""
    if user.mfa_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MFA is already enabled. Disable it before generating a new secret.",
        )

    secret = mfa.generate_secret()
    user.mfa_secret = secret
    session.commit()

    return {
        "secret": secret,
        "provisioning_uri": mfa.provisioning_uri(secret, user.email),
        "note": (
            "Add this to an authenticator app (scan the URI or enter the secret manually), "
            "then call /auth/mfa/confirm with a generated code to enable MFA."
        ),
    }


@router.post("/mfa/confirm")
def confirm_mfa(request: MfaConfirmRequest, user: User = Depends(current_user),
                session: Session = Depends(get_session)):
    """Verify a code against the pending secret and turn MFA on.

    Returns ten recovery codes in plaintext exactly once — they are stored
    hashed, like a password, and cannot be retrieved again after this response.
    """
    if not user.mfa_secret:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Call /auth/mfa/setup first.")
    if user.mfa_enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="MFA is already enabled.")
    if not mfa.verify_totp(user.mfa_secret, request.code):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Incorrect code.")

    user.mfa_enabled = True
    user.mfa_confirmed_at = dt.datetime.utcnow()

    # Clear any codes left over from a previous enable/disable cycle first.
    session.query(MfaRecoveryCode).filter(MfaRecoveryCode.user_id == user.id).delete()
    codes = mfa.generate_recovery_codes()
    for code in codes:
        session.add(MfaRecoveryCode(user_id=user.id, code_hash=hash_password(code)))
    session.commit()

    logger.info("MFA enabled for account %s", user.email)
    return {
        "enabled": True,
        "recovery_codes": codes,
        "note": "Save these recovery codes now — they will not be shown again. Each works once.",
    }


@router.post("/mfa/disable")
def disable_mfa(request: MfaDisableRequest, user: User = Depends(current_user),
                session: Session = Depends(get_session)):
    """Turn MFA off. Requires the current password — disabling a second
    factor is a security-lowering action and re-confirms the first one."""
    if not verify_password(request.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Incorrect password.")

    user.mfa_enabled = False
    user.mfa_secret = None
    user.mfa_confirmed_at = None
    session.query(MfaRecoveryCode).filter(MfaRecoveryCode.user_id == user.id).delete()
    session.commit()

    logger.info("MFA disabled for account %s", user.email)
    return {"disabled": True}


def _admin_user_payload(user: User) -> dict:
    """The account list an operator needs, and nothing more.

    Deliberately excludes `password_hash`, `mfa_secret` and `webhook_token`:
    an admin has no legitimate use for another account's secrets, and an
    endpoint that returns them turns one compromised admin session into a
    compromise of every account on the instance.
    """
    return {
        **_user_payload(user),
        "is_active": user.is_active,
        "locked": account_is_locked(user),
        "locked_until": user.locked_until.isoformat() if user.locked_until else None,
        "failed_login_count": user.failed_login_count or 0,
    }


def _active_admin_count(session: Session, excluding: int | None = None) -> int:
    query = session.query(User).filter(User.is_admin.is_(True), User.is_active.is_(True))
    if excluding is not None:
        query = query.filter(User.id != excluding)
    return query.count()


@router.get("/admin/users")
def admin_list_users(admin: User = Depends(current_admin),
                     session: Session = Depends(get_session),
                     query: str = "", limit: int = 100):
    """Every account on this instance, for the operator's admin screen.

    Secrets are never included — see `_admin_user_payload`.
    """
    users = session.query(User)
    if query:
        like = f"%{query.strip()}%"
        users = users.filter(User.email.ilike(like))
    rows = users.order_by(User.id).limit(max(1, min(limit, 500))).all()
    return {"users": [_admin_user_payload(u) for u in rows], "count": len(rows)}


@router.post("/admin/users/{user_id}/active")
def admin_set_active(user_id: int, request: AdminSetActiveRequest,
                     admin: User = Depends(current_admin),
                     session: Session = Depends(get_session)):
    """Disable or re-enable an account.

    `is_active` is already enforced on every token (`resolve_user_from_token`
    refuses a disabled account), so this takes effect on the next request
    rather than only at next sign-in.

    **An admin cannot deactivate themselves.** The obvious misclick, and one
    with no in-app recovery — it would mean editing the database by hand.

    There is a second, *currently unreachable*, guard below against
    deactivating the last active administrator. It cannot fire today: any
    caller that gets past the self-check is by definition an active admin who
    is not the target, so at least one active admin always remains. It is kept
    deliberately, and documented as unreachable rather than presented as
    working protection, because the moment an admin **demotion** or
    **promotion** endpoint is added the invariant stops being free — and a
    guard written after the fact is one written during the incident.
    `_active_admin_count` is unit-tested directly for that reason.
    """
    target = session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such account.")

    if not request.active:
        if target.id == admin.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You cannot deactivate your own account.",
            )
        # Unreachable today — see the docstring. Kept so the invariant is
        # already enforced if admin promotion/demotion is ever added.
        if target.is_admin and _active_admin_count(session, excluding=target.id) == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "This is the last active administrator. Promote another admin "
                    "before deactivating this one, or the instance becomes unmanageable."
                ),
            )

    target.is_active = request.active
    session.commit()

    logger.warning("account %s %s by admin %s", target.email,
                   "reactivated" if request.active else "deactivated", admin.email)
    return {"email": target.email, "is_active": target.is_active}


@router.post("/admin/users/{user_id}/unlock")
def admin_unlock_user(user_id: int, admin: User = Depends(current_admin),
                      session: Session = Depends(get_session)):
    """Clear a failed-login lockout immediately.

    The lockout expires on its own after `LOCKOUT_MINUTES`, so this is a
    convenience for someone who needs back in now — not a recovery from a
    permanent state.
    """
    target = session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such account.")

    was_locked = account_is_locked(target)
    target.locked_until = None
    target.failed_login_count = 0
    session.commit()

    logger.info("lockout cleared for %s by admin %s (was_locked=%s)",
                target.email, admin.email, was_locked)
    return {"email": target.email, "was_locked": was_locked, "locked": False}


@router.post("/admin/mfa/reset")
def admin_reset_mfa(request: AdminMfaResetRequest, admin: User = Depends(current_admin),
                    session: Session = Depends(get_session)):
    """Recover an account locked out of its own MFA — lost the authenticator
    *and* every recovery code — a dead end self-service password reset alone
    cannot reach, since a reset only proves the email is still yours, not
    that you still have the second factor.

    Deliberately admin-only rather than folded into password reset itself:
    letting password reset silently disable MFA would mean anyone who can
    intercept a reset email can also strip the second factor from an account
    they don't otherwise control — precisely the attack MFA exists to stop.
    This requires an authenticated admin to act instead, same reasoning as
    the admin-only gate on broker-connected execution modes.
    """
    user = session.query(User).filter(User.email == request.email).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No account with that email.")

    was_enabled = user.mfa_enabled
    user.mfa_enabled = False
    user.mfa_secret = None
    user.mfa_confirmed_at = None
    session.query(MfaRecoveryCode).filter(MfaRecoveryCode.user_id == user.id).delete()
    session.commit()

    logger.warning("MFA reset for account %s by admin %s (was_enabled=%s)",
                   user.email, admin.email, was_enabled)
    return {"reset": True, "email": user.email, "mfa_was_enabled": was_enabled}


@router.get("/mfa/status")
def mfa_status(user: User = Depends(current_user), session: Session = Depends(get_session)):
    remaining = 0
    if user.mfa_enabled:
        remaining = (
            session.query(MfaRecoveryCode)
            .filter(MfaRecoveryCode.user_id == user.id, MfaRecoveryCode.used_at.is_(None))
            .count()
        )
    return {
        "enabled": user.mfa_enabled,
        "confirmed_at": user.mfa_confirmed_at.isoformat() if user.mfa_confirmed_at else None,
        "recovery_codes_remaining": remaining,
    }


@router.post("/refresh", response_model=TokenResponse)
def refresh(request: RefreshRequest, session: Session = Depends(get_session)):
    """Exchange a refresh token for a new pair.

    Both tokens are reissued, so a stolen refresh token has a bounded life
    rather than granting indefinite access.
    """
    try:
        payload = decode_token(request.refresh_token, secret_key(), expected_type="refresh")
    except TokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    user = session.get(User, payload.subject)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="This account is no longer active.")

    if user.tokens_valid_from and payload.issued_at < utc_epoch(user.tokens_valid_from):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="This session was ended. Sign in again.")

    return _issue_tokens(user)


@router.get("/me")
def me(user: User = Depends(current_user)):
    return _user_payload(user)


@router.post("/password")
def change_password(request: PasswordChangeRequest, user: User = Depends(current_user),
                    session: Session = Depends(get_session)):
    """Change the password and invalidate every existing session."""
    if not verify_password(request.current_password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Current password is incorrect.")

    problems = password_problems(request.new_password)
    if problems:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=" ".join(problems))

    user.password_hash = hash_password(request.new_password)
    # Everything issued before now stops working — a password change that left
    # existing sessions alive would not actually lock anyone out.
    user.tokens_valid_from = revocation_cutoff()
    session.commit()

    return {
        "changed": True,
        "note": "All existing sessions were signed out. Sign in again with the new password.",
    }


@router.post("/password/forgot")
def forgot_password(request: ForgotPasswordRequest, http_request: Request,
                    session: Session = Depends(get_session)):
    """Request a password reset link.

    Always returns the same response whether or not the address is
    registered — the same enumeration-oracle concern `login` guards against,
    doubly so here since this endpoint needs no credential at all to call.
    """
    password_reset_limiter.enforce(http_request)

    email = request.email.lower().strip()
    user = session.query(User).filter(User.email == email).first()

    if user is not None and user.is_active:
        token = create_token(user.id, secret_key(), "password_reset")
        # Re-decode rather than guess: stamping the exact `iat` the token
        # carries is what lets /auth/password/reset later prove this is the
        # one outstanding link for the account, not merely a validly signed one.
        payload = decode_token(token, secret_key(), expected_type="password_reset")
        user.password_reset_requested_at = dt.datetime.utcfromtimestamp(payload.issued_at)
        session.commit()

        link = f"{settings.frontend_url.rstrip('/')}/?reset_token={token}"
        body = (
            f"Use this link to reset your Legend Trade password (expires in 15 minutes):\n\n{link}\n\n"
            "If you did not request this, you can ignore this message."
        )
        if not send_mail(user.email, "Reset your Legend Trade password", body):
            logger.info("password reset requested for user %s; link logged above (no SMTP configured)", user.id)

    return {
        "requested": True,
        "note": "If that address has an account, a reset link was sent.",
    }


@router.post("/password/reset")
def reset_password(request: ResetPasswordRequest, http_request: Request,
                   session: Session = Depends(get_session)):
    """Complete a password reset using the token from /auth/password/forgot."""
    password_reset_limiter.enforce(http_request)

    try:
        payload = decode_token(request.token, secret_key(), expected_type="password_reset")
    except TokenError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    user = session.get(User, payload.subject)

    # The token must match this account's single outstanding reset request,
    # not merely carry a valid signature — otherwise a link would stay usable
    # for its whole 15-minute window even after being used once, or after a
    # newer request superseded it.
    if (
        user is None
        or user.password_reset_requested_at is None
        or utc_epoch(user.password_reset_requested_at) != payload.issued_at
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This reset link is invalid or has already been used.",
        )

    problems = password_problems(request.new_password)
    if problems:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=" ".join(problems))

    user.password_hash = hash_password(request.new_password)
    user.password_reset_requested_at = None
    # Same reasoning as a manual password change: every existing session
    # should stop working, and a forgotten password often means an account
    # that got locked out along the way, so lift that too.
    user.tokens_valid_from = revocation_cutoff()
    user.failed_login_count = 0
    user.locked_until = None
    session.commit()

    return {
        "reset": True,
        "note": "Password changed and all existing sessions were signed out.",
    }


@router.post("/email/verify")
def verify_email(request: VerifyEmailRequest, session: Session = Depends(get_session)):
    """Confirm an email address from the link sent at registration (or resend).

    No auth required — the token itself, not a session, proves the click came
    from the mailed link. Idempotent: verifying an already-verified account
    just succeeds again rather than erroring.
    """
    try:
        payload = decode_token(request.token, secret_key(), expected_type="email_verify")
    except TokenError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    user = session.get(User, payload.subject)
    if user is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This link is invalid.")

    if user.email_verified_at is None:
        user.email_verified_at = dt.datetime.utcnow()
        session.commit()

    return {"verified": True, "email": user.email}


@router.post("/email/resend")
def resend_verification(http_request: Request, user: User = Depends(current_user),
                        session: Session = Depends(get_session)):
    """Send a fresh verification link to the signed-in account's own email."""
    email_verify_limiter.enforce(http_request, key=f"verify-{user.id}")

    if user.email_verified_at is not None:
        return {"sent": False, "note": "This email is already verified."}

    _send_verification_email(user)
    return {"sent": True, "note": f"A verification link was sent to {user.email}."}


@router.post("/logout")
def logout(everywhere: bool = False, user: User = Depends(current_user),
           session: Session = Depends(get_session)):
    """Sign out.

    By default the client simply discards its tokens. `everywhere=true` bumps
    the server-side cutoff so tokens already issued stop working — the only
    version of logout that means anything if a token has leaked.
    """
    if everywhere:
        user.tokens_valid_from = revocation_cutoff()
        session.commit()
        return {"logged_out": True, "all_sessions": True}

    return {
        "logged_out": True,
        "all_sessions": False,
        "note": (
            "Discard the tokens on the client. Existing access tokens stay valid until they "
            "expire; use everywhere=true to revoke them immediately."
        ),
    }


@router.get("/webhook-url")
def webhook_url(user: User = Depends(current_user)):
    """This account's secret TradingView webhook URL."""
    return {
        "path": f"/trading/webhook/tradingview/{user.webhook_token}",
        "note": (
            "TradingView cannot send custom headers, so the secret is in the path. Treat this "
            "URL as a credential, and rotate it if it leaks."
        ),
    }


@router.post("/webhook-url/rotate")
def rotate_webhook_url(user: User = Depends(current_user),
                       session: Session = Depends(get_session)):
    user.webhook_token = secrets.token_urlsafe(32)
    session.commit()
    return {
        "path": f"/trading/webhook/tradingview/{user.webhook_token}",
        "rotated": True,
        "note": "Update the URL in your TradingView alerts — the previous one no longer works.",
    }


# ---------------------------------------------------------------------------
# Subject access and erasure
#
# PRIVACY.md previously disclosed that neither of these existed and that an
# access or erasure request meant the operator editing the database by hand.
# That disclosure was accurate and is exactly why it needed closing: a policy
# that documents a manual process is a policy that fails the first time two
# requests arrive in the same week.
#
# Both endpoints derive the table list from `_USER_OWNED` rather than hard-coding
# a sequence of queries, so a model added later with a `user_id` is a one-line
# change here instead of a silent omission — the failure mode being data that
# survives a deletion request, which is the worst possible bug in this area.
# ---------------------------------------------------------------------------

# Every table keyed to a user, with the label used in the export. Ordered so a
# deletion removes children before the parent row.
_USER_OWNED = [
    ("mfa_recovery_codes", MfaRecoveryCode),
    ("analyses", AnalysisRecord),
    ("strategies", StrategyRecord),
    ("backtests", BacktestRecord),
    ("paper_positions", PaperPosition),
    ("alerts", PriceAlert),
    ("watchlist", WatchlistItem),
    ("webhooks", TradingWebhook),
    ("feedback", AnalysisFeedback),
    ("orders", OrderRecord),
    ("execution_state", ExecutionState),
]


# Column names whose *value* is never exported, whatever table they appear in.
#
# Dumping a row generically is what makes the export survive a schema change —
# but it also means a credential column added to any user-owned table would be
# swept into the response without anyone deciding it should be. So the exclusion
# is by column name and applies everywhere, rather than being a special case
# written next to the one table that needs it today (`mfa_recovery_codes`).
#
# The key is kept, with a marker for a value: hiding the column entirely would
# misrepresent what is stored, and the point of a subject access request is to
# tell you what is held, not to hand you the credentials themselves.
_REDACTED_COLUMNS = frozenset({"code_hash", "password_hash", "mfa_secret", "secret"})
_REDACTED = "[withheld — see not_included]"


def _row_to_dict(row) -> dict:
    """Serialise a model row without needing a schema per table.

    Datetimes become ISO strings so the result is JSON-encodable; everything
    else is already a scalar. Columns are read from the mapper rather than from
    `__dict__`, which would also pick up SQLAlchemy's internal state.
    """
    out = {}
    for column in row.__table__.columns:
        if column.name in _REDACTED_COLUMNS:
            out[column.name] = _REDACTED
            continue
        value = getattr(row, column.name)
        out[column.name] = value.isoformat() if isinstance(value, dt.datetime) else value
    return out


class DeleteAccountRequest(BaseModel):
    password: str
    # Typing the phrase is the same friction the live-trading gate uses. A
    # checkbox gets clicked reflexively; this does not.
    confirm: str = Field(..., description='Must be exactly "DELETE MY ACCOUNT"')


DELETE_CONFIRMATION_PHRASE = "DELETE MY ACCOUNT"


@router.get("/me/export")
def export_my_data(user: User = Depends(current_user),
                   session: Session = Depends(get_session)):
    """Everything this service holds about the authenticated account.

    Credentials are deliberately excluded. A password hash and a TOTP secret are
    technically "your data", but returning them turns one stolen access token
    into a permanent offline attack on the password and a working second factor.
    The export says which credentials exist without disclosing their values,
    which is what a subject access request actually needs.
    """
    account = {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "is_active": user.is_active,
        "is_admin": user.is_admin,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        "email_verified_at": (
            user.email_verified_at.isoformat() if user.email_verified_at else None
        ),
        "mfa_enabled": user.mfa_enabled,
        "failed_login_count": user.failed_login_count,
    }

    data = {}
    for label, model in _USER_OWNED:
        rows = session.query(model).filter(model.user_id == user.id).all()
        data[label] = [_row_to_dict(row) for row in rows]

    return {
        "exported_at": dt.datetime.utcnow().isoformat(),
        "account": account,
        "data": data,
        "counts": {label: len(rows) for label, rows in data.items()},
        "not_included": (
            "Password hash, two-factor secret and recovery-code hashes are excluded on "
            "purpose: disclosing them would convert a stolen session into a lasting "
            "compromise. Their existence is reported above."
        ),
    }


@router.post("/me/delete")
def delete_my_account(request: DeleteAccountRequest,
                      http_request: Request,
                      user: User = Depends(current_user),
                      session: Session = Depends(get_session)):
    """Erase this account and everything keyed to it. Irreversible.

    POST rather than DELETE because it carries a body: a password and a typed
    phrase. Some proxies and client libraries drop the body of a DELETE, and a
    deletion that silently loses its confirmation is not a safe thing to ship.

    Re-authentication is required even though the caller already holds a valid
    token — an unattended session should not be enough to destroy an account.
    """
    login_limiter.check(client_key(http_request))

    if request.confirm != DELETE_CONFIRMATION_PHRASE:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f'Type "{DELETE_CONFIRMATION_PHRASE}" exactly to confirm.',
        )

    if not verify_password(request.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Password is incorrect.")

    # Losing the last administrator would leave an instance nobody can manage —
    # no user administration, no MFA recovery, no metrics. Unlike the
    # deactivation guard this one is genuinely reachable, because the caller is
    # deleting themselves rather than someone else.
    if user.is_admin and _active_admin_count(session, excluding=user.id) == 0:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This is the only administrator. Promote another account first, or the "
            "instance would be left with nobody able to administer it.",
        )

    removed = {}
    for label, model in _USER_OWNED:
        removed[label] = (
            session.query(model).filter(model.user_id == user.id).delete(synchronize_session=False)
        )

    # Read both off the instance *before* the delete: once the transaction
    # commits, the row is gone and SQLAlchemy will refuse to reload the expired
    # attributes off a deleted object.
    user_id, email = user.id, user.email
    session.delete(user)
    session.commit()

    logger.info("account deleted: id=%s rows=%s", user_id, removed)
    return {
        "deleted": True,
        "email": email,
        "rows_removed": removed,
        "note": "This account and its data are gone. Server logs may retain request "
                "metadata for the retention period stated in the privacy policy.",
    }
