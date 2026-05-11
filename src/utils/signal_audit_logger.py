from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from src.utils.paths import runtime_logs_dir

logger = logging.getLogger(__name__)

# Writer path resolved through runtime_logs_dir() so DATA_DIR /
# RUNTIME_LOGS_DIR overrides apply consistently with the heartbeat +
# runtime_status writers. Pre-2026-05-11 this was hardcoded to
# ``Path(__file__).resolve().parents[2] / "runtime_logs"``, which
# diverged from runtime_logs_dir() the moment the OCI block-storage
# drop-in went in: liveness_watchdog + hourly_report read from the
# DATA_DIR-resolved path and found nothing while audit was being
# written at the repo path.
BASE = runtime_logs_dir()
SIGNAL_FILE = BASE / "signal_audit.jsonl"
SUMMARY_FILE = BASE / "summary_markers.json"


def _dual_write_to_db(payload: Dict[str, Any]) -> None:
    """Best-effort: also write *payload* to ``trade_journal.db::signals``.

    S-034 (architecture-audit-2026-05-02 P2-9) transition: the JSONL
    file remains the source of truth during the cutover. This dual-
    write hydrates the SQL signals log so readers can flip over once
    the operator has confirmed one full day of clean writes.

    The opt-out env flag ``SIGNAL_DUAL_WRITE_DISABLED=true`` exists so
    the operator can disable the SQL side cheaply if it ever causes
    pipeline lag — the JSONL writer is unaffected.

    Never raises. DB-side failures log a warning and return; JSONL
    write happens unconditionally upstream.
    """
    if os.environ.get("SIGNAL_DUAL_WRITE_DISABLED", "").strip().lower() in {
        "true", "1", "yes", "on",
    }:
        return
    try:
        from src.units.db.database import Database
        db_path = os.environ.get("TRADE_JOURNAL_DB") or "trade_journal.db"
        db = Database(db_path=db_path)
        db.insert_signal(payload)
    except Exception as exc:  # noqa: BLE001
        logger.warning("signal_audit_logger: SQL dual-write failed: %s", exc)


def log_signal(event: Dict[str, Any]) -> None:
    payload = dict(event or {})
    payload.setdefault("logged_at_utc", datetime.now(timezone.utc).isoformat())
    with SIGNAL_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, default=str) + "\n")
    # Dual-write to trade_journal.db::signals (S-034 transition).
    _dual_write_to_db(payload)


def _load_state() -> Dict[str, str]:
    if SUMMARY_FILE.exists():
        try:
            return json.loads(SUMMARY_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_state(state: Dict[str, str]) -> None:
    SUMMARY_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def should_send_summary(now_utc: datetime) -> bool:
    """Return True at most once per UTC hour.

    S-022 PR2: cadence flipped from twice-a-day (07:00 / 19:00) to once
    every hour. The slot key is now ``{YYYY-MM-DD}-{HH}`` so the existing
    dedupe machinery (last_slot in summary_markers.json) still applies —
    a tick loop that calls this multiple times within the same hour gets
    True only on the first call.
    """
    now_utc = now_utc.astimezone(timezone.utc)
    slot = f"{now_utc.date()}-{now_utc.hour:02d}"
    state = _load_state()
    if state.get("last_slot") == slot:
        return False
    state["last_slot"] = slot
    _save_state(state)
    return True
