"""S-014 M3 PR #1 — GET /ui/fragments/status.

Auth-gated HTMX fragment that renders ``web/templates/fragments/status.html``
from the same ``runtime_status.json`` snapshot the JSON ``GET /api/status``
endpoint serves. The home page polls this URL every 30 s via
``hx-trigger="load, every 30s"``.

Returns:

* 200 + HTML on success.
* 503 ``status_unavailable`` HTML stub if the status file is missing or
  unreadable (so the polling cell shows a neutral message rather than
  blowing up on a fresh VM where the trader hasn't ticked yet).
* 401 / 403 from ``require_session`` if the bearer token is missing or
  the email isn't allowlisted.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.web.api.auth import require_session
from src.web.api.routers.status import _load_status
from src.web.runtime_status import STATUS_PATH

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[4]
TEMPLATES_DIR = REPO_ROOT / "web" / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(prefix="/ui/fragments", tags=["ui-fragments"])


def _format_uptime(seconds: int) -> str:
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86_400)
    hours, rem = divmod(rem, 3_600)
    minutes, _ = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _render_context(payload: Dict[str, Any]) -> Dict[str, Any]:
    accounts = []
    live_map = payload.get("live") or {}
    for name in sorted(live_map.keys()):
        is_live = bool(live_map.get(name))
        accounts.append({
            "name": name,
            "live": is_live,
            "label": "live" if is_live else "dry",
        })
    return {
        "uptime_human": _format_uptime(int(payload.get("bot_uptime_s") or 0)),
        "git_sha": str(payload.get("git_sha") or "unknown"),
        "strategies": list(payload.get("strategies") or []),
        "accounts": accounts,
        "last_tick_utc": str(payload.get("last_tick_utc") or ""),
    }


@router.get("/status", response_class=HTMLResponse, include_in_schema=False)
async def status_fragment(
    request: Request,
    _session: dict = Depends(require_session),
):
    try:
        payload = _load_status(STATUS_PATH)
    except Exception:  # noqa: BLE001 — render a neutral error stub
        return templates.TemplateResponse(
            "fragments/status_unavailable.html",
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
        "fragments/status.html",
        {"request": request, **_render_context(payload)},
    )
