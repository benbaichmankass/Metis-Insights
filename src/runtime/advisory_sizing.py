"""Execution-time advisory downsize (WS7 rollout step 2, 2026-05-25).

Wires the advisory influence operator into the **live order path**.
Advisory-stage models are not scored at signal-build time (that path runs
only shadow-stage models), so this module resolves advisory-stage
predictors, scores them on a feature row built from the OrderPackage, and
returns a **reductive** size factor that
``Coordinator.multi_account_execute`` applies to the RiskManager-computed
per-account qty.

**Gated by model STAGE alone** (no separate enable flag — the former
``ADVISORY_MODE`` was removed 2026-06-13 as a redundant third gate). A
model influences only at the canonical ``advisory`` stage (the legacy
``limited_live`` / ``live_approved`` normalize to it); ``shadow`` only
logs. The per-strategy ``advisory_policy`` is
permissive config: omit ⇒ ``annotate`` (log the would-be downsize, never
resize); ``mode: off`` opts out; ``mode: downsize`` arms the cut. To turn
a model's influence off, demote it to ``shadow``. Every step is wrapped so
a model/registry/config error can never break the trading tick
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

# Canonical stage whose models are allowed to influence the order package.
# 3-stage collapse (2026-06-16): the legacy `limited_live` / `live_approved`
# both normalize to `advisory`, so a model stored under any of those three
# old names still influences. `shadow` and `candidate` never influence.
# Comparison is on the canonical form (`canonical_stage`), so behaviour is
# IDENTICAL to the prior 3-of-7 set for every currently-deployed model.
_ADVISORY_INFLUENCE_STAGES = frozenset({"advisory"})


def _influences(stage: Any) -> bool:
    """True when a (canonical-or-legacy) stage influences the order path.

    Normalizes through the alias map; unrecognized stages are treated as
    non-influencing (fail-safe — never enlarge influence on a bad value).
    """
    try:
        from ml.manifest import canonical_stage
        return canonical_stage(str(stage)) in _ADVISORY_INFLUENCE_STAGES
    except Exception:  # noqa: BLE001 — fail-safe: unknown stage never influences
        return False


def discover_advisory_stage_model_ids(registry: Any) -> list[str]:
    """Every model_id at an influence stage (canonical `advisory`; the
    legacy `limited_live` / `live_approved` normalize to it). Alphabetical
    for stable behaviour."""
    return sorted(
        e.model_id for e in registry.list()
        if _influences(e.target_deployment_stage)
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
    return ``(factor, record)``. ``factor == 1.0`` means no actual downsize
    (always the case in ``annotate`` mode, where the record still carries the
    ``would_be_factor`` for the soak). Never raises — any failure falls back
    to ``(1.0, ...)``."""
    try:
        import dataclasses
        from pathlib import Path

        from ml.registry.model_registry import ModelRegistry
        from ml.shadow.factory import DEFAULT_REGISTRY_ROOT, resolve_predictors
        from src.strategy_registry import _strategy_cfg
        from src.utils.paths import runtime_logs_dir

        strategy = str(getattr(pkg, "strategy", "") or "")
        policy = parse_policy(_strategy_cfg(strategy))
        # mode=off is the explicit per-strategy opt-out (no scoring at all).
        if policy.mode == "off":
            return 1.0, {"action": "off", "mode": "off"}
        registry = ModelRegistry(Path(DEFAULT_REGISTRY_ROOT))
        ids = discover_advisory_stage_model_ids(registry)
        if not ids:
            return 1.0, {"action": "no_advisory_models"}
        log_path = runtime_logs_dir() / "shadow_predictions.jsonl"
        predictors = resolve_predictors(ids, registry, log_path=log_path)
        # Option B (PR #4602): regime heads must NOT participate in the advisory
        # directional-downsize quorum. The advisory feature row
        # (`_feature_row_from_pkg`) carries the trade-outcome surface only — none
        # of the `market_features` (vol_bucket, yang_zhang_vol, …) a regime head
        # needs — so scoring a regime head here yields a degenerate ~constant
        # (~0.98) that both pollutes the head's advisory track record AND skews
        # the bearish quorum. That is the load-bearing cause of the demoted
        # btc-regime yz heads' "AUC 0.40" live failure. Conceptually a regime
        # `P(volatile)` score is not a bullish/bearish directional view anyway —
        # regime conviction flows via the `c_reg` conviction lens, not this
        # quorum. So exclude any predictor exposing a `regime_spec`.
        from src.runtime.regime_shadow import regime_spec_of

        directional: list[Any] = []
        excluded_regime: list[str] = []
        for p in predictors:
            base = getattr(p, "wrapped", p)
            if regime_spec_of(base) is not None:
                excluded_regime.append(p.model_id)
            else:
                directional.append(p)
        if excluded_regime:
            logger.info(
                "advisory: excluded regime heads from downsize quorum: %s",
                ",".join(sorted(excluded_regime)),
            )
        row = _feature_row_from_pkg(pkg)
        scores: dict[str, float] = {}
        for p in directional:
            try:
                scores[p.model_id] = float(p.predict(row))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "advisory_predict_failed model_id=%s err=%s", p.model_id, exc,
                )
        if not scores:
            return 1.0, {
                "action": "no_scores",
                "excluded_regime": sorted(excluded_regime),
            }
        # The quorum/floor math is mode-agnostic; compute the would-be factor
        # once, then apply it (downsize) or only log it (annotate).
        would = advisory_downsize_factor(
            scores, dataclasses.replace(policy, mode="downsize"), flag_enabled=True,
        )
        record = {
            "scores": scores,
            "excluded_regime": sorted(excluded_regime),
            "mode": policy.mode,
            "size_floor": policy.size_floor,
            "bearish_threshold": policy.bearish_threshold,
            "quorum": policy.quorum,
            "would_be_factor": would,
        }
        if policy.mode == "downsize":
            record["action"] = "downsize" if would < 1.0 else "none"
            record["factor"] = would
            return would, record
        # annotate (the permissive default): never resize, just record.
        record["action"] = "annotate"
        record["factor"] = 1.0
        return 1.0, record
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
    package. Inert (factor ``1.0``) when no model is at the influencing
    stage — the downsize is stage-gated, not gated by the removed
    ``ADVISORY_MODE`` env flag (dropped 2026-06-13 as a redundant third gate;
    see the module docstring). Never raises — on any error the qty is
    returned unchanged.
    """
    try:
        if sized_qty is None or sized_qty <= 0:
            return sized_qty
        meta = getattr(pkg, "meta", None)
        if isinstance(meta, dict) and "_advisory_factor" in meta:
            factor = meta["_advisory_factor"]
            record = meta.get("advisory_decision") or {}
        else:
            factor, record = compute_advisory_factor(pkg)
            if isinstance(meta, dict):
                meta["_advisory_factor"] = factor
                meta["advisory_decision"] = record
        action = (record or {}).get("action")
        new_qty = sized_qty * factor if factor < 1.0 else sized_qty
        # Audit every decision that actually scored models: annotate logs the
        # would-be downsize (for the operator's pre-downsize soak), downsize
        # logs the applied cut. mode=off / no-models / no-scores write nothing.
        if action in ("annotate", "downsize"):
            _log_advisory_decision(pkg, account_name, sized_qty, new_qty, factor, record)
            if factor < 1.0:
                logger.info(
                    "advisory_downsize strategy=%s account=%s factor=%.4f qty %.8f -> %.8f",
                    getattr(pkg, "strategy", "?"), account_name, factor,
                    sized_qty, new_qty,
                )
        return new_qty
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "apply_advisory_downsize failed (returning unchanged qty): %s", exc,
        )
        return sized_qty


def _log_advisory_decision(
    pkg: Any, account_name: str, intended_qty: float, final_qty: float,
    factor: float, record: dict | None = None,
) -> None:
    """Append the advisory decision (annotate or applied downsize) to the
    audit log. In annotate mode ``factor`` is 1.0 and ``final_qty ==
    intended_qty``; ``record['would_be_factor']`` carries the cut that WOULD
    have been applied — the signal the operator watches before flipping a
    strategy to ``mode: downsize``."""
    try:
        from src.utils.paths import runtime_logs_dir
        path = runtime_logs_dir() / "advisory_decisions.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        meta = getattr(pkg, "meta", None) or {}
        rec = record if record is not None else meta.get("advisory_decision")
        rec = rec or {}
        payload = {
            "logged_at_utc": datetime.now(timezone.utc).isoformat(),
            "strategy_id": str(getattr(pkg, "strategy", "") or ""),
            "symbol": str(getattr(pkg, "symbol", "") or ""),
            "account": account_name,
            "action": rec.get("action", "downsize"),
            "factor": factor,
            "would_be_factor": rec.get("would_be_factor"),
            "intended_qty": intended_qty,
            "final_qty": final_qty,
            "advisory_decision": rec or meta.get("advisory_decision"),
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload) + "\n")
    except OSError as exc:
        logger.warning("_log_advisory_decision: could not write audit log: %s", exc)
