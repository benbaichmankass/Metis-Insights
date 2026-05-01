"""Centralized outcome reporter — S-022 PR1.

Every action that the runtime cares about funnels through ``report()``.
The reporter decides:

  * whether to push to the in-process AlertsQueue (for ``/alerts``)
  * whether to append to ``runtime_logs/outcomes.jsonl`` (audit trail)
  * whether to send a Telegram message
  * whether the message is rate-limited (per-fingerprint and global)

The point is to give every site in the codebase a single, unobtrusive
call:

    from src.runtime.outcomes import report, Level
    report("order_submit", "ok", level=Level.INFO, symbol="BTCUSDT")
    report("order_submit", "failed_exchange", level=Level.ERROR,
           reason="bybit 503", symbol="BTCUSDT")

so silent ``except: pass`` blocks become loud ``report(level=WARN, ...)``
without operators getting paged for every wobble.

Design constraints:
  * Must NEVER raise. A failing reporter would mask the very failures
    we're trying to surface, and could crash hot paths (the tick loop).
  * Must NOT depend on Telegram being reachable. When the API call
    fails, fall through to ``runtime_logs/outcomes_pending.jsonl``;
    the VM-side drainer flushes it on the next pull (same pattern as
    ``docs/claude/pending-pings.jsonl``).
  * Telegram budget: 1 message per fingerprint per 5 minutes,
    hard cap 30 ERROR/CRITICAL messages per rolling hour.
    CRITICAL bypasses the per-fingerprint rate limit but still counts
    against the hourly cap.
  * Scheduled messages (hourly summary, blocker pings) bypass both
    limits — those go through ``send_scheduled()``, not ``report()``.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Deque, Dict, Optional


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class Level(str, Enum):
    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    CRITICAL = "critical"


_TELEGRAM_LEVELS = {Level.ERROR, Level.CRITICAL}
_PERSIST_LEVELS = {Level.WARN, Level.ERROR, Level.CRITICAL}


# ---------------------------------------------------------------------------
# Config (env-overridable; tests reach in via _config_for_tests)
# ---------------------------------------------------------------------------


@dataclass
class _Config:
    rate_limit_window_s: float = 300.0   # 5 min per-fingerprint
    hourly_cap: int = 30                 # global ERROR/CRITICAL per rolling hour
    outcomes_log: Path = field(
        default_factory=lambda: Path("runtime_logs/outcomes.jsonl")
    )
    pending_queue: Path = field(
        default_factory=lambda: Path("runtime_logs/outcomes_pending.jsonl")
    )

    @classmethod
    def from_env(cls) -> "_Config":
        cfg = cls()
        try:
            cfg.rate_limit_window_s = float(
                os.environ.get("OUTCOMES_RATE_LIMIT_S", cfg.rate_limit_window_s)
            )
        except (TypeError, ValueError):
            pass
        try:
            cfg.hourly_cap = int(os.environ.get("OUTCOMES_HOURLY_CAP", cfg.hourly_cap))
        except (TypeError, ValueError):
            pass
        log_override = os.environ.get("OUTCOMES_LOG_PATH")
        if log_override:
            cfg.outcomes_log = Path(log_override)
        pending_override = os.environ.get("OUTCOMES_PENDING_PATH")
        if pending_override:
            cfg.pending_queue = Path(pending_override)
        return cfg


# ---------------------------------------------------------------------------
# Internal state — single global reporter
# ---------------------------------------------------------------------------


class _Reporter:
    def __init__(self, cfg: Optional[_Config] = None) -> None:
        self._cfg = cfg or _Config.from_env()
        self._lock = threading.Lock()
        # Per-fingerprint last-sent timestamp + suppressed count
        self._last_sent: Dict[str, float] = {}
        self._suppressed: Dict[str, int] = {}
        # Rolling-window of telegram send timestamps (for hourly cap)
        self._telegram_sends: Deque[float] = deque()
        # Whether we've already warned about hitting the cap this window
        self._cap_warned_at: float = 0.0

    # --- public surface (called by report() module func) -----------------

    def emit(
        self,
        action: str,
        status: str,
        level: Level,
        reason: Optional[str],
        ctx: Dict[str, Any],
    ) -> Dict[str, Any]:
        ts = time.time()
        record = {
            "ts": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
            "action": action,
            "status": status,
            "level": level.value,
            "reason": reason,
            "context": ctx,
        }

        # 1) AlertsQueue push (in-memory ring buffer; dashboards consumer).
        self._push_alerts_queue(record)

        # 2) Persist WARN+ to outcomes.jsonl
        if level in _PERSIST_LEVELS:
            self._append_jsonl(self._cfg.outcomes_log, record)

        # 3) Telegram for ERROR/CRITICAL (subject to rate limits)
        if level in _TELEGRAM_LEVELS:
            self._maybe_send_telegram(record, ts)

        return record

    def send_scheduled(self, message: str) -> None:
        """Fire a scheduled message (hourly summary, blocker ping).

        Bypasses both the per-fingerprint rate limit and the hourly cap.
        Still falls through to the pending queue if Telegram is unreachable.
        """
        self._send_telegram_or_queue(message, scheduled=True)

    # --- internals --------------------------------------------------------

    def _push_alerts_queue(self, record: Dict[str, Any]) -> None:
        try:
            from src.units.dashboards.alerts import push_alert
        except Exception as exc:  # noqa: BLE001
            logger.debug("AlertsQueue unavailable: %s", exc)
            return
        try:
            push_alert(
                message=self._format_human(record),
                source=record.get("action", "unknown"),
                level=record.get("level", "info"),
                **(record.get("context") or {}),
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("AlertsQueue push failed: %s", exc)

    def _append_jsonl(self, path: Path, record: Dict[str, Any]) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, default=str) + "\n")
        except Exception as exc:  # noqa: BLE001
            logger.warning("outcomes: failed to append %s: %s", path, exc)

    def _maybe_send_telegram(self, record: Dict[str, Any], ts: float) -> None:
        level = Level(record["level"])
        fingerprint = self._fingerprint(record)
        message = self._format_human(record)

        with self._lock:
            # Trim the rolling-hour window
            cutoff = ts - 3600.0
            while self._telegram_sends and self._telegram_sends[0] < cutoff:
                self._telegram_sends.popleft()

            # Hard hourly cap (applies to ERROR and CRITICAL alike)
            if len(self._telegram_sends) >= self._cfg.hourly_cap:
                self._suppressed[fingerprint] = self._suppressed.get(fingerprint, 0) + 1
                # Emit a single "cap hit" warning per hour at most
                if ts - self._cap_warned_at > 3600.0:
                    self._cap_warned_at = ts
                    logger.warning(
                        "outcomes: hourly Telegram cap reached (%d); "
                        "suppressing further alerts this hour",
                        self._cfg.hourly_cap,
                    )
                return

            # Per-fingerprint rate limit (CRITICAL bypasses)
            if level is not Level.CRITICAL:
                last = self._last_sent.get(fingerprint, 0.0)
                if ts - last < self._cfg.rate_limit_window_s:
                    self._suppressed[fingerprint] = (
                        self._suppressed.get(fingerprint, 0) + 1
                    )
                    return

            suppressed_count = self._suppressed.pop(fingerprint, 0)
            self._last_sent[fingerprint] = ts
            self._telegram_sends.append(ts)

        if suppressed_count:
            message += f"\n(+{suppressed_count} suppressed in last "
            message += f"{int(self._cfg.rate_limit_window_s // 60)}m)"

        self._send_telegram_or_queue(message, scheduled=False, record=record)

    def _send_telegram_or_queue(
        self,
        message: str,
        *,
        scheduled: bool,
        record: Optional[Dict[str, Any]] = None,
    ) -> None:
        try:
            from src.runtime.notify import send_via_alert_manager

            send_via_alert_manager(message)
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("outcomes: telegram send failed (%s); queuing", exc)

        # Fallback: append to pending queue. The VM-side drainer flushes it.
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "scheduled": scheduled,
            "message": message,
            "record": record,
        }
        self._append_jsonl(self._cfg.pending_queue, entry)

    @staticmethod
    def _fingerprint(record: Dict[str, Any]) -> str:
        action = record.get("action", "unknown")
        status = record.get("status", "unknown")
        reason = record.get("reason") or ""
        # Collapse numeric noise in reasons (e.g. timestamps, IDs) by
        # keeping only the first 60 chars; good enough for dedup.
        return f"{action}:{status}:{reason[:60]}"

    @staticmethod
    def _format_human(record: Dict[str, Any]) -> str:
        level = (record.get("level") or "info").upper()
        action = record.get("action", "unknown")
        status = record.get("status", "unknown")
        reason = record.get("reason")
        ctx = record.get("context") or {}
        head = f"[{level}] {action} → {status}"
        if reason:
            head += f": {reason}"
        if ctx:
            tail = " | ".join(
                f"{k}={v}" for k, v in ctx.items() if v is not None
            )
            if tail:
                head += f" | {tail}"
        return head[:500]


# ---------------------------------------------------------------------------
# Module-level singleton + public functions
# ---------------------------------------------------------------------------


_reporter = _Reporter()
_reporter_lock = threading.Lock()


def report(
    action: str,
    status: str,
    *,
    level: Level = Level.INFO,
    reason: Optional[str] = None,
    **context: Any,
) -> Dict[str, Any]:
    """Report the outcome of an action. Never raises."""
    try:
        if isinstance(level, str):
            level = Level(level)
        return _reporter.emit(action, status, level, reason, context)
    except Exception as exc:  # noqa: BLE001
        logger.exception("outcomes.report itself failed: %s", exc)
        return {
            "ts": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "status": status,
            "level": getattr(level, "value", str(level)),
            "reason": reason,
            "context": context,
            "report_error": str(exc),
        }


def send_scheduled(message: str) -> None:
    """Send a scheduled (non-rate-limited) Telegram message. Never raises."""
    try:
        _reporter.send_scheduled(message)
    except Exception as exc:  # noqa: BLE001
        logger.exception("outcomes.send_scheduled failed: %s", exc)


def _reset_for_tests(cfg: Optional[_Config] = None) -> None:
    """Reset the global reporter. Test-only."""
    global _reporter
    with _reporter_lock:
        _reporter = _Reporter(cfg)


def _config_for_tests() -> _Config:
    """Return the active reporter's config so tests can tweak paths."""
    return _reporter._cfg
