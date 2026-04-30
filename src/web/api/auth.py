"""S-013 M2 — auth scaffolding for the web API.

This file currently exposes a **no-op** ``require_session`` decorator so
M2 routes can declare their auth contract today without enforcement
shipping until the operator has had a chance to provision
``JWT_SIGNING_KEY`` and ``ALLOWED_EMAIL`` on the VM.

TODO(S-013 M3 PR #2): flip ``require_session`` from no-op to real
``Authorization: Bearer <jwt>`` parsing + ``decode_token`` + allowlist
check, and add a ``PUBLIC_ROUTES`` set listing the only paths that may
opt out of the wall.
"""
from __future__ import annotations

from functools import wraps
from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


def require_session(func: F) -> F:
    """Mark a route as session-protected.

    No-op in M2; M3 PR #2 swaps this for real JWT enforcement. Tests
    treat the passthrough as a regression guard so the swap is the only
    moment behaviour changes.
    """

    @wraps(func)
    async def _wrapper(*args: Any, **kwargs: Any) -> Any:
        return await func(*args, **kwargs)

    return _wrapper  # type: ignore[return-value]
