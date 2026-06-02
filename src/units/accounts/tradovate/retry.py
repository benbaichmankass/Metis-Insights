"""Exponential backoff helpers shared by REST + WebSocket layers.

Pure functions so they can be unit-tested without async machinery. A
caller asks for ``delay = backoff(attempt)`` and sleeps it themselves
— this module never sleeps.
"""
from __future__ import annotations

import random


def exponential_backoff(
    attempt: int,
    *,
    base_s: float = 0.5,
    cap_s: float = 30.0,
    jitter: float = 0.25,
) -> float:
    """Return the delay (seconds) for ``attempt`` (1-indexed).

    ``2 ** (attempt-1) * base`` capped at ``cap_s``, with multiplicative
    jitter in ``[1-jitter, 1+jitter]``. Negative attempts are clamped
    to 1 so a caller passing ``0`` doesn't get an instant retry.
    """
    n = max(1, attempt)
    raw = min(cap_s, base_s * (2 ** (n - 1)))
    if jitter <= 0:
        return raw
    spread = 1.0 + random.uniform(-jitter, jitter)
    return max(0.0, raw * spread)
