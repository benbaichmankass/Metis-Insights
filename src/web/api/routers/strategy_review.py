"""GET /api/bot/strategies/{name}/review — M7 review packet read.

Serves the latest packet emitted by
``scripts/ml/strategy_review_packet.py`` for a given strategy. Tier 1 —
no auth, read-only.

Response shape (when a packet is present):

    {
      "present": true,
      "packet_path": "/.../runtime_logs/strategy_reviews/2026-06-09/vwap.json",
      "summary_md_path": "/.../runtime_logs/strategy_reviews/2026-06-09/vwap.md",
      "packet": { ... full packet JSON ... }
    }

When no packet exists yet (the strategy has never been reviewed by the
gate), the route returns HTTP 200 with ``present: false`` so the
dashboard can render an empty card without a crash. Tier-3 actions are
*read* here, not enacted.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter

from src.utils.paths import runtime_logs_dir

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bot", tags=["bot"])

# Strategy names in this repo are [a-z0-9_]+; reject anything else to keep
# the path traversal-safe (mirrors the insights router's strategy-name guard).
_NAME_RE = re.compile(r"^[a-z0-9_]+$")


def _reviews_root() -> Path:
    return runtime_logs_dir() / "strategy_reviews"


def _latest_packet(name: str) -> Optional[Path]:
    """Return the most recent <UTC-date>/<name>.json packet path, or None."""
    root = _reviews_root()
    if not root.exists():
        return None
    # Each UTC date is its own subdir; iterate newest-first.
    try:
        day_dirs = sorted(
            (p for p in root.iterdir() if p.is_dir()),
            reverse=True,
        )
    except OSError:
        return None
    for day_dir in day_dirs:
        candidate = day_dir / f"{name}.json"
        if candidate.exists():
            return candidate
    return None


@router.get("/strategies/{name}/review")
def get_strategy_review(name: str) -> Dict[str, Any]:
    if not _NAME_RE.match(name):
        return {"present": False, "error": "invalid_strategy_name"}
    path = _latest_packet(name)
    if not path:
        return {"present": False, "packet_path": None, "summary_md_path": None}
    try:
        packet = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        logger.exception("strategy_review: failed to read packet at %s", path)
        return {"present": False, "error": "read_failed", "packet_path": str(path)}
    md_path = path.with_suffix(".md")
    return {
        "present": True,
        "packet_path": str(path),
        "summary_md_path": str(md_path) if md_path.exists() else None,
        "packet": packet,
    }
