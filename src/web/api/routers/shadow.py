"""S-AI-WS8-PART-2 — Shadow-mode predictions dashboard endpoints.

Read-only views over ``runtime_logs/shadow_predictions.jsonl`` (the
WS7 audit log). Reuses ``ml.shadow.inspector`` so parsing,
filtering, and aggregation match the CLI exactly — no duplicate
implementation.

Two endpoints, both unauthenticated GET (Tier 1, operational
telemetry, no secrets):

- ``GET /api/bot/shadow/predictions`` — newest-N records, with
  filters (``limit``, ``model_id``, ``stage``, ``since``).
- ``GET /api/bot/shadow/stats`` — per-``(model_id, stage)``
  aggregate (count, score mean/min/max, first/last seen).

Both endpoints follow the S-061 contract: optional fields
serialize as ``null`` when missing; ``[]`` distinguishes "no
records matched" from "log file missing" via the ``log_present``
flag in the response envelope.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ml.shadow.drift import compute_drift
from ml.shadow.inspector import (
    aggregate,
    coverage,
    filter_records,
    iter_records,
    mean_cadence_seconds,
    resolve_soak_start,
    soak_start_basis,
    stage_entry_times,
    stage_registration_times,
)

from src.utils.paths import runtime_logs_dir

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bot/shadow", tags=["shadow"])

# The published registry mirror on THIS host. The trainer VM keeps its own
# `ml/registry-store/registry.jsonl`; the live VM reads the mirror rsynced by
# `scripts/ops/publish_trainer_mirror.sh` — the same file
# `training_center.get_registry` serves, resolved the same way so the two can
# never disagree about where the registry is.
_REGISTRY_MIRROR = ("trainer_mirror", "registry.jsonl")


def _registry_path() -> Path:
    return runtime_logs_dir().joinpath(*_REGISTRY_MIRROR)


def _registry_rows() -> tuple[list[dict[str, Any]], bool]:
    """Registry rows from the mirror, plus whether we could actually read it.

    The bool is NOT `bool(rows)`: an unreadable mirror and a genuinely empty
    one are different facts, and only the first means "we could not look".
    Collapsing them would let a missing mirror render as "no model has a
    registry soak start", which is the shape of the bug this fixes.
    """
    path = _registry_path()
    if not path.exists():
        return [], False
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
                    continue          # one bad row must not blind the surface
                if isinstance(obj, dict):
                    rows.append(obj)
    except OSError as exc:
        logger.warning("shadow: registry mirror unreadable at %s: %s", path, exc)
        return [], False
    return rows, True


def _stage_entry_lookup(rows: list[dict[str, Any]]):
    """Return a `stage -> {model_id: entered_at}` lookup memoised per request.

    `aggregate` returns many rows sharing a stage (19 live models over ~3
    stages), and without memoising, the whole registry would be re-scanned once
    per row. Deliberately a closure over a request-local dict rather than a
    default-arg cache — a module-level cache would go stale the moment the
    mirror rsyncs, serving a soak start from a registry that has since changed.
    """
    cache: dict[str, tuple[dict[str, datetime], dict[str, datetime]]] = {}

    def lookup(stage: str) -> tuple[dict[str, datetime], dict[str, datetime]]:
        """(transitions, registrations) for *stage*."""
        if stage not in cache:
            cache[stage] = (
                stage_entry_times(rows, stage=stage),
                stage_registration_times(rows, stage=stage),
            )
        return cache[stage]

    return lookup


def _log_path() -> Path:
    # Aligned with the WS7 writer (which respects runtime_logs_dir()).
    # SHADOW_PREDICTIONS_LOG remains an explicit per-path override for
    # operator overrides + tests; absent that, fall through the shared
    # helper so DATA_DIR / RUNTIME_LOGS_DIR overrides apply.
    override = os.environ.get("SHADOW_PREDICTIONS_LOG")
    return Path(override) if override else runtime_logs_dir() / "shadow_predictions.jsonl"


def _parse_since(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    try:
        ts = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=(
                f"since must be ISO-8601 (e.g. '2026-05-10' or "
                f"'2026-05-10T12:00:00+00:00'); got {raw!r} ({exc})"
            ),
        ) from exc
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _envelope(log: Path, records: list[dict[str, Any]]) -> dict[str, Any]:
    """Common response envelope. ``log_present`` lets the dashboard
    distinguish 'no records yet' from 'shadow mode never wrote here'."""
    return {
        "log_present": log.is_file(),
        "log_path": str(log),
        "records": records,
        "count": len(records),
    }


@router.get("/predictions")
def predictions(
    limit: int = Query(default=50, ge=1, le=1000),
    model_id: str | None = Query(default=None),
    stage: str | None = Query(default=None),
    since: str | None = Query(default=None),
) -> dict[str, Any]:
    """Return the newest ``limit`` shadow-prediction records,
    filtered."""
    log = _log_path()
    since_dt = _parse_since(since)
    records = list(filter_records(
        iter_records(log),
        model_id=model_id,
        stage=stage,
        since=since_dt,
    ))
    records.sort(key=lambda r: r.predicted_at_utc, reverse=True)
    records = records[:limit]
    rows = [
        {
            "predicted_at_utc": r.predicted_at_utc.isoformat(),
            "model_id": r.model_id,
            "stage": r.stage,
            "score": r.score,
            "row_keys": list(r.row_keys),
        }
        for r in records
    ]
    return _envelope(log, rows)


@router.get("/drift")
def drift(
    model_id: str = Query(..., description="model_id to slice on (drift is per-model)"),
    stage: str | None = Query(default=None),
    reference_days: float = Query(default=30.0, gt=0, le=365),
    current_days: float = Query(default=7.0, gt=0, le=365),
    bins: int = Query(default=10, ge=2, le=100),
    score_min: float = Query(default=0.0),
    score_max: float = Query(default=1.0),
) -> dict[str, Any]:
    """Window-over-window drift report (S-AI-WS8-PART-3).

    Reference window = the ``reference_days`` immediately before
    the current window. Current window = the most recent
    ``current_days``. Both windows are non-overlapping and
    anchored at "now".

    Returns the summary stats for each window, KS statistic + PSI
    score + per-metric verdict, and an ``overall_verdict``
    (worst of the two). When either window is empty,
    ``verdict == "insufficient_data"`` and no metrics are
    computed.
    """
    if score_max <= score_min:
        raise HTTPException(
            status_code=400,
            detail=f"score_max ({score_max}) must be > score_min ({score_min})",
        )
    log = _log_path()
    now = datetime.now(timezone.utc)
    current_start = now - timedelta(days=current_days)
    reference_start = current_start - timedelta(days=reference_days)
    all_records = list(filter_records(
        iter_records(log), model_id=model_id, stage=stage,
    ))
    reference_scores = [
        r.score for r in all_records
        if reference_start <= r.predicted_at_utc < current_start
    ]
    current_scores = [
        r.score for r in all_records
        if r.predicted_at_utc >= current_start
    ]
    base_envelope = {
        "log_present": log.is_file(),
        "log_path": str(log),
        "model_id": model_id,
        "stage": stage,
        "reference_window_start": reference_start.isoformat(),
        "current_window_start": current_start.isoformat(),
        "reference_count": len(reference_scores),
        "current_count": len(current_scores),
    }
    if not reference_scores or not current_scores:
        return {**base_envelope, "verdict": "insufficient_data"}
    report = compute_drift(
        reference_scores, current_scores,
        bins=bins, score_min=score_min, score_max=score_max,
    )
    return {
        **base_envelope,
        "verdict": report.overall_verdict,
        "reference_mean": report.reference.mean,
        "current_mean": report.current.mean,
        "reference_stdev": report.reference.stdev,
        "current_stdev": report.current.stdev,
        "ks": report.ks,
        "ks_verdict": report.ks_verdict,
        "psi": report.psi,
        "psi_verdict": report.psi_verdict,
    }


@router.get("/stats")
def stats(
    model_id: str | None = Query(default=None),
    stage: str | None = Query(default=None),
    since: str | None = Query(default=None),
) -> dict[str, Any]:
    """Return per-``(model_id, stage)`` aggregate stats.

    **Read ``first_seen`` under ``soak_start_basis``, never alone**
    (BL-20260810-SHADOW-STATS-FIRSTSEEN-IS-LOG-ROTATION-NOT-SOAK-START). The
    prediction log is rotated, so for any model already running when the last
    rotation fired, ``first_seen`` is the ROTATION BOUNDARY — a lower bound on
    the soak, not its start. Since ``first_seen`` is the denominator of the
    shadow->advisory promotion gate, and the error makes long soaks look short,
    a promotion that is DUE would read as not-yet-ready.

    The envelope therefore also states the retained window it was measured
    against (``log_coverage``), and each row carries ``soak_start_basis`` ∈
    ``observed`` / ``log_censored`` / ``unknown``.
    """
    log = _log_path()
    since_dt = _parse_since(since)
    # ONE pass, materialised: the censoring test needs the log's own oldest
    # edge, which a model-filtered stream cannot provide (every model would
    # then appear to start at its own first row — precisely the defect).
    all_records = list(iter_records(log))
    cov = coverage(all_records)
    records = filter_records(
        iter(all_records),
        model_id=model_id,
        stage=stage,
        since=since_dt,
    )
    registry_rows, registry_present = _registry_rows()
    entered_for_stage = _stage_entry_lookup(registry_rows)

    def _row(s: Any) -> dict[str, Any]:
        # The soak start PREFERS the registry's durable stage-transition
        # record; the log-derived answer is the fallback and declares its own
        # censoring. Resolved per row because the entry map is keyed on the
        # row's OWN stage — a model at `advisory` and the same model's earlier
        # `shadow` rows have different soak starts.
        entered, registered = entered_for_stage(s.stage)
        soak = resolve_soak_start(
            s, cov,
            registry_entered_at=entered,
            registry_registered_at=registered,
        )
        return {
            "model_id": s.model_id,
            "stage": s.stage,
            "count": s.count,
            "score_mean": s.score_mean,
            "score_min": s.score_min if s.count else None,
            "score_max": s.score_max if s.count else None,
            "first_seen": s.first_seen.isoformat() if s.first_seen else None,
            "last_seen": s.last_seen.isoformat() if s.last_seen else None,
            # Whether `first_seen` is this model's real start or the log's edge.
            # Retained unchanged: it describes the LOG, and stays readable even
            # when the registry supplies a better soak start.
            "soak_start_basis": soak_start_basis(s, cov),
            "cadence_seconds_est": mean_cadence_seconds(s),
            "row_keys_seen": sorted(s.row_keys_seen),
            # The recovery half — read `soak_days` under `soak_start_basis`.
            **{k: v for k, v in soak.to_dict().items()
               if k not in ("model_id", "stage")},
        }

    rows = [_row(s) for s in aggregate(records)]
    envelope = _envelope(log, rows)
    envelope["registry_soak_source"] = {
        "present": registry_present,
        "rows": len(registry_rows),
        "path": str(_registry_path()),
        "note": (
            "The durable, rotation-independent soak start comes from the model "
            "registry's stage_history (published mirror). When present, a row's "
            "soak_start_basis is 'registry' and soak_days is MEASURED. When "
            "absent (present:false, or no stage_history for that model), the row "
            "falls back to the log and says so via 'observed' / 'log_censored' / "
            "'unknown' — a 'log_censored' soak_days is a LOWER BOUND, never the "
            "soak. present:false means we could not look; it does not mean the "
            "models have no soak history."
        ),
    }
    envelope["log_coverage"] = {
        "oldest_retained": cov.oldest.isoformat() if cov.oldest else None,
        "newest_retained": cov.newest.isoformat() if cov.newest else None,
        "total_records": cov.total_records,
        "note": (
            "The retained window, NOT the models' soak history — the log is "
            "rotated (ict-shadow-log-rotate.timer). A row whose "
            "soak_start_basis is 'log_censored' was already running before "
            "oldest_retained, so its first_seen is a LOWER BOUND. True soak "
            "start lives in the model registry's stage-transition record."
        ),
    }
    return envelope
