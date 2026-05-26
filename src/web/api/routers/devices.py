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

    Anything else raises 400.
    """
    if value is None:
        return None
    if isinstance(value, list):
        if not all(isinstance(s, str) for s in value):
            raise HTTPException(
                status_code=400,
                detail="subscriptions list must contain only strings",
            )
        return json.dumps(value)
    if isinstance(value, dict):
        if not all(isinstance(k, str) for k in value.keys()):
            raise HTTPException(
                status_code=400,
                detail="subscriptions dict keys must be strings",
            )
        return json.dumps(value)
    raise HTTPException(
        status_code=400,
        detail="subscriptions must be null, list, or dict",
    )


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

    _ensure_device_tokens_table_exists()
    db_path = _resolve_db_path()
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "SELECT id, created_at FROM device_tokens WHERE token = ?",
            (token,),
        )
        existing = cur.fetchone()
        if existing:
            device_id, created_at = existing
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
            created_at = None  # will be re-read below
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


@router.get("")
async def list_devices(
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
async def revoke_device(
    device_id: int,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Revoke a device by id."""
    _check_admin_token(authorization)
    _ensure_device_tokens_table_exists()
    db_path = _resolve_db_path()
    conn = sqlite3.connect(db_path)
    try:
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

    _ensure_device_tokens_table_exists()
    db_path = _resolve_db_path()
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "UPDATE device_tokens SET subscriptions = ? WHERE id = ?",
            (subscriptions, device_id),
        )
        conn.commit()
        updated = cur.rowcount
    finally:
        conn.close()
    if updated == 0:
        raise HTTPException(status_code=404, detail="device not found")
    return {
        "id": device_id,
        "subscriptions": json.loads(subscriptions) if subscriptions else None,
    }
