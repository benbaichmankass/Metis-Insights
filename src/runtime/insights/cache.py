"""Atomic cache writer for the AI Analyst.

The router serves these files under ``runtime_logs/insights/``. The
generator writes them with tempfile + ``os.replace`` so a crash
mid-write leaves either the previous good file or nothing — never a
half-written JSON the router would have to fall back from.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from src.utils.paths import runtime_logs_dir

logger = logging.getLogger(__name__)


def insights_dir() -> Path:
    """Return the cache directory, creating it if missing."""
    d = runtime_logs_dir() / "insights"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cache_path(name: str) -> Path:
    """Return the cache file path for an endpoint name (no extension).

    ``name`` is the leaf filename without ``.json`` — e.g. ``summary``,
    ``recent``, ``strategy_vwap``, ``health``. The caller is responsible
    for prefix conventions (``strategy_<name>``).
    """
    return insights_dir() / f"{name}.json"


def write_cache(name: str, payload: dict[str, Any]) -> Path:
    """Atomically write ``payload`` as JSON to the named cache file.

    Returns the resolved path. Uses ``tempfile.NamedTemporaryFile`` +
    ``os.replace`` for atomicity — readers see either the previous
    good file or the new one, never a partial write.
    """
    target = cache_path(name)
    target.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{name}.", suffix=".json.tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, target)
        logger.info("insights.cache: wrote %s (%d bytes)", target, target.stat().st_size)
    except Exception:
        # Best-effort cleanup; don't shadow the original exception.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return target
