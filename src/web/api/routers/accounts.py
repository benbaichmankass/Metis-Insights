"""GET /api/bot/accounts/balances — per-account balance snapshot.

Tier-1 read (no auth — see ``docs/api-tier-policy.md``). Surfaces the
balances the trader **already tracks** in
``runtime_logs/balance_snapshots.json`` (written by the hourly-report
``account_snapshots()`` path — one entry per account: ``{balance, ts}``).

Read-only and connection-free: this endpoint does NOT open any exchange
socket or re-fetch balances. It only reflects the most recent balance
the trader recorded, so the dashboard renders it alongside the snapshot
timestamp and a stale snapshot is visible as such rather than silently
wrong.

Wire-shape:

    {
      "present": true,
      "as_of": "2026-05-23T18:00:00Z",   # newest per-account ts
      "age_seconds": 642.0,
      "balances": {
        "bybit_1": {"balance": 1234.56, "ts": "2026-05-23T18:00:00Z"},
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


def _empty() -> Dict[str, Any]:
    return {"present": False, "as_of": None, "age_seconds": None, "balances": {}}


@router.get("/accounts/balances")
async def get_account_balances() -> Dict[str, Any]:
    """Return the most recent per-account balance snapshot.

    Best-effort: a missing / unreadable snapshot returns
    ``{present: false, balances: {}}`` rather than a 500 (a fresh
    install or a trader that hasn't written its first hourly snapshot
    yet is a legitimate empty case, not an outage).
    """
    path = _snapshot_path()
    if not path.exists():
        return _empty()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("accounts: balance snapshot read failed")
        return _empty()
    if not isinstance(raw, dict):
        return _empty()

    balances: Dict[str, Any] = {}
    latest_ts: Optional[float] = None
    for aid, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        ts = entry.get("ts")
        balances[str(aid)] = {"balance": entry.get("balance"), "ts": ts}
        parsed = _parse_iso(ts)
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
    return {"present": True, "as_of": as_of, "age_seconds": age, "balances": balances}
