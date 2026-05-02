"""
Append-only event log for the comms channel.

Writes one JSON object per line to ``comms/log.ndjson``. The log is the
audit trail Claude (and humans) consult when debugging "did the bot send
this?" / "when did the operator answer?" — it is intentionally separate
from the request artifacts themselves so a corrupt log never blocks
delivery, and a corrupt artifact never erases history.

Event shape (loose by design — extra fields are allowed):

    {
      "at": "2026-05-02T14:30:15+00:00",
      "event": "request_created" | "request_sent" | "answer_received"
               | "request_answered" | "request_acknowledged"
               | "request_expired" | "request_cancelled" | "error",
      "request_id": "REQ-...",
      "actor": "claude" | "bot" | "operator" | "system",
      "details": {...optional structured payload...}
    }
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)

DEFAULT_LOG_PATH = Path("comms/log.ndjson")


def log_event(
    event: str,
    *,
    request_id: Optional[str] = None,
    actor: Optional[str] = None,
    details: Optional[Mapping[str, Any]] = None,
    log_path: Optional[Path] = None,
) -> None:
    """Append a single event to ``comms/log.ndjson``.

    Failures are swallowed and logged at WARNING — the comms log is
    diagnostic, never a critical-path dependency. If the log directory
    does not exist yet, this creates it (matches the "self-bootstrapping
    comms area" pattern documented in ``docs/claude/comms-architecture.md``).
    """
    path = Path(log_path) if log_path is not None else DEFAULT_LOG_PATH
    payload: dict[str, Any] = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "event": event,
    }
    if request_id is not None:
        payload["request_id"] = request_id
    if actor is not None:
        payload["actor"] = actor
    if details:
        payload["details"] = dict(details)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with path.open("a", encoding="utf-8") as f:
            f.write(line + os.linesep)
    except OSError as exc:
        logger.warning("comms log write failed (%s): %s", path, exc)
