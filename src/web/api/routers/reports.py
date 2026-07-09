"""Tier-1 read endpoints surfacing the consolidated /system-report artifacts.

Backs the "Reports" surface in the Streamlit dashboard (desktop) and the
Android app (mobile) — a log of links to every generated executive report.

- ``GET /api/bot/reports?limit=N&window=X`` — the report index manifest,
  newest-first, optionally filtered by window class.
- ``GET /api/bot/reports/{report_id}`` — one report's index metadata plus its
  rendered HTML body (and a link to the JSON), so a consumer can embed the
  report inline or open it in a WebView.

File-backed from ``comms/reports/`` (committed artifacts the VM's
``ict-git-sync`` mirrors). Read-only, no secrets, no DB — so this adds no
table and is exempt from the new-table-wiring guard. The reports themselves
are produced by the master skill (``.claude/skills/system-review/SKILL.md``;
``system-report`` is a back-compat alias) + ``scripts/reports/render_system_report.py``;
the schema is ``comms/schema/system_report_response.template.json``.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from src.utils.paths import repo_root

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bot/reports", tags=["reports"])

_VALID_WINDOWS = {"since-last", "daily", "weekly", "monthly", "audit"}


def _reports_dir() -> Path:
    return Path(repo_root()) / "comms" / "reports"


def _read_index() -> dict[str, Any]:
    index_path = _reports_dir() / "index.json"
    if not index_path.exists():
        return {"present": False, "schema_version": 1, "reports": []}
    try:
        data = json.loads(index_path.read_text(encoding="utf-8")) or {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("reports: failed to read index %s: %s", index_path, exc)
        return {"present": False, "schema_version": 1, "reports": [], "error": str(exc)}
    data.setdefault("reports", [])
    data["present"] = True
    return data


def _resolve_artifact(rel_path: str | None) -> Path | None:
    """Resolve an index path safely under comms/reports (no traversal)."""
    if not rel_path:
        return None
    root = Path(repo_root()).resolve()
    candidate = (root / rel_path).resolve()
    reports_root = _reports_dir().resolve()
    try:
        candidate.relative_to(reports_root)
    except ValueError:
        logger.warning("reports: rejected out-of-tree artifact path %s", rel_path)
        return None
    return candidate if candidate.exists() else None


@router.get("")
def list_reports(
    limit: int = Query(default=50, ge=1, le=500),
    window: str | None = Query(default=None),
) -> dict[str, Any]:
    """Return the report index, newest-first, optionally filtered by window."""
    data = _read_index()
    rows = data.get("reports", [])
    if window and window in _VALID_WINDOWS:
        rows = [r for r in rows if r.get("window") == window]
    return {
        "present": data.get("present", False),
        "count": len(rows[:limit]),
        "total": len(data.get("reports", [])),
        "window": window,
        "reports": rows[:limit],
    }


@router.get("/{report_id}")
def get_report(report_id: str) -> dict[str, Any]:
    """Return one report's metadata + rendered HTML body."""
    data = _read_index()
    entry = next((r for r in data.get("reports", []) if r.get("id") == report_id), None)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"report {report_id} not found")
    html_path = _resolve_artifact(entry.get("html_path"))
    json_path = _resolve_artifact(entry.get("json_path"))
    html_body = None
    if html_path is not None:
        try:
            html_body = html_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("reports: failed to read html %s: %s", html_path, exc)
    return {
        "present": html_body is not None,
        "report": entry,
        "html": html_body,
        "json_present": json_path is not None,
    }
