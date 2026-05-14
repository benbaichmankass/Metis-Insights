"""Training-center mirror endpoints — surfaces trainer VM state to the dashboard.

The trainer VM (`ict-trainer-vm`) rsyncs a small set of JSONL artifacts and a
`trainer_status.json` blob into `runtime_logs/trainer_mirror/` on this VM via
`scripts/ops/publish_trainer_mirror.sh` (every 2 minutes + at the boundaries
of each training cycle). These endpoints read that mirror; they do **not**
SSH into the trainer themselves, so a temporarily unreachable trainer does
not cause request failures here — the latest available snapshot is returned
along with a `mirror_age_seconds` field the dashboard can use to surface
staleness.

All endpoints are unauth GET (operational telemetry only). Schema-stable;
the dashboard (`page_models()` in `streamlit_app.py`) consumes these.

Mirror layout (relative to ``runtime_logs/trainer_mirror/``):

    trainer_status.json                  ← trainer's own self-report
    training_cycle.jsonl                 ← one line per cycle event
    registry.jsonl                       ← model registry rows
    trainer/dataset_builds.jsonl         ← dataset-build outcomes
    trainer/db_pulls.jsonl               ← live-VM → trainer DB sync log
    experiments-runs/<model_id>/<run_id>/metrics.json
    experiments-runs/<model_id>/<run_id>/manifest.json
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from src.utils.paths import runtime_logs_dir

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bot/ml", tags=["training-center"])

# ``runtime_logs_dir()`` is resolved per-request so tests can monkey-patch
# the env var without import-time caching. The actual mirror dir is a fixed
# subpath under it.
_MIRROR_SUBPATH = "trainer_mirror"

# Whitelist of (model_id, run_id) characters — guards against path traversal
# on the runs/{model_id}/{run_id} endpoint. Mirror keys are produced by the
# trainer and follow a predictable shape, so this is permissive but bounded.
_SAFE_ID = re.compile(r"^[A-Za-z0-9._\-]{1,128}$")


def _mirror_root() -> Path:
    return runtime_logs_dir() / _MIRROR_SUBPATH


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("training_center: failed to read %s: %s", path, exc)
        return None


def _read_jsonl_tail(path: Path, limit: int) -> list[dict[str, Any]]:
    """Return up to ``limit`` newest valid JSON rows from a JSONL file.

    Tolerant of malformed lines (skipped) and missing files (returns []).
    Walks the whole file — fine for the sizes we deal with (a few thousand
    rows at most). Move to a reverse-tail if a mirror grows past ~10 MB.
    """
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    rows.append(obj)
    except OSError as exc:
        logger.warning("training_center: failed to read %s: %s", path, exc)
        return []
    if limit > 0 and len(rows) > limit:
        return rows[-limit:]
    return rows


def _mirror_age_seconds(target: Path) -> float | None:
    if not target.exists():
        return None
    try:
        return max(0.0, time.time() - target.stat().st_mtime)
    except OSError:
        return None


def _mirror_meta() -> dict[str, Any]:
    """Common envelope describing freshness of the mirror.

    `mirror_age_seconds` reflects the newest of `trainer_status.json`'s
    mtime — that file is rewritten by the trainer every publish cycle, so
    it's the canonical heartbeat marker.
    """
    root = _mirror_root()
    status_path = root / "trainer_status.json"
    return {
        "mirror_path": str(root),
        "mirror_present": root.exists(),
        "trainer_status_present": status_path.exists(),
        "mirror_age_seconds": _mirror_age_seconds(status_path),
    }


@router.get("/status")
def get_status() -> dict[str, Any]:
    """Trainer self-report: systemd state, last cycle, registry summary."""
    root = _mirror_root()
    status_path = root / "trainer_status.json"
    payload = _read_json(status_path)
    return {
        **_mirror_meta(),
        "status": payload,
    }


@router.get("/cycle")
def get_cycle(limit: int = Query(default=50, ge=1, le=1000)) -> dict[str, Any]:
    """Tail of `training_cycle.jsonl` — one row per cycle event.

    Event types emitted by `run_training_cycle.sh`:
      pulled, venv_created, sync_ok|sync_warn, datasets_ok|datasets_warn,
      cycle_start, manifest_ok, manifest_failed, manifest_missing,
      cycle_end, publish_pre_ok|warn, publish_post_ok|warn, env_error.
    """
    root = _mirror_root()
    rows = _read_jsonl_tail(root / "training_cycle.jsonl", limit)
    return {
        **_mirror_meta(),
        "limit": limit,
        "rows": rows,
    }


@router.get("/registry")
def get_registry() -> dict[str, Any]:
    """Model registry rows — append-only history from `ml/registry-store/registry.jsonl`."""
    root = _mirror_root()
    rows = _read_jsonl_tail(root / "registry.jsonl", limit=0)
    return {
        **_mirror_meta(),
        "rows": rows,
        "count": len(rows),
    }


@router.get("/sessions")
def get_sessions(limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    """Backward-compat alias for the dashboard's existing "trainer sessions" view.

    Surfaces the same data as `/api/bot/ml/cycle` but filtered to only the
    rows that look like per-manifest training sessions (manifest_ok /
    manifest_failed / manifest_missing). Older dashboard code expects this
    endpoint; new code should prefer `/api/bot/ml/cycle` which carries the
    full event stream including cycle_start and cycle_end markers.
    """
    root = _mirror_root()
    raw = _read_jsonl_tail(root / "training_cycle.jsonl", limit=0)
    keep = {"manifest_ok", "manifest_failed", "manifest_missing"}
    sessions = [r for r in raw if r.get("status") in keep]
    if limit > 0 and len(sessions) > limit:
        sessions = sessions[-limit:]
    return {
        **_mirror_meta(),
        "limit": limit,
        "sessions": sessions,
    }


@router.get("/builds")
def get_builds(limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
    """Tail of `runtime_logs/trainer/dataset_builds.jsonl`.

    Surfaces the dataset-builder failures the 2026-05-13 cycle ran into —
    the dashboard renders these prominently so an empty registry isn't
    silently confusing.
    """
    root = _mirror_root()
    rows = _read_jsonl_tail(root / "trainer" / "dataset_builds.jsonl", limit)
    return {
        **_mirror_meta(),
        "limit": limit,
        "rows": rows,
    }


@router.get("/db_pulls")
def get_db_pulls(limit: int = Query(default=50, ge=1, le=500)) -> dict[str, Any]:
    """Tail of `runtime_logs/trainer/db_pulls.jsonl` — cross-VM data sync history."""
    root = _mirror_root()
    rows = _read_jsonl_tail(root / "trainer" / "db_pulls.jsonl", limit)
    return {
        **_mirror_meta(),
        "limit": limit,
        "rows": rows,
    }


@router.get("/runs/{model_id}/{run_id}")
def get_run(model_id: str, run_id: str) -> dict[str, Any]:
    """Read the metrics + manifest JSON for a single experiment run.

    Path components are validated against ``_SAFE_ID`` — no traversal, no
    weird characters, length-bounded. 404 if either file is missing so the
    dashboard can fall back to a "metrics not mirrored yet" message.
    """
    if not _SAFE_ID.match(model_id) or not _SAFE_ID.match(run_id):
        raise HTTPException(status_code=400, detail="invalid model_id or run_id")
    run_dir = _mirror_root() / "experiments-runs" / model_id / run_id
    metrics = _read_json(run_dir / "metrics.json")
    manifest = _read_json(run_dir / "manifest.json")
    if metrics is None and manifest is None:
        raise HTTPException(status_code=404, detail="run not found in trainer mirror")
    return {
        **_mirror_meta(),
        "model_id": model_id,
        "run_id": run_id,
        "metrics": metrics,
        "manifest": manifest,
    }
