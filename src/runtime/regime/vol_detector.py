"""Volatility-axis regime detector (S-MLOPT-S15b, Phase 3.3 track B).

The trend axis (``detector.py``) classifies the latest bar by ADX-14 into
``chop`` / ``transitional`` / ``trending`` — the taxonomy the regime-router
policy table keys on today. S15b adds a **second, orthogonal** axis: a
*volatility* regime (``calm`` / ``volatile``) so the router can shadow-evaluate
a 2-D ``trend × vol`` policy cell and accrue would-gate evidence for a later
Tier-3 decision. **Observe-only** — like phases 1+2 of the trend axis, nothing
here changes an order decision.

Why a detector (not the model)?
-------------------------------
Every registry regime head (``{btc,mes}-regime-{5m,15m,1h,1d}-lgbm-*``) is a
LightGBM classifier that predicts the volatility class (``range`` / ``volatile``,
2-class since ``S-ML-REGIME-CLASSIFIER-FIX``). We do **not** run the model here:
parallel to how the trend axis uses the ADX *detector* (a pure threshold on a
live indicator) rather than a model, the vol axis uses a pure threshold on the
live rolling volatility. The threshold is the classifier's own **frozen
``vol_bucket`` edge** — the quantile cut the trainer froze into ``model_state``
at fit time (see ``ml.trainers.lightgbm_multiclass`` /
``RegimeClassifierTrainer``) — so the detector's calm/volatile boundary is the
same boundary the model's dominant feature buckets on, per ``(symbol,
timeframe)``. The bucketing math is reused verbatim from
``src.runtime.regime_shadow`` (the live shadow-scoring path), so the serve-time
vol value is computed identically to the model's feature row.

Edge sourcing
-------------
The frozen edges come from the **same registry regime specs** the per-bar
scoring path (``src.runtime.regime_bar_scoring``) reads — resolved once per
process and cached. This keeps the calm/volatile boundary always-consistent
with the deployed heads (no hand-copied edge values to drift). When no regime
head exists for a strategy's ``(symbol, timeframe)`` — or the registry can't be
read (tests, a fresh box, a partial deploy) — the detector returns
``vol_regime="unknown"`` and the policy evaluator falls through to the
permissive default, so the live tick is unchanged.

Collapse to 2 classes
----------------------
A head freezes ``vol_bucket_labels`` (e.g. ``[vol_b0, vol_b1, vol_b2]``, lowest
first) + ``vol_bucket_edges`` (the upper cut of each bucket but the last). The
2-class projection (``market_features`` doc: ``vol_b0 → range``,
``vol_b1/b2 → volatile``) maps the **lowest** bucket to ``calm`` and every
higher bucket to ``volatile`` — i.e. ``rolling_log_return_vol <= edges[0]`` is
``calm``, else ``volatile``. This handles 2- and 3-bucket specs uniformly.

Parity caveat (``MB-20260604-005``)
-----------------------------------
The serve-time vol value is ``rolling_log_return_vol`` (close-to-close), which
matches the v2 heads' ``vol_feature_column`` but **not** the yz heads' frozen
``yang_zhang_vol`` — the same pre-existing train/serve gap the signal-time and
per-bar shadow paths already carry. S15b is observe-only and deliberately shares
that computation; closing the gap (Phase 4.2) gates any *enforcement* decision.

Pure + never-raises: every entry point swallows its own failures and degrades
to ``unknown`` so the observability path can never break the trading loop.
"""
from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

from src.runtime.regime_shadow import (
    bucket_for_vol,
    closes_from_candles,
    regime_spec_of,
    rolling_log_return_vol,
)

logger = logging.getLogger(__name__)

# Vol-axis labels. 2-class to match the existing classifier (S15b decision):
# the lowest frozen vol bucket is the "calm" tape; everything above is
# "volatile". ``unknown`` is the permissive sentinel (warmup / no spec / failure).
VOL_CALM = "calm"
VOL_VOLATILE = "volatile"
VOL_UNKNOWN = "unknown"

_VALID_VOL_REGIMES = frozenset({VOL_CALM, VOL_VOLATILE})

_SOURCE = "vol-bucket-edges"

# Per-process cache of the resolved ``(SYMBOL, TIMEFRAME) -> spec`` map. The
# specs are frozen at fit time, so resolving once per process (cleared on every
# deploy/restart) is correct; mirrors ``regime_bar_scoring._PREDICTOR_CACHE``.
_VOL_SPEC_CACHE: Optional[dict] = None


def _norm(s: Any) -> str:
    return str(s or "").strip().upper()


