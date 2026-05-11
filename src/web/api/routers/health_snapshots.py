"""Tier-1 read endpoints surfacing the health-snapshot artifacts.

Backs the dashboard's System Health tab.

- ``GET /api/bot/health/latest`` — newest ``artifacts/health/latest.json``
- ``GET /api/bot/health/history?hours=N`` — list of timestamped
  ``health_check_<TS>.json`` files in the same dir, newest-first,
  trimmed to the lookback window.
- ``GET /api/bot/health/snapshot`` — raw text snapshot
  (``artifacts/health/health_snapshot.txt``) tailed to N lines.
- ``GET /api/bot/health/services`` — systemd unit states for the
  trader + web-api services. Best-effort: returns ``[]`` on a host
  without ``systemctl`` (e.g. running locally on macOS) so the
  dashboard renders the panel either way.

All endpoints are unauth GET — operational telemetry only, no
secrets. Service-state output is a fixed allowlist of unit names;
arbitrary ``systemctl`` queries are gated behind ``/api/diag/*``.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bot/health", tags=["health"])

_REPO_ROOT = Path(__file__).resolve().parents[4]
_HEALTH_DIR = _REPO_ROOT / "artifacts" / "health"
_LATEST = _HEALTH_DIR / "latest.json"
_SNAPSHOT_TXT = _HEALTH_DIR / "health_snapshot.txt"
_HISTORY_PATTERN = re.compile(r"^health_check_(\d{8}T\d{6}Z)\.json$")
_HISTORY_TS_FMT = "%Y%m%dT%H%M%SZ"

_SERVICE_UNITS = (
    "ict-trader-live.service",
    "ict-web-api.service",
)


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("health_snapshots: failed to read %s: %s", path, exc)
        return None


@router.get("/latest")
def get_latest() -> dict[str, Any]:
    payload = _read_json(_LATEST)
    if payload is None:
        return {
            "present": False,
            "path": str(_LATEST),
            "snapshot": None,
        }
    return {
        "present": True,
        "path": str(_LATEST),
        "snapshot": payload,
    }


def _parse_history_ts(name: str) -> datetime | None:
    m = _HISTORY_PATTERN.match(name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), _HISTORY_TS_FMT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


@router.get("/history")
def get_history(
    hours: int = Query(default=24, ge=1, le=24 * 14),
    include_payload: bool = Query(default=False),
) -> dict[str, Any]:
    """Return health snapshots from the last ``hours`` hours, newest-first.

    By default, each entry holds only the summary fields (timestamp,
    status, summary, action_required, per-check status). Set
    ``include_payload=true`` to embed the full JSON of every snapshot —
    used by the modal "view raw" path in the dashboard.
    """
    if not _HEALTH_DIR.exists():
        return {"present": False, "dir": str(_HEALTH_DIR), "snapshots": []}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows: list[dict[str, Any]] = []
    for entry in _HEALTH_DIR.iterdir():
        if not entry.is_file():
            continue
        ts = _parse_history_ts(entry.name)
        if ts is None or ts < cutoff:
            continue
        payload = _read_json(entry) or {}
        checks = payload.get("checks") or {}
        # Compact per-check summary: name → status. Notes only appear in
        # the full payload to keep the list lightweight.
        check_status = {
            name: (data.get("status") if isinstance(data, dict) else None)
            for name, data in checks.items()
        }
        row: dict[str, Any] = {
            "file": entry.name,
            "timestamp": ts.isoformat(),
            "payload_timestamp": payload.get("timestamp"),
            "status": payload.get("status"),
            "summary": payload.get("summary"),
            "action_required": payload.get("action_required"),
            "model": payload.get("model"),
            "checks": check_status,
        }
        if include_payload:
            row["payload"] = payload
        rows.append(row)
    rows.sort(key=lambda r: r["timestamp"], reverse=True)
    return {
        "present": True,
        "dir": str(_HEALTH_DIR),
        "hours": hours,
        "snapshots": rows,
    }


@router.get("/snapshot")
def get_snapshot_text(lines: int = Query(default=200, ge=1, le=5000)) -> dict[str, Any]:
    if not _SNAPSHOT_TXT.exists():
        return {"present": False, "path": str(_SNAPSHOT_TXT), "lines": []}
    try:
        with _SNAPSHOT_TXT.open(encoding="utf-8", errors="replace") as fh:
            raw = fh.readlines()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"snapshot read failed: {exc}")
    tail = raw[-lines:] if len(raw) > lines else raw
    return {
        "present": True,
        "path": str(_SNAPSHOT_TXT),
        "lines": [line.rstrip("\n") for line in tail],
    }


def _systemctl_state(unit: str) -> dict[str, Any]:
    out: dict[str, Any] = {"unit": unit, "state": None, "sub_state": None, "active_enter_iso": None}
    if not shutil.which("systemctl"):
        return out
    try:
        proc = subprocess.run(
            [
                "systemctl",
                "show",
                unit,
                "--property=ActiveState",
                "--property=SubState",
                "--property=ActiveEnterTimestamp",
                "--no-pager",
            ],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("health_snapshots: systemctl show %s failed: %s", unit, exc)
        return out
    if proc.returncode != 0:
        return out
    for line in proc.stdout.splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key == "ActiveState":
            out["state"] = value.strip() or None
        elif key == "SubState":
            out["sub_state"] = value.strip() or None
        elif key == "ActiveEnterTimestamp":
            out["active_enter_iso"] = value.strip() or None
    return out


@router.get("/services")
def get_services() -> dict[str, Any]:
    """Service states for the bot's two systemd units.

    Reuses no token (unlike ``/api/diag/services``) because the unit
    allowlist is fixed and the returned shape is just (unit, state,
    sub_state, active_enter_iso) — no journal output, no command
    surface."""
    rows = [_systemctl_state(unit) for unit in _SERVICE_UNITS]
    available = shutil.which("systemctl") is not None
    return {
        "systemctl_available": available,
        "services": rows,
    }
