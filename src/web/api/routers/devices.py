"""Device-token registration endpoints (M12 S1).

Tier-1 surface used by the Android companion app to register its FCM
token with the bot so the ``mobile_push`` notifier can fan out
push notifications to the operator's phone(s).

Endpoints:

- ``POST /api/bot/devices/register`` — upsert a device by its FCM
  token. Idempotent on token: re-registering an existing token updates
  ``last_seen_at`` + label/platform/subscriptions but does NOT create a
  duplicate row.
- ``GET /api/bot/devices`` — list registered devices (without exposing
  the raw FCM token). Token-gated via ``DASHBOARD_API_TOKEN`` if set —
  protects the operator from a misconfigured network leaking device
  enumeration. The router still serves on missing-token (read-only,
  no PII), matching the rest of the dashboard API's permissive default.
- ``DELETE /api/bot/devices/{id}`` — revoke a device (lost phone, etc.).
  Token-gated when ``DASHBOARD_API_TOKEN`` is set.
- ``PATCH /api/bot/devices/{id}/subscriptions`` — update per-device
  subscription preferences (which event_kinds wake the phone). Stored
  as a JSON list of kinds; null/empty means "subscribed to all".

The table itself is created lazily on first ``Database()`` connect —
the schema lives in ``src/units/db/database.py`` alongside the other
tables.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bot/devices", tags=["devices"])

_ALLOWED_PLATFORMS = {"android", "ios"}
_MAX_LABEL_LEN = 100
_MAX_TOKEN_LEN = 4096  # FCM tokens are typically ~160 chars; cap for safety.


def _resolve_db_path() -> str:
    from src.utils.paths import trade_journal_db_path

    return str(trade_journal_db_path())


def _ensure_device_tokens_table_exists() -> None:
    """Idempotent: trigger the lazy table creation in ``Database()``.

    Avoids duplicating the schema DDL here so the canonical source of
    truth stays in ``src/units/db/database.py``.
    """
    from src.units.db.database import Database

    # Instantiating Database runs create_tables(); we don't keep the
    # connection — the router opens its own per-request below.
    Database()


def _check_admin_token(authorization: str | None) -> None:
    """Enforce ``Authorization: Bearer <DASHBOARD_API_TOKEN>`` when set.

    Returns silently when the env var is unset (permissive default,
    matches the rest of the dashboard's Tier-1 read surface). Raises
    401 on present-but-wrong bearer.
    """
    expected = os.environ.get("DASHBOARD_API_TOKEN", "").strip()
    if not expected:
        return
    if not authorization:
        raise HTTPException(status_code=401, detail="missing authorization")
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="bearer scheme required")
    presented = authorization[7:].strip()
    if presented != expected:
        raise HTTPException(status_code=401, detail="bad token")


def _normalize_subscriptions(value: Any) -> str | None:
    """Validate + serialize a subscriptions field for the DB column.

    Accepts:

    - ``None`` → stored as NULL → subscribed to everything.
    - Empty list ``[]`` → stored as ``"[]"`` → subscribed to everything
      (matches the notifier's default-permissive parsing).
    - List of strings ``["trade_closed", "signals"]`` → JSON-encoded.
    - Dict ``{"trade_closed": true, "signals": false}`` → JSON-encoded.

    Unknown kinds are rejected with 400 — the canonical taxonomy lives in
    ``src.runtime.mobile_push.event_kinds`` and a typo here would silently
    never match a publish, which is the exact bug class the validation is
    here to catch. Lands the operator's mistakes loudly at registration
    time instead of as "the toggle doesn't work" three weeks later.

    Anything else raises 400.
    """
    from src.runtime.mobile_push.event_kinds import is_known

    if value is None:
        return None
    if isinstance(value, list):
        if not all(isinstance(s, str) for s in value):
            raise HTTPException(
                status_code=400,
                detail="subscriptions list must contain only strings",
            )
        unknown = [s for s in value if not is_known(s)]
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"unknown subscription kind(s): {unknown}",
            )
        return json.dumps(value)
    if isinstance(value, dict):
        if not all(isinstance(k, str) for k in value.keys()):
            raise HTTPException(
                status_code=400,
                detail="subscriptions dict keys must be strings",
            )
        unknown = [k for k in value.keys() if not is_known(k)]
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"unknown subscription kind(s): {unknown}",
            )
        return json.dumps(value)
    raise HTTPException(
        status_code=400,
        detail="subscriptions must be null, list, or dict",
    )


def _upsert_device_row(
    token: str, platform: str, label: str | None, subscriptions: str | None,
) -> tuple[Any, bool]:
    """Blocking table-ensure + idempotent upsert, isolated for ``to_thread``.

    Returns ``(row, is_new)`` where *row* is
    ``(id, platform, label, subscriptions, created_at, last_seen_at)``.
    """
    _ensure_device_tokens_table_exists()
    db_path = _resolve_db_path()
    conn = sqlite3.connect(db_path)
    try:
        # A locked DB waits up to 3s rather than raising immediately.
        conn.execute("PRAGMA busy_timeout=3000")
        cur = conn.execute(
            "SELECT id, created_at FROM device_tokens WHERE token = ?",
            (token,),
        )
        existing = cur.fetchone()
        if existing:
            device_id = existing[0]
            conn.execute(
                """
                UPDATE device_tokens
                   SET platform = ?,
                       label = COALESCE(?, label),
                       subscriptions = COALESCE(?, subscriptions),
                       last_seen_at = CURRENT_TIMESTAMP
                 WHERE id = ?
                """,
                (platform, label, subscriptions, device_id),
            )
            conn.commit()
            is_new = False
        else:
            cur = conn.execute(
                """
                INSERT INTO device_tokens (token, platform, label, subscriptions)
                VALUES (?, ?, ?, ?)
                """,
                (token, platform, label, subscriptions),
            )
            device_id = cur.lastrowid
            conn.commit()
            is_new = True

        row = conn.execute(
            """
            SELECT id, platform, label, subscriptions, created_at, last_seen_at
              FROM device_tokens
             WHERE id = ?
            """,
            (device_id,),
        ).fetchone()
    finally:
        conn.close()
    return row, is_new


@router.post("/register")
async def register_device(request: Request) -> dict[str, Any]:
    """Upsert a device by FCM token.

    Request JSON: ``{token, platform?, label?, subscriptions?}``.

    Returns ``{id, token_suffix, platform, label, subscriptions,
    created_at, last_seen_at, is_new}`` where ``token_suffix`` is the
    last 8 chars of the token (full token is never echoed back; the
    caller already has it).
    """
    try:
        body = await request.json()
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="invalid JSON") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object")

    token = (body.get("token") or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="token required")
    if len(token) > _MAX_TOKEN_LEN:
        raise HTTPException(status_code=400, detail="token too long")

    platform = (body.get("platform") or "android").strip().lower()
    if platform not in _ALLOWED_PLATFORMS:
        raise HTTPException(
            status_code=400,
            detail=f"platform must be one of: {sorted(_ALLOWED_PLATFORMS)}",
        )

    label = body.get("label")
    if label is not None:
        if not isinstance(label, str):
            raise HTTPException(status_code=400, detail="label must be a string")
        label = label.strip()[:_MAX_LABEL_LEN] or None

    subscriptions = _normalize_subscriptions(body.get("subscriptions"))

    # Offload the blocking table-ensure + upsert to a worker thread; this route
    # stays async for ``await request.json()`` above but the DB work must not
    # run on uvicorn's event loop (RISK-3, BL-20260707-HEALTHAPI-ACCTBAL-BLOCKING-DB).
    row, is_new = await asyncio.to_thread(
        _upsert_device_row, token, platform, label, subscriptions,
    )

    return {
        "id": row[0],
        "token_suffix": token[-8:],
        "platform": row[1],
        "label": row[2],
        "subscriptions": json.loads(row[3]) if row[3] else None,
        "created_at": row[4],
        "last_seen_at": row[5],
        "is_new": is_new,
    }


@router.get("/event-kinds")
def get_event_kinds() -> dict[str, Any]:
    """Return the canonical event-kind taxonomy.

    Used by the Android Notifications screen to populate per-kind toggles
    so the app and the bot stay in sync (the app doesn't have to mirror
    the list, just iterate this response). Each entry carries the
    canonical kind string, a display label, and a one-line description.

    The ``in_flight`` field marks kinds whose payload semantics the bot
    already emits today; reserved kinds are listed so the operator can
    pre-configure their preferences ahead of the wiring landing in
    subsequent M12 sprints.
    """
    from src.runtime.mobile_push.event_kinds import (
        ALL_KINDS,
        DESCRIPTIONS,
        IN_FLIGHT,
        LABELS,
    )

    return {
        "kinds": [
            {
                "kind": k,
                "label": LABELS[k],
                "description": DESCRIPTIONS[k],
                "in_flight": k in IN_FLIGHT,
            }
            for k in ALL_KINDS
        ],
    }


@router.get("")
def list_devices(
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """List registered devices.

    Tokens are not exposed in the response — only ``token_suffix`` (last
    8 chars) for human identification. Token-gated when
    ``DASHBOARD_API_TOKEN`` is set.
    """
    _check_admin_token(authorization)
    _ensure_device_tokens_table_exists()
    db_path = _resolve_db_path()
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA busy_timeout=3000")
        rows = conn.execute(
            """
            SELECT id, token, platform, label, subscriptions,
                   created_at, last_seen_at
              FROM device_tokens
             ORDER BY id ASC
            """
        ).fetchall()
    finally:
        conn.close()
    devices = [
        {
            "id": r[0],
            "token_suffix": (r[1] or "")[-8:],
            "platform": r[2],
            "label": r[3],
            "subscriptions": json.loads(r[4]) if r[4] else None,
            "created_at": r[5],
            "last_seen_at": r[6],
        }
        for r in rows
    ]
    return {"count": len(devices), "devices": devices}


@router.delete("/{device_id}")
def revoke_device(
    device_id: int,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Revoke a device by id."""
    _check_admin_token(authorization)
    _ensure_device_tokens_table_exists()
    db_path = _resolve_db_path()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA busy_timeout=3000")
        cur = conn.execute(
            "DELETE FROM device_tokens WHERE id = ?",
            (device_id,),
        )
        conn.commit()
        deleted = cur.rowcount
    finally:
        conn.close()
    if deleted == 0:
        raise HTTPException(status_code=404, detail="device not found")
    return {"id": device_id, "deleted": True}


def _update_device_subscriptions(device_id: int, subscriptions: str | None) -> int:
    """Blocking subscriptions write, isolated for ``to_thread``. Returns rowcount."""
    _ensure_device_tokens_table_exists()
    db_path = _resolve_db_path()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA busy_timeout=3000")
        cur = conn.execute(
            "UPDATE device_tokens SET subscriptions = ? WHERE id = ?",
            (subscriptions, device_id),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


@router.patch("/{device_id}/subscriptions")
async def update_subscriptions(
    device_id: int,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Replace a device's subscription preferences."""
    _check_admin_token(authorization)
    try:
        body = await request.json()
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="invalid JSON") from exc
    if not isinstance(body, dict) or "subscriptions" not in body:
        raise HTTPException(
            status_code=400,
            detail="body must be {subscriptions: ...}",
        )
    subscriptions = _normalize_subscriptions(body["subscriptions"])

    # Offload the blocking write to a worker thread; this route stays async
    # for ``await request.json()`` above (RISK-3, BL-20260707-HEALTHAPI-ACCTBAL-BLOCKING-DB).
    updated = await asyncio.to_thread(
        _update_device_subscriptions, device_id, subscriptions,
    )
    if updated == 0:
        raise HTTPException(status_code=404, detail="device not found")
    return {
        "id": device_id,
        "subscriptions": json.loads(subscriptions) if subscriptions else None,
    }
