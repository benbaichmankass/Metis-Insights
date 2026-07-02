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

# Three-bucket deployment view (per operator directive 2026-05-18). The
# registry has 3 canonical stages; from a runtime-impact perspective:
#   LIVE    — model at the influence stage (canonical `advisory`; legacy
#             `limited_live` / `live_approved` normalize to it): the live
#             order path's advisory hook
#             (src/runtime/advisory_sizing.py::compute_advisory_factor)
#             scores it and can downsize a real order. Stage-driven and
#             registry-global (the advisory hook discovers influence-stage
#             models across the whole registry, not via shadow_model_ids),
#             so a model is LIVE on stage alone.
#   SHADOW  — `shadow`-stage model wired into a strategy's predictor list
#             (explicit shadow_model_ids or the auto-wire default):
#             predictions are logged but decisions are unchanged.
#   OFFLINE — exists in the registry but is neither influencing nor observed.
#
# History: until the advisory-influence path landed
# (src/runtime/advisory_influence.py + advisory_sizing.py) there was no
# live-influence code path, so this helper only ever returned SHADOW /
# OFFLINE. It now returns LIVE for influence-stage models.
_BUCKET_LIVE = "LIVE"
_BUCKET_SHADOW = "SHADOW"
_BUCKET_OFFLINE = "OFFLINE"

# Canonical stage whose models influence the live order package — mirrors
# src/runtime/advisory_sizing.py::_ADVISORY_INFLUENCE_STAGES. 3-stage collapse
# (2026-06-16): the legacy `limited_live` / `live_approved` both normalize to
# `advisory`. Comparison uses the canonical form, so a mirror row stored under
# any old name still buckets as LIVE. `shadow` and `candidate` never influence.
_LIVE_INFLUENCE_STAGES: frozenset[str] = frozenset({"advisory"})


def _canonical_stage_or_raw(stage: str) -> str:
    """Normalize a mirror row's stage to canonical, or pass it through.

    The trainer mirror is just JSON the trainer wrote — a row may carry a
    legacy 7-stage name (`limited_live`, `research_only`, …). Map it to the
    canonical 3-stage value so bucketing matches the order path; an
    unrecognized value falls through unchanged (rendered, never crashed).
    """
    try:
        from ml.manifest import canonical_stage
        return canonical_stage(stage)
    except Exception:  # noqa: BLE001  # allow-silent: telemetry-only stage normalization — unknown value passes through unchanged, never 5xx
        return stage


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


def _load_shadow_wiring_map() -> dict[str, list[str]]:
    """Invert ``config/strategies.yaml``'s ``shadow_model_ids`` lists.

    Returns ``{model_id: [strategy_name, ...]}`` so registry-row enrichment
    can answer "which strategy/strategies reference this model?" in O(1).
    Falls back to an empty map on any read failure — the dashboard then
    surfaces every model as OFFLINE rather than crashing.

    Path is resolved per-request via ``repo_root()`` so the env-var
    monkeypatch in tests (``ICT_REPO_ROOT``) wins over the module-level
    default in ``src.units.strategies``.
    """
    try:
        from src.utils.paths import repo_root
        from src.units.strategies import load_strategy_config
        config_path = str(Path(repo_root()) / "config" / "strategies.yaml")
        strategies = load_strategy_config(config_path)
    except Exception as exc:  # noqa: BLE001  # allow-silent: graceful fallback — any YAML/path failure yields OFFLINE rendering, never a 5xx
        logger.warning("training_center: shadow_wiring_map load failed: %s", exc)
        return {}
    inverted: dict[str, list[str]] = {}
    for strategy_name, params in (strategies or {}).items():
        if not isinstance(params, dict):
            continue
        model_ids = params.get("shadow_model_ids") or []
        if not isinstance(model_ids, list):
            continue
        for mid in model_ids:
            if not isinstance(mid, str) or not mid:
                continue
            inverted.setdefault(mid, []).append(str(strategy_name))
    return inverted


def _auto_wire_strategy_names() -> list[str]:
    """Strategy names that auto-wire every shadow-stage model.

    The 2026-05-19 auto-wire default: a strategy whose ``shadow_model_ids``
    is **missing or None** observes every model at
    ``target_deployment_stage == "shadow"`` (see
    ``Coordinator._get_shadow_predictors`` and
    ``ml.shadow.factory.discover_shadow_stage_model_ids``). An explicit
    ``[]`` opts out; an explicit list pins specific ids. This helper
    returns the auto-wire set so registry-row enrichment can mark a
    shadow-stage model SHADOW even when no strategy lists it explicitly —
    without it the dashboard reported every model OFFLINE despite the
    live pipeline observing them.

    Falls back to an empty list on any read failure (same graceful
    contract as ``_load_shadow_wiring_map``).
    """
    try:
        from src.utils.paths import repo_root
        from src.units.strategies import load_strategy_config
        config_path = str(Path(repo_root()) / "config" / "strategies.yaml")
        strategies = load_strategy_config(config_path)
    except Exception as exc:  # noqa: BLE001  # allow-silent: graceful fallback — never 5xx
        logger.warning("training_center: auto_wire_strategy_names load failed: %s", exc)
        return []
    names: list[str] = []
    for strategy_name, params in (strategies or {}).items():
        if not isinstance(params, dict):
            continue
        # `.get` returns None for a missing key; the loader also yields
        # None for an explicit `shadow_model_ids:` with no value. Both
        # mean auto-wire. An explicit list (incl. `[]`) opts out of the
        # auto set.
        if "shadow_model_ids" not in params or params.get("shadow_model_ids") is None:
            names.append(str(strategy_name))
    return names


