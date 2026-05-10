"""Per-strategy shadow-mode adapter (S-AI-WS7-PART-2 + PART-4).

Provides `with_shadow_pred` (single predictor, original PART-2 API)
and `with_shadow_preds` (plural — added in PART-4 so a strategy can
run multiple shadow models concurrently). Both return the
deterministic decision byte-for-byte regardless of any predictor
outcome.

Non-negotiables both helpers enforce by construction:

- **Shadow = observe.** The deterministic decision is returned
  byte-for-byte. There is no code path in which the model's score
  reaches the order package or the risk manager.
- **One model failure cannot crash the tick OR mask other models.**
  Each predictor call is independently wrapped in a `try/except`.
  When a model raises, the failure is logged with the offending
  model_id and the loop continues to the next predictor. A broken
  model in position 0 of a 3-predictor list still lets predictors
  1 and 2 fire normally.
- **Empty / None is a pass-through.** `predictor=None` (singular)
  or `predictors=[]` / `predictors=None` (plural) is a single-
  branch passthrough. Strategies can call the helper
  unconditionally even when no model is wired.
- **Bare `Predictor` is rejected.** `ShadowPredictor` is the
  audit-log surface and must not be bypassed.

Example: PART-3 vwap usage with a config-driven predictor list
(plural form is the production path going forward)::

    from src.runtime.shadow_adapter import with_shadow_preds

    def order_package(cfg, candles_df=None):
        package = _build_deterministic_package(cfg, candles_df)
        feature_row = _build_shadow_feature_row(package)
        return with_shadow_preds(
            package,
            predictors=_resolve_shadow_predictors(cfg),
            feature_row=feature_row,
        )

Predictor resolution from `cfg["shadow_model_ids"]` is the
strategy's responsibility (typically via
`ml.shadow.factory.resolve_predictors`). The helper accepts an
already-instantiated list of `ShadowPredictor`.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable, Mapping, Sequence, TypeVar

from ml.predictors.shadow import ShadowPredictor

_DEFAULT_LOGGER = logging.getLogger(__name__)

T = TypeVar("T")


def with_shadow_preds(
    decision: T,
    *,
    predictors: Sequence[ShadowPredictor] | Iterable[ShadowPredictor] | None,
    feature_row: Mapping[str, Any],
    logger: logging.Logger | None = None,
) -> T:
    """Run one or more shadow-mode predictors as side-effects;
    return decision unchanged.

    Each predictor is called independently in a `try/except`. A
    failure of one model never affects the others — the loop
    continues, logging the offending `model_id` and `stage` as a
    WARNING. The decision value is returned byte-identical
    regardless of any predictor outcome.

    Parameters
    ----------
    decision : T
        The strategy's deterministic output. Returned unchanged
        regardless of any predictor outcome.
    predictors : Sequence[ShadowPredictor] | Iterable[...] | None
        The shadow wrappers. `None` and empty sequence are both
        pass-through (no-op).
    feature_row : Mapping[str, Any]
        Row passed to every predictor. Construction is the
        strategy's responsibility — the strategy knows its own
        feature shape.
    logger : logging.Logger | None
        Optional logger used to report predictor failures.

    Returns
    -------
    T
        The `decision` value, byte-identical to the input.
    """
    if predictors is None:
        return decision
    log = logger if logger is not None else _DEFAULT_LOGGER
    for predictor in predictors:
        if not isinstance(predictor, ShadowPredictor):
            raise TypeError(
                f"every entry in `predictors` must be a ShadowPredictor; "
                f"got {type(predictor).__name__}"
            )
        try:
            predictor.predict(feature_row)
        except Exception as exc:  # noqa: BLE001 — see contract above
            log.warning(
                "shadow_predict_failed model_id=%s stage=%s err=%s",
                predictor.model_id,
                predictor.stage,
                exc,
            )
    return decision


def with_shadow_pred(
    decision: T,
    *,
    predictor: ShadowPredictor | None,
    feature_row: Mapping[str, Any],
    logger: logging.Logger | None = None,
) -> T:
    """Backward-compat single-predictor variant.

    Equivalent to `with_shadow_preds(decision, predictors=[predictor],
    ...)` when `predictor is not None`. Kept as the original PART-2
    API so existing callers (PART-3 vwap wiring, tests) don't need
    to migrate.
    """
    if predictor is None:
        return decision
    return with_shadow_preds(
        decision,
        predictors=[predictor],
        feature_row=feature_row,
        logger=logger,
    )
