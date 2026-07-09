"""GET /api/bot/accounts/balances — per-account balance snapshot.

Tier-1 read (no auth — see ``docs/api-tier-policy.md``). Surfaces the
balances the trader **already tracks** via the hourly-report
``account_snapshots()`` path.

**DB-authoritative (WC-5, dashboard-truth 2026-06-16).** The canonical
source is now ``trade_journal.db::balance_snapshots`` (append-only history,
latest row per account). The legacy ``runtime_logs/balance_snapshots.json``
(which only ever held the LATEST reading per account, overwritten each
cycle) is kept as a degraded fallback for the window before the DB table is
populated. ``source`` in the envelope records which path served the read.

Read-only and connection-free: this endpoint does NOT open any exchange
socket or re-fetch balances. It only reflects the most recent balance
the trader recorded, so the dashboard renders it alongside the snapshot
timestamp and a stale snapshot is visible as such rather than silently
wrong.

Wire-shape:

    {
      "present": true,
      "source": "db",                    # "db" | "json_fallback"
      "as_of": "2026-05-23T18:00:00Z",   # newest per-account ts
      "age_seconds": 642.0,
      "balances": {
        "bybit_1": {"balance": 1234.56, "ts": "2026-05-23T18:00:00Z",
                    "delta_1h": 3.2, "open_positions": 1, "api_ok": true},
        ...
      }
    }
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter

from src.utils.paths import runtime_logs_dir

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bot", tags=["bot"])

_SNAPSHOT_NAME = "balance_snapshots.json"


def _snapshot_path() -> Path:
    return runtime_logs_dir() / _SNAPSHOT_NAME


def _parse_iso(value: Any) -> Optional[float]:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _empty(source: str = "db") -> Dict[str, Any]:
    return {
        "present": False, "source": source,
        "as_of": None, "age_seconds": None, "balances": {},
    }


def _finalize(balances: Dict[str, Any], source: str) -> Dict[str, Any]:
    """Compute as_of / age_seconds from the newest per-account ts and wrap."""
    latest_ts: Optional[float] = None
    for entry in balances.values():
        parsed = _parse_iso(entry.get("ts"))
        if parsed is not None and (latest_ts is None or parsed > latest_ts):
            latest_ts = parsed
    as_of = None
    age = None
    if latest_ts is not None:
        as_of = (
            datetime.fromtimestamp(latest_ts, tz=timezone.utc)
            .replace(microsecond=0).isoformat().replace("+00:00", "Z")
        )
        age = round(time.time() - latest_ts, 1)
    return {
        "present": True, "source": source,
        "as_of": as_of, "age_seconds": age, "balances": balances,
    }


def _from_db() -> Optional[Dict[str, Any]]:
    """Latest balance row per account from the canonical DB table.

    Returns the wire envelope, or ``None`` if the table is empty/absent or the
    read raised — the caller then falls back to the JSON snapshot. Best-effort:
    never raises out of here.
    """
    try:
        from src.units.db.database import Database

        rows = Database().get_latest_balance_snapshots()
    except Exception:  # noqa: BLE001  # allow-silent: returns None to fall back to the JSON snapshot (degraded but present), not an empty/silent result — the JSON path still serves balances and logs the DB failure with a stack trace. Narrowing isn't possible (the import + sqlite layer raise heterogeneous types).
        logger.warning("accounts: DB balance read failed; falling back to JSON", exc_info=True)
        return None
    if not rows:
        return None
    balances: Dict[str, Any] = {}
    for aid, row in rows.items():
        balances[str(aid)] = {
            "balance": row.get("balance"),
            "ts": row.get("ts"),
            "delta_1h": row.get("delta_1h"),
            "open_positions": row.get("open_positions"),
            "api_ok": row.get("api_ok"),
        }
    return _finalize(balances, "db")


def _from_json() -> Dict[str, Any]:
    """Legacy fallback: latest-per-account from balance_snapshots.json."""
    path = _snapshot_path()
    if not path.exists():
        return _empty("json_fallback")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("accounts: balance snapshot read failed")
        return _empty("json_fallback")
    if not isinstance(raw, dict):
        return _empty("json_fallback")

    balances: Dict[str, Any] = {}
    for aid, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        balances[str(aid)] = {"balance": entry.get("balance"), "ts": entry.get("ts")}
    return _finalize(balances, "json_fallback")


@router.get("/accounts/balances")
def get_account_balances() -> Dict[str, Any]:
    """Return the most recent per-account balance snapshot.

    DB-authoritative (``balance_snapshots`` table), with the legacy JSON
    snapshot as a degraded fallback for the window before the table is
    populated. Best-effort: a missing / unreadable source returns
    ``{present: false, balances: {}}`` rather than a 500 (a fresh install or a
    trader that hasn't written its first hourly snapshot yet is a legitimate
    empty case, not an outage).
    """
    db_envelope = _from_db()
    if db_envelope is not None:
        return db_envelope
    return _from_json()
