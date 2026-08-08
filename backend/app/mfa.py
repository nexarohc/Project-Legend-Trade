"""Time-based one-time passwords (RFC 6238), stdlib only.

Same reasoning as `security.py`'s hand-rolled JWT: the only cryptography
involved is HMAC-SHA1, which is in the standard library, and a TOTP library
would mostly be a thin wrapper around it plus base32 encoding — also stdlib.
Rolling it here means no new dependency for six functions.

**On using SHA1.** TOTP's HMAC-SHA1 is not the same threat model as a
signature or a password hash: RFC 6238 uses it as a keyed PRF over a shared
32-bit counter, not as a collision-resistant digest, and every mainstream
authenticator app (Google Authenticator, Authy, 1Password) only implements
SHA1 TOTP. Using SHA256 here would produce codes no authenticator app could
generate, which defeats the entire point.

**On where the secret lives.** Unlike a password, a TOTP secret cannot be
hashed at rest — verifying a future code requires reproducing it, which is
only possible with the secret in symmetric form. It is stored in the database
as-is, the same tradeoff already made for the per-user webhook token. This is
standard practice; it means the shared secret is only as safe as the database
is, which is a real limitation stated plainly rather than papered over with an
encryption layer this codebase has no vetted crypto library to build safely
(the same reasoning `trading/execution/alpaca.py` gives for broker keys).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
import urllib.parse

# 20 bytes (160 bits) is what RFC 4226 recommends for the shared secret.
SECRET_BYTES = 20
PERIOD_SECONDS = 30
CODE_DIGITS = 6
# Bidirectional slack of one period tolerates a phone whose clock is up to 30
# seconds off in either direction without the tolerance becoming replay-able
# for more than one extra guess to either side.
VERIFY_WINDOW = 1


def generate_secret() -> str:
    """A fresh base32 secret, unpadded (padding is re-added on decode)."""
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode("ascii").rstrip("=")


def _decode_secret(secret: str) -> bytes:
    cleaned = secret.strip().replace(" ", "").upper()
    padded = cleaned + "=" * (-len(cleaned) % 8)
    return base64.b32decode(padded)


def hotp(secret: str, counter: int, digits: int = CODE_DIGITS) -> str:
    """RFC 4226 HOTP: an HMAC-SHA1 code for one counter value."""
    key = _decode_secret(secret)
    message = counter.to_bytes(8, "big")
    digest = hmac.new(key, message, hashlib.sha1).digest()

    # Dynamic truncation (RFC 4226 §5.3): the low nibble of the last byte picks
    # a 4-byte window, whose top bit is then dropped to avoid sign ambiguity.
    offset = digest[-1] & 0x0F
    truncated = int.from_bytes(digest[offset:offset + 4], "big") & 0x7FFFFFFF
    return str(truncated % (10 ** digits)).zfill(digits)


def totp(secret: str, timestamp: int | None = None, period: int = PERIOD_SECONDS,
         digits: int = CODE_DIGITS) -> str:
    """The code for the current (or given) moment."""
    ts = int(time.time()) if timestamp is None else timestamp
    return hotp(secret, ts // period, digits)


def verify_totp(secret: str, code: str, timestamp: int | None = None,
                period: int = PERIOD_SECONDS, digits: int = CODE_DIGITS,
                window: int = VERIFY_WINDOW) -> bool:
    """Check a code against the current period and `window` adjacent ones.

    Every candidate is compared in constant time; which period (if any)
    matched is never revealed to the caller, only whether one did.
    """
    if not code or not code.isdigit() or len(code) != digits:
        return False

    ts = int(time.time()) if timestamp is None else timestamp
    counter = ts // period
    matched = False
    for delta in range(-window, window + 1):
        candidate = hotp(secret, counter + delta, digits)
        if hmac.compare_digest(candidate, code):
            matched = True
    return matched


def provisioning_uri(secret: str, account_email: str, issuer: str = "Legend Trade") -> str:
    # The issuer is the label the user sees in their authenticator app. Changing
    # it affects new enrolments only: an existing entry keeps whatever label it
    # was created with, and its codes keep working because the shared secret is
    # untouched. So this is safe to change, but it does mean an instance can end
    # up with both labels in the wild.
    """An `otpauth://` URI an authenticator app can add directly.

    No QR image is generated here — that would need an image-encoding
    dependency for a single settings-screen convenience. The URI is shown as
    text and most authenticator apps accept manual entry of the secret itself,
    which is also returned alongside this.
    """
    label = urllib.parse.quote(f"{issuer}:{account_email}")
    query = urllib.parse.urlencode({
        "secret": secret,
        "issuer": issuer,
        "algorithm": "SHA1",
        "digits": CODE_DIGITS,
        "period": PERIOD_SECONDS,
    })
    return f"otpauth://totp/{label}?{query}"


def generate_recovery_codes(count: int = 10) -> list[str]:
    """One-time backup codes for when the authenticator device is unavailable.

    Each is high-entropy (8 random bytes) and single-use — `users.py` marks
    one consumed the moment it's accepted, so a code intercepted once cannot
    be replayed.
    """
    return [f"{secrets.token_hex(4)}-{secrets.token_hex(4)}" for _ in range(count)]
