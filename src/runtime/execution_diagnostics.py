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
from typing import Optional

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
