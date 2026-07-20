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

This module is the verdict SOURCE for all three Design-A modes; the live mode is
selected by ``REGIME_ML_VERDICT_MODE`` (``off`` default / ``shadow`` / ``use``)
and consumed in ``src/runtime/intents.py``:

- ``shadow`` — the gate logs a ``regime_ml_vol_shadow`` audit row comparing the
  ML label against the frozen ``intent.vol_regime``; the gate DECISION is
  unchanged (still the frozen label).
- ``use`` — the gate DECISION substitutes the advisory head's ML vol label via
  ``intents._decision_vol_regime`` (per-SYMBOL resolution,
  :func:`ml_vol_regime_for_symbol`); fail-permissive → frozen when the verdict is
  ``unknown``. **Wired + LIVE since 2026-06-28** (commit ``e0d052e7`` / #4896): on
  BTC ``use`` already changes real-money routing (the 15m advisory head covers
  every BTC cell). ETH/SOL cells activate per-symbol as their 15m heads promote
  ``shadow → advisory``. ``use`` and ``ML_VOL_VERDICT_THRESHOLD`` are Tier-3
  (order-routing-affecting), operator-gated + walk-forward-gated.

(Historical note: ``use``/enforce were a documented placeholder until #4896 —
before that the gate decision always used the frozen label.)

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
# (registry_fingerprint, specs) — the fingerprint gates cache validity so a
# registry stage change (promotion) rotates the cache without a restart.
_ADVISORY_SPEC_CACHE: Optional[tuple] = None


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
    # Registry fingerprint gates the cache (2026-07-20): a promotion changes
    # per-model JSON mtimes (the mirror publish rewrites them), so a stage
    # change reaches this process on the next call — no restart needed. A
    # fingerprint error yields the stable -1.0, retaining the cached specs.
    try:
        from ml.shadow.factory import DEFAULT_REGISTRY_ROOT as _REG_ROOT

        from src.runtime.registry_fingerprint import registry_fingerprint

        _fp = registry_fingerprint(_REG_ROOT)
    except Exception:  # noqa: BLE001 — fingerprint is best-effort
        _fp = -1.0
    if _ADVISORY_SPEC_CACHE is not None and not force:
        cached_fp, cached_specs = _ADVISORY_SPEC_CACHE
        if cached_fp == _fp:
            return cached_specs
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
    _ADVISORY_SPEC_CACHE = (_fp, specs)
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


def _advisory_entry_for_symbol(symbol: Optional[str], specs: Optional[dict]) -> Optional[dict]:
    """Pick the SYMBOL's advisory vol head, regardless of the strategy timeframe.

    The validated Design-A A/B (``scripts/backtest_system.py`` clock_tf=15m,
    ml_model_id=btc-regime-15m-lgbm-v2) applied a SINGLE advisory head's vol
    label to every BTC strategy cell — the vol regime is a per-SYMBOL market
    label, not a per-strategy-timeframe one. This resolver mirrors that: among
    the advisory heads for ``symbol`` it prefers the non-yz head (yz saturates
    live) and, when several timeframes exist, the **15m** head (the backtest's
    clock), else the shortest available. Returns ``None`` when the symbol has no
    advisory head.
    """
    table = discover_advisory_stage_regime_specs() if specs is None else specs
    sym = _norm(symbol)
    if not sym:
        return None
    cands = [v for (s, _tf), v in table.items() if s == sym]
    if not cands:
        return None
    # Prefer non-yz, then the 15m timeframe (the backtest clock), then shortest.
    def _rank(entry: dict) -> tuple:
        is_yz = bool(entry.get("is_yz"))
        tf = _norm(entry.get("timeframe"))
        prefer_15m = 0 if tf == "15M" else 1
        # crude timeframe ordering (shorter first) for the tie-break
        order = {"1M": 1, "5M": 5, "15M": 15, "30M": 30, "1H": 60, "2H": 120, "4H": 240, "1D": 1440}
        return (1 if is_yz else 0, prefer_15m, order.get(tf, 9999))
    return sorted(cands, key=_rank)[0]


def ml_vol_regime_for_symbol(
    symbol: Optional[str],
    candles_df: Any = None,
    *,
    specs: Optional[dict] = None,
) -> dict:
    """Per-SYMBOL ML vol verdict — the decision-path resolver for Design-A.

    Like :func:`ml_vol_regime` but resolves the advisory head by **symbol** (via
    :func:`_advisory_entry_for_symbol`) instead of an exact ``(symbol,
    timeframe)`` match, so a 1h/4h strategy gets the symbol's advisory vol label
    (e.g. BTC → ``btc-regime-15m-lgbm-v2``) — matching how the validated A/B
    gated 1h/4h cells with the single 15m head. Same fail-permissive contract:
    no advisory head / uncomputable ``P(volatile)`` / any exception →
    ``{"vol_regime": "unknown", ...}``.
    """
    out = {"vol_regime": VOL_UNKNOWN, "p_volatile": None,
           "source": "unavailable", "model_id": None}
    if not symbol:
        return out
    try:
        entry = _advisory_entry_for_symbol(symbol, specs)
        if entry is None:
            return out
        model_id = entry.get("model_id")
        predictor = entry.get("predictor")
        p_vol = _p_volatile_from_cache(model_id)
        if p_vol is None and predictor is not None:
            p_vol = _p_volatile_inline(
                predictor, str(symbol), str(entry.get("timeframe") or ""), candles_df)
        if p_vol is None:
            return out
        threshold = _ml_vol_verdict_threshold()
        out["vol_regime"] = VOL_VOLATILE if float(p_vol) >= threshold else VOL_CALM
        out["p_volatile"] = round(float(p_vol), 8)
        out["model_id"] = model_id
        out["source"] = f"ml-advisory:{model_id}" if model_id else "ml-advisory"
        return out
    except Exception:  # noqa: BLE001 — observability-only, never break a tick
        return {"vol_regime": VOL_UNKNOWN, "p_volatile": None,
                "source": "unavailable", "model_id": None}


__all__ = [
    "VOL_CALM",
    "VOL_UNKNOWN",
    "VOL_VOLATILE",
    "clear_ml_vol_cache",
    "discover_advisory_stage_regime_specs",
    "ml_vol_regime",
    "ml_vol_regime_for_symbol",
    "publish_p_volatile",
]