def resolve_vol_specs(*, force: bool = False) -> dict:
    """Return the cached ``(SYMBOL, TIMEFRAME) -> regime_spec`` map.

    Built once per process from the registry's shadow-stage regime heads (the
    same source ``regime_bar_scoring`` scores), keyed by the spec's own
    ``(symbol, timeframe)`` upper-cased. When two heads share a
    ``(symbol, timeframe)`` the first resolved wins — the frozen edges are
    quantiles of the same dataset family/timeframe, so they agree closely; the
    detector only needs the lowest cut and is not sensitive to the choice.

    Never raises: any failure (no registry, import error, empty shadow set)
    yields ``{}`` so the detector degrades to ``unknown`` everywhere.
    """
    global _VOL_SPEC_CACHE
    if _VOL_SPEC_CACHE is not None and not force:
        return _VOL_SPEC_CACHE
    specs: dict = {}
    try:
        from pathlib import Path

        from ml.registry.model_registry import ModelRegistry
        from ml.shadow.factory import (
            DEFAULT_REGISTRY_ROOT,
            discover_shadow_stage_model_ids,
            resolve_predictors,
        )
        from src.utils.paths import runtime_logs_dir

        registry = ModelRegistry(Path(DEFAULT_REGISTRY_ROOT))
        ids = discover_shadow_stage_model_ids(registry)
        if ids:
            log_path = runtime_logs_dir() / "shadow_predictions.jsonl"
            predictors = resolve_predictors(list(ids), registry, log_path=log_path)
            for predictor in predictors:
                spec = regime_spec_of(predictor)
                if spec is None:
                    continue
                key = (_norm(spec.get("symbol")), _norm(spec.get("timeframe")))
                if not key[0] or not key[1]:
                    continue
                # Parity: the live vol value is close-to-close
                # ``rolling_log_return_vol``, so only adopt edges from a head
                # that froze its bucket against the SAME column. The yz heads
                # (``vol_feature_column: yang_zhang_vol``) bucket a different
                # estimator — using their edges would mis-place the
                # calm/volatile boundary (a sharper case of MB-20260604-005).
                # Skip them; a (symbol, timeframe) served only by yz heads
                # stays ``unknown`` (permissive) rather than wrongly labelled.
                vol_col = str(spec.get("vol_feature_column") or "rolling_log_return_vol")
                if vol_col != "rolling_log_return_vol":
                    continue
                # Store a compact spec (only the bucketing fields + a precise
                # model_id) so the cache never carries the head's booster_str.
                compact = {
                    "symbol": spec.get("symbol"),
                    "timeframe": spec.get("timeframe"),
                    "vol_bucket_labels": list(spec.get("vol_bucket_labels") or []),
                    "vol_bucket_edges": list(spec.get("vol_bucket_edges") or []),
                    "vol_window_n": spec.get("vol_window_n"),
                    "model_id": getattr(predictor, "model_id", None),
                }
                specs.setdefault(key, compact)
    except Exception:  # noqa: BLE001 — observability-only, never break a tick
        logger.debug("vol_detector: spec resolution failed; degrading to unknown", exc_info=False)
        specs = {}
    _VOL_SPEC_CACHE = specs
    return specs


def vol_regime_from_spec(
    spec: Optional[Mapping[str, Any]],
    closes: Any,
) -> tuple[str, Optional[float]]:
    """Classify ``calm`` / ``volatile`` for a single frozen spec.

    Returns ``(vol_regime, rolling_log_return_vol)``. ``vol_regime`` is
    ``unknown`` (with ``None`` vol) when the spec is degenerate (fewer than 2
    labels), the live vol can't be computed, or the bucket can't be resolved.
    Pure; never raises.
    """
    if not isinstance(spec, Mapping):
        return VOL_UNKNOWN, None
    labels = list(spec.get("vol_bucket_labels") or [])
    if len(labels) < 2:
        # A 1-bucket (or empty) spec can't separate calm from volatile.
        return VOL_UNKNOWN, None
    edges = [float(e) for e in (spec.get("vol_bucket_edges") or [])]
    if not edges:
        return VOL_UNKNOWN, None
    try:
        window_n = int(spec.get("vol_window_n") or 20)
    except (TypeError, ValueError):
        window_n = 20
    vol = rolling_log_return_vol(closes, window_n)
    if vol is None:
        return VOL_UNKNOWN, None
    bucket = bucket_for_vol(vol, edges, labels)
    if bucket is None:
        return VOL_UNKNOWN, vol
    # Collapse: lowest bucket -> calm, every higher bucket -> volatile.
    vol_regime = VOL_CALM if bucket == labels[0] else VOL_VOLATILE
    return vol_regime, vol


def detect_vol_regime(
    candles_df: Any,
    *,
    symbol: Optional[str],
    timeframe: Optional[str],
    specs: Optional[dict] = None,
) -> dict:
    """Classify the latest bar's volatility regime for ``(symbol, timeframe)``.

    Mirrors ``detector.detect_regime``'s contract: a pure, never-raising
    function returning ``{"vol_regime", "rolling_log_return_vol", "source"}``.

    - ``vol_regime`` ∈ ``{"calm", "volatile", "unknown"}``.
    - ``unknown`` when there is no frozen regime spec for ``(symbol,
      timeframe)``, the candles are missing/short, or the bucket is
      unresolvable — the permissive default that leaves the tick unchanged.

    ``specs`` is injectable (tests / a non-registry caller); when ``None`` the
    cached registry-resolved map is used.
    """
    out = {"vol_regime": VOL_UNKNOWN, "rolling_log_return_vol": None, "source": _SOURCE}
    if not symbol or not timeframe:
        return out
    try:
        table = resolve_vol_specs() if specs is None else specs
        spec = table.get((_norm(symbol), _norm(timeframe)))
        if spec is None:
            return out
        closes = closes_from_candles(candles_df)
        vol_regime, vol = vol_regime_from_spec(spec, closes)
        out["vol_regime"] = vol_regime
        out["rolling_log_return_vol"] = round(vol, 8) if vol is not None else None
        model_id = spec.get("model_id") or spec.get("symbol")
        if model_id:
            out["source"] = f"{_SOURCE}:{model_id}"
    except Exception:  # noqa: BLE001 — observability-only, never break a tick
        return {"vol_regime": VOL_UNKNOWN, "rolling_log_return_vol": None, "source": _SOURCE}
    return out
