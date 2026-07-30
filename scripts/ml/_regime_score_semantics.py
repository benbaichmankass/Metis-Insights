#!/usr/bin/env python3
"""ONE module owns the answer to: *what is the shadow log's ``score``?*

Import it. Do not re-derive the answer per diagnostic — that is exactly how
two probes ended up printing the same substituted quantity under two different
confident labels on the same day (2026-07-30).

THE FIELD IS NOT A PROBABILITY OF ANYTHING IN PARTICULAR
--------------------------------------------------------
``runtime_logs/shadow_predictions.jsonl::score`` and
``order_packages.model_scores[<id>]["score"]`` are both written by
``ShadowPredictor.predict`` (``ml/predictors/shadow.py``), which returns
``self._wrapped.predict(row)``. What that means depends on the WRAPPED
predictor's class:

* binary head (``LightGBMPredictor``)      -> P(positive class). A probability.
* multiclass head (``MulticlassPredictor``) -> ``max(proba.values())``
  (``ml/predictors/multiclass.py``) — the confidence of whichever class won,
  hence ``>= 0.5`` by construction for a 2-class regime head, and **HIGH for a
  head that is confidently CALM**.

Every regime head in the fleet is multiclass. So bucketing regime ``score`` as
"P(volatile)" does not merely mislabel — it INVERTS the meaning for half the
range, and empties the low bucket by construction. A 2026-07-30 fleet sweep
measured ``frac(score < 0.5) == 0.0000`` across all ~30 regime heads while
non-multiclass heads in the same log ranged freely below 0.5. That reading
came one step from a false P1 against the live real-money BTC vol gate.

THE QUANTITY THE LIVE GATE ACTUALLY THRESHOLDS
-----------------------------------------------
``src/runtime/regime_bar_scoring._maybe_publish_p_volatile`` ->
``predict_proba(row)[VOLATILE_CLASS]``, thresholded at
``ML_VOL_VERDICT_THRESHOLD``. A diagnostic that claims to say anything about
the gate MUST report that number, not ``score``.

Read-only, trainer-side (the registry lives there). Never touches the order
path.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

#: The class whose probability the live vol verdict thresholds.
VOLATILE_CLASS = "volatile"
#: ``ML_VOL_VERDICT_THRESHOLD`` default — the gate's calm/volatile cut.
VOL_THRESHOLD = 0.5

#: The honest label for the raw logged field. Use it verbatim wherever the
#: logged value is displayed, so no reader can mistake it for a class
#: probability.
LOGGED_SCORE_LABEL = "logged score = predict() [max(proba) for multiclass]"

_PREDICTOR_CACHE: Dict[str, Any] = {}


def resolve_proba_fn(model_id: str):
    """The model's ``predict_proba``, or ``None`` when unresolvable here.

    ``None`` is a first-class answer — a probe running off-trainer has no
    registry. Callers must then report only the logged value under
    :data:`LOGGED_SCORE_LABEL` and say the gate quantity was unavailable.
    Never substitute ``score`` for it.
    """
    if model_id in _PREDICTOR_CACHE:
        return _PREDICTOR_CACHE[model_id]
    fn = None
    try:
        from ml.registry.model_registry import ModelRegistry  # noqa: PLC0415
        from ml.shadow import factory as _factory  # noqa: PLC0415
        from ml.shadow.factory import resolve_predictor  # noqa: PLC0415

        reg = ModelRegistry(_factory._resolve_default_registry_root())
        sp = resolve_predictor(model_id, reg, log_path=None)  # no audit writes
        base = getattr(sp, "wrapped", sp)
        fn = getattr(base, "predict_proba", None)
    except Exception:  # noqa: BLE001 — a probe degrades, never dies
        fn = None
    _PREDICTOR_CACHE[model_id] = fn
    return fn


def p_volatile(model_id: str, feature_row: Mapping[str, Any]) -> Optional[float]:
    """``predict_proba(row)[volatile]`` for one logged row, or ``None``.

    The row is passed through UNMODIFIED, exactly as the live path does, so
    the predictor's own ``_encode_row`` handles categoricals identically.
    """
    fn = resolve_proba_fn(model_id)
    if fn is None:
        return None
    try:
        proba = fn(feature_row) or {}
    except Exception:  # noqa: BLE001 — one bad row must not kill a sweep
        return None
    val = proba.get(VOLATILE_CLASS)
    return None if val is None else float(val)


def p_volatile_for_rows(
    model_id: str, feature_rows: List[Mapping[str, Any]],
) -> Tuple[List[float], int]:
    """``(p_volatile values, n_unresolved)`` over many rows.

    The second element is the honest coverage denominator — the same shape
    ``/performance`` uses for ``rCoverage`` (*transparency, never a raw-value
    fallback*). Report it; a silently short list reads as a clean sample.
    """
    out: List[float] = []
    unresolved = 0
    for row in feature_rows:
        val = p_volatile(model_id, row)
        if val is None:
            unresolved += 1
        else:
            out.append(val)
    return out, unresolved
