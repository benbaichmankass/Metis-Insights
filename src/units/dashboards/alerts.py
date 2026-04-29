"""Alerts queue — dashboards subunit (S-008 PR #123).

A lightweight in-process queue that collects alerts from all units and
surfaces them to the Telegram bot / App unit.

Sources:
  - Accounts: trade executed, order failed, account paused/resumed
  - Strategies: signal fired
  - Return commands: halt/resume issued

Consumers:
  - Telegram bot (/status, /alerts)
  - App dashboard

Thread-safety: uses a plain list guarded by a lock (no async required;
the bot is single-threaded per account).
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class AlertsQueue:
    """Append-only ring buffer of alert dicts.

    Each alert is a dict with at minimum:
        ``ts``      : ISO-8601 UTC timestamp
        ``source``  : unit name (e.g. "accounts", "strategies")
        ``level``   : "info" | "warning" | "error"
        ``message`` : human-readable string
        + any additional fields the source wants to attach

    The buffer is capped at *maxlen* to prevent unbounded growth.
    """

    def __init__(self, maxlen: int = 200) -> None:
        self._buf: List[Dict[str, Any]] = []
        self._maxlen = maxlen
        self._lock = threading.Lock()

    def push(
        self,
        message: str,
        source: str = "unknown",
        level: str = "info",
        **extra: Any,
    ) -> Dict[str, Any]:
        """Append an alert and return it.

        Parameters
        ----------
        message : str
            Human-readable alert text.
        source : str
            Unit name that generated the alert.
        level : str
            "info" | "warning" | "error".
        **extra
            Any additional metadata (e.g. account_id, strategy, trade_id).
        """
        alert = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "level": level,
            "message": message,
            **extra,
        }
        with self._lock:
            self._buf.append(alert)
            if len(self._buf) > self._maxlen:
                self._buf = self._buf[-self._maxlen :]
        return alert

    def list_all(self, n: Optional[int] = None) -> List[Dict[str, Any]]:
        """Return up to *n* most-recent alerts (newest last)."""
        with self._lock:
            items = list(self._buf)
        return items[-n:] if n is not None else items

    def pop_all(self) -> List[Dict[str, Any]]:
        """Drain and return all pending alerts."""
        with self._lock:
            items = list(self._buf)
            self._buf.clear()
        return items

    def clear(self) -> None:
        """Discard all alerts."""
        with self._lock:
            self._buf.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._buf)


# Module-level singleton — shared across all Coordinator instances in one process.
_global_queue = AlertsQueue()


def push_alert(message: str, source: str = "unknown", level: str = "info", **extra) -> Dict[str, Any]:
    """Push an alert to the global queue."""
    return _global_queue.push(message, source=source, level=level, **extra)


def list_alerts(n: Optional[int] = None) -> List[Dict[str, Any]]:
    """Return up to *n* most-recent alerts from the global queue."""
    return _global_queue.list_all(n)


def pop_alerts() -> List[Dict[str, Any]]:
    """Drain and return all alerts from the global queue."""
    return _global_queue.pop_all()


def clear_alerts() -> None:
    """Clear the global alerts queue (used in tests)."""
    _global_queue.clear()
