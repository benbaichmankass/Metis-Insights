"""Per-strategy shadow-mode adapter (S-AI-WS7-PART-2).

`with_shadow_pred` is the integration glue between a strategy's
deterministic `order_package(...)` output and a shadow-mode
predictor. It is intentionally tiny and stateless: the strategy
builds its package and a feature row, calls this helper, and gets
the SAME package back. The helper's only side effects are:

1. The predictor's own audit logger fires (per
   `ml.predictors.shadow.ShadowPredictor`).
2. If the predictor raises, this helper catches the exception and
   logs a warning. The strategy NEVER sees the model failure.

Non-negotiables this helper enforces by construction:

- **Shadow = observe.** The deterministic decision is returned
  byte-for-byte. There is no code path in which the model's score
  reaches the order package or the risk manager.
- **Model failure cannot crash the tick.** A misbehaving model
  (broken pickle, schema drift, division-by-zero) is contained to
  a `try/except` around `predictor.predict(...)`. The strategy's
  signal continues to execute as if shadow mode were off.
- **No-predictor mode is the default.** When `predictor is None`,
  the helper is a pass-through — strategies can call it
  unconditionally and pay nothing at runtime if no model is wired.

Example usage in a hypothetical strategy adapter::

    from src.runtime.shadow_adapter import with_shadow_pred

    def order_package(cfg, candles_df=None):
        package = _build_deterministic_package(cfg, candles_df)
        feature_row = {
            "strategy_name": "vwap",
            "setup_type":    package["meta"].get("setup_type", ""),
            "killzone":      package["meta"].get("killzone", ""),
            "direction":     package["direction"],
        }
        return with_shadow_pred(
            package,
            predictor=cfg.get("_shadow_predictor"),
            feature_row=feature_row,
        )

Wiring a real strategy with a real predictor instance is filed
for S-AI-WS7-PART-3 (operator-reviewed integration point per the
"No model in live strategy logic without staged promotion +
operator approval" rule).
"""
from __future__ import annotations

import logging
from typing import Any, Mapping, TypeVar

from ml.predictors.shadow import ShadowPredictor

_DEFAULT_LOGGER = logging.getLogger(__name__)

T = TypeVar("T")


def with_shadow_pred(
    decision: T,
    *,
    predictor: ShadowPredictor | None,
    feature_row: Mapping[str, Any],
    logger: logging.Logger | None = None,
) -> T:
    """Run a shadow-mode predictor as a side-effect; return decision unchanged.

    Parameters
    ----------
    decision : T
        The strategy's deterministic output. Returned unchanged
        regardless of predictor outcome.
    predictor : ShadowPredictor | None
        The shadow wrapper. Use `None` for no-op (useful when the
        strategy is shadow-aware but no model is configured for
        the current run).
    feature_row : Mapping[str, Any]
        Row passed to `predictor.predict(...)`. Construction is the
        strategy's responsibility — the strategy knows its own
        feature shape.
    logger : logging.Logger | None
        Optional logger used to report predictor failures. Defaults
        to `logging.getLogger(__name__)`.

    Returns
    -------
    T
        The `decision` value, byte-identical to the input.

    Notes
    -----
    This helper does NOT catch exceptions raised by the strategy
    itself; it only catches exceptions raised inside
    `predictor.predict(...)`. A misbehaving model cannot crash the
    tick; a misbehaving strategy still propagates as before.
    """
    if predictor is None:
        return decision

    if not isinstance(predictor, ShadowPredictor):
        # Misconfiguration — strategies are expected to pass a
        # ShadowPredictor (a no-op wrapper around any base Predictor).
        # Surface, don't silently consume.
        raise TypeError(
            f"predictor must be a ShadowPredictor or None; got "
            f"{type(predictor).__name__}"
        )

    log = logger if logger is not None else _DEFAULT_LOGGER
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
