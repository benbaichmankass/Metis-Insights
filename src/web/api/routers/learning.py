"""Learning-center router — /api/bot/learning/* (dashboard Learning tab).

Serves the committed curriculum content (``comms/learning/curriculum.json``)
and a small per-resource progress store
(``trade_journal.db::learning_progress``) so the dashboard's Learning tab can
render the syllabus AND let the operator mark resources done — durable and
cross-device (unlike browser-local state), and ready to mirror to the Android
app later.

Tier 1: observability read + a tiny operator-only progress write (no trading
impact, no order path, no notification). The write is an **unauthenticated
client self-service** POST — the same shape as ``POST /devices/register`` (a
client records state without holding the shared ``DASHBOARD_API_TOKEN``) — so
BOTH the dashboard and the Android app can mark progress. The store holds no
secret, so open write is acceptable here (unlike the fail-closed prop-money
POST). The content read is best-effort (``present:false`` on missing/garbled
file).
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from src.utils.paths import repo_root

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bot/learning", tags=["learning"])

_VALID_STATUSES = {"not_started", "in_progress", "done"}


# ── curriculum content (file-backed, mtime-cached) ──────────────────────────

def _curriculum_path() -> Path:
    return Path(repo_root()) / "comms" / "learning" / "curriculum.json"


_CACHE: dict[str, Any] = {}


def _cache_key() -> Any:
    try:
        return _curriculum_path().stat().st_mtime_ns
    except OSError:
        return None


def _load_curriculum() -> dict[str, Any]:
    path = _curriculum_path()
    if not path.exists():
        return {"present": False, "curriculum": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8")) or {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("learning: failed to read %s: %s", path, exc)
        return {"present": False, "curriculum": None, "error": str(exc)}
    return {"present": True, "curriculum": data}


def _get_curriculum() -> dict[str, Any]:
    key = _cache_key()
    if _CACHE.get("key") != key or "data" not in _CACHE:
        _CACHE["key"] = key
        _CACHE["data"] = _load_curriculum()
    return _CACHE["data"]


# ── progress store (trade_journal.db::learning_progress) ────────────────────

def _resolve_db_path() -> str:
    from src.utils.paths import trade_journal_db_path
    return str(trade_journal_db_path())


def _ensure_table() -> None:
    """Idempotent: trigger the centralized DDL in ``Database()`` (Pattern A,
    same as the sibling ``device_tokens`` table)."""
    from src.units.db.database import Database
    Database()


def _read_progress() -> dict[str, Any]:
    _ensure_table()
    db_path = _resolve_db_path()
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=3000")
        rows = conn.execute(
            "SELECT resource_id, status, note, updated_at FROM learning_progress"
        ).fetchall()
    finally:
        conn.close()
    items = {
        r["resource_id"]: {
            "status": r["status"],
            "note": r["note"],
            "updated_at": r["updated_at"],
        }
        for r in rows
    }
    done = sum(1 for v in items.values() if v["status"] == "done")
    in_prog = sum(1 for v in items.values() if v["status"] == "in_progress")
    return {
        "present": True,
        "items": items,
        "summary": {
            "tracked": len(items),
            "done": done,
            "in_progress": in_prog,
        },
    }


def _upsert_progress(resource_id: str, status: str, note: str | None) -> dict[str, Any]:
    _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    db_path = _resolve_db_path()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA busy_timeout=3000")
        conn.execute(
            "INSERT INTO learning_progress (resource_id, status, note, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(resource_id) DO UPDATE SET "
            "status=excluded.status, note=excluded.note, updated_at=excluded.updated_at",
            (resource_id, status, note, now),
        )
        conn.commit()
    finally:
        conn.close()
    return {"resource_id": resource_id, "status": status, "note": note, "updated_at": now}


# ── endpoints ───────────────────────────────────────────────────────────────

@router.get("/curriculum")
def get_curriculum() -> dict[str, Any]:
    """Serve the committed curriculum content. Best-effort: ``present:false``
    if the file is missing/garbled (the dashboard falls back to its bundled
    copy)."""
    return _get_curriculum()


@router.get("/progress")
def get_progress() -> dict[str, Any]:
    """Per-resource progress + a small roll-up. Best-effort: a degraded
    envelope (never a 5xx) if the DB read fails."""
    try:
        return _read_progress()
    except Exception as exc:  # noqa: BLE001  # allow-silent: best-effort read — logs a warning and returns a degraded (present:false) envelope instead of a 5xx, mirroring the reports/roadmap read-path pattern
        logger.warning("learning: progress read failed: %s", exc)
        return {
            "present": False,
            "items": {},
            "summary": {"tracked": 0, "done": 0, "in_progress": 0},
            "error": str(exc),
        }


@router.post("/progress")
async def post_progress(request: Request) -> dict[str, Any]:
    """Upsert one resource's progress. Body:
    ``{resource_id, status ∈ {not_started,in_progress,done}, note?}``.
    Unauthenticated client self-service write (like ``POST /devices/register``)
    so both the dashboard and the Android app can record progress. Tier 1
    (operator observability; no trading impact, no order path, no notification).
    """
    try:
        body = await request.json()
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="invalid JSON") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object")
    resource_id = str(body.get("resource_id") or "").strip()
    if not resource_id:
        raise HTTPException(status_code=400, detail="resource_id is required")
    status = str(body.get("status") or "").strip().lower()
    if status not in _VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"status must be one of {sorted(_VALID_STATUSES)}",
        )
    note = body.get("note")
    if note is not None:
        note = str(note)[:2000]
    try:
        return await asyncio.to_thread(_upsert_progress, resource_id, status, note)
    except Exception as exc:  # noqa: BLE001  # allow-silent: not silent — logs.exception (full trace) then re-raises as HTTP 500; the broad catch stops an internal error leaking its trace to the client
        logger.exception("learning: progress upsert failed")
        raise HTTPException(status_code=500, detail="progress write failed") from exc
