"""S-013 M2 PR #1 — GET /api/status.

Reads the runtime status JSON produced by ``src/web/runtime_status.py``
on every pipeline tick and serves it read-only. The endpoint never reads
from the live bot process directly — it only tails the file the tick
loop writes.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status

from src.web.api.auth import require_session
from src.web.runtime_status import STATUS_PATH

router = APIRouter(prefix="/api", tags=["status"])


def _load_status(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "status_unavailable", "reason": "no status file yet"},
        )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "status_unavailable", "reason": "status file unreadable"},
        )


@router.get("/status")
def get_status(_session: dict = Depends(require_session)) -> Dict[str, Any]:
    return _load_status(STATUS_PATH)
