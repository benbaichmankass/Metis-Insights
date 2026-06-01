"""Per-strategy signal builder functions — extracted from pipeline.py (PR-6).

Each builder fetches candles, calls the strategy unit, and returns a
pipeline-shape signal dict: {symbol, side, price, stop_loss, take_profit,
meta}. No qty — sizing is the per-account RiskManager's job (S-026 G1).

The module-level ``_build_killzone_exchange`` shim is kept as a named
attribute so tests can monkeypatch it: ``monkeypatch.setattr(
strategy_signal_builders, "_build_killzone_exchange", ...)``.

S3 (M11): every builder now also attaches a typed ``SignalPackage`` under
``sig["signal_package"]`` via ``_with_signal_package()``. All existing dict
keys are preserved unchanged — live pipeline consumers are unaffected.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import pandas as pd

from src.runtime.regime import detect_regime
from src.utils.signal_audit_logger import log_signal

logger = logging.getLogger(__name__)


def _stamp_regime_on_meta(meta: Dict[str, Any], candles_df: Any) -> Dict[str, Any]:
    """Stamp regime + ADX-14 onto a signal's meta dict (phase 2 wiring).

    Phase 2 of the regime router carries the detector's verdict through
    ``signal.meta`` so ``intent_from_signal`` can attach it to the typed
    ``StrategyIntent`` and ``aggregate_intents`` can evaluate the policy
    table in shadow mode (log-only, no enforcement). Mirrors the phase-1
    audit-row stamp (``_stamp_regime``) — same fields (``regime``,
    ``adx_14``, ``regime_source``), same setdefault semantics (a builder
    that pre-computed its own regime is not overwritten), same
    "never raise" contract (phase-2 is observability-only).

    Returns the meta dict for fluent chaining.
    """
    try:
        rg = detect_regime(candles_df)
        meta.setdefault("regime", rg["regime"])
        meta.setdefault("adx_14", rg["adx"])
        meta.setdefault("regime_source", rg["source"])
    except Exception:  # noqa: BLE001 — observability-only, never break a tick
        meta.setdefault("regime", "unknown")
        meta.setdefault("adx_14", None)
        meta.setdefault("regime_source", "adx-14")
    return meta


def _stamp_regime(payload: Dict[str, Any], candles_df: Any) -> Dict[str, Any]:
    """Stamp the ADX-14 regime detector's output onto an eval audit row.

    Phase 1 of the regime router (``PERF-20260601-002`` step 2): every
    per-strategy ``*_eval`` row in ``signal_audit.jsonl`` carries the
    regime + ADX value computed on the same candles the strategy was
    evaluated against (per-strategy timeframe — matches how the
    regime-roster matrix was measured). Pure observability — no
    enforcement, no decision change. Returns the payload (mutated in
    place) so call sites stay one-liners:
    ``log_signal(_stamp_regime({...eval row...}, candles_df))``.

    The detector NEVER raises; missing columns / empty frames / NaN
    bars produce ``regime="unknown"``, ``adx_14=None``. ``setdefault`` is
    used so a builder that has its own ADX (e.g. fade/fvg gate on it)
    can pre-set ``regime``/``adx_14`` and we won't clobber it.
    """
    try:
        rg = detect_regime(candles_df)
        payload.setdefault("regime", rg["regime"])
        payload.setdefault("adx_14", rg["adx"])
        payload.setdefault("regime_source", rg["source"])
    except Exception:  # noqa: BLE001 — observability-only, never break a tick
        payload.setdefault("regime", "unknown")
        payload.setdefault("adx_14", None)
        payload.setdefault("regime_source", "adx-14")
    return payload


def _build_killzone_exchange(settings: dict):
    """Shim — canonical home is ``src.runtime.market_data._build_exchange_client``."""
    from src.runtime.market_data import _build_exchange_client
    return _build_exchange_client(settings)


def _publish_liquidity_state(symbol: str, candles_df: Any) -> None:
    """Best-effort hook to persist per-symbol liquidity zones."""
    try:
        from src.runtime.liquidity_state import write_state
        write_state(symbol, candles_df)
    except Exception:
        logger.exception("liquidity state publish failed for symbol=%s", symbol)


def _with_signal_package(strategy_id: str, sig: dict) -> dict:
    """Attach a typed SignalPackage to a builder result dict (S3 wiring).

    Purely additive — all existing dict keys are preserved unchanged.
    Downstream pipeline consumers that expect a plain dict are unaffected.
    S4 (allocator wiring) will consume sig["signal_package"] directly.
    """
    from datetime import datetime, timezone
    from src.core.signal_contract import SignalPackage
    raw_side = sig.get("side", "none")
    sp_side: str = {"buy": "long", "sell": "short"}.get(raw_side, "none")
    sig["signal_package"] = SignalPackage(
        strategy_id=strategy_id,
        symbol=str(sig.get("symbol", "")),
        account_id="",  # bound by allocator in S4
        side=sp_side,  # type: ignore[arg-type]
        entry_price=sig.get("price") or sig.get("entry_price"),
        stop_loss=sig.get("stop_loss"),
        take_profit=sig.get("take_profit"),
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
        raw={k: v for k, v in sig.items() if k != "signal_package"},
        source_context=dict(sig.get("meta") or {}),
    )
    return sig


# Per-process shadow predictor cache for the signal-builder path.
# Keyed by (strategy_name, tuple(resolved_model_ids)) so a config reload
# or registry promotion that changes the resolved set gets a fresh
# resolution. The coordinator keeps its own _shadow_predictors_cache for
# the order_package() path — which is dead in the multiplexed live
# pipeline — so this cache covers the signal-builder path that bypasses
# the coordinator.
_SHADOW_PREDICTOR_CACHE: dict = {}

# Sentinel distinguishing "key absent / None" (auto-wire) from an
# explicit "shadow_model_ids: []" (opt-out), mirroring
# Coordinator._get_shadow_predictors.
_SHADOW_IDS_MISSING = object()


def _resolve_shadow_predictors(strategy_name: str, strat_cfg: dict) -> list:
    """Resolve the shadow predictor list for a strategy's signal-builder path.

    Mirrors ``Coordinator._get_shadow_predictors`` tri-state semantics
    (the 2026-05-19 auto-wire default), which the previous vwap-only
    emitter did not — it read ``shadow_model_ids`` directly and returned
    on the empty/omitted case, so the auto-wire default (every strategy
    omits the key) silenced shadow observation entirely in the live
    multiplexed pipeline:

      * ``shadow_model_ids`` missing / None — auto-wire every model at
        ``target_deployment_stage == "shadow"`` from the registry.
      * ``shadow_model_ids: []`` — explicit opt-out, no predictors.
      * ``shadow_model_ids: [...]`` — exactly those ids (the factory
        still applies its own per-id stage gate).

    Cached per (strategy, resolved ids). Never raises — any failure
    yields an empty list so the signal-builder hot path is unaffected.
    """
    try:
        raw_ids = strat_cfg.get("shadow_model_ids", _SHADOW_IDS_MISSING)
        auto_wire = raw_ids is _SHADOW_IDS_MISSING or raw_ids is None
        explicit_ids = [] if auto_wire else list(raw_ids)
        if not auto_wire and not explicit_ids:
            return []  # explicit opt-out
        from pathlib import Path
        from ml.registry.model_registry import ModelRegistry
        from ml.shadow.factory import (
            DEFAULT_REGISTRY_ROOT,
            discover_shadow_stage_model_ids,
            resolve_predictors,
        )
        from src.utils.paths import runtime_logs_dir
        registry_root = Path(
            strat_cfg.get("_shadow_registry_root") or DEFAULT_REGISTRY_ROOT
        )
        configured_log = strat_cfg.get("_shadow_log_path")
        log_path = (
            Path(configured_log) if configured_log
            else runtime_logs_dir() / "shadow_predictions.jsonl"
        )
        registry = ModelRegistry(registry_root)
        ids = (
            discover_shadow_stage_model_ids(registry) if auto_wire
            else explicit_ids
        )
        if not ids:
            return []
        cache_key = (strategy_name, tuple(ids))
        if cache_key not in _SHADOW_PREDICTOR_CACHE:
            _SHADOW_PREDICTOR_CACHE[cache_key] = resolve_predictors(
                list(ids), registry, log_path=log_path,
            )
        return _SHADOW_PREDICTOR_CACHE[cache_key]
    except Exception:  # noqa: BLE001
        logger.warning(
            "%s: shadow predictor resolve failed", strategy_name, exc_info=False
        )
        return []


def _emit_shadow_preds(
    strategy_name: str,
    sig: dict,
    strat_cfg: dict,
    symbol: str,
    *,
    timeframe: str = "",
    candles_df: Any = None,
) -> None:
    """Emit shadow predictions for an actionable signal (side=buy/sell).

    Called as a side-effect from each strategy's signal builder. Per WS7:
    zero effect on order placement or trading decisions — data gathering
    only. Swallows all exceptions so a factory failure never breaks the
    signal-builder hot path.

    Regime-model wiring (2026-05-22): regime classifiers key on
    ``vol_bucket``, which the trade-signal row lacks — so before this they
    fell to their training marginal and logged a constant score every
    tick. Each predictor is now scored on a row tailored to it: a regime
    model whose ``(symbol, timeframe)`` match this signal's gets the live
    ``vol_bucket`` computed from ``candles_df`` against the edges frozen
    in its model state; a mismatched regime model is skipped; everything
    else is scored on the base trade-signal row exactly as before. See
    ``src/runtime/regime_shadow.py``.
    """
    try:
        predictors = _resolve_shadow_predictors(strategy_name, strat_cfg)
        if not predictors:
            return
        from src.runtime.shadow_adapter import with_shadow_preds
        from src.runtime.regime_shadow import (
            closes_from_candles,
            feature_row_for_predictor,
        )
        meta = sig.get("meta") or {}
        sig_symbol = str(sig.get("symbol") or symbol)
        base_row = {
            "strategy_name": strategy_name,
            "symbol": sig_symbol,
            "direction": "long" if sig.get("side") == "buy" else "short",
            "confidence": float(
                sig.get("confidence") or meta.get("confidence") or 0.0
            ),
            "setup_type": str(meta.get("setup_type") or ""),
            "killzone": str(meta.get("killzone") or ""),
        }
        closes = closes_from_candles(candles_df)
        for predictor in predictors:
            row = feature_row_for_predictor(
                predictor,
                base_row,
                closes=closes,
                symbol=sig_symbol,
                timeframe=str(timeframe or ""),
            )
            if row is None:
                continue  # mismatched regime model — skip (don't log a constant)
            # One predictor per call preserves with_shadow_preds' per-model
            # try/except isolation + ShadowPredictor type-check.
            with_shadow_preds(sig, predictors=[predictor], feature_row=row)
    except Exception:  # noqa: BLE001
        logger.warning(
            "%s: shadow prediction emit failed", strategy_name, exc_info=False
        )


def turtle_soup_signal_builder(settings: dict) -> Dict[str, Any]:
    """Sweep + reversal at 15m. S-012 PR C3 wires it into the multiplexer.

    Calls the units-layer ``src.units.strategies.turtle_soup.order_package``
    so the same signal logic exercised by tests/test_s012_turtle_soup.py
    is what runs in production. Routes through the same pipeline-level
    signal shape used by VWAP / killzone / ict so downstream consumers
    (multiplexer, RiskManager, order layer) need no changes.

    Returns
    -------
    dict
        Pipeline signal: {symbol, side, price, stop_loss, take_profit,
        meta, signal_package} where side ∈ {"buy", "sell", "none"}.
        ``signal_package`` is a typed ``SignalPackage`` (S3 wiring).
        S-026 G1: no qty — sizing is the per-account RiskManager's job.
    """
    from src.units.strategies.turtle_soup import order_package
    from src.runtime.market_data import fetch_candles

    symbol = settings.get("SYMBOL", settings.get("symbol", "BTCUSDT"))
    timeframe = settings.get("TURTLE_SOUP_TIMEFRAME", settings.get("timeframe", "15m"))

    # Construct the connector through the local shim (patched by
    # existing tests) and hand it to fetch_candles.
    exchange = _build_killzone_exchange(settings)
    candles_df = fetch_candles(
        symbol, timeframe, exchange_client=exchange, limit=200,
    )
    if candles_df is None:
        raise RuntimeError(
            f"Turtle Soup: no candle data returned for symbol={symbol} "
            f"timeframe={timeframe}. "
            "Check that the exchange connection is configured and the symbol is valid."
        )

    _publish_liquidity_state(symbol, candles_df)

    cfg: Dict[str, Any] = {"symbol": symbol, "timeframe": timeframe}
    # Merge per-strategy params from config/strategies.yaml when available.
    try:
        from src.units.strategies import load_strategy_config
        params = load_strategy_config().get("turtle_soup", {})
        cfg.update(params)
    except Exception as exc:
        logger.warning("Turtle Soup: could not load strategies.yaml params (%s); using adapter defaults", exc)

    try:
        pkg = order_package(cfg, candles_df=candles_df)
    except ValueError as exc:
        # No setup in the lookback window — return a flat signal, not an error.
        # Surface per-stage rejection counts when the detector attached
        # them so the audit log can answer "which gate killed this
        # candidate". See src/units/strategies/turtle_soup.py for the
        # gate ordering (sweep depth → reversal close → body strength).
        stage_rejections = getattr(exc, "stage_rejections", None)
        logger.info(
            "Turtle Soup: no actionable signal (%s) stage_rejections=%s",
            exc, stage_rejections,
        )
        # The multiplexer absorbs per-strategy ``side=none`` signals
        # and emits one combined ``strategy=multiplexed`` row per tick,
        # so the per-strategy meta we attach below is invisible in the
        # audit JSONL on flat ticks. Land a dedicated turtle-only row
        # (``event=turtle_soup_eval``) directly so the stage_rejections
        # data the operator needs to tune cadence is queryable in
        # ``signal_audit.jsonl`` regardless of multiplexer routing.
        # Best-effort — never let an audit failure break the strategy.
        try:
            log_signal(_stamp_regime(
                {
                    "event": "turtle_soup_eval",
                    "strategy": "turtle_soup",
                    "symbol": symbol,
                    "side": "none",
                    "reason": str(exc),
                    "stage_rejections": stage_rejections,
                }
            , candles_df))
        except Exception:  # noqa: BLE001
            logger.exception("Turtle Soup: dedicated audit emit failed")
        meta: Dict[str, Any] = {
            "strategy_name": "turtle_soup",
            "reason": str(exc),
        }
        if stage_rejections is not None:
            meta["stage_rejections"] = stage_rejections
        return _with_signal_package("turtle_soup", {
            "symbol": symbol,
            "side": "none",
            "meta": meta,
        })

    side = "buy" if pkg["direction"] == "long" else "sell"
    logger.info(
        "Turtle Soup: %s signal at %s (entry=%s sl=%s tp=%s confidence=%.3f)",
        side, symbol, pkg["entry"], pkg["sl"], pkg["tp"], pkg["confidence"],
    )
    # Mirror of the no-signal path's dedicated audit row: every
    # turtle eval lands a row keyed off ``event=turtle_soup_eval`` so
    # the operator can reconstruct cadence + stage rejections per
    # tick even when the multiplexer routes a different strategy's
    # signal through the main pipeline_result row.
    pkg_meta = pkg.get("meta") or {}
    try:
        log_signal(_stamp_regime(
            {
                "event": "turtle_soup_eval",
                "strategy": "turtle_soup",
                "symbol": symbol,
                "side": side,
                "entry": pkg["entry"],
                "sl": pkg["sl"],
                "tp": pkg["tp"],
                "confidence": pkg["confidence"],
                "bars_back_of_setup": pkg_meta.get("bars_back_of_setup"),
                "stage_rejections": pkg_meta.get("stage_rejections"),
            }
        , candles_df))
    except Exception:  # noqa: BLE001
        logger.exception("Turtle Soup: dedicated audit emit failed")
    sig = {
        "symbol": symbol,
        "side": side,
        "price": pkg["entry"],
        "entry_price": pkg["entry"],
        "stop_loss": pkg["sl"],
        "take_profit": pkg["tp"],
        # Set ``pattern`` at the top level so the pipeline-result audit
        # row (`signal.get("signal_type") or signal.get("pattern")`)
        # has a non-null value the dashboard can filter on. Other
        # builders rely on the strategy-registry prefix lookup; turtle
        # soup pre-fix emitted with no pattern, which made
        # /api/bot/signals?pattern=... blind to its rows.
        "pattern": "turtle_soup",
        "meta": {
            **pkg_meta,
            "strategy_name": "turtle_soup",
            "confidence": pkg["confidence"],
            "direction": pkg["direction"],
        },
    }
    _emit_shadow_preds(
        "turtle_soup", sig, cfg, symbol,
        timeframe=timeframe, candles_df=candles_df,
    )
    _stamp_regime_on_meta(sig.setdefault("meta", {}), candles_df)
    return _with_signal_package("turtle_soup", sig)


def ict_scalp_signal_builder(settings: dict) -> Dict[str, Any]:
    """ICT scalp v1 — liquidity sweep + displacement + FVG mitigation.

    Default timeframe is 5m; ``config/strategies.yaml::ict_scalp_5m.timeframe``
    is the source of truth and can be flipped to "1m" (or any other TF
    the connector serves) without touching this builder.

    Honours the ``enabled`` flag in ``config/strategies.yaml``. When
    ``enabled: false`` (the default) the builder short-circuits to a
    ``side="none"`` no-op so live behaviour is unchanged — the
    multiplexer's existing skip path absorbs it. Flipping the flag to
    ``true`` is the deliberate operator action that promotes the
    strategy into the live loop.
    """
    from src.units.strategies import load_strategy_config
    from src.units.strategies.ict_scalp import order_package
    from src.runtime.market_data import fetch_candles

    try:
        strategies_cfg = load_strategy_config()
    except Exception:  # noqa: BLE001 — never fail-open on a config error
        strategies_cfg = {}
    ict_cfg = strategies_cfg.get("ict_scalp_5m", {}) or {}

    symbol = settings.get("SYMBOL", settings.get("symbol", "BTCUSDT"))

    if not bool(ict_cfg.get("enabled", False)):
        logger.info(
            "ict_scalp_5m: strategy disabled in config/strategies.yaml — "
            "returning side=none"
        )
        return _with_signal_package("ict_scalp_5m", {
            "symbol": symbol,
            "side": "none",
            "meta": {
                "strategy_name": "ict_scalp_5m",
                "reason": "disabled_in_yaml",
            },
        })

    timeframe = str(
        ict_cfg.get("timeframe")
        or settings.get("ICT_SCALP_TIMEFRAME")
        or settings.get("TIMEFRAME")
        or "5m"
    )

    exchange = _build_killzone_exchange(settings)
    # Same lookback as turtle_soup so the rolling windows have headroom.
    candles_df = fetch_candles(
        symbol, timeframe, exchange_client=exchange, limit=200,
    )
    if candles_df is None:
        raise RuntimeError(
            f"ict_scalp_5m: no candle data returned for symbol={symbol} "
            f"timeframe={timeframe}. Check that the exchange connection "
            "is configured and the symbol is valid."
        )

    _publish_liquidity_state(symbol, candles_df)

    # HTF bias fetch (v2): when ``htf_trend_filter_enabled`` is on in
    # YAML, fetch the HTF candles + compute the EMA and inject the
    # values into cfg so the unit's filter can run. Failure degrades
    # gracefully — the unit treats missing values as filter-off, which
    # is the v2-no-HTF variant from the backtest (still positive but
    # weaker). Same fetch pattern as vwap's htf_trend_filter at L378-397.
    htf_close: Optional[float] = None
    htf_ema: Optional[float] = None
    if bool(ict_cfg.get("htf_trend_filter_enabled", True)):
        htf_tf = str(ict_cfg.get("htf_filter_timeframe") or "1h")
        ema_period = int(ict_cfg.get("htf_filter_ema_period") or 20)
        try:
            htf_df = fetch_candles(
                symbol, htf_tf, exchange_client=exchange,
                limit=max(ema_period * 3, 60),
            )
            if htf_df is not None and not htf_df.empty and "close" in htf_df.columns:
                ema_series = htf_df["close"].ewm(span=ema_period, adjust=False).mean()
                if pd.notna(ema_series.iloc[-1]):
                    htf_close = float(htf_df["close"].iloc[-1])
                    htf_ema = float(ema_series.iloc[-1])
                    logger.info(
                        "ict_scalp_5m: HTF bias %s (close=%.2f ema=%.2f tf=%s)",
                        "bullish" if htf_close > htf_ema else "bearish",
                        htf_close, htf_ema, htf_tf,
                    )
        except Exception as exc:  # noqa: BLE001 — degrade to no-gate
            logger.warning(
                "ict_scalp_5m: HTF fetch failed for symbol=%s tf=%s: %s — "
                "filter degrades to no-gate this tick",
                symbol, htf_tf, exc,
            )

    cfg: Dict[str, Any] = {"symbol": symbol, "timeframe": timeframe, **ict_cfg}
    if htf_close is not None and htf_ema is not None:
        cfg["htf_close"] = htf_close
        cfg["htf_ema"] = htf_ema

    try:
        pkg = order_package(cfg, candles_df=candles_df)
    except ValueError as exc:
        logger.info("ict_scalp_5m: no actionable signal (%s)", exc)
        try:
            log_signal(_stamp_regime({
                "event": "ict_scalp_eval",
                "strategy": "ict_scalp_5m",
                "symbol": symbol,
                "timeframe": timeframe,
                "side": "none",
                "reason": str(exc),
            }, candles_df))
        except Exception:  # noqa: BLE001
            logger.exception("ict_scalp_5m: dedicated audit emit failed")
        return _with_signal_package("ict_scalp_5m", {
            "symbol": symbol,
            "side": "none",
            "meta": {
                "strategy_name": "ict_scalp_5m",
                "reason": str(exc),
            },
        })

    side = "buy" if pkg["direction"] == "long" else "sell"
    logger.info(
        "ict_scalp_5m: %s signal at %s (entry=%s sl=%s tp=%s confidence=%.3f)",
        side, symbol, pkg["entry"], pkg["sl"], pkg["tp"], pkg["confidence"],
    )
    pkg_meta = pkg.get("meta") or {}
    try:
        log_signal(_stamp_regime({
            "event": "ict_scalp_eval",
            "strategy": "ict_scalp_5m",
            "symbol": symbol,
            "timeframe": timeframe,
            "side": side,
            "entry": pkg["entry"],
            "stop_loss": pkg["sl"],
            "take_profit": pkg["tp"],
            "confidence": pkg["confidence"],
            # Decision geometry the strategy already computed (order_package
            # meta) — surfaced so the dashboard can DRAW the FVG zone +
            # liquidity-sweep level it actually traded on. Not new tracking;
            # purely the values this evaluation already produced.
            "fvg_low": pkg_meta.get("fvg_low"),
            "fvg_high": pkg_meta.get("fvg_high"),
            "sweep_level": pkg_meta.get("sweep_level"),
            "sweep_extreme": pkg_meta.get("sweep_extreme"),
        }, candles_df))
    except Exception:  # noqa: BLE001
        logger.exception("ict_scalp_5m: dedicated audit emit failed")

    sig = {
        "symbol": symbol,
        "side": side,
        "price": pkg["entry"],
        "entry_price": pkg["entry"],
        "stop_loss": pkg["sl"],
        "take_profit": pkg["tp"],
        "pattern": "ict_scalp",
        "meta": {
            **pkg_meta,
            "strategy_name": "ict_scalp_5m",
            "confidence": pkg["confidence"],
            "direction": pkg["direction"],
        },
    }
    _emit_shadow_preds(
        "ict_scalp_5m", sig, ict_cfg, symbol,
        timeframe=timeframe, candles_df=candles_df,
    )
    _stamp_regime_on_meta(sig.setdefault("meta", {}), candles_df)
    return _with_signal_package("ict_scalp_5m", sig)


def vwap_signal_builder(settings: dict) -> Dict[str, Any]:
    """
    Fetch OHLCV candles from the configured exchange and return a VWAP
    mean-reversion signal.

    Safe in dry-run mode: fetches market data for signal computation but
    relies on safe_place_order to prevent any actual order submission.

    If candle data is unavailable or insufficient, raises a clear,
    non-secret error rather than silently doing nothing.

    Timeframe resolution (S-015 mid-sprint fix):

      1. Per-strategy ``timeframe`` from ``config/strategies.yaml`` —
         the operator-controlled source of truth. VWAP runs at 5m as
         of S-015; the legacy 15m setting is no longer compatible.
      2. ``settings["TIMEFRAME"]`` env var, then ``settings["timeframe"]``
         — only consulted if the strategies.yaml entry is missing.
      3. Hard default ``"5m"``.

    This ordering ensures the YAML change wins even on accounts whose
    .env file still has the legacy ``TIMEFRAME=15m`` line.
    """
    from src.units.strategies import load_strategy_config
    from src.units.strategies.vwap import build_vwap_signal

    try:
        strategies_cfg = load_strategy_config()
    except Exception:  # noqa: BLE001 — never fail-open on a config error
        strategies_cfg = {}
    vwap_cfg = strategies_cfg.get("vwap", {}) or {}

    symbol = settings.get("SYMBOL", settings.get("symbol", "BTCUSDT"))
    timeframe = (
        vwap_cfg.get("timeframe")
        or settings.get("TIMEFRAME")
        or settings.get("timeframe")
        or "5m"
    )

    from src.runtime.market_data import fetch_candles

    exchange = _build_killzone_exchange(settings)
    candles_df = fetch_candles(
        symbol, timeframe, exchange_client=exchange, limit=100,
    )
    if candles_df is None:
        raise RuntimeError(
            f"VWAP strategy: no candle data returned for symbol={symbol} "
            f"timeframe={timeframe}. "
            "Check that the exchange connection is configured and the symbol is valid."
        )

    if candles_df[["high", "low", "close", "volume"]].isnull().all().any():
        raise RuntimeError(
            f"VWAP strategy: candle data for symbol={symbol} timeframe={timeframe} "
            "contains all-NaN columns after parsing. Data may be malformed."
        )

    _publish_liquidity_state(symbol, candles_df)

    logger.info(
        "VWAP signal builder: symbol=%s timeframe=%s candles=%d",
        symbol, timeframe, len(candles_df),
    )

    # Legacy HTF trend gate (4h EMA-200) — kept in code but only runs when
    # explicitly enabled. DISABLED 2026-05-13: gate was biased long by the
    # 38-month bull-market training dataset (see strategies.yaml comment).
    htf_close: Optional[float] = None
    htf_ema: Optional[float] = None
    htf_band_pct: Optional[float] = None
    htf_filter_cfg = vwap_cfg.get("htf_trend_filter") or {}
    if htf_filter_cfg.get("enabled"):
        htf_tf = str(htf_filter_cfg.get("htf_timeframe") or "4h")
        ema_period = int(htf_filter_cfg.get("ema_period") or 200)
        htf_band_pct = float(htf_filter_cfg.get("band_pct") or 0.02)
        try:
            htf_df = fetch_candles(
                symbol, htf_tf, exchange_client=exchange,
                limit=max(ema_period * 2, 250),
            )
            if htf_df is not None and not htf_df.empty and "close" in htf_df.columns:
                ema_series = htf_df["close"].ewm(span=ema_period, adjust=False).mean()
                if pd.notna(ema_series.iloc[-1]):
                    htf_close = float(htf_df["close"].iloc[-1])
                    htf_ema = float(ema_series.iloc[-1])
        except Exception as exc:  # noqa: BLE001 — degrade to no-gate
            logger.warning(
                "VWAP HTF fetch failed for symbol=%s tf=%s: %s — degrading to no-gate",
                symbol, htf_tf, exc,
            )

    # Daily bias filter (operator directive 2026-05-13): fetch ≤24h of 1h
    # candles to compute the intra-day directional lean. Informational only
    # — neither side is blocked. Failure degrades gracefully to no bias data.
    # Recent context filter (operator directive 2026-05-13): fetch ≤24h of
    # 1h candles for a recency-weighted short-term trend measure. Informational
    # only — neither side is blocked. Failure degrades gracefully to no context.
    recent_context_df: Optional[pd.DataFrame] = None
    recent_context_filter_cfg = vwap_cfg.get("recent_context_filter") or {}
    if recent_context_filter_cfg.get("enabled"):
        ctx_tf = str(recent_context_filter_cfg.get("timeframe") or "1h")
        lookback_bars = int(recent_context_filter_cfg.get("lookback_bars") or 24)
        try:
            recent_context_df = fetch_candles(
                symbol, ctx_tf, exchange_client=exchange, limit=lookback_bars,
            )
            if recent_context_df is not None and recent_context_df.empty:
                recent_context_df = None
        except Exception as exc:  # noqa: BLE001 — degrade gracefully
            logger.warning(
                "VWAP recent-context fetch failed for symbol=%s tf=%s: %s — skipping",
                symbol, ctx_tf, exc,
            )

    kwargs: Dict[str, Any] = {"symbol": symbol, "timeframe": timeframe}
    if htf_close is not None and htf_ema is not None:
        kwargs["htf_close"] = htf_close
        kwargs["htf_ema"] = htf_ema
        if htf_band_pct is not None:
            kwargs["htf_band_pct"] = htf_band_pct
    if recent_context_df is not None:
        kwargs["recent_context_candles_df"] = recent_context_df
        neutral_band = recent_context_filter_cfg.get("neutral_band_pct")
        if neutral_band is not None:
            kwargs["recent_context_neutral_band_pct"] = float(neutral_band)

    sig = build_vwap_signal(candles_df, **kwargs)

    # Shadow predictions: emit on every actionable vwap signal regardless
    # of the pipeline-level open-package gate (which fires after signal
    # generation and would otherwise suppress all shadow data while a
    # trade is open). Per WS7: zero effect on order placement.
    if sig.get("side") in ("buy", "sell"):
        _emit_shadow_preds(
            "vwap", sig, vwap_cfg, symbol,
            timeframe=timeframe, candles_df=candles_df,
        )

    # Mirror turtle_soup's per-tick audit row (event=turtle_soup_eval at
    # L482-491 / L518-531 of the original pipeline.py). VWAP previously
    # emitted nothing on flat ticks, leaving operators with no way to
    # distinguish "evaluating but no signal" from "strategy not running" —
    # the very gap that turned an 8h silence on 2026-05-10 into a
    # multi-hour debug session. Best-effort; never let an audit failure
    # break the strategy.
    try:
        _sig = sig or {}
        _meta = _sig.get("meta") or {}
        log_signal(_stamp_regime({
            "event": "vwap_eval",
            "strategy": "vwap",
            "symbol": symbol,
            "timeframe": timeframe,
            "side": _sig.get("side", "none"),
            "entry": _sig.get("entry_price") or _sig.get("price"),
            "stop_loss": _sig.get("stop_loss"),
            "take_profit": _sig.get("take_profit"),
            "confidence": _sig.get("confidence") or _meta.get("confidence"),
            "vwap": _meta.get("vwap"),
            "deviation_std": _meta.get("deviation_std"),
            "htf_blocked": _meta.get("htf_blocked"),
            "recent_context": _meta.get("recent_context"),
            "recent_context_pct": _meta.get("recent_context_pct"),
            "reason": _meta.get("reason") or _sig.get("reason"),
        }, candles_df))
    except Exception:  # noqa: BLE001
        logger.exception("VWAP: dedicated audit emit failed")
    _stamp_regime_on_meta(sig.setdefault("meta", {}), candles_df)
    return _with_signal_package("vwap", sig)


def trend_donchian_signal_builder(settings: dict) -> Dict[str, Any]:
    """Donchian-breakout trend-follower (S-STRAT-IMPROVE-S8).

    Fetches 1h candles, calls
    ``src.units.strategies.trend_donchian.order_package``, and maps the
    result into the pipeline-shape signal dict. The first net-positive
    strategy in the strategy-improvement program (net +22.5R/3yr; see
    docs/audits/complementary-trend-strategy-2026-05-23.md), going live
    on bybit_2 per docs/sprint-plans/TREND-GOLIVE-PLAN-2026-05-23.md.

    Honours the ``enabled`` flag in ``config/strategies.yaml`` as the
    single source of truth: ``enabled: false`` short-circuits to
    ``side="none"`` without code changes.
    """
    from src.units.strategies import load_strategy_config
    from src.units.strategies.trend_donchian import order_package
    from src.runtime.market_data import fetch_candles

    try:
        strategies_cfg = load_strategy_config()
    except Exception:  # noqa: BLE001 — never fail-open on a config error
        strategies_cfg = {}
    trend_cfg = strategies_cfg.get("trend_donchian", {}) or {}

    symbol = settings.get("SYMBOL", settings.get("symbol", "BTCUSDT"))

    if not bool(trend_cfg.get("enabled", False)):
        logger.info(
            "trend_donchian: strategy disabled in config/strategies.yaml — "
            "returning side=none"
        )
        return _with_signal_package("trend_donchian", {
            "symbol": symbol,
            "side": "none",
            "meta": {
                "strategy_name": "trend_donchian",
                "reason": "disabled_in_yaml",
            },
        })

    timeframe = str(
        trend_cfg.get("timeframe")
        or settings.get("TREND_DONCHIAN_TIMEFRAME")
        or settings.get("TIMEFRAME")
        or "1h"
    )

    exchange = _build_killzone_exchange(settings)
    candles_df = fetch_candles(
        symbol, timeframe, exchange_client=exchange, limit=200,
    )
    if candles_df is None:
        raise RuntimeError(
            f"trend_donchian: no candle data returned for symbol={symbol} "
            f"timeframe={timeframe}. Check that the exchange connection "
            "is configured and the symbol is valid."
        )

    _publish_liquidity_state(symbol, candles_df)

    cfg: Dict[str, Any] = {"symbol": symbol, "timeframe": timeframe, **trend_cfg}

    try:
        pkg = order_package(cfg, candles_df=candles_df)
    except ValueError as exc:
        logger.info("trend_donchian: no actionable signal (%s)", exc)
        try:
            log_signal(_stamp_regime({
                "event": "trend_donchian_eval",
                "strategy": "trend_donchian",
                "symbol": symbol,
                "timeframe": timeframe,
                "side": "none",
                "reason": str(exc),
            }, candles_df))
        except Exception:  # noqa: BLE001
            logger.exception("trend_donchian: dedicated audit emit failed")
        return _with_signal_package("trend_donchian", {
            "symbol": symbol,
            "side": "none",
            "meta": {
                "strategy_name": "trend_donchian",
                "reason": str(exc),
            },
        })

    # LONG-ONLY gate (operator-approved 2026-06-01, Tier-3). The
    # regime×direction matrix (docs/research/regime-roster-matrix-2026-06-01.md)
    # showed trend_donchian's SHORT side is a net −37 R drag that earns only in
    # chop, while the LONG side is the +47 R trend edge. Honour an opt-in
    # ``long_only`` flag from strategies.yaml — suppress shorts, keep longs.
    # Default off, so omitting it preserves the two-sided behaviour.
    if bool(trend_cfg.get("long_only", False)) and pkg["direction"] != "long":
        logger.info("trend_donchian: short signal suppressed (long_only)")
        try:
            log_signal(_stamp_regime({
                "event": "trend_donchian_eval",
                "strategy": "trend_donchian",
                "symbol": symbol,
                "timeframe": timeframe,
                "side": "none",
                "reason": "short_suppressed_long_only",
            }, candles_df))
        except Exception:  # noqa: BLE001
            logger.exception("trend_donchian: dedicated audit emit failed")
        return _with_signal_package("trend_donchian", {
            "symbol": symbol,
            "side": "none",
            "meta": {
                "strategy_name": "trend_donchian",
                "reason": "short_suppressed_long_only",
            },
        })

    side = "buy" if pkg["direction"] == "long" else "sell"
    logger.info(
        "trend_donchian: %s signal at %s (entry=%s sl=%s tp=%s confidence=%.3f)",
        side, symbol, pkg["entry"], pkg["sl"], pkg["tp"], pkg["confidence"],
    )
    pkg_meta = pkg.get("meta") or {}
    try:
        log_signal(_stamp_regime({
            "event": "trend_donchian_eval",
            "strategy": "trend_donchian",
            "symbol": symbol,
            "timeframe": timeframe,
            "side": side,
            "entry": pkg["entry"],
            "stop_loss": pkg["sl"],
            "take_profit": pkg["tp"],
            "confidence": pkg["confidence"],
        }, candles_df))
    except Exception:  # noqa: BLE001
        logger.exception("trend_donchian: dedicated audit emit failed")

    sig = {
        "symbol": symbol,
        "side": side,
        "price": pkg["entry"],
        "entry_price": pkg["entry"],
        "stop_loss": pkg["sl"],
        "take_profit": pkg["tp"],
        "pattern": "trend_donchian",
        "meta": {
            **pkg_meta,
            "strategy_name": "trend_donchian",
            "confidence": pkg["confidence"],
            "direction": pkg["direction"],
            # Carry the conservative per-strategy risk multiplier from
            # this strategy's YAML directly on the signal meta. The
            # registry-driven STRATEGY_RISK_PCT does NOT surface the
            # strategies.yaml `risk_pct` field (load_strategies() omits
            # it), so without this the downstream sizer would default
            # the multiplier to 1.0 and trade trend at the FULL account
            # risk_pct instead of the operator-mandated 0.3 for the
            # initial live period. Both multiplexers preserve a
            # builder-provided strategy_risk_pct.
            "strategy_risk_pct": float(trend_cfg.get("risk_pct", 0.3) or 0.3),
        },
    }
    _emit_shadow_preds(
        "trend_donchian", sig, trend_cfg, symbol,
        timeframe=timeframe, candles_df=candles_df,
    )
    _stamp_regime_on_meta(sig.setdefault("meta", {}), candles_df)
    return _with_signal_package("trend_donchian", sig)


def fade_breakout_4h_signal_builder(settings: dict) -> Dict[str, Any]:
    """Failed-breakout fade (S-STRAT-IMPROVE-S9), the mirror of the trend
    follower: fades failed Donchian breakouts (pierce-and-reject) in chop
    (ADX<20) with a Chandelier trail.

    Fetches 4h candles, calls
    ``src.units.strategies.fade_breakout_4h.order_package``, and maps the
    result into the pipeline-shape signal dict. Validated as an
    uncorrelated complement to trend_donchian (monthly_corr 0.035; blend
    ret/DD 1.97->3.80) but more fragile OOS, so it is run
    ``execution: shadow`` — logs order packages on real ticks, never
    sends a live order. Full evidence:
    docs/audits/fade-breakout-complement-2026-05-24.md.

    Honours the ``enabled`` flag in ``config/strategies.yaml`` as the
    single source of truth: ``enabled: false`` short-circuits to
    ``side="none"`` without code changes. (The ``execution: shadow``
    gate is enforced downstream in the Accounts layer, not here — this
    builder always produces the real signal so the shadow log captures
    exactly what would have traded.)
    """
    from src.units.strategies import load_strategy_config
    from src.units.strategies.fade_breakout_4h import order_package
    from src.runtime.market_data import fetch_candles

    try:
        strategies_cfg = load_strategy_config()
    except Exception:  # noqa: BLE001 — never fail-open on a config error
        strategies_cfg = {}
    fade_cfg = strategies_cfg.get("fade_breakout_4h", {}) or {}

    symbol = settings.get("SYMBOL", settings.get("symbol", "BTCUSDT"))

    if not bool(fade_cfg.get("enabled", False)):
        logger.info(
            "fade_breakout_4h: strategy disabled in config/strategies.yaml — "
            "returning side=none"
        )
        return _with_signal_package("fade_breakout_4h", {
            "symbol": symbol,
            "side": "none",
            "meta": {
                "strategy_name": "fade_breakout_4h",
                "reason": "disabled_in_yaml",
            },
        })

    timeframe = str(
        fade_cfg.get("timeframe")
        or settings.get("FADE_BREAKOUT_4H_TIMEFRAME")
        or settings.get("TIMEFRAME")
        or "4h"
    )

    exchange = _build_killzone_exchange(settings)
    candles_df = fetch_candles(
        symbol, timeframe, exchange_client=exchange, limit=200,
    )
    if candles_df is None:
        raise RuntimeError(
            f"fade_breakout_4h: no candle data returned for symbol={symbol} "
            f"timeframe={timeframe}. Check that the exchange connection "
            "is configured and the symbol is valid."
        )

    _publish_liquidity_state(symbol, candles_df)

    cfg: Dict[str, Any] = {"symbol": symbol, "timeframe": timeframe, **fade_cfg}

    try:
        pkg = order_package(cfg, candles_df=candles_df)
    except ValueError as exc:
        logger.info("fade_breakout_4h: no actionable signal (%s)", exc)
        try:
            log_signal(_stamp_regime({
                "event": "fade_breakout_4h_eval",
                "strategy": "fade_breakout_4h",
                "symbol": symbol,
                "timeframe": timeframe,
                "side": "none",
                "reason": str(exc),
            }, candles_df))
        except Exception:  # noqa: BLE001
            logger.exception("fade_breakout_4h: dedicated audit emit failed")
        return _with_signal_package("fade_breakout_4h", {
            "symbol": symbol,
            "side": "none",
            "meta": {
                "strategy_name": "fade_breakout_4h",
                "reason": str(exc),
            },
        })

    side = "buy" if pkg["direction"] == "long" else "sell"
    logger.info(
        "fade_breakout_4h: %s signal at %s (entry=%s sl=%s tp=%s confidence=%.3f)",
        side, symbol, pkg["entry"], pkg["sl"], pkg["tp"], pkg["confidence"],
    )
    pkg_meta = pkg.get("meta") or {}
    try:
        log_signal(_stamp_regime({
            "event": "fade_breakout_4h_eval",
            "strategy": "fade_breakout_4h",
            "symbol": symbol,
            "timeframe": timeframe,
            "side": side,
            "entry": pkg["entry"],
            "stop_loss": pkg["sl"],
            "take_profit": pkg["tp"],
            "confidence": pkg["confidence"],
        }, candles_df))
    except Exception:  # noqa: BLE001
        logger.exception("fade_breakout_4h: dedicated audit emit failed")

    sig = {
        "symbol": symbol,
        "side": side,
        "price": pkg["entry"],
        "entry_price": pkg["entry"],
        "stop_loss": pkg["sl"],
        "take_profit": pkg["tp"],
        "pattern": "fade_breakout_4h",
        "meta": {
            **pkg_meta,
            "strategy_name": "fade_breakout_4h",
            "confidence": pkg["confidence"],
            "direction": pkg["direction"],
            # Same rationale as trend_donchian: carry the per-strategy risk
            # multiplier from YAML on the signal meta because the
            # registry-driven STRATEGY_RISK_PCT does not surface the
            # strategies.yaml `risk_pct` field. Moot while execution:shadow
            # (never sends a live order) but correct for any future flip.
            "strategy_risk_pct": float(fade_cfg.get("risk_pct", 0.3) or 0.3),
        },
    }
    _emit_shadow_preds(
        "fade_breakout_4h", sig, fade_cfg, symbol,
        timeframe=timeframe, candles_df=candles_df,
    )
    _stamp_regime_on_meta(sig.setdefault("meta", {}), candles_df)
    return _with_signal_package("fade_breakout_4h", sig)


def htf_pullback_trend_2h_signal_builder(settings: dict) -> Dict[str, Any]:
    """HTF-pullback trend-follower (overnight research 2026-06-01).

    Requires an HTF Donchian-midline uptrend, then buys a pullback into the
    lower ``pullback_frac`` of the recent range, with the shared Chandelier
    ATR trail (let-winners-run). Fetches 2h candles, calls
    ``src.units.strategies.htf_pullback_trend_2h.order_package``, maps to the
    pipeline-shape signal dict.

    Cleared for ``execution: shadow`` after net-of-fee + walk-forward
    (IS +32.7R / OOS +22.4R), 3-fold robustness (+30/+42/+8 across 2y folds),
    fee-robustness (+67R even at 15 bps), and additive correlation to the live
    roster (0.20-0.54). Validated config: trend_lookback 40 / pullback_frac
    0.5 / trail_mult 5.0. Evidence:
    docs/research/overnight-strategy-research-2026-06-01.md; harness:
    scripts/backtest_pullback.py.

    Honours the YAML ``enabled`` flag as the single source of truth
    (``enabled: false`` short-circuits to ``side="none"``). The
    ``execution: shadow`` gate is enforced downstream in the Accounts layer,
    not here - this builder always produces the real signal so the shadow log
    captures exactly what would have traded.
    """
    from src.units.strategies import load_strategy_config
    from src.units.strategies.htf_pullback_trend_2h import order_package
    from src.runtime.market_data import fetch_candles

    try:
        strategies_cfg = load_strategy_config()
    except Exception:  # noqa: BLE001 - never fail-open on a config error
        strategies_cfg = {}
    hp_cfg = strategies_cfg.get("htf_pullback_trend_2h", {}) or {}

    symbol = settings.get("SYMBOL", settings.get("symbol", "BTCUSDT"))

    if not bool(hp_cfg.get("enabled", False)):
        logger.info(
            "htf_pullback_trend_2h: strategy disabled in config/strategies.yaml - "
            "returning side=none"
        )
        return _with_signal_package("htf_pullback_trend_2h", {
            "symbol": symbol,
            "side": "none",
            "meta": {
                "strategy_name": "htf_pullback_trend_2h",
                "reason": "disabled_in_yaml",
            },
        })

    timeframe = str(
        hp_cfg.get("timeframe")
        or settings.get("HTF_PULLBACK_TREND_2H_TIMEFRAME")
        or settings.get("TIMEFRAME")
        or "2h"
    )

    exchange = _build_killzone_exchange(settings)
    candles_df = fetch_candles(
        symbol, timeframe, exchange_client=exchange, limit=200,
    )
    if candles_df is None:
        raise RuntimeError(
            f"htf_pullback_trend_2h: no candle data returned for symbol={symbol} "
            f"timeframe={timeframe}. Check that the exchange connection "
            "is configured and the symbol is valid."
        )

    _publish_liquidity_state(symbol, candles_df)

    cfg: Dict[str, Any] = {"symbol": symbol, "timeframe": timeframe, **hp_cfg}

    try:
        pkg = order_package(cfg, candles_df=candles_df)
    except ValueError as exc:
        logger.info("htf_pullback_trend_2h: no actionable signal (%s)", exc)
        try:
            log_signal(_stamp_regime({
                "event": "htf_pullback_trend_2h_eval",
                "strategy": "htf_pullback_trend_2h",
                "symbol": symbol,
                "timeframe": timeframe,
                "side": "none",
                "reason": str(exc),
            }, candles_df))
        except Exception:  # noqa: BLE001
            logger.exception("htf_pullback_trend_2h: dedicated audit emit failed")
        return _with_signal_package("htf_pullback_trend_2h", {
            "symbol": symbol,
            "side": "none",
            "meta": {
                "strategy_name": "htf_pullback_trend_2h",
                "reason": str(exc),
            },
        })

    side = "buy" if pkg["direction"] == "long" else "sell"
    logger.info(
        "htf_pullback_trend_2h: %s signal at %s (entry=%s sl=%s tp=%s confidence=%.3f)",
        side, symbol, pkg["entry"], pkg["sl"], pkg["tp"], pkg["confidence"],
    )
    pkg_meta = pkg.get("meta") or {}
    try:
        log_signal(_stamp_regime({
            "event": "htf_pullback_trend_2h_eval",
            "strategy": "htf_pullback_trend_2h",
            "symbol": symbol,
            "timeframe": timeframe,
            "side": side,
            "entry": pkg["entry"],
            "stop_loss": pkg["sl"],
            "take_profit": pkg["tp"],
            "confidence": pkg["confidence"],
        }, candles_df))
    except Exception:  # noqa: BLE001
        logger.exception("htf_pullback_trend_2h: dedicated audit emit failed")

    sig = {
        "symbol": symbol,
        "side": side,
        "price": pkg["entry"],
        "entry_price": pkg["entry"],
        "stop_loss": pkg["sl"],
        "take_profit": pkg["tp"],
        "pattern": "htf_pullback_trend_2h",
        "meta": {
            **pkg_meta,
            "strategy_name": "htf_pullback_trend_2h",
            "confidence": pkg["confidence"],
            "direction": pkg["direction"],
            "strategy_risk_pct": float(hp_cfg.get("risk_pct", 0.3) or 0.3),
        },
    }
    _emit_shadow_preds(
        "htf_pullback_trend_2h", sig, hp_cfg, symbol,
        timeframe=timeframe, candles_df=candles_df,
    )
    _stamp_regime_on_meta(sig.setdefault("meta", {}), candles_df)
    return _with_signal_package("htf_pullback_trend_2h", sig)


def trend_donchian_1h_signal_builder(settings: dict) -> Dict[str, Any]:
    """trend_donchian on the 1h timeframe with a wide trail — shadow A/B
    (overnight research 2026-06-01).

    A faster-timeframe / wider-trail variant of the LIVE trend_donchian
    flagship: same Donchian-breakout entry + Chandelier ATR trail, but on 1h
    candles with donchian 20 / trail_mult 5.0. Reuses the SAME unit
    (``src.units.strategies.trend_donchian.order_package``) parametrised by its
    own ``trend_donchian_1h`` config block — it is a distinct strategy instance,
    NOT a change to the live 2h strategy.

    Cleared for ``execution: shadow`` after the overnight sweep + pre-shadow
    validation: net-of-fee walk-forward IS +42.4R / OOS +43.8R (near-symmetric),
    net-positive in all three 2y folds (+14/+54/+24), fee-robust (+55R at 15bps),
    and only 0.46 monthly-return correlation to the live 2h trend_donchian
    (additive A/B, not a re-skin). Evidence:
    docs/research/overnight-strategy-research-2026-06-01.md.

    Honours the YAML ``enabled`` flag as the single source of truth. The
    ``execution: shadow`` gate is enforced downstream in the Accounts layer —
    this builder always produces the real signal so the shadow log captures
    exactly what would have traded.
    """
    from src.units.strategies import load_strategy_config
    from src.units.strategies.trend_donchian import order_package
    from src.runtime.market_data import fetch_candles

    try:
        strategies_cfg = load_strategy_config()
    except Exception:  # noqa: BLE001 - never fail-open on a config error
        strategies_cfg = {}
    td1h_cfg = strategies_cfg.get("trend_donchian_1h", {}) or {}

    symbol = settings.get("SYMBOL", settings.get("symbol", "BTCUSDT"))

    if not bool(td1h_cfg.get("enabled", False)):
        logger.info(
            "trend_donchian_1h: strategy disabled in config/strategies.yaml - "
            "returning side=none"
        )
        return _with_signal_package("trend_donchian_1h", {
            "symbol": symbol,
            "side": "none",
            "meta": {
                "strategy_name": "trend_donchian_1h",
                "reason": "disabled_in_yaml",
            },
        })

    timeframe = str(
        td1h_cfg.get("timeframe")
        or settings.get("TREND_DONCHIAN_1H_TIMEFRAME")
        or "1h"
    )

    exchange = _build_killzone_exchange(settings)
    candles_df = fetch_candles(
        symbol, timeframe, exchange_client=exchange, limit=200,
    )
    if candles_df is None:
        raise RuntimeError(
            f"trend_donchian_1h: no candle data returned for symbol={symbol} "
            f"timeframe={timeframe}. Check that the exchange connection "
            "is configured and the symbol is valid."
        )

    _publish_liquidity_state(symbol, candles_df)

    cfg: Dict[str, Any] = {"symbol": symbol, "timeframe": timeframe, **td1h_cfg}

    try:
        pkg = order_package(cfg, candles_df=candles_df)
    except ValueError as exc:
        logger.info("trend_donchian_1h: no actionable signal (%s)", exc)
        try:
            log_signal(_stamp_regime({
                "event": "trend_donchian_1h_eval",
                "strategy": "trend_donchian_1h",
                "symbol": symbol,
                "timeframe": timeframe,
                "side": "none",
                "reason": str(exc),
            }, candles_df))
        except Exception:  # noqa: BLE001
            logger.exception("trend_donchian_1h: dedicated audit emit failed")
        return _with_signal_package("trend_donchian_1h", {
            "symbol": symbol,
            "side": "none",
            "meta": {
                "strategy_name": "trend_donchian_1h",
                "reason": str(exc),
            },
        })

    side = "buy" if pkg["direction"] == "long" else "sell"
    logger.info(
        "trend_donchian_1h: %s signal at %s (entry=%s sl=%s tp=%s confidence=%.3f)",
        side, symbol, pkg["entry"], pkg["sl"], pkg["tp"], pkg["confidence"],
    )
    pkg_meta = pkg.get("meta") or {}
    try:
        log_signal(_stamp_regime({
            "event": "trend_donchian_1h_eval",
            "strategy": "trend_donchian_1h",
            "symbol": symbol,
            "timeframe": timeframe,
            "side": side,
            "entry": pkg["entry"],
            "stop_loss": pkg["sl"],
            "take_profit": pkg["tp"],
            "confidence": pkg["confidence"],
        }, candles_df))
    except Exception:  # noqa: BLE001
        logger.exception("trend_donchian_1h: dedicated audit emit failed")

    sig = {
        "symbol": symbol,
        "side": side,
        "price": pkg["entry"],
        "entry_price": pkg["entry"],
        "stop_loss": pkg["sl"],
        "take_profit": pkg["tp"],
        "pattern": "trend_donchian_1h",
        "meta": {
            **pkg_meta,
            "strategy_name": "trend_donchian_1h",
            "confidence": pkg["confidence"],
            "direction": pkg["direction"],
            "strategy_risk_pct": float(td1h_cfg.get("risk_pct", 0.3) or 0.3),
        },
    }
    _emit_shadow_preds(
        "trend_donchian_1h", sig, td1h_cfg, symbol,
        timeframe=timeframe, candles_df=candles_df,
    )
    _stamp_regime_on_meta(sig.setdefault("meta", {}), candles_df)
    return _with_signal_package("trend_donchian_1h", sig)


def mes_trend_long_1d_signal_builder(settings: dict) -> Dict[str, Any]:
    """MES daily LONG-ONLY trend-follower (overnight research 2026-06-01).

    A BTC-uncorrelated equity-index diversifier: Donchian-breakout + Chandelier
    ATR trail on the MES daily candle, reusing the SAME unit
    (``src.units.strategies.trend_donchian.order_package``) but **gated
    long-only** — short signals are suppressed (``side="none"``) because the
    equity-index edge is the long side (the secular uptrend punishes
    trend-breakout shorts; the research short-side was net-negative on SPX/MES).

    Routed to the IBKR ``ib_paper`` account (MES futures); ``fetch_candles``
    routes the MES symbol to IBKR automatically via
    ``connector_for_symbol``. Cleared for ``execution: shadow`` after the
    overnight sweep + pre-shadow validation on SPX (the CFD proxy): net-positive
    in IS+OOS across THREE independent families (trend/TSMOM/MA-cross),
    near-zero correlation to the BTC roster, tiny drawdown. CAVEAT: validated on
    SPX500-CFD, not live MES, and live MES history is short — this shadow run
    collects the live-instrument data needed before any promotion. Evidence:
    docs/research/overnight-strategy-research-2026-06-01.md (PERF-20260531-001).

    Honours the YAML ``enabled`` flag as the single source of truth.
    """
    from src.units.strategies import load_strategy_config
    from src.units.strategies.trend_donchian import order_package
    from src.runtime.market_data import fetch_candles

    try:
        strategies_cfg = load_strategy_config()
    except Exception:  # noqa: BLE001 - never fail-open on a config error
        strategies_cfg = {}
    mes_cfg = strategies_cfg.get("mes_trend_long_1d", {}) or {}

    # This strategy is routed only to the MES (ib_paper) account, so the tick
    # symbol is MES; default to MES if a caller omits it.
    symbol = settings.get("SYMBOL", settings.get("symbol", "MES"))

    if not bool(mes_cfg.get("enabled", False)):
        logger.info(
            "mes_trend_long_1d: strategy disabled in config/strategies.yaml - "
            "returning side=none"
        )
        return _with_signal_package("mes_trend_long_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {
                "strategy_name": "mes_trend_long_1d",
                "reason": "disabled_in_yaml",
            },
        })

    timeframe = str(
        mes_cfg.get("timeframe")
        or settings.get("MES_TREND_LONG_1D_TIMEFRAME")
        or "1d"
    )

    exchange = _build_killzone_exchange(settings)
    candles_df = fetch_candles(
        symbol, timeframe, exchange_client=exchange, limit=200,
    )
    if candles_df is None:
        raise RuntimeError(
            f"mes_trend_long_1d: no candle data returned for symbol={symbol} "
            f"timeframe={timeframe}. Check that the IBKR connection is "
            "configured and the symbol is valid."
        )

    _publish_liquidity_state(symbol, candles_df)

    cfg: Dict[str, Any] = {"symbol": symbol, "timeframe": timeframe, **mes_cfg}

    try:
        pkg = order_package(cfg, candles_df=candles_df)
    except ValueError as exc:
        logger.info("mes_trend_long_1d: no actionable signal (%s)", exc)
        try:
            log_signal(_stamp_regime({
                "event": "mes_trend_long_1d_eval",
                "strategy": "mes_trend_long_1d",
                "symbol": symbol,
                "timeframe": timeframe,
                "side": "none",
                "reason": str(exc),
            }, candles_df))
        except Exception:  # noqa: BLE001
            logger.exception("mes_trend_long_1d: dedicated audit emit failed")
        return _with_signal_package("mes_trend_long_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {
                "strategy_name": "mes_trend_long_1d",
                "reason": str(exc),
            },
        })

    # LONG-ONLY gate: suppress short signals — the equity-index edge is the long
    # side only (the validated short-side was net-negative). This is the one
    # behavioural difference from the live trend_donchian unit.
    if pkg["direction"] != "long":
        logger.info("mes_trend_long_1d: short signal suppressed (long-only strategy)")
        try:
            log_signal(_stamp_regime({
                "event": "mes_trend_long_1d_eval",
                "strategy": "mes_trend_long_1d",
                "symbol": symbol,
                "timeframe": timeframe,
                "side": "none",
                "reason": "short_suppressed_long_only",
            }, candles_df))
        except Exception:  # noqa: BLE001
            logger.exception("mes_trend_long_1d: dedicated audit emit failed")
        return _with_signal_package("mes_trend_long_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {
                "strategy_name": "mes_trend_long_1d",
                "reason": "short_suppressed_long_only",
            },
        })

    side = "buy"
    logger.info(
        "mes_trend_long_1d: buy signal at %s (entry=%s sl=%s tp=%s confidence=%.3f)",
        symbol, pkg["entry"], pkg["sl"], pkg["tp"], pkg["confidence"],
    )
    pkg_meta = pkg.get("meta") or {}
    try:
        log_signal(_stamp_regime({
            "event": "mes_trend_long_1d_eval",
            "strategy": "mes_trend_long_1d",
            "symbol": symbol,
            "timeframe": timeframe,
            "side": side,
            "entry": pkg["entry"],
            "stop_loss": pkg["sl"],
            "take_profit": pkg["tp"],
            "confidence": pkg["confidence"],
        }, candles_df))
    except Exception:  # noqa: BLE001
        logger.exception("mes_trend_long_1d: dedicated audit emit failed")

    sig = {
        "symbol": symbol,
        "side": side,
        "price": pkg["entry"],
        "entry_price": pkg["entry"],
        "stop_loss": pkg["sl"],
        "take_profit": pkg["tp"],
        "pattern": "mes_trend_long_1d",
        "meta": {
            **pkg_meta,
            "strategy_name": "mes_trend_long_1d",
            "confidence": pkg["confidence"],
            "direction": "long",
            "strategy_risk_pct": float(mes_cfg.get("risk_pct", 0.3) or 0.3),
        },
    }
    _emit_shadow_preds(
        "mes_trend_long_1d", sig, mes_cfg, symbol,
        timeframe=timeframe, candles_df=candles_df,
    )
    _stamp_regime_on_meta(sig.setdefault("meta", {}), candles_df)
    return _with_signal_package("mes_trend_long_1d", sig)


def squeeze_breakout_4h_signal_builder(settings: dict) -> Dict[str, Any]:
    """Volatility-squeeze breakout (S-STRAT-IMPROVE-S9), member-#3 candidate.

    Fetches 4h candles, calls
    ``src.units.strategies.squeeze_breakout_4h.order_package``, maps to the
    pipeline-shape signal dict. The best member-#3 candidate found
    (uncorrelated 0.30 vs the live trend, robust plateau) but run
    ``execution: shadow`` — logs order packages on real ticks, never sends
    a live order — pending live proof. Evidence:
    docs/audits/squeeze-breakout-complement-2026-05-24.md. Honours the YAML
    ``enabled`` flag as the single source of truth.
    """
    from src.units.strategies import load_strategy_config
    from src.units.strategies.squeeze_breakout_4h import order_package
    from src.runtime.market_data import fetch_candles

    try:
        strategies_cfg = load_strategy_config()
    except Exception:  # noqa: BLE001 — never fail-open on a config error
        strategies_cfg = {}
    sqz_cfg = strategies_cfg.get("squeeze_breakout_4h", {}) or {}

    symbol = settings.get("SYMBOL", settings.get("symbol", "BTCUSDT"))

    if not bool(sqz_cfg.get("enabled", False)):
        logger.info(
            "squeeze_breakout_4h: strategy disabled in config/strategies.yaml — "
            "returning side=none"
        )
        return _with_signal_package("squeeze_breakout_4h", {
            "symbol": symbol,
            "side": "none",
            "meta": {
                "strategy_name": "squeeze_breakout_4h",
                "reason": "disabled_in_yaml",
            },
        })

    timeframe = str(
        sqz_cfg.get("timeframe")
        or settings.get("SQUEEZE_BREAKOUT_4H_TIMEFRAME")
        or settings.get("TIMEFRAME")
        or "4h"
    )

    exchange = _build_killzone_exchange(settings)
    candles_df = fetch_candles(
        symbol, timeframe, exchange_client=exchange, limit=200,
    )
    if candles_df is None:
        raise RuntimeError(
            f"squeeze_breakout_4h: no candle data returned for symbol={symbol} "
            f"timeframe={timeframe}. Check that the exchange connection "
            "is configured and the symbol is valid."
        )

    _publish_liquidity_state(symbol, candles_df)

    cfg: Dict[str, Any] = {"symbol": symbol, "timeframe": timeframe, **sqz_cfg}

    try:
        pkg = order_package(cfg, candles_df=candles_df)
    except ValueError as exc:
        logger.info("squeeze_breakout_4h: no actionable signal (%s)", exc)
        try:
            log_signal(_stamp_regime({
                "event": "squeeze_breakout_4h_eval",
                "strategy": "squeeze_breakout_4h",
                "symbol": symbol,
                "timeframe": timeframe,
                "side": "none",
                "reason": str(exc),
            }, candles_df))
        except Exception:  # noqa: BLE001
            logger.exception("squeeze_breakout_4h: dedicated audit emit failed")
        return _with_signal_package("squeeze_breakout_4h", {
            "symbol": symbol,
            "side": "none",
            "meta": {
                "strategy_name": "squeeze_breakout_4h",
                "reason": str(exc),
            },
        })

    side = "buy" if pkg["direction"] == "long" else "sell"
    logger.info(
        "squeeze_breakout_4h: %s signal at %s (entry=%s sl=%s tp=%s confidence=%.3f)",
        side, symbol, pkg["entry"], pkg["sl"], pkg["tp"], pkg["confidence"],
    )
    pkg_meta = pkg.get("meta") or {}
    try:
        log_signal(_stamp_regime({
            "event": "squeeze_breakout_4h_eval",
            "strategy": "squeeze_breakout_4h",
            "symbol": symbol,
            "timeframe": timeframe,
            "side": side,
            "entry": pkg["entry"],
            "stop_loss": pkg["sl"],
            "take_profit": pkg["tp"],
            "confidence": pkg["confidence"],
        }, candles_df))
    except Exception:  # noqa: BLE001
        logger.exception("squeeze_breakout_4h: dedicated audit emit failed")

    sig = {
        "symbol": symbol,
        "side": side,
        "price": pkg["entry"],
        "entry_price": pkg["entry"],
        "stop_loss": pkg["sl"],
        "take_profit": pkg["tp"],
        "pattern": "squeeze_breakout_4h",
        "meta": {
            **pkg_meta,
            "strategy_name": "squeeze_breakout_4h",
            "confidence": pkg["confidence"],
            "direction": pkg["direction"],
            # Moot while execution:shadow (never sends a live order) but
            # carried for any future flip, same pattern as the other members.
            "strategy_risk_pct": float(sqz_cfg.get("risk_pct", 0.3) or 0.3),
        },
    }
    _emit_shadow_preds(
        "squeeze_breakout_4h", sig, sqz_cfg, symbol,
        timeframe=timeframe, candles_df=candles_df,
    )
    _stamp_regime_on_meta(sig.setdefault("meta", {}), candles_df)
    return _with_signal_package("squeeze_breakout_4h", sig)


def fvg_range_15m_signal_builder(settings: dict) -> Dict[str, Any]:
    """FVG range / mean-reversion (S-STRAT-IMPROVE, 2026-05-30) — the range
    member the roster was missing. Inside a confirmed STATIC horizontal range
    (low ADX = chop, sane width, both boundaries touched >=4x), an unfilled FVG
    in the lower/upper third is a mean-reversion S/R level: enter on a
    wick-rejection at the gap, stop beyond the gap/boundary, target the OPPOSITE
    boundary (full-range reversion). The deliberate opposite of ict_scalp_5m,
    which uses an FVG DIRECTIONALLY (continuation, not reversion).

    Fetches 15m candles, calls
    ``src.units.strategies.fvg_range_15m.order_package``, and maps the result
    into the pipeline-shape signal dict. Validated net-positive in the chop
    regime where the trend-followers are flat (FULL 5y +24.4R, exp +0.363; OOS
    2024-2026 +21.8R, exp +0.518, no overfit decay) but low-frequency and
    recent-regime-concentrated, so it runs ``execution: shadow`` — logs order
    packages on real ticks, never sends a live order. Full evidence:
    docs/audits/fvg-range-complement-2026-05-30.md.

    Honours the ``enabled`` flag in ``config/strategies.yaml`` as the single
    source of truth: ``enabled: false`` short-circuits to ``side="none"``. The
    ``execution: shadow`` gate is enforced downstream in the Accounts layer, not
    here — this builder always produces the real signal so the shadow log
    captures exactly what would have traded.
    """
    from src.units.strategies import load_strategy_config
    from src.units.strategies.fvg_range_15m import order_package
    from src.runtime.market_data import fetch_candles

    try:
        strategies_cfg = load_strategy_config()
    except Exception:  # noqa: BLE001 — never fail-open on a config error
        strategies_cfg = {}
    fvg_cfg = strategies_cfg.get("fvg_range_15m", {}) or {}

    symbol = settings.get("SYMBOL", settings.get("symbol", "BTCUSDT"))

    if not bool(fvg_cfg.get("enabled", False)):
        logger.info(
            "fvg_range_15m: strategy disabled in config/strategies.yaml — "
            "returning side=none"
        )
        return _with_signal_package("fvg_range_15m", {
            "symbol": symbol,
            "side": "none",
            "meta": {
                "strategy_name": "fvg_range_15m",
                "reason": "disabled_in_yaml",
            },
        })

    timeframe = str(
        fvg_cfg.get("timeframe")
        or settings.get("FVG_RANGE_TIMEFRAME")
        or settings.get("TIMEFRAME")
        or "15m"
    )

    exchange = _build_killzone_exchange(settings)
    # range_lookback (48) + ADX/ATR warmup; 250 gives generous headroom.
    candles_df = fetch_candles(
        symbol, timeframe, exchange_client=exchange, limit=250,
    )
    if candles_df is None:
        raise RuntimeError(
            f"fvg_range_15m: no candle data returned for symbol={symbol} "
            f"timeframe={timeframe}. Check that the exchange connection "
            "is configured and the symbol is valid."
        )

    _publish_liquidity_state(symbol, candles_df)

    cfg: Dict[str, Any] = {"symbol": symbol, "timeframe": timeframe, **fvg_cfg}

    try:
        pkg = order_package(cfg, candles_df=candles_df)
    except ValueError as exc:
        logger.info("fvg_range_15m: no actionable signal (%s)", exc)
        try:
            log_signal(_stamp_regime({
                "event": "fvg_range_15m_eval",
                "strategy": "fvg_range_15m",
                "symbol": symbol,
                "timeframe": timeframe,
                "side": "none",
                "reason": str(exc),
            }, candles_df))
        except Exception:  # noqa: BLE001
            logger.exception("fvg_range_15m: dedicated audit emit failed")
        return _with_signal_package("fvg_range_15m", {
            "symbol": symbol,
            "side": "none",
            "meta": {
                "strategy_name": "fvg_range_15m",
                "reason": str(exc),
            },
        })

    side = "buy" if pkg["direction"] == "long" else "sell"
    logger.info(
        "fvg_range_15m: %s signal at %s (entry=%s sl=%s tp=%s confidence=%.3f)",
        side, symbol, pkg["entry"], pkg["sl"], pkg["tp"], pkg["confidence"],
    )
    pkg_meta = pkg.get("meta") or {}
    try:
        log_signal(_stamp_regime({
            "event": "fvg_range_15m_eval",
            "strategy": "fvg_range_15m",
            "symbol": symbol,
            "timeframe": timeframe,
            "side": side,
            "entry": pkg["entry"],
            "stop_loss": pkg["sl"],
            "take_profit": pkg["tp"],
            "confidence": pkg["confidence"],
            # Decision geometry the strategy already computed — surfaced so the
            # dashboard can DRAW the FVG zone it traded on (same keys as
            # ict_scalp). range_hi/range_lo bound the channel for the overlay.
            "fvg_low": pkg_meta.get("fvg_low"),
            "fvg_high": pkg_meta.get("fvg_high"),
            "range_hi": pkg_meta.get("range_hi"),
            "range_lo": pkg_meta.get("range_lo"),
        }, candles_df))
    except Exception:  # noqa: BLE001
        logger.exception("fvg_range_15m: dedicated audit emit failed")

    sig = {
        "symbol": symbol,
        "side": side,
        "price": pkg["entry"],
        "entry_price": pkg["entry"],
        "stop_loss": pkg["sl"],
        "take_profit": pkg["tp"],
        "pattern": "fvg_range_15m",
        "meta": {
            **pkg_meta,
            "strategy_name": "fvg_range_15m",
            "confidence": pkg["confidence"],
            "direction": pkg["direction"],
            # Moot while execution:shadow (never sends a live order) but carried
            # for any future flip, same pattern as the other members.
            "strategy_risk_pct": float(fvg_cfg.get("risk_pct", 0.3) or 0.3),
        },
    }
    _emit_shadow_preds(
        "fvg_range_15m", sig, fvg_cfg, symbol,
        timeframe=timeframe, candles_df=candles_df,
    )
    _stamp_regime_on_meta(sig.setdefault("meta", {}), candles_df)
    return _with_signal_package("fvg_range_15m", sig)
