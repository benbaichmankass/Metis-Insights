"""Prop manual-bridge inbound + read surface (P2/P3).

The inbound half of the Breakout manual bridge
(``docs/integrations/breakout-poc-manual-bridge-DESIGN.md``): the executor /
operator posts a fill/close or account-status report back, and the dashboard
reads the journal + rule-distance.

Endpoints:

- ``POST /api/bot/prop/report`` — ingest a fill/close OR account-status report
  (auto-detected, or set ``kind``). Token-gated via ``DASHBOARD_API_TOKEN`` when
  set (a write that fires a notification). Fires ``prop_closed`` on a close.
- ``GET  /api/bot/prop/fills?account_id=&limit=`` — inbound fills/closes.
- ``GET  /api/bot/prop/tickets?account_id=&status=&limit=`` — outbound tickets.
- ``GET  /api/bot/prop/status?account_id=`` — latest account-status snapshot +
  computed rule-distance (distance to the $150 daily-loss / $300 static-DD).
- ``GET  /api/bot/prop/reconcile?account_id=`` — un-acted tickets (emitted, past
  validity, no matching fill) + a summary.

The read endpoints are Tier-1; the POST is Tier-2 (a DB write + notification).
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bot/prop", tags=["prop"])

_DEFAULT_ACCOUNT = "breakout_1"


def _check_admin_token(authorization: str | None) -> None:
    """Enforce ``Authorization: Bearer <DASHBOARD_API_TOKEN>`` when set.

    Permissive default (matches the rest of the dashboard API): returns
    silently when the env var is unset; 401 on present-but-wrong bearer.
    """
    expected = os.environ.get("DASHBOARD_API_TOKEN", "").strip()
    if not expected:
        return
    if not authorization:
        raise HTTPException(status_code=401, detail="missing authorization")
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="bearer scheme required")
    if authorization[7:].strip() != expected:
        raise HTTPException(status_code=401, detail="bad token")


@router.post("/report")
async def post_report(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Ingest a prop fill/close or account-status report-back."""
    _check_admin_token(authorization)
    try:
        body = await request.json()
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="invalid JSON") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object")

    from src.prop.prop_report import ingest_report

    try:
        # Offload the blocking DB write + notification to a worker thread so it
        # never runs on uvicorn's event loop; this route stays async for
        # ``await request.json()`` above (RISK-3,
        # BL-20260707-HEALTHAPI-ACCTBAL-BLOCKING-DB).
        return await asyncio.to_thread(ingest_report, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001  # allow-silent: re-raises as HTTP 500 after logging the stack
        logger.exception("prop report ingest failed")
        raise HTTPException(status_code=500, detail="ingest failed") from exc


@router.get("/fills")
def get_fills(account_id: str | None = None, limit: int = 100) -> dict[str, Any]:
    # Sync route → FastAPI runs it in a threadpool, so the blocking DB read
    # never touches uvicorn's event loop. Graceful-degrade to a present:false
    # envelope on any read error (a locked/absent DB is degraded, not a 500).
    from src.prop import prop_journal

    limit = max(1, min(int(limit), 500))
    try:
        rows = prop_journal.list_fills(account_id=account_id, limit=limit)
        return {"present": bool(prop_journal.tables_present()), "count": len(rows),
                "fills": rows}
    except Exception:  # noqa: BLE001  # allow-silent: degrade to present:false, not a 500
        logger.warning("prop: /fills read failed; degrading to present:false", exc_info=True)
        return {"present": False, "count": 0, "fills": []}


@router.get("/tickets")
def get_tickets(
    account_id: str | None = None, status: str | None = None, limit: int = 100,
) -> dict[str, Any]:
    from src.prop import prop_journal

    limit = max(1, min(int(limit), 500))
    # Canonical view: project over order_packages (every prop decision is
    # journaled there) enriched by the prop_tickets sidecar — NOT the sidecar
    # alone, so tickets emitted before the sidecar existed still appear.
    try:
        rows = prop_journal.list_outbound_tickets(
            account_id=account_id, status=status, limit=limit)
        return {"present": bool(rows) or bool(prop_journal.tables_present()),
                "count": len(rows), "tickets": rows}
    except Exception:  # noqa: BLE001  # allow-silent: degrade to present:false, not a 500
        logger.warning("prop: /tickets read failed; degrading to present:false", exc_info=True)
        return {"present": False, "count": 0, "tickets": []}


@router.get("/status")
def get_status(account_id: str | None = None) -> dict[str, Any]:
    from src.prop import prop_journal, prop_reconcile

    acct = account_id or _DEFAULT_ACCOUNT
    try:
        snapshot = prop_journal.latest_account_status(acct)
        rule_distance = prop_reconcile.compute_rule_distance(acct, snapshot)
        return {
            "account_id": acct,
            "present": snapshot is not None,
            "status": snapshot,
            "rule_distance": rule_distance,
        }
    except Exception:  # noqa: BLE001  # allow-silent: degrade to present:false, not a 500
        logger.warning("prop: /status read failed; degrading to present:false", exc_info=True)
        return {"account_id": acct, "present": False, "status": None,
                "rule_distance": None}


@router.get("/reconcile")
def get_reconcile(account_id: str | None = None) -> dict[str, Any]:
    from src.prop import prop_journal, prop_reconcile

    try:
        unacted = prop_reconcile.find_unacted_tickets(account_id=account_id)
        all_tickets = prop_journal.list_tickets(account_id=account_id, limit=500)
        fills = prop_journal.list_fills(account_id=account_id, limit=500)
        return {
            "present": bool(prop_journal.tables_present()),
            "summary": {
                "tickets_total": len(all_tickets),
                "fills_total": len(fills),
                "unacted_count": len(unacted),
            },
            "unacted_tickets": unacted,
        }
    except Exception:  # noqa: BLE001  # allow-silent: degrade to present:false, not a 500
        logger.warning("prop: /reconcile read failed; degrading to present:false", exc_info=True)
        return {"present": False,
                "summary": {"tickets_total": 0, "fills_total": 0, "unacted_count": 0},
                "unacted_tickets": []}
