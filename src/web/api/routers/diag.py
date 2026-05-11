"""S-051 — Read-only diagnostic endpoints for off-VM Claude / operator scripts.

Token-gated by ``DIAG_READ_TOKEN``. GET-only. Never returns secret material.
The PM-side / web-sandbox session has no mutation authority on the VM by
design — see ``docs/claude/vm-operator-mode.md`` § 9.

Allowlists for tables, systemd units, and log files are hard-coded at module
load. There is no path-traversal or arbitrary-SQL surface: callers pass an
alias which the server resolves via a static mapping. The sqlite connection
is opened with ``mode=ro`` so a downstream bug introducing UPDATE/DELETE
would still fail at the driver level.

Failure modes:
- 503 ``diag_disabled`` if ``DIAG_READ_TOKEN`` is unset (feature off).
- 401 ``missing_token`` / ``invalid_token`` on bad bearer.
- 400 ``unknown_<thing>`` on requests outside the allowlists.
- 503 ``journal_unavailable`` on a structural sqlite3.Error inside
  ``_journal_select`` (S-067 — was previously a silent ``[]``).
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from src.web.runtime_status import _resolve_git_sha

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/diag", tags=["diag"])

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DB_PATH = Path(os.environ.get("TRADE_JOURNAL_DB", str(_REPO_ROOT / "trade_journal.db")))
_RUNTIME_LOGS = _REPO_ROOT / "runtime_logs"
_AUDIT_LOG = _RUNTIME_LOGS / "signal_audit.jsonl"
_HEARTBEAT = _RUNTIME_LOGS / "heartbeat.txt"
_STATUS_JSON = _RUNTIME_LOGS / "runtime_status.json"
_BOT_LOG = _REPO_ROOT / "bot.log"

_JOURNAL_TABLES: dict[str, str] = {
    "order_packages": "datetime(updated_at)",
    "trades": "id",
}

_CANONICAL_UNITS: tuple[str, ...] = (
    "ict-bot.service",
    "ict-trader-live.service",
    "ict-web-api.service",
    "ict-telegram-bot.service",
    "ict-heartbeat.service",
    "ict-git-sync.service",
    "ict-git-sync.timer",
)

_LOG_FILES: dict[str, Path] = {
    "audit": _AUDIT_LOG,
    "status": _STATUS_JSON,
    "heartbeat": _HEARTBEAT,
    "bot_log": _BOT_LOG,
}

_DEFAULT_LIMIT = 100
_MAX_LIMIT = 1000
_DEFAULT_JOURNAL_LINES = 200
_MAX_JOURNAL_LINES = 2000
_JOURNALCTL_TIMEOUT_S = 10
_SYSTEMCTL_TIMEOUT_S = 5


def _diag_token() -> str | None:
    tok = os.environ.get("DIAG_READ_TOKEN", "").strip()
    return tok or None


def _require_diag_token(request: Request) -> None:
    expected = _diag_token()
    if expected is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "diag_disabled"},
        )
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "missing_token"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    presented = auth[len("Bearer "):].strip()
    if not presented or not hmac.compare_digest(presented, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_token"},
            headers={"WWW-Authenticate": "Bearer"},
        )


def _clamp(value: int | None, default: int, max_: int) -> int:
    if value is None or value < 1:
        return default
    return min(value, max_)


def _normalize_unit(unit: str) -> str:
    canonical = unit if "." in unit else f"{unit}.service"
    if canonical not in _CANONICAL_UNITS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "unknown_unit", "allowed": list(_CANONICAL_UNITS)},
        )
    return canonical


def _heartbeat_snapshot() -> dict[str, Any]:
    from src.runtime.heartbeat import heartbeat_label  # local import to keep router cheap
    if not _HEARTBEAT.exists():
        return {"present": False, "mtime": None, "age_seconds": None, "label": "stopped"}
    mtime = _HEARTBEAT.stat().st_mtime
    age = time.time() - mtime
    return {
        "present": True,
        "mtime": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
        "age_seconds": round(age, 2),
        "label": heartbeat_label(age),
    }


def _status_json_payload() -> dict[str, Any] | None:
    if not _STATUS_JSON.exists():
        return None
    try:
        with _STATUS_JSON.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        # S-067 borderline: was silently `return None`. Keep the
        # `None` sentinel (callers branch on it) but log so a
        # corrupt status.json is visible in bot.log next time.
        logger.warning(
            "diag: status_json read failed: %s: %s",
            type(exc).__name__, exc,
        )
        return None


def _audit_tail(limit: int) -> list[dict[str, Any]]:
    if not _AUDIT_LOG.exists():
        return []
    try:
        with _AUDIT_LOG.open(encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError as exc:
        # S-067 borderline: was silently `return []`. Log so a
        # signal_audit.jsonl read failure surfaces.
        logger.warning(
            "diag: audit_tail read failed: %s: %s",
            type(exc).__name__, exc,
        )
        return []
    out: list[dict[str, Any]] = []
    for raw in lines[-limit:]:
        if not raw.strip():
            continue
        try:
            out.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return out


def _journal_select(table: str, limit: int) -> list[dict[str, Any]]:
    if table not in _JOURNAL_TABLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "unknown_table", "allowed": sorted(_JOURNAL_TABLES.keys())},
        )
    if not _DB_PATH.exists():
        # Genuine "DB hasn't been created yet" — distinct from "DB
        # reachable but broken". Keep the empty-list shape here so a
        # fresh install doesn't 503 out of the gate.
        return []
    order_col = _JOURNAL_TABLES[table]
    try:
        # mode=ro guarantees no mutation can happen here even if a future
        # change accidentally introduces an UPDATE/DELETE statement.
        conn = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True)
        try:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                f"SELECT * FROM {table} ORDER BY {order_col} DESC LIMIT ?",
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        # S-067: "no such table" / schema mismatch / locked DB / corrupt
        # file used to be silently swallowed and surfaced as ``[]`` —
        # indistinguishable from "table empty". The /db_info endpoint
        # was added in #624 specifically to work around this; this is
        # the actual fix. Operator scripts and off-VM Claude sessions
        # now see a real 503 instead of a misleading empty result.
        logger.exception("diag: _journal_select(table=%s) sqlite read failed", table)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "journal_unavailable",
                "table": table,
                "reason": f"sqlite error: {type(exc).__name__}",
            },
        )


def _db_info_payload() -> dict[str, Any]:
    """Return DB metadata for diagnostic cross-referencing of trader vs
    web-api. Resolves the same ``_DB_PATH`` the journal endpoint reads,
    plus inode + size + table list + per-table row count.

    The 2026-05-09 ``order_packages returns []`` mystery surfaced
    because the existing ``journal`` endpoint silently swallowed
    ``sqlite3.Error`` (returns ``[]``) — so a "no such table" or schema
    mismatch was indistinguishable from "table empty". S-067 fixed the
    journal endpoint itself; this endpoint stays as the
    failure-surfacing companion (it returns the per-table error string
    even when the journal endpoint already 503s on the same condition).

    Best-effort: every step is wrapped so a single failure never
    aborts the whole payload. ``error_per_table`` is only populated
    when a SELECT raised; missing keys mean the count succeeded.
    """
    payload: dict[str, Any] = {
        "db_path": str(_DB_PATH),
        "db_path_resolved": None,
        "exists": False,
        "size_bytes": None,
        "inode": None,
        "tables": [],
        "row_counts": {},
        "error_per_table": {},
        "load_error": None,
    }
    try:
        payload["db_path_resolved"] = str(_DB_PATH.resolve())
    except Exception as exc:  # noqa: BLE001
        payload["load_error"] = f"resolve: {type(exc).__name__}: {exc}"
        return payload

    if not _DB_PATH.exists():
        return payload
    payload["exists"] = True
    try:
        st = os.stat(_DB_PATH)
        payload["size_bytes"] = st.st_size
        payload["inode"] = st.st_ino
    except OSError as exc:
        payload["load_error"] = f"stat: {type(exc).__name__}: {exc}"

    try:
        conn = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True)
        try:
            tables = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "ORDER BY name"
                ).fetchall()
            ]
            payload["tables"] = tables
            for tbl in tables:
                try:
                    cur = conn.execute(f"SELECT COUNT(*) FROM {tbl}")
                    payload["row_counts"][tbl] = int(cur.fetchone()[0])
                except sqlite3.Error as exc:
                    payload["error_per_table"][tbl] = (
                        f"{type(exc).__name__}: {exc}"
                    )
        finally:
            conn.close()
    except sqlite3.Error as exc:
        payload["load_error"] = f"connect: {type(exc).__name__}: {exc}"

    return payload


# S-067 follow-up #9: vm_health implementation moved to
# src/web/api/_vm_health.py to remove the diag.py / dashboard.py
# fork. Re-exported under the legacy ``_vm_health`` name so
# tests (e.g. tests/test_web_api_diag.py + the monkeypatching in
# the S-067 silent-empty regression tests) keep working without
# modification.
from src.web.api._vm_health import vm_health as _vm_health  # noqa: E402


def _is_active_batch(units: list[str]) -> dict[str, str]:
    if not units:
        return {}
    try:
        proc = subprocess.run(
            ["systemctl", "is-active", *units],
            capture_output=True,
            text=True,
            timeout=_SYSTEMCTL_TIMEOUT_S,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {u: "unknown" for u in units}
    states = (proc.stdout or "").splitlines()
    return {
        u: (states[i].strip() if i < len(states) else "unknown")
        for i, u in enumerate(units)
    }


def _journalctl_tail(unit: str, lines: int) -> dict[str, Any]:
    canonical = _normalize_unit(unit)
    try:
        proc = subprocess.run(
            [
                "journalctl",
                "-u",
                canonical,
                "-n",
                str(lines),
                "--no-pager",
                "--output=short-iso",
            ],
            capture_output=True,
            text=True,
            timeout=_JOURNALCTL_TIMEOUT_S,
            check=False,
        )
    except FileNotFoundError:
        return {"unit": canonical, "available": False, "reason": "journalctl_not_found", "lines": []}
    except subprocess.TimeoutExpired:
        return {"unit": canonical, "available": False, "reason": "timeout", "lines": []}
    output = proc.stdout or ""
    out_lines = output.splitlines()[-lines:] if output else []
    return {
        "unit": canonical,
        "available": proc.returncode == 0,
        "returncode": proc.returncode,
        "lines": out_lines,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/snapshot")
async def get_snapshot(request: Request, limit: int = _DEFAULT_LIMIT) -> dict[str, Any]:
    _require_diag_token(request)
    n = _clamp(limit, _DEFAULT_LIMIT, _MAX_LIMIT)
    states = _is_active_batch(list(_CANONICAL_UNITS))
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "heartbeat": _heartbeat_snapshot(),
        "status": _status_json_payload(),
        "audit_tail": _audit_tail(n),
        "order_packages": _journal_select("order_packages", n),
        "trades": _journal_select("trades", n),
        "vm_health": _vm_health(),
        "services": [{"unit": u, "state": states.get(u, "unknown")} for u in _CANONICAL_UNITS],
    }


@router.get("/audit")
async def get_audit(request: Request, limit: int = _DEFAULT_LIMIT) -> list[dict[str, Any]]:
    _require_diag_token(request)
    return _audit_tail(_clamp(limit, _DEFAULT_LIMIT, _MAX_LIMIT))


@router.get("/journal")
async def get_journal(
    request: Request,
    table: str,
    limit: int = _DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    _require_diag_token(request)
    return _journal_select(table, _clamp(limit, _DEFAULT_LIMIT, _MAX_LIMIT))


@router.get("/db_info")
async def get_db_info(request: Request) -> dict[str, Any]:
    """Diagnostic — resolved DB path, inode, table list, row counts.

    Companion to ``/journal``. Surfaces the per-table error string when
    a SELECT raises (``journal`` swallows it as ``[]``). Trader vs
    web-api inode mismatch on the same logical path is the canonical
    signature for the 2026-05-09 ``order_packages returns []`` mystery.
    """
    _require_diag_token(request)
    return _db_info_payload()


@router.get("/status")
async def get_status(request: Request) -> dict[str, Any]:
    _require_diag_token(request)
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "heartbeat": _heartbeat_snapshot(),
        "status": _status_json_payload(),
        "vm_health": _vm_health(),
    }


@router.get("/services")
async def get_services(request: Request) -> list[dict[str, str]]:
    _require_diag_token(request)
    states = _is_active_batch(list(_CANONICAL_UNITS))
    return [{"unit": u, "state": states.get(u, "unknown")} for u in _CANONICAL_UNITS]


@router.get("/journalctl")
async def get_journalctl(
    request: Request,
    unit: str,
    lines: int = _DEFAULT_JOURNAL_LINES,
) -> dict[str, Any]:
    _require_diag_token(request)
    return _journalctl_tail(unit, _clamp(lines, _DEFAULT_JOURNAL_LINES, _MAX_JOURNAL_LINES))


@router.get("/version")
async def get_version(request: Request) -> dict[str, Any]:
    """Diagnostic — git SHA + captured timestamp of the running web-api
    process. Used by ``scripts/deploy_pull_restart.sh`` to assert that
    a post-deploy restart actually rolled the running code forward
    (the 2026-05-09 24h-stale-code incident shipped because nothing
    in the deploy chain confirmed the running web-api had rebooted).

    Returns ``git_sha`` resolved by the same helper that powers
    ``runtime_logs/runtime_status.json::git_sha`` so the value is consistent
    between read sources. ``"unknown"`` is a legitimate value on
    sandbox / dev hosts without git available; the deploy script
    treats ``unknown`` as a soft failure.
    """
    _require_diag_token(request)
    return {
        "git_sha": _resolve_git_sha(),
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/log_file")
async def get_log_file(
    request: Request,
    name: str,
    lines: int = _DEFAULT_LIMIT,
) -> dict[str, Any]:
    _require_diag_token(request)
    if name not in _LOG_FILES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "unknown_log_file", "allowed": sorted(_LOG_FILES.keys())},
        )
    n = _clamp(lines, _DEFAULT_LIMIT, _MAX_LIMIT)
    path = _LOG_FILES[name]
    if not path.exists():
        return {"name": name, "path": str(path), "present": False, "lines": []}
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            content = fh.readlines()
    except OSError as exc:
        return {
            "name": name,
            "path": str(path),
            "present": True,
            "error": str(exc),
            "lines": [],
        }
    return {
        "name": name,
        "path": str(path),
        "present": True,
        "size_bytes": path.stat().st_size,
        "lines": [ln.rstrip("\n") for ln in content[-n:]],
    }
