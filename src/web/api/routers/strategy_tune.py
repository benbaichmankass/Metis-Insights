"""GET /api/bot/strategies/{name}/tune — M8 tune-result read.

Serves the most recent tune-sweep results emitted by
``scripts/ml/strategy_tune_sweep.py`` for a given strategy. A strategy may have
several tuned parameters (e.g. ``min_confidence``, ``trail_mult``), each written
as ``runtime_logs/strategy_tunes/<UTC-date>/<strategy>__<param>.json``; this
route returns every param from the newest date that has any. Tier 1 — no auth,
read-only. The Tier-3 *application* of a tune is the operator's; this surfaces
the evidence only.

Response shape (when results are present):

    {
      "present": true,
      "date": "2026-06-10",
      "dir": "/.../runtime_logs/strategy_tunes/2026-06-10",
      "results": [ { ...strategy_tune_result/v1 JSON... }, ... ]
    }

When no result exists yet the route returns HTTP 200 with ``present: false`` so
the dashboard can render an empty card without a crash.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter

from src.utils.paths import runtime_logs_dir

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bot", tags=["bot"])

# Strategy names are [a-z0-9_]+; reject anything else to keep the path
# traversal-safe (mirrors the strategy_review / insights guards).
_NAME_RE = re.compile(r"^[a-z0-9_]+$")


def _tunes_root() -> Path:
    return runtime_logs_dir() / "strategy_tunes"


def _latest_date_dir_with(name: str) -> Optional[Path]:
    """Return the newest <UTC-date> dir holding at least one <name>__*.json."""
    root = _tunes_root()
    if not root.exists():
        return None
    try:
        day_dirs = sorted((p for p in root.iterdir() if p.is_dir()), reverse=True)
    except OSError:
        return None
    for day_dir in day_dirs:
        if any(day_dir.glob(f"{name}__*.json")):
            return day_dir
    return None


@router.get("/strategies/{name}/tune")
def get_strategy_tune(name: str) -> Dict[str, Any]:
    if not _NAME_RE.match(name):
        return {"present": False, "error": "invalid_strategy_name"}
    day_dir = _latest_date_dir_with(name)
    if day_dir is None:
        return {"present": False, "date": None, "dir": None, "results": []}
    results: List[Dict[str, Any]] = []
    for path in sorted(day_dir.glob(f"{name}__*.json")):
        try:
            results.append(json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError):
            logger.exception("strategy_tune: failed to read %s", path)
    return {
        "present": bool(results),
        "date": day_dir.name,
        "dir": str(day_dir),
        "results": results,
    }