def _compute_deployment_bucket(stage: str, linked_strategies: list[str]) -> str:
    """Collapse the canonical registry stages to the operator's deployment view.

    * LIVE    — model at the influence stage (canonical `advisory`; legacy
      `limited_live` / `live_approved` normalize to it): the live order
      path's advisory hook scores it and can
      downsize a real order. Stage-driven and registry-global, so LIVE on
      stage alone — independent of ``shadow_model_ids``.
    * SHADOW  — ``shadow``-stage model wired into a strategy's predictor list
      (explicit or auto-wire): predictions logged, decisions unchanged.
    * OFFLINE — neither influencing nor observed.
    """
    if _canonical_stage_or_raw(stage) in _LIVE_INFLUENCE_STAGES:
        return _BUCKET_LIVE
    return _BUCKET_SHADOW if linked_strategies else _BUCKET_OFFLINE


def _enrich_registry_row(
    row: dict[str, Any],
    shadow_map: dict[str, list[str]],
    auto_wire_names: list[str] | None = None,
) -> dict[str, Any]:
    """Flatten useful manifest fields onto the row + compute bucket.

    All new keys are additive — existing consumers continue to see the
    same fields they always did. Dashboard renderers should treat every
    enriched field as nullable.

    ``linked_strategies`` resolution honours the auto-wire default: a
    model explicitly listed in a strategy's ``shadow_model_ids`` is linked
    to those strategies; otherwise a model at ``target_deployment_stage ==
    "shadow"`` is linked to every auto-wiring strategy (those that omit
    ``shadow_model_ids``). Explicit links take precedence so an
    operator-pinned list renders verbatim.
    """
    model_id = str(row.get("model_id") or "")
    manifest = row.get("manifest") if isinstance(row.get("manifest"), dict) else {}
    dataset = manifest.get("dataset") if isinstance(manifest.get("dataset"), dict) else None
    runs = row.get("runs") if isinstance(row.get("runs"), list) else []
    latest_run = runs[-1] if runs else None
    explicit_linked = list(shadow_map.get(model_id, []))
    stage = _canonical_stage_or_raw(str(row.get("target_deployment_stage") or ""))
    if explicit_linked:
        linked_strategies = explicit_linked
    elif stage == "shadow" and auto_wire_names:
        linked_strategies = list(auto_wire_names)
    else:
        linked_strategies = []
    enriched = dict(row)
    enriched["linked_strategies"] = linked_strategies
    enriched["deployment_bucket"] = _compute_deployment_bucket(stage, linked_strategies)
    enriched["model_family"] = manifest.get("model_family")
    enriched["trainer"] = manifest.get("trainer")
    enriched["evaluator"] = manifest.get("evaluator")
    enriched["dataset_ref"] = dataset
    enriched["latest_run"] = latest_run
    # Human-readable "about this model" prose from the manifest (added
    # 2026-05-25). Nullable: rows whose stored manifest predates the
    # field carry None until the trainer re-registers the model.
    enriched["description"] = manifest.get("description")
    return enriched


@router.get("/registry")
def get_registry() -> dict[str, Any]:
    """Model registry rows — append-only history from the trainer mirror
    `runtime_logs/trainer_mirror/registry.jsonl` (`_mirror_root()/'registry.jsonl'`),
    NOT `ml/registry-store/registry.jsonl` (the trainer VM's own copy — this
    router reads the live-VM mirror published from it).

    Each row is enriched (2026-05-18) with:

      * ``linked_strategies`` — list of strategy names whose
        ``shadow_model_ids`` references this ``model_id``. Empty list
        means the model exists in the registry but no strategy uses it.
      * ``deployment_bucket`` — ``"LIVE" | "SHADOW" | "OFFLINE"``. LIVE for
        a model at the influence stage (canonical `advisory`; legacy
        `limited_live` / `live_approved` normalize to it — scored by the
        live order path's advisory hook),
        SHADOW for a wired ``shadow``-stage model (logged, decisions
        unchanged), OFFLINE otherwise. The dashboard renders this as the
        headline pill on each per-model card.
      * ``model_family`` — flattened from ``manifest.model_family``
        (e.g. ``trade_outcome_classifier``).
      * ``trainer`` / ``evaluator`` — fully-qualified callable names
        from the manifest.
      * ``dataset_ref`` — ``{family, symbol_scope, timeframe, version}``
        for the dataset this model was trained on.
      * ``latest_run`` — newest entry from ``runs[]`` (run_id, at,
        metrics, etc.) or ``None`` if no runs are recorded yet.
      * ``description`` — human-readable "about this model" prose from
        ``manifest.description``. ``None`` for rows whose stored manifest
        predates the field (re-populates on the next registration).

    All enriched fields are additive — pre-existing consumers see the
    same shape with extra keys, never missing ones. Enriched fields are
    nullable; renderers must treat missing manifest data as "—".
    """
    root = _mirror_root()
    rows = _read_jsonl_tail(root / "registry.jsonl", limit=0)
    shadow_map = _load_shadow_wiring_map()
    auto_wire_names = _auto_wire_strategy_names()
    enriched = [_enrich_registry_row(r, shadow_map, auto_wire_names) for r in rows]
    return {
        **_mirror_meta(),
        "rows": enriched,
        "count": len(enriched),
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
    keep = {"manifest_ok", "manifest_failed", "manifest_missing", "manifest_skipped"}
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
