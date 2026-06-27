"""ML vol-axis verdict — Design A, Phase 1 (shadow).

The regime heads predict a 2-class **volatility** label ``{range, volatile}``
(``P(volatile)``). Today the live ``vol_regime`` axis (``calm`` / ``volatile``)
is produced by ``vol_detector.py`` — a **frozen-edge threshold** on the live
``rolling_log_return_vol``, NOT the head's ``predict_proba``. Design A replaces
that frozen-edge threshold with the **advisory** head's live ``P(volatile)``
thresholded by ``ML_VOL_VERDICT_THRESHOLD`` (default 0.5):

    ml_vol_regime(symbol, timeframe, candles_df=None) ->
      {vol_regime: calm|volatile|unknown, p_volatile: float|None,
       source: "ml-advisory:<model_id>" | "unavailable",
       model_id: <model_id> | None}

This module is the **Phase-1 (observe-only)** verdict source: the gate logs a
``regime_ml_vol_shadow`` audit row comparing the ML label against the frozen
label, but the gate DECISION still uses the frozen ``intent.vol_regime``. Phase
2 (use) / Phase 3 (enforce) are deferred (separate operator-gated PRs).

Design contract (``docs/research/A-regime-router-ml-vol-verdict-DESIGN-2026-06-27.md``):

- **Advisory-only.** Only ``target_deployment_stage == "advisory"`` regime
  heads are consulted (canonical via ``ml.manifest.canonical_stage``). A
  ``shadow``-stage head is NEVER read here — promotion stays the meaningful
  act. (``vol_detector`` reads ``shadow``-stage heads; this module reads
  ``advisory``-stage heads — the two are intentionally disjoint by stage.)
- **Prefer v2 / non-yz.** When several advisory heads cover a
  ``(symbol, timeframe)`` we keep the v2 / non-yz head (the yz heads saturate
  live — the same skip ``vol_detector`` applies, mirrored here).
- **Zero new fetches (preferred path).** ``P(volatile)`` is read from a small
  in-process cache the per-bar scorer (``regime_bar_scoring``) publishes; the
  inline ``predict_proba`` scoring is only a fallback for when that cache is
  cold and a ``candles_df`` is supplied.
- **Fail-permissive everywhere.** No advisory head / unreadable registry /
  uncomputable features / any exception → ``vol_regime="unknown"`` (which
  ``would_gate`` treats as the permissive default — never strands a signal).

Pure + never-raises (every entry point swallows its own failures and degrades
to ``unknown``).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from src.runtime.regime.vol_detector import (
    VOL_CALM,
    VOL_UNKNOWN,
    VOL_VOLATILE,
)
from src.runtime.regime_shadow import (
    closes_from_candles,
    feature_row_for_predictor,
    regime_spec_of,
)
from src.runtime.runtime_flags import _ml_vol_verdict_threshold

logger = logging.getLogger(__name__)

# The 2-class regime label whose probability drives the calm/volatile verdict.
_VOLATILE_CLASS = "volatile"

# Per-bar publish cache the regime-bar scorer populates: {model_id: (bar_ts,
# p_volatile)}. ``ml_vol_regime`` reads this first so the decision path adds
# ZERO fetches. Cleared on process restart (in-process only) like the
# regime_bar_scoring caches; ``clear_ml_vol_cache`` is for tests.
_ML_VOL_PBAR_CACHE: dict[str, tuple[Any, Optional[float]]] = {}

# Per-process cache of the resolved advisory-stage ``(SYMBOL, TIMEFRAME) ->
# {spec fields + predictor}`` map. Mirrors ``vol_detector._VOL_SPEC_CACHE``:
# the specs are frozen at fit time, so resolving once per process (cleared on
# every deploy/restart) is correct. ``None`` = not yet resolved.
_ADVISORY_SPEC_CACHE: Optional[dict] = None


def _norm(s: Any) -> str:
    return str(s or "").strip().upper()


def publish_p_volatile(model_id: str, bar_ts: Any, p_volatile: Optional[float]) -> None:
    """Publish a head's current-bar ``P(volatile)`` into the in-process cache.

    Called by ``regime_bar_scoring.emit_regime_bar_predictions`` for every
    advisory-stage regime head it scores, so ``ml_vol_regime`` can read the
    score off the per-bar cadence without a fetch. Never raises.
    """
    try:
        _ML_VOL_PBAR_CACHE[str(model_id)] = (bar_ts, p_volatile)
    except Exception:  # noqa: BLE001 — publish is best-effort
        pass


def clear_ml_vol_cache() -> None:
    """Clear both per-process caches (advisory specs + per-bar publish).

    Test/ops helper — the live process never needs to clear them (a deploy
    restarts the process, which empties both).
    """
    global _ADVISORY_SPEC_CACHE
    _ADVISORY_SPEC_CACHE = None
    _ML_VOL_PBAR_CACHE.clear()


def _is_yz_spec(spec: Any) -> bool:
    """True when a regime spec buckets the yang-zhang estimator (a yz head).

    The yz heads saturate live (frozen edges sit above the calm-regime live
    values), so we prefer their v2 sibling — same skip ``vol_detector``
    applies. A spec whose ``vol_feature_column`` is not the close-to-close
    ``rolling_log_return_vol`` is treated as a yz/non-v2 head.
    """
    try:
        vol_col = str((spec or {}).get("vol_feature_column") or "rolling_log_return_vol")
    except Exception:  # noqa: BLE001
        return True
    return vol_col != "rolling_log_return_vol"


def discover_advisory_stage_regime_specs(*, force: bool = False) -> dict:
    """Return the cached ``(SYMBOL, TIMEFRAME) -> entry`` map for ADVISORY heads.

    Parallel to ``vol_detector.resolve_vol_specs`` but:

    - filters ``target_deployment_stage == "advisory"`` (canonical via
      ``ml.manifest.canonical_stage``) instead of ``shadow``; and
    - keeps the **resolved predictor** (so the verdict can call
      ``predict_proba``), not just the frozen bucketing fields.

    When two advisory heads cover the same ``(symbol, timeframe)`` the **v2 /
    non-yz** head wins (the yz heads saturate live). Each map value is a dict:
    ``{"symbol", "timeframe", "model_id", "predictor", "is_yz"}``.

    Never raises: any failure (no registry, import error, empty advisory set)
    yields ``{}`` so the verdict degrades to ``unknown`` everywhere.
    """
    global _ADVISORY_SPEC_CACHE
    if _ADVISORY_SPEC_CACHE is not None and not force:
        return _ADVISORY_SPEC_CACHE
    specs: dict = {}
    try:
        from pathlib import Path

        from ml.manifest import canonical_stage
        from ml.registry.model_registry import ModelRegistry
        from ml.shadow.factory import DEFAULT_REGISTRY_ROOT, resolve_predictors
        from src.utils.paths import runtime_logs_dir

        registry = ModelRegistry(Path(DEFAULT_REGISTRY_ROOT))
        advisory_ids: list[str] = []
        for entry in registry.list():
            try:
                stage = canonical_stage(entry.target_deployment_stage)
            except Exception:  # noqa: BLE001 — skip an unrecognized stage row
                continue
            if stage == "advisory":
                advisory_ids.append(entry.model_id)
        if advisory_ids:
            log_path = runtime_logs_dir() / "shadow_predictions.jsonl"
            predictors = resolve_predictors(
                sorted(advisory_ids), registry, log_path=log_path
            )
            for predictor in predictors:
                spec = regime_spec_of(predictor)
                if spec is None:
                    continue  # non-regime advisory model — not a vol head
                key = (_norm(spec.get("symbol")), _norm(spec.get("timeframe")))
                if not key[0] or not key[1]:
                    continue
                is_yz = _is_yz_spec(spec)
                existing = specs.get(key)
                # Prefer the v2 / non-yz head: keep a non-yz over a yz, never
                # replace a non-yz with a yz. First non-yz wins; a yz fills the
                # slot only until a v2 sibling arrives.
                if existing is not None and (existing.get("is_yz") is False or is_yz):
                    continue
                specs[key] = {
                    "symbol": spec.get("symbol"),
                    "timeframe": spec.get("timeframe"),
                    "model_id": getattr(predictor, "model_id", None),
                    "predictor": predictor,
                    "is_yz": is_yz,
                }
    except Exception:  # noqa: BLE001 — observability-only, never break a tick
        logger.debug(
            "ml_vol_verdict: advisory spec resolution failed; degrading to unknown",
            exc_info=False,
        )
        specs = {}
    _ADVISORY_SPEC_CACHE = specs
    return specs


def _p_volatile_from_cache(model_id: Optional[str]) -> Optional[float]:
    """Read a head's published current-bar ``P(volatile)`` from the cache.

    Returns ``None`` when the head hasn't been per-bar scored yet (cold cache)
    or its published score was ``None``. The bar timestamp is stored but not
    range-checked here — the per-bar scorer only publishes for a closed bar it
    actually scored, and a stale-but-present score still beats an inline fetch
    on the decision path (the staleness is at most one bar duration).
    """
    if not model_id:
        return None
    cached = _ML_VOL_PBAR_CACHE.get(str(model_id))
    if cached is None:
        return None
    _bar_ts, p_vol = cached
    return p_vol


def _p_volatile_inline(predictor: Any, symbol: str, timeframe: str, candles_df: Any) -> Optional[float]:
    """Fallback: score ``P(volatile)`` inline for the current bar.

    Builds the parity feature row via ``feature_row_for_predictor`` (identical
    to the per-bar + signal-time paths) and reads the ``volatile`` class prob
    off ``predictor.wrapped.predict_proba``. Returns ``None`` (caller →
    ``unknown``) when candles are absent, the feature row can't be built, the
    predictor has no ``predict_proba``, or anything raises. Never raises.
    """
    if candles_df is None:
        return None
    try:
        closes = closes_from_candles(candles_df)
        row = feature_row_for_predictor(
            predictor,
            {"symbol": symbol, "timeframe": timeframe, "event_source": "ml_vol_verdict"},
            closes=closes,
            symbol=symbol,
            timeframe=timeframe,
            candles_df=candles_df,
        )
        if row is None:
            return None
        wrapped = getattr(predictor, "wrapped", None) or predictor
        proba_fn = getattr(wrapped, "predict_proba", None)
        if proba_fn is None:
            return None
        proba = proba_fn(row)
        if not proba:
            return None
        val = proba.get(_VOLATILE_CLASS)
        return float(val) if val is not None else None
    except Exception:  # noqa: BLE001 — fail-permissive, never break a tick
        return None


def ml_vol_regime(
    symbol: Optional[str],
    timeframe: Optional[str],
    candles_df: Any = None,
    *,
    specs: Optional[dict] = None,
) -> dict:
    """Return the advisory head's ``calm`` / ``volatile`` / ``unknown`` verdict.

    Resolution:

    1. Resolve the advisory regime head for ``(symbol, timeframe)`` (v2 / non-yz
       preferred) via ``discover_advisory_stage_regime_specs``.
    2. Read the current-bar ``P(volatile)`` — **preferred** from the per-bar
       publish cache (zero fetches); **fallback** inline via ``predict_proba``
       over a ``candles_df``, only when the cache is cold.
    3. Map ``P(volatile) >= ML_VOL_VERDICT_THRESHOLD`` → ``volatile`` else
       ``calm``.

    Fail-permissive: no advisory head, no resolvable ``P(volatile)``, or any
    exception → ``{"vol_regime": "unknown", "p_volatile": None,
    "source": "unavailable", "model_id": None}``.

    ``specs`` is injectable (tests / a non-registry caller); when ``None`` the
    cached registry-resolved advisory map is used.
    """
    out = {
        "vol_regime": VOL_UNKNOWN,
        "p_volatile": None,
        "source": "unavailable",
        "model_id": None,
    }
    if not symbol or not timeframe:
        return out
    try:
        table = discover_advisory_stage_regime_specs() if specs is None else specs
        entry = table.get((_norm(symbol), _norm(timeframe)))
        if entry is None:
            return out  # no advisory head for this market → unknown (permissive)
        model_id = entry.get("model_id")
        predictor = entry.get("predictor")
        # Preferred: the per-bar publish cache (zero fetches on the decision path).
        p_vol = _p_volatile_from_cache(model_id)
        # Fallback: inline score (only when the cache is cold AND candles given).
        if p_vol is None and predictor is not None:
            p_vol = _p_volatile_inline(predictor, str(symbol), str(timeframe), candles_df)
        if p_vol is None:
            return out  # uncomputable P(volatile) → unknown (permissive)
        threshold = _ml_vol_verdict_threshold()
        vol_regime = VOL_VOLATILE if float(p_vol) >= threshold else VOL_CALM
        out["vol_regime"] = vol_regime
        out["p_volatile"] = round(float(p_vol), 8)
        out["model_id"] = model_id
        out["source"] = f"ml-advisory:{model_id}" if model_id else "ml-advisory"
        return out
    except Exception:  # noqa: BLE001 — observability-only, never break a tick
        return {
            "vol_regime": VOL_UNKNOWN,
            "p_volatile": None,
            "source": "unavailable",
            "model_id": None,
        }


__all__ = [
    "VOL_CALM",
    "VOL_UNKNOWN",
    "VOL_VOLATILE",
    "clear_ml_vol_cache",
    "discover_advisory_stage_regime_specs",
    "ml_vol_regime",
    "publish_p_volatile",
]
