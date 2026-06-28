"""Drift-triggered retraining (S-MLOPT-S16, M14 Phase 4.1, 2026-06-07).

Composes the ADWIN drift detector (``ml.shadow.adwin``) with the
existing recency-weighted training pipeline (the S-MLOPT-S2
``sample_weight: {half_life_days: N}`` knob, already wired into both
LightGBM trainers) to fire retrains *on drift*, not just on the daily
cron.

Flow per deployed head:
  1. Stream the head's shadow-prediction scores from
     ``runtime_logs/shadow_predictions.jsonl`` (real-time only —
     backfill records use synthetic timestamps so they'd pollute the
     online detector).
  2. Feed them in chronological order to ADWIN.
  3. When drift fires, dispatch the head's manifest through
     ``python -m ml train ...`` — the trainer reuses whatever
     ``sample_weight`` recency-decay the manifest already declares (S2
     adopted ``half_life_days`` on every regime head), so the retrain
     naturally down-weights the stale tail ADWIN told us to forget.

**Conservative + logged:** the dispatch is opt-in (the orchestrator
script keeps a `DRY_RUN` mode for the first soak), every decision lands
as a JSONL row in ``runtime_logs/drift_retrain.jsonl`` (one event per
manifest considered, drift or not), and there is no per-head retrain
cooldown other than the daily trainer-cycle timer that runs alongside.
Auto-firing a retrain on the trainer is fine (trainer is autonomous),
but the trainer can ONLY write into the registry up to ``advisory``
(``live_approved`` is its retired alias) — it never auto-promotes past
``shadow``, which is the operator-gated
flip. So this loop is bounded: more retrains → more candidate runs in
the registry → ``/ml-review`` and ``promotion-readiness`` decide what
clears the bar.

Pure decision-support module: enumerates manifests + applies the
detector + emits dispatch records. The shell orchestrator
(``scripts/ops/run_drift_retrain.sh``) actually runs the trainer.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import yaml

from ..manifest import canonical_stage
from ..registry.model_registry import ModelRegistry
from .adwin import DEFAULT_DELTA, DEFAULT_MAX_WINDOW, MIN_WINDOW, scan_stream
from .inspector import ShadowRecord, filter_records, iter_records

_LOGGER = logging.getLogger(__name__)

# A model whose canonical registry stage is in this set is considered
# "deployed" enough to be worth watching for drift — anything still
# pre-shadow (canonical ``candidate``) is off the live evaluation path and
# doesn't benefit from drift-triggered retrains. 3-stage collapse
# (2026-06-16): ``shadow`` (track-record accrual) + ``advisory`` (influence;
# legacy limited_live / live_approved normalize to it). Comparisons go
# through ``canonical_stage`` so a stage stored under an old alias still
# matches.
WATCHED_STAGES: frozenset[str] = frozenset({"shadow", "advisory"})


@dataclass(frozen=True)
class RetrainDecision:
    """One head's drift-retrain decision."""

    model_id: str
    stage: str
    manifest_path: str | None
    n_observations: int
    drift_detected: bool
    last_drift_index: int
    n_window_after: int
    mean_window_after: float
    action: str  # "dispatch" | "skip_no_manifest" | "skip_no_drift" | "skip_thin_data"
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "stage": self.stage,
            "manifest_path": self.manifest_path,
            "n_observations": self.n_observations,
            "drift_detected": self.drift_detected,
            "last_drift_index": self.last_drift_index,
            "n_window_after": self.n_window_after,
            "mean_window_after": self.mean_window_after,
            "action": self.action,
            "reason": self.reason,
        }


def _manifest_path_for(entry: Any, configs_root: Path) -> Path | None:
    """Locate the YAML manifest that produced ``entry``.

    Strategy:
      1. The registry entry's manifest dict carries the full body; if
         it has a ``model_id`` field, look for
         ``<configs_root>/<model_id>.yaml`` first.
      2. Fall back to a full glob over ``configs_root`` and match by
         ``model_id`` inside each file.

    Returns ``None`` when no manifest is found — the orchestrator logs
    that as ``skip_no_manifest`` rather than guessing a path.
    """
    direct = configs_root / f"{entry.model_id}.yaml"
    if direct.is_file():
        return direct
    if not configs_root.is_dir():
        return None
    for candidate in sorted(configs_root.glob("*.yaml")):
        try:
            raw = yaml.safe_load(candidate.read_text())
        except (OSError, yaml.YAMLError):
            continue
        if isinstance(raw, dict) and raw.get("model_id") == entry.model_id:
            return candidate
    return None


