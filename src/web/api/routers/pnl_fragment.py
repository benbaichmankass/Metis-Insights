"""S-014 M3 PR #2 — GET /ui/fragments/pnl.

Auth-gated HTMX fragment that renders the per-account P&L block on the
home dashboard. Wraps ``build_pnl()`` from the JSON ``/api/pnl`` router
(single source of truth) and renders ``fragments/pnl.html``. Polled
every 30 s via the home.html ``hx-get="/ui/fragments/pnl"`` wiring
already in M1 PR #1.

* 200 + HTML on success.
* 503 ``pnl_unavailable`` HTML stub if the DB is corrupt.
* 401 / 403 from ``require_session`` if auth fails.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.web.api.auth import require_session
from src.web.api.routers.pnl import build_pnl

REPO_ROOT = Path(__file__).resolve().parents[4]
TEMPLATES_DIR = REPO_ROOT / "web" / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(prefix="/ui/fragments", tags=["ui-fragments"])


def _render_context(payload: Dict[str, Any]) -> Dict[str, Any]:
    accounts: List[Dict[str, Any]] = []
    raw = payload.get("accounts") or {}
    for name in sorted(raw.keys()):
        a = raw.get(name) or {}
        realized = float(a.get("realized_usd") or 0.0)
        unrealized = float(a.get("unrealized_usd") or 0.0)
        accounts.append({
            "name": name,
            "realized": realized,
            "unrealized": unrealized,
            "trades_today": int(a.get("trades_today") or 0),
            "realized_class": "positive" if realized > 0 else "negative" if realized < 0 else "muted",
            "unrealized_class": "positive" if unrealized > 0 else "negative" if unrealized < 0 else "muted",
        })
    return {
        "accounts": accounts,
        "as_of_utc": str(payload.get("as_of_utc") or ""),
    }


@router.get("/pnl", response_class=HTMLResponse, include_in_schema=False)
async def pnl_fragment(
    request: Request,
    _session: dict = Depends(require_session),
):
    try:
        payload = build_pnl()
    except HTTPException:
        return templates.TemplateResponse(
            "fragments/pnl_unavailable.html",
            {
                "request": request,
                "as_of_utc": datetime.now(timezone.utc)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z"),
            },
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return templates.TemplateResponse(
        "fragments/pnl.html",
        {"request": request, **_render_context(payload)},
    )
