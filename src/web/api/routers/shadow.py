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

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ml.shadow.drift import compute_drift
from ml.shadow.inspector import (
    aggregate,
    filter_records,
    iter_records,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bot/shadow", tags=["shadow"])

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_LOG = _REPO_ROOT / "runtime_logs" / "shadow_predictions.jsonl"


def _log_path() -> Path:
    override = os.environ.get("SHADOW_PREDICTIONS_LOG")
    return Path(override) if override else _DEFAULT_LOG


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
    """Return per-``(model_id, stage)`` aggregate stats."""
    log = _log_path()
    since_dt = _parse_since(since)
    records = filter_records(
        iter_records(log),
        model_id=model_id,
        stage=stage,
        since=since_dt,
    )
    rows = [
        {
            "model_id": s.model_id,
            "stage": s.stage,
            "count": s.count,
            "score_mean": s.score_mean,
            "score_min": s.score_min if s.count else None,
            "score_max": s.score_max if s.count else None,
            "first_seen": s.first_seen.isoformat() if s.first_seen else None,
            "last_seen": s.last_seen.isoformat() if s.last_seen else None,
            "row_keys_seen": sorted(s.row_keys_seen),
        }
        for s in aggregate(records)
    ]
    return _envelope(log, rows)
