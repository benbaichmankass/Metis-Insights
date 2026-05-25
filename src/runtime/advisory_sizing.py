"""Execution-time advisory downsize (WS7 rollout step 2, 2026-05-25).

Wires the advisory influence operator into the **live order path**.
Advisory-stage models are not scored at signal-build time (that path runs
only shadow-stage models), so this module resolves advisory-stage
predictors, scores them on a feature row built from the OrderPackage, and
returns a **reductive** size factor that
``Coordinator.multi_account_execute`` applies to the RiskManager-computed
per-account qty.

**Default off.** Everything is gated by ``ADVISORY_MODE`` (env/settings,
default false). With the flag off, :func:`compute_advisory_factor`
returns ``1.0`` before doing any work and :func:`apply_advisory_downsize`
returns the qty unchanged — provably inert. Every step is wrapped so a
model/registry/config error can never break the trading tick
(deterministic fallback to factor ``1.0``).

The factor is computed **once per package** and cached on ``pkg.meta`` so
the advisory models are scored a single time, not once per account.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from src.runtime.advisory_influence import advisory_downsize_factor, parse_policy

logger = logging.getLogger(__name__)

# Stages whose models are allowed to influence the order package. Mirrors
# the shadow-factory stage gate; `shadow` and below never influence.
_ADVISORY_INFLUENCE_STAGES = frozenset({"advisory", "limited_live", "live_approved"})


def discover_advisory_stage_model_ids(registry: Any) -> list[str]:
    """Every model_id at an influence stage (advisory / limited_live /
    live_approved). Alphabetical for stable behaviour."""
    return sorted(
        e.model_id for e in registry.list()
        if e.target_deployment_stage in _ADVISORY_INFLUENCE_STAGES
    )


def _feature_row_from_pkg(pkg: Any) -> dict[str, Any]:
    """Signal-time feature row from an OrderPackage — same surface the
    shadow trade-outcome models see at signal-build time."""
    meta = getattr(pkg, "meta", None) or {}
    return {
        "strategy_name": str(getattr(pkg, "strategy", "") or ""),
        "symbol": str(getattr(pkg, "symbol", "") or ""),
        "direction": str(getattr(pkg, "direction", "") or ""),
        "confidence": float(getattr(pkg, "confidence", 0.0) or 0.0),
        "setup_type": str(meta.get("setup_type") or ""),
        "killzone": str(meta.get("killzone") or ""),
    }


def compute_advisory_factor(
    pkg: Any, *, settings: dict | None = None,
) -> tuple[float, dict]:
    """Resolve + score advisory-stage models for ``pkg.strategy`` and
    return ``(factor, record)``. ``factor == 1.0`` means no downsize.
    Never raises — any failure falls back to ``(1.0, ...)``."""
    from src.runtime.runtime_flags import _advisory_mode_enabled

    if not _advisory_mode_enabled(settings or {}):
        return 1.0, {"action": "disabled", "reason": "ADVISORY_MODE off"}
    try:
        from pathlib import Path

        from ml.registry.model_registry import ModelRegistry
        from ml.shadow.factory import DEFAULT_REGISTRY_ROOT, resolve_predictors
        from src.strategy_registry import _strategy_cfg
        from src.utils.paths import runtime_logs_dir

        strategy = str(getattr(pkg, "strategy", "") or "")
        policy = parse_policy(_strategy_cfg(strategy))
        if policy.mode != "downsize":
            return 1.0, {"action": "no_policy", "mode": policy.mode}
        registry = ModelRegistry(Path(DEFAULT_REGISTRY_ROOT))
        ids = discover_advisory_stage_model_ids(registry)
        if not ids:
            return 1.0, {"action": "no_advisory_models"}
        log_path = runtime_logs_dir() / "shadow_predictions.jsonl"
        predictors = resolve_predictors(ids, registry, log_path=log_path)
        row = _feature_row_from_pkg(pkg)
        scores: dict[str, float] = {}
        for p in predictors:
            try:
                scores[p.model_id] = float(p.predict(row))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "advisory_predict_failed model_id=%s err=%s", p.model_id, exc,
                )
        if not scores:
            return 1.0, {"action": "no_scores"}
        factor = advisory_downsize_factor(scores, policy, flag_enabled=True)
        return factor, {
            "action": "downsize" if factor < 1.0 else "none",
            "factor": factor,
            "scores": scores,
            "mode": policy.mode,
            "size_floor": policy.size_floor,
            "bearish_threshold": policy.bearish_threshold,
            "quorum": policy.quorum,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("compute_advisory_factor failed: %s", exc)
        return 1.0, {"action": "error", "error": str(exc)}


def apply_advisory_downsize(
    pkg: Any, sized_qty: float, *, account_name: str = "",
) -> float:
    """Scale a RiskManager-computed per-account qty by the advisory factor.

    Reductive: returns ``sized_qty * factor`` with ``factor ∈ [size_floor,
    1.0]`` (never amplifies). The factor is computed once and cached on
    ``pkg.meta['_advisory_factor']`` so the models score a single time per
    package. Inert when ``ADVISORY_MODE`` is off (factor ``1.0``). Never
    raises — on any error the qty is returned unchanged.
    """
    try:
        if sized_qty is None or sized_qty <= 0:
            return sized_qty
        meta = getattr(pkg, "meta", None)
        if isinstance(meta, dict) and "_advisory_factor" in meta:
            factor = meta["_advisory_factor"]
        else:
            factor, record = compute_advisory_factor(pkg)
            if isinstance(meta, dict):
                meta["_advisory_factor"] = factor
                meta["advisory_decision"] = record
        if factor >= 1.0:
            return sized_qty
        new_qty = sized_qty * factor
        logger.info(
            "advisory_downsize strategy=%s account=%s factor=%.4f qty %.8f -> %.8f",
            getattr(pkg, "strategy", "?"), account_name, factor, sized_qty, new_qty,
        )
        _log_advisory_decision(pkg, account_name, sized_qty, new_qty, factor)
        return new_qty
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "apply_advisory_downsize failed (returning unchanged qty): %s", exc,
        )
        return sized_qty


def _log_advisory_decision(
    pkg: Any, account_name: str, intended_qty: float, final_qty: float, factor: float,
) -> None:
    """Append the applied downsize to the existing advisory audit log."""
    try:
        from src.utils.paths import runtime_logs_dir
        path = runtime_logs_dir() / "advisory_decisions.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        meta = getattr(pkg, "meta", None) or {}
        payload = {
            "logged_at_utc": datetime.now(timezone.utc).isoformat(),
            "strategy_id": str(getattr(pkg, "strategy", "") or ""),
            "symbol": str(getattr(pkg, "symbol", "") or ""),
            "account": account_name,
            "action": "downsize",
            "factor": factor,
            "intended_qty": intended_qty,
            "final_qty": final_qty,
            "advisory_decision": meta.get("advisory_decision"),
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload) + "\n")
    except OSError as exc:
        logger.warning("_log_advisory_decision: could not write audit log: %s", exc)
