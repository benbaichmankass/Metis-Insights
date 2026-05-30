"""SIM Phase-3 — decision-attrition report.

Answers the operator's "looks great in eval, barely fires live" problem: a
model's isolated holdout `n_eval` says it was judged on N rows, but in the
INTEGRATED funnel it only gets to score the decisions that actually reach the
advisory stage — which on a quiet/regime-gated tape can be a tiny fraction.
Promotion confidence built on the holdout n can be badly miscalibrated to the
real live-funnel decision volume.

This module reads the Phase-2 ledger trades (each carries ``model_scores`` +
``model_factor``) and, per model, computes:

  * ``funnel_scored``   — decisions the model actually scored in the replay
  * ``eval_n``          — the model's holdout n_eval (from the registry)
  * ``attrition_ratio`` — funnel_scored / eval_n (how much smaller live-funnel
                          decision volume is than the eval implied)
  * ``bearish``         — decisions it voted bearish on
  * ``influenced``      — downsized decisions where THIS model was bearish
                          (i.e. it contributed to a real size change)
  * ``bearish_net_r``   — without-model net R of the trades it flagged bearish
                          (strongly negative ⇒ the model correctly flags losers)
  * a one-line **promotion-readiness** verdict anchored on real funnel volume.

Pure analysis over the ledger — no model loading, no live writes.
"""
from __future__ import annotations

from typing import Any, Optional

# Below this many scored live-funnel decisions, a shadow→advisory promotion
# can't be justified on funnel evidence regardless of holdout metrics. Mirrors
# the spirit of the ml-review "insufficient data" gates.
_MIN_FUNNEL_VOLUME = 30


def compute_attrition(
    trades: list[Any],
    *,
    bearish_threshold: float,
    eval_n_by_model: Optional[dict[str, int]] = None,
) -> dict[str, dict[str, Any]]:
    """Per-model decision-attrition over the replay's closed, scored trades.

    ``trades`` are SimTrade objects; only closed trades with a ``model_scores``
    dict contribute. ``eval_n_by_model`` maps model_id → holdout n_eval (from
    the registry); omit when unavailable (attrition_ratio is then None).
    """
    eval_n_by_model = eval_n_by_model or {}
    scored = [
        t for t in trades
        if not t.is_open() and t.r_multiple is not None and t.model_scores
    ]

    # Collect every model_id that scored at least one decision.
    model_ids: set[str] = set()
    for t in scored:
        model_ids.update(t.model_scores.keys())

    report: dict[str, dict[str, Any]] = {}
    for mid in sorted(model_ids):
        funnel_scored = 0
        bearish = 0
        influenced = 0
        bearish_r_sum = 0.0
        for t in scored:
            if mid not in t.model_scores:
                continue
            funnel_scored += 1
            is_bearish = float(t.model_scores[mid]) < bearish_threshold
            if is_bearish:
                bearish += 1
                bearish_r_sum += t.r_multiple
                if t.model_factor is not None and t.model_factor < 1.0:
                    influenced += 1
        eval_n = eval_n_by_model.get(mid)
        attrition_ratio = (
            round(funnel_scored / eval_n, 5) if eval_n else None
        )
        report[mid] = {
            "funnel_scored": funnel_scored,
            "eval_n": eval_n,
            "attrition_ratio": attrition_ratio,
            "bearish": bearish,
            "influenced": influenced,
            "bearish_net_r": round(bearish_r_sum, 4),
            "readiness": _readiness(funnel_scored, bearish, bearish_r_sum, eval_n),
        }
    return report


def _readiness(funnel_scored: int, bearish: int, bearish_r_sum: float,
               eval_n: Optional[int]) -> str:
    """One-line promotion-readiness verdict anchored on real funnel volume."""
    if funnel_scored == 0:
        return "never scored a live-funnel decision — cannot evaluate"
    if funnel_scored < _MIN_FUNNEL_VOLUME:
        base = (f"insufficient funnel volume ({funnel_scored} < {_MIN_FUNNEL_VOLUME}) "
                f"for promotion confidence")
        if eval_n and eval_n > 0:
            base += f"; holdout n_eval={eval_n} overstates live decision volume {eval_n // max(funnel_scored,1)}x"
        return base
    if bearish == 0:
        return f"scored {funnel_scored} decisions but never bearish — no influence to evaluate"
    # bearish_r_sum is the without-model R of the flagged trades; negative is
    # GOOD (the model flags losers), positive is BAD (it flags winners).
    direction = "flags losers (good)" if bearish_r_sum < 0 else "flags winners (bad)"
    return (f"scored {funnel_scored}, bearish on {bearish} "
            f"(flagged-trade net_r={round(bearish_r_sum,2)} — {direction})")


def eval_n_from_registry(model_ids: list[str], *, registry_root: Optional[str] = None) -> dict[str, int]:
    """Best-effort {model_id: holdout n_eval} from the model registry.

    ``n_eval`` lives in each model's state JSON (``metrics.n_eval``), not on the
    flattened RegistryEntry, so this resolves ``entry.model_state_path`` via the
    same loader the live factory uses and reads the metrics block. Returns only
    the ids that resolved; missing/unreadable models are silently omitted (the
    attrition report then shows attrition_ratio=None for them).
    """
    out: dict[str, int] = {}
    try:
        from pathlib import Path

        from ml.registry.model_registry import ModelRegistry
        from ml.shadow.factory import DEFAULT_REGISTRY_ROOT, _load_model_state
    except Exception:  # noqa: BLE001
        return out
    try:
        registry = ModelRegistry(Path(registry_root or DEFAULT_REGISTRY_ROOT))
    except Exception:  # noqa: BLE001
        return out
    for mid in model_ids:
        try:
            entry = registry.get(mid)
            state = _load_model_state(entry.model_state_path, registry_root=registry.root)
            metrics = state.get("metrics") or {}
            n = metrics.get("n_eval")
            if n is not None:
                out[mid] = int(float(n))
        except Exception:  # noqa: BLE001
            continue
    return out
