"""Per-strategy shadow-mode adapter (S-AI-WS7-PART-2 + PART-4 + S10).

Provides `with_shadow_pred` (single predictor, original PART-2 API),
`with_shadow_preds` (plural — added in PART-4 so a strategy can run
multiple shadow models concurrently), and `with_shadow_preds_advisory`
(S10 — like `with_shadow_preds` but also captures scores from
`advisory`-stage predictors and returns them alongside the decision).

Non-negotiables all helpers enforce by construction:

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
- **Advisory scores are observation-only.** `with_shadow_preds_advisory`
  returns scores for advisory-stage predictors as a dict. The caller
  (Coordinator advisory hook) logs them; no code path acts on them
  until the operator explicitly enables the advisory gate.

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

_ADVISORY_STAGE = "advisory"


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


def capture_shadow_preds(
    predictors: Sequence[ShadowPredictor] | Iterable[ShadowPredictor] | None,
    feature_row: Mapping[str, Any],
    *,
    logger: logging.Logger | None = None,
) -> dict[str, dict[str, Any]]:
    """Run shadow predictors and RETURN each model's decision score.

    Same observe-only contract as :func:`with_shadow_preds` — one
    ``predict`` per predictor (so the WS7 audit log is written exactly as
    before), each call independently wrapped so one model's failure never
    masks another. The difference is the scores are *returned* (and meant to
    be persisted onto the order package) instead of discarded. **Observe-only:
    the returned scores are metadata for the journal — they must never be
    read back into any sizing/gating decision (the shadow non-influence
    contract above still holds).**

    Returns ``{model_id: {"stage": <stage>, "score": <float>}}`` for every
    predictor that scored successfully; empty dict on ``None``/empty input.
    """
    out: dict[str, dict[str, Any]] = {}
    if predictors is None:
        return out
    log = logger if logger is not None else _DEFAULT_LOGGER
    for predictor in predictors:
        if not isinstance(predictor, ShadowPredictor):
            raise TypeError(
                f"every entry in `predictors` must be a ShadowPredictor; "
                f"got {type(predictor).__name__}"
            )
        try:
            score = predictor.predict(feature_row)
            out[predictor.model_id] = {
                "stage": predictor.stage,
                "score": float(score),
            }
        except Exception as exc:  # noqa: BLE001 — see contract above
            log.warning(
                "shadow_predict_failed model_id=%s stage=%s err=%s",
                predictor.model_id, predictor.stage, exc,
            )
    return out


def with_shadow_preds_advisory(
    decision: T,
    *,
    predictors: Sequence[ShadowPredictor] | Iterable[ShadowPredictor] | None,
    feature_row: Mapping[str, Any],
    logger: logging.Logger | None = None,
) -> tuple[T, dict[str, float]]:
    """Run shadow predictors and capture scores from advisory-stage models.

    Identical to `with_shadow_preds` in its observe-only contract — the
    decision is returned byte-identical regardless of any predictor outcome.
    Additionally returns a ``dict[model_id, score]`` for every predictor
    whose ``stage == "advisory"``. Shadow-stage predictor scores are still
    written to the audit log but are NOT included in the returned dict.

    Parameters
    ----------
    decision : T
        The strategy's deterministic output. Returned unchanged.
    predictors : Sequence[ShadowPredictor] | Iterable[...] | None
        The shadow wrappers. ``None`` and empty sequence are pass-through.
    feature_row : Mapping[str, Any]
        Row passed to every predictor.
    logger : logging.Logger | None
        Optional logger for predictor failures.

    Returns
    -------
    tuple[T, dict[str, float]]
        ``(decision, advisory_scores)`` where ``advisory_scores`` maps
        ``model_id → score`` for every advisory-stage predictor that
        succeeded. Empty dict when no advisory predictors are wired or
        all advisory calls fail.
    """
    if predictors is None:
        return decision, {}
    log = logger if logger is not None else _DEFAULT_LOGGER
    advisory_scores: dict[str, float] = {}
    for predictor in predictors:
        if not isinstance(predictor, ShadowPredictor):
            raise TypeError(
                f"every entry in `predictors` must be a ShadowPredictor; "
                f"got {type(predictor).__name__}"
            )
        try:
            score = predictor.predict(feature_row)
            if predictor.stage == _ADVISORY_STAGE:
                advisory_scores[predictor.model_id] = score
        except Exception as exc:  # noqa: BLE001 — see contract above
            log.warning(
                "shadow_predict_failed model_id=%s stage=%s err=%s",
                predictor.model_id,
                predictor.stage,
                exc,
            )
    return decision, advisory_scores
