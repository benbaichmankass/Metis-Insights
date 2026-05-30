"""SIM Phase-2 — models-in-the-loop.

Layers advisory-stage ML influence INTO the Phase-1 integrated replay so we
can answer the operator's "test the MLs and strategies together" question:
run the same history **with-model vs without-model** and diff the realized
portfolio.

The influence math is the LIVE function — ``src/runtime/advisory_influence.py
::advisory_downsize_factor`` — not a SIM copy. A model (or quorum) scoring
bearish shrinks the position to ``size_floor``; SIM applies that same factor
to each trade's realized R (R is risk-normalized, so a size factor scales the
R contribution linearly). With-model portfolio = Σ(factor·R); without = Σ(R).

Counterfactual loading: SIM can score a model at ANY stage (incl. shadow /
candidate / research_only), because the whole point is to evaluate a model
**before** the operator promotes it past shadow. We reuse the live factory's
model-state load + predictor-class resolution + ``ShadowPredictor`` wrapper,
skipping ONLY the live stage gate (``_check_stage``) — the scoring path itself
is identical to live, so a model that looks good here is being judged exactly
as it would judge live orders.

Leakage discipline (inherits ``ml/shadow/backfill.py``): the feature row a
model sees contains ONLY signal-time columns (strategy_name, symbol,
direction, confidence, setup_type, killzone, bias). No outcome/forward column
ever enters the row. Asserted by ``tests/test_sim_phase2.py``.

Read-only against the registry + model state. Writes nothing on its own (the
engine's caller owns output).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Signal-time feature columns a trade-outcome / setup model keys off. Mirrors
# ml/shadow/backfill.py::_build_signal_feature_row — the leakage-safe surface.
_FEATURE_COLUMNS = (
    "strategy_name", "symbol", "direction",
    "confidence", "setup_type", "killzone", "bias",
)


def feature_row_for_trade(
    *, strategy: str, symbol: str, direction: str, confidence: float,
    meta: Optional[dict] = None,
) -> dict[str, Any]:
    """Build the leakage-safe signal-time feature row for a SIM decision.

    Only signal-time fields — never entry/exit/pnl/r. Mirrors the live
    real-time + backfill projections so a model scores SIM rows identically.
    """
    meta = meta or {}
    return {
        "strategy_name": strategy,
        "symbol": symbol,
        "direction": direction,
        "confidence": float(confidence or 0.0),
        "setup_type": str(meta.get("setup_type") or ""),
        "killzone": str(meta.get("killzone") or ""),
        "bias": str(meta.get("bias") or ""),
    }


def _assert_leakage_safe(row: dict[str, Any]) -> None:
    """Defence-in-depth: the feature row must carry no outcome column."""
    forbidden = {
        "pnl", "pnl_percent", "r_multiple", "won", "exit", "exit_price",
        "exit_reason", "exit_ts", "forward_log_return", "forward_log_return_vol",
        "regime_label",
    }
    leaked = forbidden & set(row)
    if leaked:
        raise AssertionError(f"SIM feature row leaks outcome columns: {sorted(leaked)}")


@dataclass
class ModelScorer:
    """Scores SIM decisions against one or more models, returns the advisory
    size factor via the LIVE ``advisory_downsize_factor``.

    Parameters
    ----------
    model_ids : models to score (any stage — counterfactual).
    policy_cfg : a strategy-cfg-shaped dict with an ``advisory_policy`` block
        (mode/bearish_threshold/size_floor/quorum), parsed by the live
        ``parse_policy``. Omit for the default downsize policy.
    registry_root : registry-store path (defaults to the live resolver).
    """

    model_ids: list[str]
    policy_cfg: Optional[dict] = None
    registry_root: Optional[str] = None

    def __post_init__(self) -> None:
        from src.runtime.advisory_influence import parse_policy

        self._predictors: list[Any] = []
        self._policy = parse_policy(self.policy_cfg or {"advisory_policy": {"mode": "downsize"}})
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        # Reuse the LIVE factory internals (model-state load + predictor class
        # + ShadowPredictor wrap) but skip ONLY the stage gate so a pre-advisory
        # model can be counterfactually scored. The scoring path is identical
        # to live.
        from ml.registry.model_registry import ModelRegistry
        from ml.shadow.factory import (
            DEFAULT_REGISTRY_ROOT,
            _load_model_state,
            _resolve_predictor_class,
        )
        from ml.predictors.shadow import ShadowPredictor

        root = Path(self.registry_root or DEFAULT_REGISTRY_ROOT)
        registry = ModelRegistry(root)
        for mid in self.model_ids:
            try:
                entry = registry.get(mid)
                state = _load_model_state(entry.model_state_path, registry_root=registry.root)
                base = _resolve_predictor_class(state.get("trainer", ""))(state)
                self._predictors.append(
                    ShadowPredictor(
                        base, model_id=entry.model_id,
                        stage=entry.target_deployment_stage, log_path=None,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("sim: could not load model %s for scoring: %s", mid, exc)

    def factor_for(
        self, row: dict[str, Any], *,
        closes: Optional[list] = None, symbol: str = "", timeframe: str = "",
    ) -> tuple[float, dict[str, float]]:
        """Return ``(size_factor, {model_id: score})`` for a decision.

        Each predictor is scored on the row TAILORED to it, via the LIVE
        ``src.runtime.regime_shadow.feature_row_for_predictor``:
          * a regime model whose ``(symbol, timeframe)`` match this decision's
            gets ``row`` enriched with the live ``vol_bucket`` +
            ``rolling_log_return_vol`` computed from ``closes`` against the
            edges frozen in its model state — exactly as the live signal
            builder does;
          * a mismatched regime model (or one with no computable vol) is
            SKIPPED (not scored on a meaningless constant);
          * a non-regime model (trade-outcome / setup-quality) gets ``row``
            unchanged.

        ``size_factor`` comes from the LIVE ``advisory_downsize_factor`` —
        never a SIM reimplementation. ``1.0`` = no influence.
        """
        from src.runtime.advisory_influence import advisory_downsize_factor
        from src.runtime.regime_shadow import feature_row_for_predictor

        _assert_leakage_safe(row)
        self._ensure_loaded()
        closes = closes or []
        scores: dict[str, float] = {}
        for p in self._predictors:
            tailored = feature_row_for_predictor(
                p, row, closes=closes, symbol=symbol, timeframe=timeframe,
            )
            if tailored is None:
                continue  # mismatched regime model — skip (don't score a constant)
            try:
                scores[p.model_id] = float(p.predict(tailored))
            except Exception as exc:  # noqa: BLE001
                logger.debug("sim: model %s predict failed: %s", p.model_id, exc)
        if not scores:
            return 1.0, {}
        factor = advisory_downsize_factor(scores, self._policy, flag_enabled=True)
        return factor, scores
