"""Validation log for the M5 strategy-testing workflow.

Append-only NDJSON at ``runtime_logs/validation.jsonl`` — one line per
backtest run kicked off by the M5 ``BacktestConsumer``. The format
mirrors ``signal_audit_logger`` (M5 reuses the convention so the
dashboard log endpoints can ingest it later without a parser branch).

Each entry is a flat JSON object with the keys:

  ``event``           — always ``"backtest_run"`` for now (room to grow).
  ``request_id``      — comms request id this run was triggered by.
  ``strategy``        — strategy name from the registry.
  ``outcome``         — ``"ok" | "error"``.
  ``started_at_utc``  — ISO 8601, when the consumer began the run.
  ``completed_at_utc``— ISO 8601, when it finished.
  ``db_row_id``       — ``backtest_results.id`` (only on ``outcome=ok``).
  ``summary``         — small dict of headline metrics (only on ``ok``).
  ``error``           — short error string (only on ``outcome=error``).
  ``logged_at_utc``   — set automatically by ``log_validation``.

The writer never raises — a stray FS error must not crash the comms
poller. Callers that care about the log landing successfully can read
it back themselves.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from src.utils.paths import runtime_logs_dir

logger = logging.getLogger(__name__)


def _log_path(base: Optional[Path] = None) -> Path:
    # ``base`` is the test override; in production the path resolves
    # through runtime_logs_dir() so DATA_DIR / RUNTIME_LOGS_DIR overrides
    # match every other runtime-log writer. VALIDATION_LOG_PATH remains
    # an explicit-file override for the M5 backtest consumer (matches
    # TRADE_JOURNAL_DB's pattern).
    if base is not None:
        return Path(base) / "validation.jsonl"
    override = os.environ.get("VALIDATION_LOG_PATH")
    if override:
        return Path(override)
    return runtime_logs_dir() / "validation.jsonl"


def log_validation(event: Dict[str, Any], *, base: Optional[Path] = None) -> None:
    """Append one NDJSON record to the validation log.

    ``base`` overrides the default ``runtime_logs/`` directory and is
    only used by tests. In production the env var
    ``VALIDATION_LOG_PATH`` is the only override (matches the
    ``TRADE_JOURNAL_DB`` pattern).
    """
    payload = dict(event or {})
    payload.setdefault("logged_at_utc", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    path = _log_path(base)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, default=str) + "\n")
    except OSError as exc:
        # Never raise — log writes are best-effort. The consumer's own
        # apply_answer call is the durable record of what happened.
        logger.warning("validation_logger: write failed (%s): %s", path, exc)
