"""Per-account execution-failure diagnostic ping.

When ``Coordinator.multi_account_execute`` fails to route a strategy's
order package to a live account, the operator needs an immediate
human-readable description of *which* account refused, *what* package
was dropped, and *why*. The previous wiring buried the failure inside
the audit log; this module surfaces it via the existing pending-pings
inbox (``runtime_logs/pending_pings/``) — the same channel the
``ict-telegram-bot`` job-queue tick drains every ~5 s.

Design rules:

- **Asynchronous.** Producers drop a JSON file via ``os.replace`` and
  return; nothing in the order path waits on Telegram. A failed
  enqueue only logs a warning — the order-routing failure is already
  surfaced via the result dict + pipeline audit log, so the diagnostic
  ping is best-effort.
- **No secrets.** The body is plain text limited to fields the operator
  already sees in ``/accounts_status`` (account name, strategy, symbol,
  side, qty) and a short failure reason. No API keys, no balance
  values, no SDK exception payloads beyond ``type(exc).__name__``.
- **Idempotent enough.** Each ping gets a unique filename via
  ``uuid.uuid4`` so duplicates from a flapping pipeline tick don't
  collide. The bot's drainer deletes after send; nothing here needs a
  retry queue.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PENDING_PINGS_DIR = _REPO_ROOT / "runtime_logs" / "pending_pings"


def enqueue_execution_failure(
    *,
    account: str,
    strategy: str,
    symbol: str,
    side: str,
    qty: Optional[float],
    reason: str,
    priority: str = "high",
) -> Optional[Path]:
    """Drop a Telegram-ready JSON ping for a per-account execution failure.

    Returns the path of the queued file on success, ``None`` when the
    enqueue itself fails (e.g. read-only filesystem in a sandboxed
    test). Failure to enqueue is logged at WARN — never raises.
    """
    try:
        body = (
            "⚠️ Order execution failed\n"
            f"Account: {account}\n"
            f"Strategy: {strategy}\n"
            f"Symbol: {symbol} | Side: {side} | Qty: {qty if qty is not None else '?'}\n"
            f"Reason: {reason}"
        )[:1024]
        payload = {"priority": priority, "body": body}
        PENDING_PINGS_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{int(uuid.uuid4().int % 10**12):012d}-execfail.json"
        path = PENDING_PINGS_DIR / name
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, path)
        return path
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "execution_diagnostics: enqueue failed for account=%s reason=%r: %s",
            account, reason[:80], exc,
        )
        return None


def enqueue_orphan_reconciliation(
    *,
    account: str,
    symbol: str,
    side: str,
    db_trade_id: Any,
    linked_package_id: Optional[str],
    reason: str = "reconciler",
    priority: str = "high",
) -> Optional[Path]:
    """Drop a Telegram-ready JSON ping for a monitor-loop orphan match.

    Mirrors :func:`enqueue_execution_failure`'s shape so the bot's
    drainer treats both pings the same way. Fired by
    ``order_monitor._reconcile_open_trades`` whenever the DB shows a
    trade as ``status='open'`` but the exchange's open-positions list
    does not include the matching ``(symbol, side)`` row — meaning the
    exchange independently closed the position without the trader
    seeing the close, and the DB row has been re-tagged
    ``status='orphaned'`` with ``exit_reason='reconciler'``.

    The body is operator-actionable (`/last5` will show the linked
    trade) and intentionally lean — no SDK exception payloads, no
    balance values, just identifiers.
    """
    try:
        body = (
            "🧹 Monitor reconciler — orphaned trade swept\n"
            f"Account: {account}\n"
            f"Symbol: {symbol} | Side: {side}\n"
            f"DB trade id: {db_trade_id}\n"
            f"Package: {linked_package_id or '(unlinked)'}\n"
            f"Reason: {reason}"
        )[:1024]
        payload = {"priority": priority, "body": body}
        PENDING_PINGS_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{int(uuid.uuid4().int % 10**12):012d}-reconciler.json"
        path = PENDING_PINGS_DIR / name
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, path)
        return path
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "execution_diagnostics: orphan-ping enqueue failed for "
            "account=%s symbol=%s db_trade_id=%s: %s",
            account, symbol, db_trade_id, exc,
        )
        return None


def enqueue_orphan_rollup(
    *,
    suppressed_count: int,
    priority: str = "high",
) -> Optional[Path]:
    """One roll-up ping summarising orphans the per-orphan cap dropped.

    The reconciler caps individual orphan pings per tick to avoid
    flooding the operator when a long-stale DB has accumulated dozens
    of ghosts. Anything past the cap is summarised here.
    """
    try:
        body = (
            "🧹 Monitor reconciler — additional orphans not individually pinged\n"
            f"Suppressed: {suppressed_count} more orphan(s) this tick. "
            f"See /last5 / /packages for the full list."
        )[:1024]
        payload = {"priority": priority, "body": body}
        PENDING_PINGS_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{int(uuid.uuid4().int % 10**12):012d}-reconciler-rollup.json"
        path = PENDING_PINGS_DIR / name
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, path)
        return path
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "execution_diagnostics: orphan-rollup enqueue failed "
            "(suppressed=%d): %s",
            suppressed_count, exc,
        )
        return None