def evaluate_models(
    *,
    registry_root: Path | str,
    shadow_log: Path | str,
    configs_root: Path | str,
    delta: float = DEFAULT_DELTA,
    min_window: int = MIN_WINDOW,
    max_window: int = DEFAULT_MAX_WINDOW,
    watched_stages: Iterable[str] = WATCHED_STAGES,
) -> list[RetrainDecision]:
    """For every deployed head, scan its real-time shadow scores and decide.

    Pure: reads files + the in-memory registry, returns decisions. The
    caller (orchestrator) writes the JSONL log and fires the actual
    ``python -m ml train`` subprocess for each ``action == "dispatch"``.
    """
    registry = ModelRegistry(Path(registry_root))
    configs_root_path = Path(configs_root)
    records: list[ShadowRecord] = []
    log_path = Path(shadow_log)
    if log_path.is_file():
        # Materialize once: filter_records iterates many times (one per
        # model). Backfill records carry synthetic timestamps so they
        # pollute the chronological online detector — exclude them.
        records = [
            r for r in iter_records(log_path)
            if r.backfill_kind is None
        ]
    # Normalize the watched set so a caller passing legacy alias names still
    # matches canonical registry stages.
    watched = set()
    for s in watched_stages:
        try:
            watched.add(canonical_stage(s))
        except ValueError:
            watched.add(s)
    decisions: list[RetrainDecision] = []
    for entry in registry.list():
        try:
            entry_stage = canonical_stage(entry.target_deployment_stage)
        except ValueError:
            entry_stage = entry.target_deployment_stage
        if entry_stage not in watched:
            continue
        head_records = sorted(
            filter_records(records, model_id=entry.model_id),
            key=lambda r: r.predicted_at_utc,
        )
        manifest_path = _manifest_path_for(entry, configs_root_path)
        if not head_records:
            decisions.append(RetrainDecision(
                model_id=entry.model_id,
                stage=entry.target_deployment_stage,
                manifest_path=str(manifest_path) if manifest_path else None,
                n_observations=0,
                drift_detected=False,
                last_drift_index=-1,
                n_window_after=0,
                mean_window_after=0.0,
                action="skip_thin_data",
                reason="no real-time shadow predictions for this head",
            ))
            continue
        event = scan_stream(
            (r.score for r in head_records),
            model_id=entry.model_id,
            delta=delta, min_window=min_window, max_window=max_window,
        )
        if not event.drift_detected:
            decisions.append(RetrainDecision(
                model_id=entry.model_id,
                stage=entry.target_deployment_stage,
                manifest_path=str(manifest_path) if manifest_path else None,
                n_observations=event.n_observations,
                drift_detected=False,
                last_drift_index=event.last_drift_index,
                n_window_after=event.n_window_after,
                mean_window_after=event.mean_window_after,
                action="skip_no_drift",
                reason=f"ADWIN found no cut over {event.n_observations} records",
            ))
            continue
        if manifest_path is None:
            decisions.append(RetrainDecision(
                model_id=entry.model_id,
                stage=entry.target_deployment_stage,
                manifest_path=None,
                n_observations=event.n_observations,
                drift_detected=True,
                last_drift_index=event.last_drift_index,
                n_window_after=event.n_window_after,
                mean_window_after=event.mean_window_after,
                action="skip_no_manifest",
                reason=(
                    f"drift detected at index {event.last_drift_index} but "
                    f"no manifest found under {configs_root_path}"
                ),
            ))
            continue
        decisions.append(RetrainDecision(
            model_id=entry.model_id,
            stage=entry.target_deployment_stage,
            manifest_path=str(manifest_path),
            n_observations=event.n_observations,
            drift_detected=True,
            last_drift_index=event.last_drift_index,
            n_window_after=event.n_window_after,
            mean_window_after=event.mean_window_after,
            action="dispatch",
            reason=(
                f"ADWIN drift at index {event.last_drift_index}; "
                f"post-cut window n={event.n_window_after}, "
                f"mean={event.mean_window_after:.4f}"
            ),
        ))
    return decisions


def write_log(
    decisions: Sequence[RetrainDecision],
    log_path: Path | str,
    *,
    now_utc: datetime | None = None,
) -> int:
    """Append one JSONL row per decision to ``log_path``; return rows written.

    Idempotent only by convention (the orchestrator runs daily-ish, and
    each row carries the run timestamp); no de-dupe across runs.
    """
    ts = (now_utc or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    out = Path(log_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with out.open("a") as fh:
        for d in decisions:
            payload = {"ts": ts, **d.to_dict()}
            fh.write(json.dumps(payload, sort_keys=True) + "\n")
            written += 1
    return written
