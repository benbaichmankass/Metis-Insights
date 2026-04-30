"""S-013 M3 PR #1 — JWT helpers + (still no-op) ``require_session``.

Token contract (binding for S-013):

    alg = HS256, signed with env ``JWT_SIGNING_KEY``
    ttl = 3600 s (1 hour)
    claims = {"email": <str>, "iat": <int>, "exp": <int>}

Allowlist gate ``email == ALLOWED_EMAIL`` is enforced at issuance time
(``POST /api/auth/login``) and again at decode time inside
``require_session`` once M3 PR #2 flips the decorator.

Secrets stay server-side. Envs (``JWT_SIGNING_KEY``,
``WEBAPP_PASSWORD_SHA256``, ``ALLOWED_EMAIL``) are read **per call**, not
at import time, so unit tests can monkeypatch them and the unit on the
VM picks up an updated ``EnvironmentFile`` without a process restart.

Error responses must never leak which env var is missing — only that
auth is unavailable.

TODO(S-013 M3 PR #2): replace ``require_session`` body with header
parsing + ``decode_token`` + allowlist check. Tests in
``tests/test_web_api_status.py`` and ``tests/test_web_api_pnl.py`` pin
the current passthrough as a regression guard so the swap is the only
moment behaviour changes.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from functools import wraps
from typing import Any, Callable, Optional, TypeVar

import jwt

logger = logging.getLogger(__name__)

JWT_ALGORITHM = "HS256"
TOKEN_TTL_SECONDS = 3600
F = TypeVar("F", bound=Callable[..., Any])


# ---------------------------------------------------------------------------
# Env accessors. Read per-call so the systemd EnvironmentFile can update
# without a process restart and so tests can monkeypatch.
# ---------------------------------------------------------------------------


def _signing_key() -> str:
    key = os.environ.get("JWT_SIGNING_KEY", "").strip()
    if not key:
        # Don't echo the env-var name out to callers — see error-handling
        # contract above.
        raise RuntimeError("auth misconfigured")
    return key


def _allowed_email() -> str:
    email = os.environ.get("ALLOWED_EMAIL", "").strip().lower()
    if not email:
        raise RuntimeError("auth misconfigured")
    return email


def _password_hash() -> str:
    pw_hash = os.environ.get("WEBAPP_PASSWORD_SHA256", "").strip().lower()
    if not pw_hash:
        raise RuntimeError("auth misconfigured")
    return pw_hash


# ---------------------------------------------------------------------------
# Password verification.
# ---------------------------------------------------------------------------


def _hash_password(plain: str) -> str:
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


def verify_password(plain: str, expected_hash: str) -> bool:
    """Constant-time SHA-256 hex compare."""
    return hmac.compare_digest(_hash_password(plain), expected_hash.lower())


# ---------------------------------------------------------------------------
# Token helpers.
# ---------------------------------------------------------------------------


def issue_token(email: str, *, now: Optional[int] = None) -> str:
    iat = int(now if now is not None else time.time())
    payload = {"email": email, "iat": iat, "exp": iat + TOKEN_TTL_SECONDS}
    return jwt.encode(payload, _signing_key(), algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    """Return claims dict on success, ``None`` on any failure.

    Failures swallowed: missing/invalid signing key, expired token,
    bad signature, ``alg=none`` (PyJWT rejects it because we pin
    ``algorithms=[HS256]``), malformed payload.
    """
    try:
        key = _signing_key()
    except RuntimeError:
        return None
    try:
        return jwt.decode(token, key, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None


# ---------------------------------------------------------------------------
# Decorator. Currently a no-op; M3 PR #2 swaps the body for real
# enforcement.
# ---------------------------------------------------------------------------


def require_session(func: F) -> F:
    """Mark a route as session-protected.

    No-op in M2 / M3 PR #1; M3 PR #2 swaps this for real JWT enforcement.
    Tests treat the passthrough as a regression guard so the swap is
    the only moment behaviour changes.
    """

    @wraps(func)
    async def _wrapper(*args: Any, **kwargs: Any) -> Any:
        return await func(*args, **kwargs)

    return _wrapper  # type: ignore[return-value]
