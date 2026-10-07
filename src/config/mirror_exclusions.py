"""Reader for ``config/mirror_window_exclusions.yaml`` (one owner).

Returns the acknowledged mirror-only trades. ``readState`` is explicit so "the
file could not be read" is never folded into "nothing is excluded".
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

EXCLUSIONS_PATH = Path(__file__).resolve().parents[2] / "config" / "mirror_window_exclusions.yaml"


def load_mirror_exclusions(path: Optional[Path] = None) -> Dict[str, Any]:
    """``{"readState": "ok"|"absent"|"unreadable", "exclusions": [...]}``."""
    p = path or EXCLUSIONS_PATH
    if not p.exists():
        return {"readState": "absent", "exclusions": []}
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        rows = data.get("exclusions") or []
        if not isinstance(rows, list):
            raise ValueError("exclusions is not a list")
        return {"readState": "ok", "exclusions": [r for r in rows if isinstance(r, dict)]}
    except Exception:  # noqa: BLE001  # allow-silent: logged + returned as readState "unreadable"; callers publish it rather than treating it as "none excluded"
        logger.exception("mirror exclusions unreadable: %s", p)
        return {"readState": "unreadable", "exclusions": []}


def excluded_trade_ids(path: Optional[Path] = None) -> List[int]:
    out: List[int] = []
    for r in load_mirror_exclusions(path)["exclusions"]:
        try:
            out.append(int(r["trade_id"]))
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(set(out))
