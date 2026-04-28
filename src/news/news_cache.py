"""
Simple in-memory TTL cache for news fetch results.

Keyed by an arbitrary string; entries expire after a configurable TTL.
Thread-safe via a single module-level lock so the live trading loop
can share one cache instance without contention.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Optional

_DEFAULT_TTL_SECONDS = 300  # 5 minutes


class NewsCache:
    """In-memory TTL cache.

    Parameters
    ----------
    default_ttl:
        Seconds before a cached value expires.  Can be overridden per-set.
    """

    def __init__(self, default_ttl: float = _DEFAULT_TTL_SECONDS) -> None:
        self._default_ttl = default_ttl
        self._store: dict[str, tuple[Any, float]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, key: str) -> Optional[Any]:
        """Return the cached value for *key*, or ``None`` if absent/expired."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if time.monotonic() > expires_at:
                del self._store[key]
                return None
            return value

    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        """Store *value* under *key* for *ttl* seconds (default: ``default_ttl``)."""
        ttl = ttl if ttl is not None else self._default_ttl
        expires_at = time.monotonic() + ttl
        with self._lock:
            self._store[key] = (value, expires_at)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def __len__(self) -> int:
        with self._lock:
            now = time.monotonic()
            return sum(1 for _, (_, exp) in self._store.items() if exp > now)


# Module-level singleton shared across the process.
_cache = NewsCache()


def get_cache() -> NewsCache:
    return _cache
