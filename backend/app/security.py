"""Authentication primitives: password hashing, token signing, secret management.

**On implementing JWT here rather than importing PyJWT.** The only cryptography
involved in an HS256 token is HMAC-SHA256, which is in the standard library and
is not something this module reimplements. What is implemented here is the
encoding and — more importantly — the *validation*, which is where JWT
libraries earn their keep. The specific attacks that validation must stop are
handled explicitly and named in the code below:

* **Algorithm confusion / `alg: none`.** The algorithm is pinned to HS256 by
  the verifier. The header's own `alg` claim is never trusted to select the
  verification method; a token declaring anything else is rejected outright.
* **Timing attacks on signature comparison.** `hmac.compare_digest` only.
* **Expiry bypass.** `exp` is required. A token without one is rejected rather
  than treated as eternal.
* **Token-type confusion.** A refresh token cannot be used as an access token,
  because the type is signed into the payload and checked on use.

Passwords use `hashlib.scrypt`, which is memory-hard and in the standard
library. Parameters follow current OWASP guidance (n=2^15, r=8, p=1); each hash
carries its own salt and parameters so they can be raised later without
invalidating existing passwords.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("legend.security")

# --- password hashing --------------------------------------------------------

# OWASP-recommended scrypt parameters. n is the CPU/memory cost and dominates:
# 2^15 with r=8 needs ~32 MB per hash, which is deliberately expensive for an
# attacker running many guesses in parallel and unnoticeable for one login.
SCRYPT_N = 2 ** 15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SALT_BYTES = 16


def _maxmem(n: int, r: int) -> int:
    """Memory ceiling to hand OpenSSL for these scrypt parameters.

    scrypt needs about 128 * n * r bytes. OpenSSL rejects a limit that merely
    equals that figure, so allow headroom for its internal overhead — passing
    the bare requirement fails with "memory limit exceeded".
    """
    return 128 * n * r * 2

MIN_PASSWORD_LENGTH = 10


def hash_password(password: str) -> str:
    """Hash a password. Returns `scrypt$n$r$p$salt$hash`, all base64url."""
    if not isinstance(password, str) or not password:
        raise ValueError("Password must be a non-empty string.")

    salt = secrets.token_bytes(SALT_BYTES)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_DKLEN,
        maxmem=_maxmem(SCRYPT_N, SCRYPT_R),
    )
    return "$".join([
        "scrypt", str(SCRYPT_N), str(SCRYPT_R), str(SCRYPT_P),
        _b64encode(salt), _b64encode(derived),
    ])


def verify_password(password: str, stored: str) -> bool:
    """Check a password against a stored hash, in constant time.

    Parameters are read back from the stored string rather than assumed, so
    hashes created under older settings keep working after the cost is raised.
    """
    if not password or not stored:
        return False
    try:
        scheme, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = _b64decode(salt_b64)
        expected = _b64decode(hash_b64)
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n), r=int(r), p=int(p), dklen=len(expected),
            maxmem=_maxmem(int(n), int(r)),
        )
    except (ValueError, TypeError, MemoryError):
        # A malformed hash must fail closed, never raise into the request path.
        return False

    return hmac.compare_digest(derived, expected)


def password_problems(password: str) -> list[str]:
    """Validate password strength. Returns an empty list when acceptable.

    Length is the requirement that actually matters; composition rules mostly
    push people toward predictable substitutions. A long passphrase passes.
    """
    problems: list[str] = []
    if len(password or "") < MIN_PASSWORD_LENGTH:
        problems.append(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if password and password.lower() in _COMMON_PASSWORDS:
        problems.append("That password is among the most commonly used ones. Choose another.")
    if password and len(set(password)) < 5:
        problems.append("Password must contain at least 5 distinct characters.")
    return problems


_COMMON_PASSWORDS = {
    "password", "password1", "password123", "12345678", "123456789", "1234567890",
    "qwertyuiop", "letmein123", "iloveyou1", "admin12345", "welcome123",
    "changeme123", "trustno1234", "dragon1234", "sunshine12", "princess12",
}


# --- token signing -----------------------------------------------------------

ALGORITHM = "HS256"
ACCESS_TOKEN_TTL = 30 * 60              # 30 minutes
REFRESH_TOKEN_TTL = 14 * 24 * 60 * 60   # 14 days
MFA_TOKEN_TTL = 5 * 60                  # 5 minutes — just long enough to type a code
PASSWORD_RESET_TOKEN_TTL = 15 * 60      # 15 minutes — long enough to check email
EMAIL_VERIFY_TOKEN_TTL = 24 * 60 * 60   # 24 hours — people don't always check email right away
CLOCK_SKEW_LEEWAY = 30                  # seconds

TOKEN_TYPES = ("access", "refresh", "mfa", "password_reset", "email_verify")


class TokenError(Exception):
    """Raised when a token is missing, malformed, expired or unverifiable."""


@dataclass(frozen=True)
class TokenPayload:
    subject: int          # user id
    token_type: str       # "access" | "refresh" | "mfa" | "password_reset" | "email_verify"
    issued_at: int
    expires_at: int
    jti: str

    def to_dict(self) -> dict:
        return {
            "sub": str(self.subject),
            "type": self.token_type,
            "iat": self.issued_at,
            "exp": self.expires_at,
            "jti": self.jti,
        }


_DEFAULT_TTL = {
    "access": ACCESS_TOKEN_TTL,
    "refresh": REFRESH_TOKEN_TTL,
    "mfa": MFA_TOKEN_TTL,
    "password_reset": PASSWORD_RESET_TOKEN_TTL,
    "email_verify": EMAIL_VERIFY_TOKEN_TTL,
}


def create_token(user_id: int, secret: str, token_type: str = "access",
                 ttl: int | None = None) -> str:
    """Sign an HS256 token for `user_id`."""
    if token_type not in TOKEN_TYPES:
        raise ValueError(f"Unknown token type: {token_type!r}")
    if not secret:
        raise TokenError("No signing secret is configured.")

    now = int(time.time())
    lifetime = ttl if ttl is not None else _DEFAULT_TTL[token_type]
    payload = TokenPayload(
        subject=user_id,
        token_type=token_type,
        issued_at=now,
        expires_at=now + lifetime,
        # A unique id per token, so individual tokens can be revoked later.
        jti=secrets.token_urlsafe(16),
    )

    header = {"alg": ALGORITHM, "typ": "JWT"}
    segments = [
        _b64encode(json.dumps(header, separators=(",", ":"), sort_keys=True).encode()),
        _b64encode(json.dumps(payload.to_dict(), separators=(",", ":"), sort_keys=True).encode()),
    ]
    signing_input = ".".join(segments).encode("ascii")
    signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    segments.append(_b64encode(signature))
    return ".".join(segments)


def decode_token(token: str, secret: str, expected_type: str | None = None) -> TokenPayload:
    """Verify a token and return its payload, or raise TokenError.

    Verification is done with the algorithm this module pins, never with the
    algorithm the token asks for.
    """
    if not token or not isinstance(token, str):
        raise TokenError("No token supplied.")
    if not secret:
        raise TokenError("No signing secret is configured.")

    parts = token.split(".")
    if len(parts) != 3:
        raise TokenError("Malformed token.")

    header_b64, payload_b64, signature_b64 = parts

    # Verify the signature BEFORE trusting anything inside the token.
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    expected_signature = hmac.new(
        secret.encode("utf-8"), signing_input, hashlib.sha256
    ).digest()
    try:
        provided_signature = _b64decode(signature_b64)
    except (ValueError, TypeError) as exc:
        raise TokenError("Malformed token signature.") from exc

    if not hmac.compare_digest(expected_signature, provided_signature):
        raise TokenError("Token signature does not verify.")

    try:
        header = json.loads(_b64decode(header_b64))
        payload = json.loads(_b64decode(payload_b64))
    except (ValueError, TypeError) as exc:
        raise TokenError("Malformed token contents.") from exc

    # Reject 'none' and any algorithm substitution outright. The signature above
    # was checked with HS256 regardless of what the header claims; this makes
    # the mismatch an explicit failure rather than a silent acceptance.
    if header.get("alg") != ALGORITHM:
        raise TokenError(f"Unsupported token algorithm: {header.get('alg')!r}")

    now = int(time.time())

    expires_at = payload.get("exp")
    if not isinstance(expires_at, int):
        raise TokenError("Token has no expiry.")
    if now > expires_at + CLOCK_SKEW_LEEWAY:
        raise TokenError("Token has expired.")

    issued_at = payload.get("iat")
    if not isinstance(issued_at, int):
        raise TokenError("Token has no issued-at time.")
    if issued_at > now + CLOCK_SKEW_LEEWAY:
        raise TokenError("Token was issued in the future.")

    token_type = payload.get("type")
    if token_type not in TOKEN_TYPES:
        raise TokenError("Token has no valid type.")
    if expected_type and token_type != expected_type:
        # Stops a long-lived refresh token being replayed as an access token.
        raise TokenError(f"Expected a {expected_type} token but received a {token_type} token.")

    raw_subject = payload.get("sub")
    try:
        subject = int(raw_subject)
    except (TypeError, ValueError) as exc:
        raise TokenError("Token has no valid subject.") from exc

    return TokenPayload(
        subject=subject,
        token_type=token_type,
        issued_at=issued_at,
        expires_at=expires_at,
        jti=str(payload.get("jti", "")),
    )


# --- secret management -------------------------------------------------------

INSECURE_DEFAULTS = {"", "change-me", "changeme", "secret", "dev", "test", "dex"}


def resolve_secret_key(configured: str, data_dir: Path | None = None) -> str:
    """Return the signing secret, generating and persisting one if needed.

    A generated secret is written to disk so that restarting the server does not
    silently log every user out. It is created with owner-only permissions.
    Production deployments should set `LEGEND_SECRET_KEY` explicitly — a secret on
    disk beside the database is fine for a desktop app and wrong for a fleet.
    """
    if configured and configured.strip().lower() not in INSECURE_DEFAULTS:
        return configured.strip()

    directory = data_dir or (Path.home() / ".legend-trade")
    directory.mkdir(parents=True, exist_ok=True)
    secret_file = directory / "secret_key"

    if secret_file.exists():
        existing = secret_file.read_text(encoding="utf-8").strip()
        if existing:
            return existing

    generated = secrets.token_urlsafe(48)
    secret_file.write_text(generated, encoding="utf-8")
    try:
        os.chmod(secret_file, 0o600)
    except OSError:  # noqa: BLE001 - best effort on filesystems without POSIX modes
        logger.warning("could not restrict permissions on %s", secret_file)

    logger.warning(
        "LEGEND_SECRET_KEY was not set, so a random signing secret was generated and saved to %s. "
        "Set LEGEND_SECRET_KEY explicitly for any multi-instance or production deployment.",
        secret_file,
    )
    return generated


def is_loopback(host: str) -> bool:
    """True when the server is bound to localhost only."""
    return (host or "").strip() in {"127.0.0.1", "localhost", "::1", ""}


# --- token revocation --------------------------------------------------------

# Revocation cutoffs are pushed this far past "now" so that a token minted in
# the same whole second as the revocation is still caught. Token `iat` has
# one-second resolution, so without this a logout and a token issued in the
# same second would compare equal and the token would survive.
REVOCATION_SKEW = 1


def utc_epoch(value) -> int:
    """Epoch seconds for a datetime stored by SQLAlchemy.

    The DateTime columns are naive and hold UTC. Calling `.timestamp()` on a
    naive datetime makes Python interpret it as *local* time, which silently
    shifts the value by the host's UTC offset — so token revocation would work
    on a UTC server and fail everywhere else. Attaching UTC explicitly before
    converting is what makes this correct on any host.
    """
    if value is None:
        return 0
    from datetime import timezone as _tz

    if value.tzinfo is None:
        value = value.replace(tzinfo=_tz.utc)
    return int(value.timestamp())


def revocation_cutoff():
    """The `tokens_valid_from` value to store when revoking existing sessions."""
    from datetime import datetime, timedelta, timezone as _tz

    return (datetime.now(_tz.utc) + timedelta(seconds=REVOCATION_SKEW)).replace(tzinfo=None)


# --- base64url helpers -------------------------------------------------------

def _b64encode(raw: bytes) -> str:
    """Unpadded base64url, as JWT requires."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
