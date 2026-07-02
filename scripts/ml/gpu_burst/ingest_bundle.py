#!/usr/bin/env python3
"""Ingest a GPU-burst model bundle into the model registry (M19 Tier-1).

The burst driver (``runpod_burst.py``) returns a JSON **bundle**
``{run_dir, model_state, metrics, manifest}`` — the LightGBM booster trained on the
rented pod (the pod-local registry dies with the pod, so only the bundle survives).
This script materializes that bundle into the standard experiment layout
(``experiments-runs/<model_id>/<run_id>/{model_state,metrics,manifest}.json``) and
registers it via :class:`ml.registry.ModelRegistry`, so the model shows up on the
dashboard **Models** page (`/api/bot/ml/registry`, fed from the trainer mirror's
``registry.jsonl``) — at **`candidate`** deployment stage, forced, under a
**burst-namespaced id** (``<model_id>-gpuburst``) so it can never overwrite a
same-named production model.

**Why forced `candidate` (safety):** a freshly-registered model defaults to
``target_deployment_stage: shadow``, which auto-wires onto every strategy's
predictor list (the shadow-default flip). A burst-trained model must influence
nothing until an operator promotes it, so we register it at ``candidate`` — which
the shadow factory REFUSES to load. The ``shadow -> advisory`` promotion stays the
operator gate; this ingest never promotes. (Re-registering an existing model_id
appends a run and PRESERVES its current stage — so an operator's later promotion is
never clobbered by a re-burst.)

**Where it runs:** the TRAINER VM — the canonical ``ml/registry-store/`` owner and
mirror publisher. Writing ``ml/registry-store/<model_id>.json`` there means the next
``publish_trainer_mirror.sh`` carries it to the live Models page (≤2 min). Idempotent
on ``run_id``. Never touches ``trade_journal.db`` or any live-order path.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

# A burst model is inert until an operator promotes it: candidate is refused by the
# shadow factory, so it observes nothing. NEVER default to shadow here.
_FORCED_STAGE = "candidate"

# A burst artifact is namespaced with this suffix so it is ALWAYS a distinct
# registry entry — it can never collide with (and thus never refresh / hijack the
# served weights of) a same-named PRODUCTION model. The manifest a burst trains is
# often an existing live head (e.g. `btc-regime-15m-lgbm-v2` is the advisory BTC
# vol-gate), and ModelRegistry.register() on an existing id refreshes the top-level
# model_state_path while PRESERVING its stage — so ingesting under the bare id would
# silently repoint a live advisory model at the burst weights. Namespacing prevents
# that: a burst retrain lands as its own candidate for comparison; replacing the
# production model is a deliberate, separate operator action.
_BURST_SUFFIX = "-gpuburst"


def _run_id_from_bundle(bundle: dict, fallback: str) -> str:
    """Reuse the pod's run_id (basename of ``run_dir``) for idempotency; else fallback."""
    run_dir = str(bundle.get("run_dir") or "").rstrip("/")
    return os.path.basename(run_dir) or fallback


def _numeric_metrics(metrics: dict) -> dict:
    """Registry ``metrics`` is a flat ``{str: float}`` — keep only scalar numerics
    (the FULL metrics.json is still materialized in the experiment dir)."""
    out: dict[str, float] = {}
    for k, v in (metrics or {}).items():
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            out[k] = float(v)
    return out


def ingest(
    *,
    bundle_path: str,
    registry_root: str,
    experiments_root: str,
    code_revision: str,
    by: str = "gpu-burst",
) -> str:
    """Materialize the bundle into the experiment layout + register it at candidate.

    Returns the path of the written registry entry.
    """
    from ml.registry.model_registry import ModelRegistry

    bundle = json.loads(Path(bundle_path).read_text(encoding="utf-8"))
    model_state = bundle.get("model_state")
    metrics = bundle.get("metrics") or {}
    manifest = bundle.get("manifest")
    if not isinstance(model_state, dict) or not isinstance(manifest, dict):
        raise ValueError(f"bundle {bundle_path} is missing model_state/manifest dicts")
    base_model_id = manifest.get("model_id")
    if not base_model_id:
        raise ValueError(f"bundle {bundle_path} manifest has no model_id")

    # Namespace to a burst-only id so we can NEVER touch a production model of the
    # same name (idempotent if the bundle's id already carries the suffix).
    model_id = base_model_id if base_model_id.endswith(_BURST_SUFFIX) else f"{base_model_id}{_BURST_SUFFIX}"

    # Force candidate stage — a burst model must be inert until an operator promotes.
    manifest = {**manifest, "model_id": model_id, "target_deployment_stage": _FORCED_STAGE}

    reg = ModelRegistry(Path(registry_root))
    # Belt-and-suspenders: if an operator has already promoted THIS burst id past
    # candidate, refuse to refresh its served weights — a re-burst must not silently
    # swap a model someone deliberately advanced. (Fresh + still-candidate ids proceed.)
    if reg.exists(model_id):
        stage = getattr(reg.get(model_id), "target_deployment_stage", _FORCED_STAGE)
        if stage != _FORCED_STAGE:
            raise ValueError(
                f"{model_id} already exists at stage '{stage}' (operator-promoted) — "
                "refusing to overwrite its served weights via a burst ingest."
            )

    run_id = _run_id_from_bundle(bundle, fallback="gpu-burst")
    run_dir = Path(experiments_root) / model_id / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    model_state_path = run_dir / "model_state.json"
    model_state_path.write_text(json.dumps(model_state), encoding="utf-8")
    (run_dir / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    reg.register(
        model_id=model_id,
        manifest=manifest,
        model_state_path=str(model_state_path.resolve()),
        metrics=_numeric_metrics(metrics),
        code_revision=code_revision,
        run_id=run_id,
        by=by,
    )
    dest = str((Path(registry_root) / f"{model_id}.json").resolve())
    print(f"registered {model_id} @ run {run_id} (stage={_FORCED_STAGE}) -> {dest}")
    return dest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bundle", required=True, help="Path to the .bundle.json returned by the burst.")
    ap.add_argument("--registry-root", default="ml/registry-store")
    ap.add_argument("--experiments-root", default="ml/experiments-runs")
    ap.add_argument("--code-revision", default=os.environ.get("GITHUB_SHA") or "gpu-burst")
    ap.add_argument("--by", default="gpu-burst")
    args = ap.parse_args(argv)
    try:
        ingest(
            bundle_path=args.bundle,
            registry_root=args.registry_root,
            experiments_root=args.experiments_root,
            code_revision=args.code_revision,
            by=args.by,
        )
        return 0
    except (ValueError, OSError, json.JSONDecodeError) as e:
        print(f"::error::gpu-burst ingest failed: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
