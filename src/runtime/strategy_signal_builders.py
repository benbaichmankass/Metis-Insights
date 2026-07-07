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

from src.runtime.regime import detect_regime, detect_vol_regime
from src.utils.signal_audit_logger import log_signal

logger = logging.getLogger(__name__)


def _stamp_regime_on_meta(
    meta: Dict[str, Any],
    candles_df: Any,
    *,
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
) -> Dict[str, Any]:
    """Stamp the trend + vol regime axes onto a signal's meta dict.

    Phase 2 of the regime router carries the detector's verdict through
    ``signal.meta`` so ``intent_from_signal`` can attach it to the typed
    ``StrategyIntent`` and ``aggregate_intents`` can evaluate the policy
    table in shadow mode (log-only, no enforcement). Mirrors the phase-1
    audit-row stamp (``_stamp_regime``) — same fields (``regime``,
    ``adx_14``, ``regime_source``), same setdefault semantics (a builder
    that pre-computed its own regime is not overwritten), same
    "never raise" contract (phase-2 is observability-only).

    S-MLOPT-S15b adds the **vol axis**: when ``symbol`` + ``timeframe`` are
    given, the vol-regime detector classifies the same candles into
    ``calm`` / ``volatile`` against the deployed head's frozen ``vol_bucket``
    edges (``vol_regime`` + ``rolling_log_return_vol`` + ``vol_regime_source``).
    Absent a frozen spec for ``(symbol, timeframe)`` — or when called without
    them — ``vol_regime`` stays ``unknown`` and the 2-D policy falls through to
    permissive, leaving the tick unchanged. Observe-only.

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
    try:
        vr = detect_vol_regime(candles_df, symbol=symbol, timeframe=timeframe)
        meta.setdefault("vol_regime", vr["vol_regime"])
        meta.setdefault("rolling_log_return_vol", vr["rolling_log_return_vol"])
        meta.setdefault("vol_regime_source", vr["source"])
    except Exception:  # noqa: BLE001 — observability-only, never break a tick
        meta.setdefault("vol_regime", "unknown")
        meta.setdefault("rolling_log_return_vol", None)
        meta.setdefault("vol_regime_source", "vol-bucket-edges")
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
        # Symbol-aware auto-wire (2026-06-18): restrict the auto-discovered
        # shadow set to models trained on THIS strategy's symbol (or the
        # symbol-agnostic `symbol_scope: all` decision models). Without this,
        # an alt/futures strategy auto-wires BTC-only regime heads that score
        # it out-of-distribution and pollute those heads' shadow track record.
        _syms = strat_cfg.get("symbols") or []
        _strat_symbol = _syms[0] if _syms else None
        ids = (
            discover_shadow_stage_model_ids(registry, symbol=_strat_symbol)
            if auto_wire else explicit_ids
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
        from src.runtime.shadow_adapter import capture_shadow_preds
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
        captured: dict[str, dict[str, Any]] = {}
        for predictor in predictors:
            row = feature_row_for_predictor(
                predictor,
                base_row,
                closes=closes,
                symbol=sig_symbol,
                timeframe=str(timeframe or ""),
                candles_df=candles_df,
            )
            if row is None:
                continue  # mismatched regime model — skip (don't log a constant)
            # One predictor per call preserves the per-model try/except
            # isolation + ShadowPredictor type-check. capture_shadow_preds
            # runs the same single predict() (so the audit log is unchanged)
            # but returns the score so we can persist the ML decisions onto
            # the order package — observe-only, never fed back into the order.
            captured.update(
                capture_shadow_preds([predictor], row)
            )
        # Stamp the per-model scores onto the signal's meta so they flow with
        # the signal → intent → order_packages.meta and get persisted as part
        # of the trade record (a cheap SELECT later, instead of recompiling
        # from the prediction log). Mutate sig["meta"] in place (sig.get(...)
        # may have returned a detached copy above).
        if captured:
            sig_meta = sig.get("meta")
            if not isinstance(sig_meta, dict):
                sig_meta = {}
                sig["meta"] = sig_meta
            existing = sig_meta.get("model_scores")
            if isinstance(existing, dict):
                existing.update(captured)
            else:
                sig_meta["model_scores"] = captured
        # OBSERVE-ONLY unified conviction (design doc § 3 / § 4a, P1). Blends the
        # strategy's calibrated signal confidence with the captured model scores
        # (each gated by its own stage) into a single conviction, stamped on meta
        # alongside model_scores. NEVER read back into the order — pure logging,
        # so the score can soak before any sizing/arbitration influence (P2+).
        try:
            from src.runtime.conviction import compute_conviction
            from src.runtime.conviction_inputs import (
                build_conviction_inputs,
                load_calibrators_cached,
                load_regime_alignment_cached,
            )

            cal = load_calibrators_cached()
            ra = load_regime_alignment_cached()
            conv_inputs, conv_prov = build_conviction_inputs(
                strategy_name, base_row["confidence"], captured,
                calibrators=cal, direction=base_row["direction"],
                regime_alignment=ra,
            )
            conv = compute_conviction(conv_inputs)
            sig_meta2 = sig.get("meta")
            if isinstance(sig_meta2, dict):
                sig_meta2["conviction"] = {**conv.to_dict(), "provenance": conv_prov}
        except Exception:  # noqa: BLE001 — observe-only, never strand a signal
            pass
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
    _stamp_regime_on_meta(sig.setdefault("meta", {}), candles_df, symbol=symbol, timeframe=timeframe)
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
    _stamp_regime_on_meta(sig.setdefault("meta", {}), candles_df, symbol=symbol, timeframe=timeframe)
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

    # Honour the YAML ``enabled`` flag as the single source of truth — match
    # every other builder (ict_scalp/trend_donchian/fade/htf_pullback/…) which
    # short-circuit to side="none" BEFORE any fetch + eval emission. vwap was
    # the lone builder missing this gate, so the M7-killed (enabled:false) vwap
    # kept emitting ``vwap_eval`` rows + burning per-tick eval cycles even
    # though the order path correctly skipped it (BL-20260610-001). A disabled
    # strategy must go fully silent on the audit surface.
    if not bool(vwap_cfg.get("enabled", False)):
        logger.info(
            "vwap: strategy disabled in config/strategies.yaml — "
            "returning side=none"
        )
        return _with_signal_package("vwap", {
            "symbol": symbol,
            "side": "none",
            "meta": {
                "strategy_name": "vwap",
                "reason": "disabled_in_yaml",
            },
        })

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
    _stamp_regime_on_meta(sig.setdefault("meta", {}), candles_df, symbol=symbol, timeframe=timeframe)
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
    cfg["strategy_label"] = "trend_donchian"

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
        },
    }
    _emit_shadow_preds(
        "trend_donchian", sig, trend_cfg, symbol,
        timeframe=timeframe, candles_df=candles_df,
    )
    _stamp_regime_on_meta(sig.setdefault("meta", {}), candles_df, symbol=symbol, timeframe=timeframe)
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
        },
    }
    _emit_shadow_preds(
        "fade_breakout_4h", sig, fade_cfg, symbol,
        timeframe=timeframe, candles_df=candles_df,
    )
    _stamp_regime_on_meta(sig.setdefault("meta", {}), candles_df, symbol=symbol, timeframe=timeframe)
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
    cfg["strategy_label"] = "htf_pullback_trend_2h"

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
        },
    }
    _emit_shadow_preds(
        "htf_pullback_trend_2h", sig, hp_cfg, symbol,
        timeframe=timeframe, candles_df=candles_df,
    )
    _stamp_regime_on_meta(sig.setdefault("meta", {}), candles_df, symbol=symbol, timeframe=timeframe)
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
    cfg["strategy_label"] = "trend_donchian_1h"

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
        },
    }
    _emit_shadow_preds(
        "trend_donchian_1h", sig, td1h_cfg, symbol,
        timeframe=timeframe, candles_df=candles_df,
    )
    _stamp_regime_on_meta(sig.setdefault("meta", {}), candles_df, symbol=symbol, timeframe=timeframe)
    return _with_signal_package("trend_donchian_1h", sig)


def _trend_donchian_variant_builder(name: str, settings: dict) -> Dict[str, Any]:
    """Shared builder for the prop alt variants (trend_donchian_sol/_eth).

    Reuses ``src.units.strategies.trend_donchian.order_package`` parametrised by
    the variant's own ``<name>`` config block. The traded symbol is pinned from
    the variant's ``symbols:`` (it is a single-instrument prop instance), NOT
    from the tick settings — so the variant always evaluates its own alt even if
    invoked on another tick symbol. Honours an opt-in per-variant ``long_only``
    flag (PB-20260616-004 A/B: SOL holds long-only, ETH stays two-sided) and
    ``enabled`` as the single source of truth; the ``execution: live|shadow``
    gate is enforced downstream.
    """
    from src.units.strategies import load_strategy_config
    from src.units.strategies.trend_donchian import order_package
    from src.runtime.market_data import fetch_candles

    try:
        strategies_cfg = load_strategy_config()
    except Exception:  # noqa: BLE001
        strategies_cfg = {}
    vcfg = strategies_cfg.get(name, {}) or {}

    syms = vcfg.get("symbols") or []
    symbol = str(syms[0]) if syms else settings.get("SYMBOL", settings.get("symbol", ""))

    if not bool(vcfg.get("enabled", False)):
        return _with_signal_package(name, {
            "symbol": symbol, "side": "none",
            "meta": {"strategy_name": name, "reason": "disabled_in_yaml"},
        })

    timeframe = str(vcfg.get("timeframe") or "1h")
    exchange = _build_killzone_exchange(settings)
    candles_df = fetch_candles(symbol, timeframe, exchange_client=exchange, limit=200)
    if candles_df is None:
        raise RuntimeError(
            f"{name}: no candle data for symbol={symbol} timeframe={timeframe}.")

    _publish_liquidity_state(symbol, candles_df)
    cfg: Dict[str, Any] = {"symbol": symbol, "timeframe": timeframe, **vcfg}
    cfg["strategy_label"] = name

    try:
        pkg = order_package(cfg, candles_df=candles_df)
    except ValueError as exc:
        logger.info("%s: no actionable signal (%s)", name, exc)
        try:
            log_signal(_stamp_regime({
                "event": f"{name}_eval", "strategy": name, "symbol": symbol,
                "timeframe": timeframe, "side": "none", "reason": str(exc),
            }, candles_df))
        except Exception:  # noqa: BLE001
            logger.exception("%s: dedicated audit emit failed", name)
        return _with_signal_package(name, {
            "symbol": symbol, "side": "none",
            "meta": {"strategy_name": name, "reason": str(exc)},
        })

    # LONG-ONLY gate (operator-approved 2026-06-16, Tier-3). Mirrors the
    # flagship trend_donchian builder's directional discipline. The Breakout
    # daily-swap walk-forward A/B (PB-20260616-004) showed SOL's edge survives
    # long-only (pre +$1,325 → post +$1,158, EV@1.5% +$1,131/86%, 4/4 folds)
    # while ETH's edge is short-side-dependent (long-only flips it negative) —
    # so trend_donchian_sol sets ``long_only: true`` and trend_donchian_eth
    # stays two-sided. Honoured per-variant from strategies.yaml; default off.
    if bool(vcfg.get("long_only", False)) and pkg["direction"] != "long":
        logger.info("%s: short signal suppressed (long_only)", name)
        try:
            log_signal(_stamp_regime({
                "event": f"{name}_eval", "strategy": name, "symbol": symbol,
                "timeframe": timeframe, "side": "none",
                "reason": "short_suppressed_long_only",
            }, candles_df))
        except Exception:  # noqa: BLE001
            logger.exception("%s: dedicated audit emit failed", name)
        return _with_signal_package(name, {
            "symbol": symbol, "side": "none",
            "meta": {
                "strategy_name": name,
                "reason": "short_suppressed_long_only",
            },
        })

    side = "buy" if pkg["direction"] == "long" else "sell"
    pkg_meta = pkg.get("meta") or {}
    try:
        log_signal(_stamp_regime({
            "event": f"{name}_eval", "strategy": name, "symbol": symbol,
            "timeframe": timeframe, "side": side, "entry": pkg["entry"],
            "stop_loss": pkg["sl"], "take_profit": pkg["tp"],
            "confidence": pkg["confidence"],
        }, candles_df))
    except Exception:  # noqa: BLE001
        logger.exception("%s: dedicated audit emit failed", name)

    sig = {
        "symbol": symbol, "side": side, "price": pkg["entry"],
        "entry_price": pkg["entry"], "stop_loss": pkg["sl"], "take_profit": pkg["tp"],
        "pattern": name,
        "meta": {
            **pkg_meta, "strategy_name": name, "confidence": pkg["confidence"],
            "direction": pkg["direction"], "timeframe": timeframe,
        },
    }
    _emit_shadow_preds(name, sig, vcfg, symbol, timeframe=timeframe, candles_df=candles_df)
    _stamp_regime_on_meta(sig.setdefault("meta", {}), candles_df, symbol=symbol, timeframe=timeframe)
    return _with_signal_package(name, sig)


def trend_donchian_sol_signal_builder(settings: dict) -> Dict[str, Any]:
    """trend_donchian on SOLUSDT for the Breakout prop account (PB-20260616-004)."""
    return _trend_donchian_variant_builder("trend_donchian_sol", settings)


def trend_donchian_eth_signal_builder(settings: dict) -> Dict[str, Any]:
    """trend_donchian on ETHUSDT for the Breakout prop account (shadow soak)."""
    return _trend_donchian_variant_builder("trend_donchian_eth", settings)


# ── SWAP-ROBUST prop exit variants (Unit C, Phase 0) — breakout_1 shadow soak ───
# Tightened-exit prop-only siblings of trend_donchian_sol / trend_donchian_eth:
# same entries, but the eth_pullback_prop_2h exit recipe (trail_mult 3.5,
# tp_r 6.0 vs the live 5.0/50.0) so multi-day holds don't bleed to the Breakout
# daily swap. Both reuse the symbol-generic _trend_donchian_variant_builder,
# which pins the traded symbol from each variant's own `symbols:` block and
# reads the tightened exits from its config. Routed to breakout_1 as
# execution: shadow (observe-only — DRAFT Tier-3, prop-EV-gated; 2026-06-29).
def trend_donchian_sol_prop_signal_builder(settings: dict) -> Dict[str, Any]:
    """trend_donchian SOLUSDT 1h, swap-robust prop exits — breakout_1 shadow soak."""
    return _trend_donchian_variant_builder("trend_donchian_sol_prop", settings)


def trend_donchian_eth_prop_signal_builder(settings: dict) -> Dict[str, Any]:
    """trend_donchian ETHUSDT 1h, swap-robust prop exits — breakout_1 shadow soak."""
    return _trend_donchian_variant_builder("trend_donchian_eth_prop", settings)


# ── trend_4h alt cells — bybit_1 DEMO soak (paper_ready, WS-C-validated) ────────
# Five symbol-pinned trend_donchian instances on the 4h candle (ETH/SOL/XRP/ADA/
# AVAX), routed to bybit_1 (Bybit demo — paper money) for decision/ML soak. The
# M15 WS-C k-fold sweep classified the trend_4h cell `paper_ready`: net-of-fee
# positive overall + survives 2x fees, failing only the strict every-fold gate on
# the recent (late-2025) regime (SRQ-20260618-001). Demo-only, observe-for-soak —
# NOT live-money-ready (real-money bybit_2 stays a separate Tier-3 gate). Each is
# BOTH-SIDES (no long_only — matches the WS-C validation run, unlike the prop
# trend_donchian_sol long-only cell). All reuse the symbol-generic
# _trend_donchian_variant_builder, which pins the traded symbol from each
# variant's own `symbols:` block and honours `enabled` as the single source of
# truth (2026-06-18, Tier-3).
def trend_donchian_eth_4h_signal_builder(settings: dict) -> Dict[str, Any]:
    """trend_donchian on ETHUSDT 4h — bybit_1 demo soak (paper_ready)."""
    return _trend_donchian_variant_builder("trend_donchian_eth_4h", settings)


def trend_donchian_sol_4h_signal_builder(settings: dict) -> Dict[str, Any]:
    """trend_donchian on SOLUSDT 4h — bybit_1 demo soak (paper_ready)."""
    return _trend_donchian_variant_builder("trend_donchian_sol_4h", settings)


def trend_donchian_xrp_4h_signal_builder(settings: dict) -> Dict[str, Any]:
    """trend_donchian on XRPUSDT 4h — bybit_1 demo soak (paper_ready)."""
    return _trend_donchian_variant_builder("trend_donchian_xrp_4h", settings)


def trend_donchian_ada_4h_signal_builder(settings: dict) -> Dict[str, Any]:
    """trend_donchian on ADAUSDT 4h — bybit_1 demo soak (paper_ready)."""
    return _trend_donchian_variant_builder("trend_donchian_ada_4h", settings)


def trend_donchian_avax_4h_signal_builder(settings: dict) -> Dict[str, Any]:
    """trend_donchian on AVAXUSDT 4h — bybit_1 demo soak (paper_ready)."""
    return _trend_donchian_variant_builder("trend_donchian_avax_4h", settings)


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
    cfg["strategy_label"] = "mes_trend_long_1d"

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
        },
    }
    _emit_shadow_preds(
        "mes_trend_long_1d", sig, mes_cfg, symbol,
        timeframe=timeframe, candles_df=candles_df,
    )
    _stamp_regime_on_meta(sig.setdefault("meta", {}), candles_df, symbol=symbol, timeframe=timeframe)
    return _with_signal_package("mes_trend_long_1d", sig)


def _metals_pullback_signal_builder(
    settings: dict,
    *,
    strategy_name: str,
    default_symbol: str,
) -> Dict[str, Any]:
    """Shared body for the WS-A metals pullback sleeve (MGC / MHG).

    A BTC-uncorrelated WS-A-validated diversifier: the HTF trend-pullback
    continuation unit (``src.units.strategies.htf_pullback_trend_2h.order_package``)
    on the COMEX-micro-metal daily candle, routed to the IBKR ``ib_paper``
    account. Unlike ``mes_trend_long_1d`` this trades BOTH directions — the
    pullback edge is symmetric (long pullbacks in uptrends, short pullbacks in
    downtrends), so there is NO long-only gate. The live config exactly
    reproduces the backtest-validated WS-A S2/S3 params (mirrored by
    ``scripts/backtest_pullback.py``); honours the YAML ``enabled`` flag as the
    single source of truth.
    """
    from src.units.strategies import load_strategy_config
    from src.units.strategies.htf_pullback_trend_2h import order_package
    from src.runtime.market_data import fetch_candles

    try:
        strategies_cfg = load_strategy_config()
    except Exception:  # noqa: BLE001 - never fail-open on a config error
        strategies_cfg = {}
    strat_cfg = strategies_cfg.get(strategy_name, {}) or {}

    # Routed only to the metals (ib_paper) account, so the tick symbol is the
    # metal; default to it if a caller omits it.
    symbol = settings.get("SYMBOL", settings.get("symbol", default_symbol))

    if not bool(strat_cfg.get("enabled", False)):
        logger.info(
            "%s: strategy disabled in config/strategies.yaml - returning side=none",
            strategy_name,
        )
        return _with_signal_package(strategy_name, {
            "symbol": symbol,
            "side": "none",
            "meta": {
                "strategy_name": strategy_name,
                "reason": "disabled_in_yaml",
            },
        })

    timeframe = str(strat_cfg.get("timeframe") or "1d")

    exchange = _build_killzone_exchange(settings)
    candles_df = fetch_candles(
        symbol, timeframe, exchange_client=exchange, limit=200,
    )
    if candles_df is None:
        raise RuntimeError(
            f"{strategy_name}: no candle data returned for symbol={symbol} "
            f"timeframe={timeframe}. Check that the IBKR connection is "
            "configured and the symbol is valid."
        )

    _publish_liquidity_state(symbol, candles_df)

    cfg: Dict[str, Any] = {"symbol": symbol, "timeframe": timeframe, **strat_cfg}
    cfg["strategy_label"] = strategy_name

    try:
        pkg = order_package(cfg, candles_df=candles_df)
    except ValueError as exc:
        logger.info("%s: no actionable signal (%s)", strategy_name, exc)
        try:
            log_signal(_stamp_regime({
                "event": f"{strategy_name}_eval",
                "strategy": strategy_name,
                "symbol": symbol,
                "timeframe": timeframe,
                "side": "none",
                "reason": str(exc),
            }, candles_df))
        except Exception:  # noqa: BLE001
            logger.exception("%s: dedicated audit emit failed", strategy_name)
        return _with_signal_package(strategy_name, {
            "symbol": symbol,
            "side": "none",
            "meta": {
                "strategy_name": strategy_name,
                "reason": str(exc),
            },
        })

    # No long-only gate: the pullback edge is symmetric, so honour BOTH
    # directions (the deliberate difference from mes_trend_long_1d).
    side = "buy" if pkg["direction"] == "long" else "sell"
    logger.info(
        "%s: %s signal at %s (entry=%s sl=%s tp=%s confidence=%.3f)",
        strategy_name, side, symbol, pkg["entry"], pkg["sl"], pkg["tp"],
        pkg["confidence"],
    )
    pkg_meta = pkg.get("meta") or {}
    try:
        log_signal(_stamp_regime({
            "event": f"{strategy_name}_eval",
            "strategy": strategy_name,
            "symbol": symbol,
            "timeframe": timeframe,
            "side": side,
            "entry": pkg["entry"],
            "stop_loss": pkg["sl"],
            "take_profit": pkg["tp"],
            "confidence": pkg["confidence"],
        }, candles_df))
    except Exception:  # noqa: BLE001
        logger.exception("%s: dedicated audit emit failed", strategy_name)

    sig = {
        "symbol": symbol,
        "side": side,
        "price": pkg["entry"],
        "entry_price": pkg["entry"],
        "stop_loss": pkg["sl"],
        "take_profit": pkg["tp"],
        "pattern": strategy_name,
        "meta": {
            **pkg_meta,
            "strategy_name": strategy_name,
            "confidence": pkg["confidence"],
            "direction": pkg["direction"],
        },
    }
    _emit_shadow_preds(
        strategy_name, sig, strat_cfg, symbol,
        timeframe=timeframe, candles_df=candles_df,
    )
    _stamp_regime_on_meta(sig.setdefault("meta", {}), candles_df, symbol=symbol, timeframe=timeframe)
    return _with_signal_package(strategy_name, sig)


def mgc_pullback_1d_signal_builder(settings: dict) -> Dict[str, Any]:
    """Micro Gold (MGC) daily HTF-pullback diversifier (WS-A S2/S3).

    Reuses ``htf_pullback_trend_2h.order_package`` on the MGC daily candle,
    routed to the IBKR ``ib_paper`` account (COMEX Micro Gold). Trades both
    directions (no long-only gate). Validated params (pullback_frac 0.618,
    trend_lookback 40, pullback_lookback 15) live in config/strategies.yaml and
    are mirrored by scripts/backtest_pullback.py. Honours the YAML ``enabled``
    flag as the single source of truth.
    """
    return _metals_pullback_signal_builder(
        settings, strategy_name="mgc_pullback_1d", default_symbol="MGC",
    )


def mhg_pullback_1d_signal_builder(settings: dict) -> Dict[str, Any]:
    """Micro Copper (MHG) daily HTF-pullback diversifier (WS-A S2/S3).

    Reuses ``htf_pullback_trend_2h.order_package`` on the MHG daily candle,
    routed to the IBKR ``ib_paper`` account (COMEX Micro Copper). Trades both
    directions (no long-only gate). Validated params (pullback_frac 0.5,
    trend_lookback 40, pullback_lookback 15) live in config/strategies.yaml and
    are mirrored by scripts/backtest_pullback.py. Honours the YAML ``enabled``
    flag as the single source of truth.
    """
    return _metals_pullback_signal_builder(
        settings, strategy_name="mhg_pullback_1d", default_symbol="MHG",
    )


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
        },
    }
    _emit_shadow_preds(
        "squeeze_breakout_4h", sig, sqz_cfg, symbol,
        timeframe=timeframe, candles_df=candles_df,
    )
    _stamp_regime_on_meta(sig.setdefault("meta", {}), candles_df, symbol=symbol, timeframe=timeframe)
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
        },
    }
    _emit_shadow_preds(
        "fvg_range_15m", sig, fvg_cfg, symbol,
        timeframe=timeframe, candles_df=candles_df,
    )
    _stamp_regime_on_meta(sig.setdefault("meta", {}), candles_df, symbol=symbol, timeframe=timeframe)
    return _with_signal_package("fvg_range_15m", sig)


def xauusd_trend_1h_signal_builder(settings: dict) -> Dict[str, Any]:
    """XAU/USD 1h trend-follower (M15 Phase 3, S-M15-PHASE3-XAU-SHADOW).

    The strongest cell of the M15 Phase-0 generalization sweep
    (docs/research/m15-phase0-results-2026-06-10.md): Donchian-breakout +
    Chandelier ATR trail on gold 1h, net-of-fee train +78.4R (1024
    trades) / OOS +36.8R (245 trades) at the harness defaults pinned in
    config/strategies.yaml. Reuses the SAME unit
    (``src.units.strategies.trend_donchian.order_package``),
    BIDIRECTIONAL — both sides validated on gold (unlike the long-only
    equity-index variant). Routed only to the ``oanda_practice`` account
    (paper money); ``fetch_candles`` routes XAUUSD to OANDA via the
    instrument profile.

    FX WEEKEND GATE: gold trades 24/5 — from Friday 21:00 UTC to Sunday
    21:00 UTC the market is closed and the last fetched candles are
    stale. The builder returns ``side=none`` (reason
    ``fx_market_closed``) during that window so closed-market data can
    never produce entries. Fail-permissive: only an explicit closed
    verdict from ``src.runtime.market_hours`` gates.

    Honours the YAML ``enabled`` flag as the single source of truth.
    """
    from src.units.strategies import load_strategy_config
    from src.units.strategies.trend_donchian import order_package
    from src.runtime.market_data import fetch_candles
    from src.runtime.market_hours import is_market_open

    try:
        strategies_cfg = load_strategy_config()
    except Exception:  # noqa: BLE001 - never fail-open on a config error
        strategies_cfg = {}
    xau_cfg = strategies_cfg.get("xauusd_trend_1h", {}) or {}

    symbol = settings.get("SYMBOL", settings.get("symbol", "XAUUSD"))

    if not bool(xau_cfg.get("enabled", False)):
        logger.info(
            "xauusd_trend_1h: strategy disabled in config/strategies.yaml - "
            "returning side=none"
        )
        return _with_signal_package("xauusd_trend_1h", {
            "symbol": symbol,
            "side": "none",
            "meta": {
                "strategy_name": "xauusd_trend_1h",
                "reason": "disabled_in_yaml",
            },
        })

    try:
        fx_open = is_market_open("fx")
    except Exception:  # noqa: BLE001 — gate is best-effort, never strands
        fx_open = True
    if not fx_open:
        logger.info("xauusd_trend_1h: FX market closed (weekend) - side=none")
        return _with_signal_package("xauusd_trend_1h", {
            "symbol": symbol,
            "side": "none",
            "meta": {
                "strategy_name": "xauusd_trend_1h",
                "reason": "fx_market_closed",
            },
        })

    timeframe = str(xau_cfg.get("timeframe") or "1h")

    exchange = _build_killzone_exchange(settings)
    candles_df = fetch_candles(
        symbol, timeframe, exchange_client=exchange, limit=200,
    )
    if candles_df is None:
        raise RuntimeError(
            f"xauusd_trend_1h: no candle data returned for symbol={symbol} "
            f"timeframe={timeframe}. Check that the OANDA connection is "
            "configured and the symbol is valid."
        )

    _publish_liquidity_state(symbol, candles_df)

    cfg: Dict[str, Any] = {"symbol": symbol, "timeframe": timeframe, **xau_cfg}
    cfg["strategy_label"] = "xauusd_trend_1h"

    try:
        pkg = order_package(cfg, candles_df=candles_df)
    except ValueError as exc:
        logger.info("xauusd_trend_1h: no actionable signal (%s)", exc)
        try:
            log_signal(_stamp_regime({
                "event": "xauusd_trend_1h_eval",
                "strategy": "xauusd_trend_1h",
                "symbol": symbol,
                "timeframe": timeframe,
                "side": "none",
                "reason": str(exc),
            }, candles_df))
        except Exception:  # noqa: BLE001
            logger.exception("xauusd_trend_1h: dedicated audit emit failed")
        return _with_signal_package("xauusd_trend_1h", {
            "symbol": symbol,
            "side": "none",
            "meta": {
                "strategy_name": "xauusd_trend_1h",
                "reason": str(exc),
            },
        })

    side = "buy" if pkg["direction"] == "long" else "sell"
    logger.info(
        "xauusd_trend_1h: %s signal at %s (entry=%s sl=%s tp=%s confidence=%.3f)",
        side, symbol, pkg["entry"], pkg["sl"], pkg["tp"], pkg["confidence"],
    )
    pkg_meta = pkg.get("meta") or {}
    try:
        log_signal(_stamp_regime({
            "event": "xauusd_trend_1h_eval",
            "strategy": "xauusd_trend_1h",
            "symbol": symbol,
            "timeframe": timeframe,
            "side": side,
            "entry": pkg["entry"],
            "stop_loss": pkg["sl"],
            "take_profit": pkg["tp"],
            "confidence": pkg["confidence"],
        }, candles_df))
    except Exception:  # noqa: BLE001
        logger.exception("xauusd_trend_1h: dedicated audit emit failed")

    sig = {
        "symbol": symbol,
        "side": side,
        "price": pkg["entry"],
        "entry_price": pkg["entry"],
        "stop_loss": pkg["sl"],
        "take_profit": pkg["tp"],
        "pattern": "xauusd_trend_1h",
        "meta": {
            **pkg_meta,
            "strategy_name": "xauusd_trend_1h",
            "confidence": pkg["confidence"],
            "direction": pkg["direction"],
        },
    }
    _emit_shadow_preds(
        "xauusd_trend_1h", sig, xau_cfg, symbol,
        timeframe=timeframe, candles_df=candles_df,
    )
    _stamp_regime_on_meta(sig.setdefault("meta", {}), candles_df, symbol=symbol, timeframe=timeframe)
    return _with_signal_package("xauusd_trend_1h", sig)


def mgc_trend_1h_signal_builder(settings: dict) -> Dict[str, Any]:
    """MGC (micro gold futures) 1h trend-follower — the IBKR sibling of
    ``xauusd_trend_1h``.

    Same validated edge, same underlying (gold), different venue: MGC is
    COMEX micro-gold futures on IBKR, so the strategy runs on the same
    Donchian-breakout + Chandelier ATR trail
    (``src.units.strategies.trend_donchian.order_package``), BIDIRECTIONAL,
    at the params pinned in ``config/strategies.yaml``. Confirmatory backtest
    (gold 1h at MGC-realistic 1.5 bps round-trip cost): TRAIN 2019-24 +49.4R /
    1024t, OOS 2025-26 +32.2R / 245t (expectancy 0.13R, maxDD 9.1R), robust to
    3 bps (+27.6R OOS). MGC's low futures cost (~1-1.5 bps) is why this venue
    clears where OANDA spot was marginal — see the #3447/#3448 trainer
    backtests. Routed to the IBKR ``ib_paper`` account (paper money);
    ``fetch_candles`` routes MGC to IBKR via the instrument profile.

    No FX weekend gate (unlike the OANDA-spot ``xauusd_trend_1h``): MGC is a
    CME future, ``market_hours`` has no CME asset class, and the sibling IBKR
    futures sleeves (``mes_trend_long_1d``, ``mgc_pullback_1d``) don't gate —
    IBKR won't fire a fresh Donchian breakout on repeated stale closed-market
    candles. Honours the YAML ``enabled`` flag as the single source of truth.
    """
    from src.units.strategies import load_strategy_config
    from src.units.strategies.trend_donchian import order_package
    from src.runtime.market_data import fetch_candles

    try:
        strategies_cfg = load_strategy_config()
    except Exception:  # noqa: BLE001 - never fail-open on a config error
        strategies_cfg = {}
    mgc_cfg = strategies_cfg.get("mgc_trend_1h", {}) or {}

    symbol = settings.get("SYMBOL", settings.get("symbol", "MGC"))

    if not bool(mgc_cfg.get("enabled", False)):
        logger.info(
            "mgc_trend_1h: strategy disabled in config/strategies.yaml - "
            "returning side=none"
        )
        return _with_signal_package("mgc_trend_1h", {
            "symbol": symbol,
            "side": "none",
            "meta": {
                "strategy_name": "mgc_trend_1h",
                "reason": "disabled_in_yaml",
            },
        })

    timeframe = str(mgc_cfg.get("timeframe") or "1h")

    exchange = _build_killzone_exchange(settings)
    candles_df = fetch_candles(
        symbol, timeframe, exchange_client=exchange, limit=200,
    )
    if candles_df is None:
        raise RuntimeError(
            f"mgc_trend_1h: no candle data returned for symbol={symbol} "
            f"timeframe={timeframe}. Check that the IBKR connection is "
            "configured and the symbol is valid."
        )

    _publish_liquidity_state(symbol, candles_df)

    cfg: Dict[str, Any] = {"symbol": symbol, "timeframe": timeframe, **mgc_cfg}
    cfg["strategy_label"] = "mgc_trend_1h"

    try:
        pkg = order_package(cfg, candles_df=candles_df)
    except ValueError as exc:
        logger.info("mgc_trend_1h: no actionable signal (%s)", exc)
        try:
            log_signal(_stamp_regime({
                "event": "mgc_trend_1h_eval",
                "strategy": "mgc_trend_1h",
                "symbol": symbol,
                "timeframe": timeframe,
                "side": "none",
                "reason": str(exc),
            }, candles_df))
        except Exception:  # noqa: BLE001
            logger.exception("mgc_trend_1h: dedicated audit emit failed")
        return _with_signal_package("mgc_trend_1h", {
            "symbol": symbol,
            "side": "none",
            "meta": {
                "strategy_name": "mgc_trend_1h",
                "reason": str(exc),
            },
        })

    side = "buy" if pkg["direction"] == "long" else "sell"
    logger.info(
        "mgc_trend_1h: %s signal at %s (entry=%s sl=%s tp=%s confidence=%.3f)",
        side, symbol, pkg["entry"], pkg["sl"], pkg["tp"], pkg["confidence"],
    )
    pkg_meta = pkg.get("meta") or {}
    try:
        log_signal(_stamp_regime({
            "event": "mgc_trend_1h_eval",
            "strategy": "mgc_trend_1h",
            "symbol": symbol,
            "timeframe": timeframe,
            "side": side,
            "entry": pkg["entry"],
            "stop_loss": pkg["sl"],
            "take_profit": pkg["tp"],
            "confidence": pkg["confidence"],
        }, candles_df))
    except Exception:  # noqa: BLE001
        logger.exception("mgc_trend_1h: dedicated audit emit failed")

    sig = {
        "symbol": symbol,
        "side": side,
        "price": pkg["entry"],
        "entry_price": pkg["entry"],
        "stop_loss": pkg["sl"],
        "take_profit": pkg["tp"],
        "pattern": "mgc_trend_1h",
        "meta": {
            **pkg_meta,
            "strategy_name": "mgc_trend_1h",
            "confidence": pkg["confidence"],
            "direction": pkg["direction"],
        },
    }
    _emit_shadow_preds(
        "mgc_trend_1h", sig, mgc_cfg, symbol,
        timeframe=timeframe, candles_df=candles_df,
    )
    _stamp_regime_on_meta(sig.setdefault("meta", {}), candles_df, symbol=symbol, timeframe=timeframe)
    return _with_signal_package("mgc_trend_1h", sig)



def spy_trend_long_1d_signal_builder(settings: dict) -> Dict[str, Any]:
    """SPY daily LONG-ONLY trend-follower (M15 Phase 4 buildout, S-M15-PHASE4).

    The direct ETF replacement for the MES futures leg (Phase-0: train +16.0R / OOS +9.2R). Runs on the ``alpaca_paper`` account (PAPER money; bracket
    orders carry broker-side SL/TP). Reuses the
    ``src.units.strategies.trend_donchian.order_package`` unit. LONG-ONLY (shorts suppressed).

    US-SESSION GATE: evaluation is skipped outside the US cash session
    (``market_hours.is_market_open("us_equity")`` → side=none, reason
    ``us_market_closed``) so closed-market candles never produce
    entries. Fail-permissive on gate errors. Honours the YAML
    ``enabled`` flag as the single source of truth.
    """
    from src.units.strategies import load_strategy_config
    from src.units.strategies.trend_donchian import order_package
    from src.runtime.market_data import fetch_candles
    from src.runtime.market_hours import is_market_open

    try:
        strategies_cfg = load_strategy_config()
    except Exception:  # noqa: BLE001 - never fail-open on a config error
        strategies_cfg = {}
    cfg_yaml = strategies_cfg.get("spy_trend_long_1d", {}) or {}

    symbol = settings.get("SYMBOL", settings.get("symbol", "SPY"))

    if not bool(cfg_yaml.get("enabled", False)):
        logger.info("spy_trend_long_1d: strategy disabled in config/strategies.yaml - returning side=none")
        return _with_signal_package("spy_trend_long_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "spy_trend_long_1d", "reason": "disabled_in_yaml"},
        })

    try:
        us_open = is_market_open("us_equity")
    except Exception:  # noqa: BLE001 — gate is best-effort, never strands
        us_open = True
    if not us_open:
        logger.info("spy_trend_long_1d: US market closed - side=none")
        return _with_signal_package("spy_trend_long_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "spy_trend_long_1d", "reason": "us_market_closed"},
        })

    timeframe = str(cfg_yaml.get("timeframe") or "1d")

    exchange = _build_killzone_exchange(settings)
    candles_df = fetch_candles(symbol, timeframe, exchange_client=exchange, limit=200)
    if candles_df is None:
        raise RuntimeError(
            f"spy_trend_long_1d: no candle data returned for symbol={symbol} "
            f"timeframe={timeframe}. Check that the Alpaca connection is "
            "configured and the symbol is valid."
        )

    _publish_liquidity_state(symbol, candles_df)

    cfg: Dict[str, Any] = {"symbol": symbol, "timeframe": timeframe, **cfg_yaml}
    cfg["strategy_label"] = "spy_trend_long_1d"

    try:
        pkg = order_package(cfg, candles_df=candles_df)
    except ValueError as exc:
        logger.info("spy_trend_long_1d: no actionable signal (%s)", exc)
        try:
            log_signal(_stamp_regime({
                "event": "spy_trend_long_1d_eval",
                "strategy": "spy_trend_long_1d",
                "symbol": symbol,
                "timeframe": timeframe,
                "side": "none",
                "reason": str(exc),
            }, candles_df))
        except Exception:  # noqa: BLE001
            logger.exception("spy_trend_long_1d: dedicated audit emit failed")
        return _with_signal_package("spy_trend_long_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "spy_trend_long_1d", "reason": str(exc)},
        })

    # LONG-ONLY gate: the equity-index edge is the long side (mirrors
    # mes_trend_long_1d; the validated short side was net-negative).
    if pkg["direction"] != "long":
        logger.info("spy_trend_long_1d: short signal suppressed (long-only strategy)")
        try:
            log_signal(_stamp_regime({
                "event": "spy_trend_long_1d_eval",
                "strategy": "spy_trend_long_1d",
                "symbol": symbol,
                "timeframe": timeframe,
                "side": "none",
                "reason": "short_suppressed_long_only",
            }, candles_df))
        except Exception:  # noqa: BLE001
            logger.exception("spy_trend_long_1d: dedicated audit emit failed")
        return _with_signal_package("spy_trend_long_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "spy_trend_long_1d", "reason": "short_suppressed_long_only"},
        })

    side = "buy" if pkg["direction"] == "long" else "sell"
    logger.info(
        "spy_trend_long_1d: %s signal at %s (entry=%s sl=%s tp=%s confidence=%.3f)",
        side, symbol, pkg["entry"], pkg["sl"], pkg["tp"], pkg["confidence"],
    )
    pkg_meta = pkg.get("meta") or {}
    try:
        log_signal(_stamp_regime({
            "event": "spy_trend_long_1d_eval",
            "strategy": "spy_trend_long_1d",
            "symbol": symbol,
            "timeframe": timeframe,
            "side": side,
            "entry": pkg["entry"],
            "stop_loss": pkg["sl"],
            "take_profit": pkg["tp"],
            "confidence": pkg["confidence"],
        }, candles_df))
    except Exception:  # noqa: BLE001
        logger.exception("spy_trend_long_1d: dedicated audit emit failed")

    sig = {
        "symbol": symbol,
        "side": side,
        "price": pkg["entry"],
        "entry_price": pkg["entry"],
        "stop_loss": pkg["sl"],
        "take_profit": pkg["tp"],
        "pattern": "spy_trend_long_1d",
        "meta": {
            **pkg_meta,
            "strategy_name": "spy_trend_long_1d",
            "confidence": pkg["confidence"],
            "direction": pkg["direction"],
        },
    }
    _emit_shadow_preds("spy_trend_long_1d", sig, cfg_yaml, symbol, timeframe=timeframe, candles_df=candles_df)
    _stamp_regime_on_meta(sig.setdefault("meta", {}), candles_df, symbol=symbol, timeframe=timeframe)
    return _with_signal_package("spy_trend_long_1d", sig)


def iwm_trend_long_1d_signal_builder(settings: dict) -> Dict[str, Any]:
    """IWM daily LONG-ONLY trend-follower (ETF-breadth daily sweep 2026-06-20).

    Russell-2000 small-cap ETF trend leg — the small-cap sibling of
    spy_trend_long_1d / qqq_trend_long_1d. The only ``live_ready`` /
    every-fold cell in the ETF-breadth daily sweep (2026-06-20). Runs on the
    ``alpaca_paper`` account (PAPER money; bracket orders carry broker-side
    SL/TP). Reuses the ``src.units.strategies.trend_donchian.order_package``
    unit. LONG-ONLY (shorts suppressed).

    US-SESSION GATE: evaluation is skipped outside the US cash session
    (``market_hours.is_market_open("us_equity")`` → side=none, reason
    ``us_market_closed``) so closed-market candles never produce
    entries. Fail-permissive on gate errors. Honours the YAML
    ``enabled`` flag as the single source of truth.
    """
    from src.units.strategies import load_strategy_config
    from src.units.strategies.trend_donchian import order_package
    from src.runtime.market_data import fetch_candles
    from src.runtime.market_hours import is_market_open

    try:
        strategies_cfg = load_strategy_config()
    except Exception:  # noqa: BLE001 - never fail-open on a config error
        strategies_cfg = {}
    cfg_yaml = strategies_cfg.get("iwm_trend_long_1d", {}) or {}

    symbol = settings.get("SYMBOL", settings.get("symbol", "IWM"))

    if not bool(cfg_yaml.get("enabled", False)):
        logger.info("iwm_trend_long_1d: strategy disabled in config/strategies.yaml - returning side=none")
        return _with_signal_package("iwm_trend_long_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "iwm_trend_long_1d", "reason": "disabled_in_yaml"},
        })

    try:
        us_open = is_market_open("us_equity")
    except Exception:  # noqa: BLE001 — gate is best-effort, never strands
        us_open = True
    if not us_open:
        logger.info("iwm_trend_long_1d: US market closed - side=none")
        return _with_signal_package("iwm_trend_long_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "iwm_trend_long_1d", "reason": "us_market_closed"},
        })

    timeframe = str(cfg_yaml.get("timeframe") or "1d")

    exchange = _build_killzone_exchange(settings)
    candles_df = fetch_candles(symbol, timeframe, exchange_client=exchange, limit=200)
    if candles_df is None:
        raise RuntimeError(
            f"iwm_trend_long_1d: no candle data returned for symbol={symbol} "
            f"timeframe={timeframe}. Check that the Alpaca connection is "
            "configured and the symbol is valid."
        )

    _publish_liquidity_state(symbol, candles_df)

    cfg: Dict[str, Any] = {"symbol": symbol, "timeframe": timeframe, **cfg_yaml}
    cfg["strategy_label"] = "iwm_trend_long_1d"

    try:
        pkg = order_package(cfg, candles_df=candles_df)
    except ValueError as exc:
        logger.info("iwm_trend_long_1d: no actionable signal (%s)", exc)
        try:
            log_signal(_stamp_regime({
                "event": "iwm_trend_long_1d_eval",
                "strategy": "iwm_trend_long_1d",
                "symbol": symbol,
                "timeframe": timeframe,
                "side": "none",
                "reason": str(exc),
            }, candles_df))
        except Exception:  # noqa: BLE001
            logger.exception("iwm_trend_long_1d: dedicated audit emit failed")
        return _with_signal_package("iwm_trend_long_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "iwm_trend_long_1d", "reason": str(exc)},
        })

    # LONG-ONLY gate: the equity-index edge is the long side (mirrors
    # spy_trend_long_1d; the validated short side was net-negative).
    if pkg["direction"] != "long":
        logger.info("iwm_trend_long_1d: short signal suppressed (long-only strategy)")
        try:
            log_signal(_stamp_regime({
                "event": "iwm_trend_long_1d_eval",
                "strategy": "iwm_trend_long_1d",
                "symbol": symbol,
                "timeframe": timeframe,
                "side": "none",
                "reason": "short_suppressed_long_only",
            }, candles_df))
        except Exception:  # noqa: BLE001
            logger.exception("iwm_trend_long_1d: dedicated audit emit failed")
        return _with_signal_package("iwm_trend_long_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "iwm_trend_long_1d", "reason": "short_suppressed_long_only"},
        })

    side = "buy" if pkg["direction"] == "long" else "sell"
    logger.info(
        "iwm_trend_long_1d: %s signal at %s (entry=%s sl=%s tp=%s confidence=%.3f)",
        side, symbol, pkg["entry"], pkg["sl"], pkg["tp"], pkg["confidence"],
    )
    pkg_meta = pkg.get("meta") or {}
    try:
        log_signal(_stamp_regime({
            "event": "iwm_trend_long_1d_eval",
            "strategy": "iwm_trend_long_1d",
            "symbol": symbol,
            "timeframe": timeframe,
            "side": side,
            "entry": pkg["entry"],
            "stop_loss": pkg["sl"],
            "take_profit": pkg["tp"],
            "confidence": pkg["confidence"],
        }, candles_df))
    except Exception:  # noqa: BLE001
        logger.exception("iwm_trend_long_1d: dedicated audit emit failed")

    sig = {
        "symbol": symbol,
        "side": side,
        "price": pkg["entry"],
        "entry_price": pkg["entry"],
        "stop_loss": pkg["sl"],
        "take_profit": pkg["tp"],
        "pattern": "iwm_trend_long_1d",
        "meta": {
            **pkg_meta,
            "strategy_name": "iwm_trend_long_1d",
            "confidence": pkg["confidence"],
            "direction": pkg["direction"],
        },
    }
    _emit_shadow_preds("iwm_trend_long_1d", sig, cfg_yaml, symbol, timeframe=timeframe, candles_df=candles_df)
    _stamp_regime_on_meta(sig.setdefault("meta", {}), candles_df, symbol=symbol, timeframe=timeframe)
    return _with_signal_package("iwm_trend_long_1d", sig)


def splg_trend_long_1d_signal_builder(settings: dict) -> Dict[str, Any]:
    """SPLG daily LONG-ONLY trend-follower (cheap-share SPY proxy).

    SPDR Portfolio S&P 500 ETF (SPLG) trend leg — a cheap-share (~$84) proxy
    for SPY, the S&P-500 sibling of spy_trend_long_1d / iwm_trend_long_1d.
    Runs on the ``alpaca_paper`` account (PAPER money; bracket orders carry
    broker-side SL/TP). Reuses the
    ``src.units.strategies.trend_donchian.order_package`` unit. LONG-ONLY
    (shorts suppressed).

    US-SESSION GATE: evaluation is skipped outside the US cash session
    (``market_hours.is_market_open("us_equity")`` → side=none, reason
    ``us_market_closed``) so closed-market candles never produce
    entries. Fail-permissive on gate errors. Honours the YAML
    ``enabled`` flag as the single source of truth.
    """
    from src.units.strategies import load_strategy_config
    from src.units.strategies.trend_donchian import order_package
    from src.runtime.market_data import fetch_candles
    from src.runtime.market_hours import is_market_open

    try:
        strategies_cfg = load_strategy_config()
    except Exception:  # noqa: BLE001 - never fail-open on a config error
        strategies_cfg = {}
    cfg_yaml = strategies_cfg.get("splg_trend_long_1d", {}) or {}

    symbol = settings.get("SYMBOL", settings.get("symbol", "SPLG"))

    if not bool(cfg_yaml.get("enabled", False)):
        logger.info("splg_trend_long_1d: strategy disabled in config/strategies.yaml - returning side=none")
        return _with_signal_package("splg_trend_long_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "splg_trend_long_1d", "reason": "disabled_in_yaml"},
        })

    try:
        us_open = is_market_open("us_equity")
    except Exception:  # noqa: BLE001 — gate is best-effort, never strands
        us_open = True
    if not us_open:
        logger.info("splg_trend_long_1d: US market closed - side=none")
        return _with_signal_package("splg_trend_long_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "splg_trend_long_1d", "reason": "us_market_closed"},
        })

    timeframe = str(cfg_yaml.get("timeframe") or "1d")

    exchange = _build_killzone_exchange(settings)
    candles_df = fetch_candles(symbol, timeframe, exchange_client=exchange, limit=200)
    if candles_df is None:
        raise RuntimeError(
            f"splg_trend_long_1d: no candle data returned for symbol={symbol} "
            f"timeframe={timeframe}. Check that the Alpaca connection is "
            "configured and the symbol is valid."
        )

    _publish_liquidity_state(symbol, candles_df)

    cfg: Dict[str, Any] = {"symbol": symbol, "timeframe": timeframe, **cfg_yaml}
    cfg["strategy_label"] = "splg_trend_long_1d"

    try:
        pkg = order_package(cfg, candles_df=candles_df)
    except ValueError as exc:
        logger.info("splg_trend_long_1d: no actionable signal (%s)", exc)
        try:
            log_signal(_stamp_regime({
                "event": "splg_trend_long_1d_eval",
                "strategy": "splg_trend_long_1d",
                "symbol": symbol,
                "timeframe": timeframe,
                "side": "none",
                "reason": str(exc),
            }, candles_df))
        except Exception:  # noqa: BLE001
            logger.exception("splg_trend_long_1d: dedicated audit emit failed")
        return _with_signal_package("splg_trend_long_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "splg_trend_long_1d", "reason": str(exc)},
        })

    # LONG-ONLY gate: the equity-index edge is the long side (mirrors
    # spy_trend_long_1d; the validated short side was net-negative).
    if pkg["direction"] != "long":
        logger.info("splg_trend_long_1d: short signal suppressed (long-only strategy)")
        try:
            log_signal(_stamp_regime({
                "event": "splg_trend_long_1d_eval",
                "strategy": "splg_trend_long_1d",
                "symbol": symbol,
                "timeframe": timeframe,
                "side": "none",
                "reason": "short_suppressed_long_only",
            }, candles_df))
        except Exception:  # noqa: BLE001
            logger.exception("splg_trend_long_1d: dedicated audit emit failed")
        return _with_signal_package("splg_trend_long_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "splg_trend_long_1d", "reason": "short_suppressed_long_only"},
        })

    side = "buy" if pkg["direction"] == "long" else "sell"
    logger.info(
        "splg_trend_long_1d: %s signal at %s (entry=%s sl=%s tp=%s confidence=%.3f)",
        side, symbol, pkg["entry"], pkg["sl"], pkg["tp"], pkg["confidence"],
    )
    pkg_meta = pkg.get("meta") or {}
    try:
        log_signal(_stamp_regime({
            "event": "splg_trend_long_1d_eval",
            "strategy": "splg_trend_long_1d",
            "symbol": symbol,
            "timeframe": timeframe,
            "side": side,
            "entry": pkg["entry"],
            "stop_loss": pkg["sl"],
            "take_profit": pkg["tp"],
            "confidence": pkg["confidence"],
        }, candles_df))
    except Exception:  # noqa: BLE001
        logger.exception("splg_trend_long_1d: dedicated audit emit failed")

    sig = {
        "symbol": symbol,
        "side": side,
        "price": pkg["entry"],
        "entry_price": pkg["entry"],
        "stop_loss": pkg["sl"],
        "take_profit": pkg["tp"],
        "pattern": "splg_trend_long_1d",
        "meta": {
            **pkg_meta,
            "strategy_name": "splg_trend_long_1d",
            "confidence": pkg["confidence"],
            "direction": pkg["direction"],
        },
    }
    _emit_shadow_preds("splg_trend_long_1d", sig, cfg_yaml, symbol, timeframe=timeframe, candles_df=candles_df)
    _stamp_regime_on_meta(sig.setdefault("meta", {}), candles_df, symbol=symbol, timeframe=timeframe)
    return _with_signal_package("splg_trend_long_1d", sig)


def scha_trend_long_1d_signal_builder(settings: dict) -> Dict[str, Any]:
    """SCHA daily LONG-ONLY trend-follower (cheap-share IWM proxy).

    Schwab US Small-Cap ETF (SCHA) trend leg — a cheap-share (~$35) proxy for
    IWM, a small-cap sibling of iwm_trend_long_1d. Observe-only paper soak:
    the edge is marginal (backtest +9.8R / +0.19R expectancy vs IWM +12.3R).
    Runs on the ``alpaca_paper`` account (PAPER money; bracket orders carry
    broker-side SL/TP). Reuses the
    ``src.units.strategies.trend_donchian.order_package`` unit. LONG-ONLY
    (shorts suppressed).

    US-SESSION GATE: evaluation is skipped outside the US cash session
    (``market_hours.is_market_open("us_equity")`` → side=none, reason
    ``us_market_closed``) so closed-market candles never produce
    entries. Fail-permissive on gate errors. Honours the YAML
    ``enabled`` flag as the single source of truth.
    """
    from src.units.strategies import load_strategy_config
    from src.units.strategies.trend_donchian import order_package
    from src.runtime.market_data import fetch_candles
    from src.runtime.market_hours import is_market_open

    try:
        strategies_cfg = load_strategy_config()
    except Exception:  # noqa: BLE001 - never fail-open on a config error
        strategies_cfg = {}
    cfg_yaml = strategies_cfg.get("scha_trend_long_1d", {}) or {}

    symbol = settings.get("SYMBOL", settings.get("symbol", "SCHA"))

    if not bool(cfg_yaml.get("enabled", False)):
        logger.info("scha_trend_long_1d: strategy disabled in config/strategies.yaml - returning side=none")
        return _with_signal_package("scha_trend_long_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "scha_trend_long_1d", "reason": "disabled_in_yaml"},
        })

    try:
        us_open = is_market_open("us_equity")
    except Exception:  # noqa: BLE001 — gate is best-effort, never strands
        us_open = True
    if not us_open:
        logger.info("scha_trend_long_1d: US market closed - side=none")
        return _with_signal_package("scha_trend_long_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "scha_trend_long_1d", "reason": "us_market_closed"},
        })

    timeframe = str(cfg_yaml.get("timeframe") or "1d")

    exchange = _build_killzone_exchange(settings)
    candles_df = fetch_candles(symbol, timeframe, exchange_client=exchange, limit=200)
    if candles_df is None:
        raise RuntimeError(
            f"scha_trend_long_1d: no candle data returned for symbol={symbol} "
            f"timeframe={timeframe}. Check that the Alpaca connection is "
            "configured and the symbol is valid."
        )

    _publish_liquidity_state(symbol, candles_df)

    cfg: Dict[str, Any] = {"symbol": symbol, "timeframe": timeframe, **cfg_yaml}
    cfg["strategy_label"] = "scha_trend_long_1d"

    try:
        pkg = order_package(cfg, candles_df=candles_df)
    except ValueError as exc:
        logger.info("scha_trend_long_1d: no actionable signal (%s)", exc)
        try:
            log_signal(_stamp_regime({
                "event": "scha_trend_long_1d_eval",
                "strategy": "scha_trend_long_1d",
                "symbol": symbol,
                "timeframe": timeframe,
                "side": "none",
                "reason": str(exc),
            }, candles_df))
        except Exception:  # noqa: BLE001
            logger.exception("scha_trend_long_1d: dedicated audit emit failed")
        return _with_signal_package("scha_trend_long_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "scha_trend_long_1d", "reason": str(exc)},
        })

    # LONG-ONLY gate: the equity-index edge is the long side (mirrors
    # spy_trend_long_1d; the validated short side was net-negative).
    if pkg["direction"] != "long":
        logger.info("scha_trend_long_1d: short signal suppressed (long-only strategy)")
        try:
            log_signal(_stamp_regime({
                "event": "scha_trend_long_1d_eval",
                "strategy": "scha_trend_long_1d",
                "symbol": symbol,
                "timeframe": timeframe,
                "side": "none",
                "reason": "short_suppressed_long_only",
            }, candles_df))
        except Exception:  # noqa: BLE001
            logger.exception("scha_trend_long_1d: dedicated audit emit failed")
        return _with_signal_package("scha_trend_long_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "scha_trend_long_1d", "reason": "short_suppressed_long_only"},
        })

    side = "buy" if pkg["direction"] == "long" else "sell"
    logger.info(
        "scha_trend_long_1d: %s signal at %s (entry=%s sl=%s tp=%s confidence=%.3f)",
        side, symbol, pkg["entry"], pkg["sl"], pkg["tp"], pkg["confidence"],
    )
    pkg_meta = pkg.get("meta") or {}
    try:
        log_signal(_stamp_regime({
            "event": "scha_trend_long_1d_eval",
            "strategy": "scha_trend_long_1d",
            "symbol": symbol,
            "timeframe": timeframe,
            "side": side,
            "entry": pkg["entry"],
            "stop_loss": pkg["sl"],
            "take_profit": pkg["tp"],
            "confidence": pkg["confidence"],
        }, candles_df))
    except Exception:  # noqa: BLE001
        logger.exception("scha_trend_long_1d: dedicated audit emit failed")

    sig = {
        "symbol": symbol,
        "side": side,
        "price": pkg["entry"],
        "entry_price": pkg["entry"],
        "stop_loss": pkg["sl"],
        "take_profit": pkg["tp"],
        "pattern": "scha_trend_long_1d",
        "meta": {
            **pkg_meta,
            "strategy_name": "scha_trend_long_1d",
            "confidence": pkg["confidence"],
            "direction": pkg["direction"],
        },
    }
    _emit_shadow_preds("scha_trend_long_1d", sig, cfg_yaml, symbol, timeframe=timeframe, candles_df=candles_df)
    _stamp_regime_on_meta(sig.setdefault("meta", {}), candles_df, symbol=symbol, timeframe=timeframe)
    return _with_signal_package("scha_trend_long_1d", sig)


def qqq_trend_long_1d_signal_builder(settings: dict) -> Dict[str, Any]:
    """QQQ daily LONG-ONLY trend-follower (M15 Phase 4 buildout, S-M15-PHASE4).

    Equity-index ETF diversifier, mes_trend_long_1d mirror (Phase-0: train +16.2R / OOS +10.9R). Runs on the ``alpaca_paper`` account (PAPER money; bracket
    orders carry broker-side SL/TP). Reuses the
    ``src.units.strategies.trend_donchian.order_package`` unit. LONG-ONLY (shorts suppressed).

    US-SESSION GATE: evaluation is skipped outside the US cash session
    (``market_hours.is_market_open("us_equity")`` → side=none, reason
    ``us_market_closed``) so closed-market candles never produce
    entries. Fail-permissive on gate errors. Honours the YAML
    ``enabled`` flag as the single source of truth.
    """
    from src.units.strategies import load_strategy_config
    from src.units.strategies.trend_donchian import order_package
    from src.runtime.market_data import fetch_candles
    from src.runtime.market_hours import is_market_open

    try:
        strategies_cfg = load_strategy_config()
    except Exception:  # noqa: BLE001 - never fail-open on a config error
        strategies_cfg = {}
    cfg_yaml = strategies_cfg.get("qqq_trend_long_1d", {}) or {}

    symbol = settings.get("SYMBOL", settings.get("symbol", "QQQ"))

    if not bool(cfg_yaml.get("enabled", False)):
        logger.info("qqq_trend_long_1d: strategy disabled in config/strategies.yaml - returning side=none")
        return _with_signal_package("qqq_trend_long_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "qqq_trend_long_1d", "reason": "disabled_in_yaml"},
        })

    try:
        us_open = is_market_open("us_equity")
    except Exception:  # noqa: BLE001 — gate is best-effort, never strands
        us_open = True
    if not us_open:
        logger.info("qqq_trend_long_1d: US market closed - side=none")
        return _with_signal_package("qqq_trend_long_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "qqq_trend_long_1d", "reason": "us_market_closed"},
        })

    timeframe = str(cfg_yaml.get("timeframe") or "1d")

    exchange = _build_killzone_exchange(settings)
    candles_df = fetch_candles(symbol, timeframe, exchange_client=exchange, limit=200)
    if candles_df is None:
        raise RuntimeError(
            f"qqq_trend_long_1d: no candle data returned for symbol={symbol} "
            f"timeframe={timeframe}. Check that the Alpaca connection is "
            "configured and the symbol is valid."
        )

    _publish_liquidity_state(symbol, candles_df)

    cfg: Dict[str, Any] = {"symbol": symbol, "timeframe": timeframe, **cfg_yaml}
    cfg["strategy_label"] = "qqq_trend_long_1d"

    try:
        pkg = order_package(cfg, candles_df=candles_df)
    except ValueError as exc:
        logger.info("qqq_trend_long_1d: no actionable signal (%s)", exc)
        try:
            log_signal(_stamp_regime({
                "event": "qqq_trend_long_1d_eval",
                "strategy": "qqq_trend_long_1d",
                "symbol": symbol,
                "timeframe": timeframe,
                "side": "none",
                "reason": str(exc),
            }, candles_df))
        except Exception:  # noqa: BLE001
            logger.exception("qqq_trend_long_1d: dedicated audit emit failed")
        return _with_signal_package("qqq_trend_long_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "qqq_trend_long_1d", "reason": str(exc)},
        })

    # LONG-ONLY gate: the equity-index edge is the long side (mirrors
    # mes_trend_long_1d; the validated short side was net-negative).
    if pkg["direction"] != "long":
        logger.info("qqq_trend_long_1d: short signal suppressed (long-only strategy)")
        try:
            log_signal(_stamp_regime({
                "event": "qqq_trend_long_1d_eval",
                "strategy": "qqq_trend_long_1d",
                "symbol": symbol,
                "timeframe": timeframe,
                "side": "none",
                "reason": "short_suppressed_long_only",
            }, candles_df))
        except Exception:  # noqa: BLE001
            logger.exception("qqq_trend_long_1d: dedicated audit emit failed")
        return _with_signal_package("qqq_trend_long_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "qqq_trend_long_1d", "reason": "short_suppressed_long_only"},
        })

    side = "buy" if pkg["direction"] == "long" else "sell"
    logger.info(
        "qqq_trend_long_1d: %s signal at %s (entry=%s sl=%s tp=%s confidence=%.3f)",
        side, symbol, pkg["entry"], pkg["sl"], pkg["tp"], pkg["confidence"],
    )
    pkg_meta = pkg.get("meta") or {}
    try:
        log_signal(_stamp_regime({
            "event": "qqq_trend_long_1d_eval",
            "strategy": "qqq_trend_long_1d",
            "symbol": symbol,
            "timeframe": timeframe,
            "side": side,
            "entry": pkg["entry"],
            "stop_loss": pkg["sl"],
            "take_profit": pkg["tp"],
            "confidence": pkg["confidence"],
        }, candles_df))
    except Exception:  # noqa: BLE001
        logger.exception("qqq_trend_long_1d: dedicated audit emit failed")

    sig = {
        "symbol": symbol,
        "side": side,
        "price": pkg["entry"],
        "entry_price": pkg["entry"],
        "stop_loss": pkg["sl"],
        "take_profit": pkg["tp"],
        "pattern": "qqq_trend_long_1d",
        "meta": {
            **pkg_meta,
            "strategy_name": "qqq_trend_long_1d",
            "confidence": pkg["confidence"],
            "direction": pkg["direction"],
        },
    }
    _emit_shadow_preds("qqq_trend_long_1d", sig, cfg_yaml, symbol, timeframe=timeframe, candles_df=candles_df)
    _stamp_regime_on_meta(sig.setdefault("meta", {}), candles_df, symbol=symbol, timeframe=timeframe)
    return _with_signal_package("qqq_trend_long_1d", sig)


def tqqq_trend_long_1d_signal_builder(settings: dict) -> Dict[str, Any]:
    """TQQQ (3x Nasdaq-100) daily LONG-ONLY trend-follower.

    Leveraged-ETF sibling of ``qqq_trend_long_1d`` — same validated
    ``trend_donchian`` unit, same params (donchian 30 / atr-stop 2.5 /
    trail 4.0), same US-session gate, LONG-ONLY. TQQQ tracks 3x the daily
    Nasdaq-100; backtested on the ACTUAL TQQQ price series (which embeds
    leverage decay + the ~0.95% expense ratio): net +13.8R OOS 2019-2026,
    `paper_ready`, beat the QQQ cell — the Donchian trend filter sidesteps
    the high-vol chop where leveraged decay bites (docs/research/
    leveraged-etf-research-2026-06-30.md). Runs on ``alpaca_paper`` (PAPER
    money), bracket orders carry broker-side SL/TP. Honours the YAML
    ``enabled`` flag.
    """
    from src.units.strategies import load_strategy_config
    from src.units.strategies.trend_donchian import order_package
    from src.runtime.market_data import fetch_candles
    from src.runtime.market_hours import is_market_open

    try:
        strategies_cfg = load_strategy_config()
    except Exception:  # noqa: BLE001 - never fail-open on a config error
        strategies_cfg = {}
    cfg_yaml = strategies_cfg.get("tqqq_trend_long_1d", {}) or {}

    symbol = settings.get("SYMBOL", settings.get("symbol", "TQQQ"))

    if not bool(cfg_yaml.get("enabled", False)):
        logger.info("tqqq_trend_long_1d: strategy disabled in config/strategies.yaml - returning side=none")
        return _with_signal_package("tqqq_trend_long_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "tqqq_trend_long_1d", "reason": "disabled_in_yaml"},
        })

    try:
        us_open = is_market_open("us_equity")
    except Exception:  # noqa: BLE001 — gate is best-effort, never strands
        us_open = True
    if not us_open:
        logger.info("tqqq_trend_long_1d: US market closed - side=none")
        return _with_signal_package("tqqq_trend_long_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "tqqq_trend_long_1d", "reason": "us_market_closed"},
        })

    timeframe = str(cfg_yaml.get("timeframe") or "1d")

    exchange = _build_killzone_exchange(settings)
    candles_df = fetch_candles(symbol, timeframe, exchange_client=exchange, limit=200)
    if candles_df is None:
        raise RuntimeError(
            f"tqqq_trend_long_1d: no candle data returned for symbol={symbol} "
            f"timeframe={timeframe}. Check that the Alpaca connection is "
            "configured and the symbol is valid."
        )

    _publish_liquidity_state(symbol, candles_df)

    cfg: Dict[str, Any] = {"symbol": symbol, "timeframe": timeframe, **cfg_yaml}
    cfg["strategy_label"] = "tqqq_trend_long_1d"

    try:
        pkg = order_package(cfg, candles_df=candles_df)
    except ValueError as exc:
        logger.info("tqqq_trend_long_1d: no actionable signal (%s)", exc)
        try:
            log_signal(_stamp_regime({
                "event": "tqqq_trend_long_1d_eval",
                "strategy": "tqqq_trend_long_1d",
                "symbol": symbol,
                "timeframe": timeframe,
                "side": "none",
                "reason": str(exc),
            }, candles_df))
        except Exception:  # noqa: BLE001
            logger.exception("tqqq_trend_long_1d: dedicated audit emit failed")
        return _with_signal_package("tqqq_trend_long_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "tqqq_trend_long_1d", "reason": str(exc)},
        })

    if pkg["direction"] != "long":
        logger.info("tqqq_trend_long_1d: short signal suppressed (long-only strategy)")
        try:
            log_signal(_stamp_regime({
                "event": "tqqq_trend_long_1d_eval",
                "strategy": "tqqq_trend_long_1d",
                "symbol": symbol,
                "timeframe": timeframe,
                "side": "none",
                "reason": "short_suppressed_long_only",
            }, candles_df))
        except Exception:  # noqa: BLE001
            logger.exception("tqqq_trend_long_1d: dedicated audit emit failed")
        return _with_signal_package("tqqq_trend_long_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "tqqq_trend_long_1d", "reason": "short_suppressed_long_only"},
        })

    side = "buy" if pkg["direction"] == "long" else "sell"
    logger.info(
        "tqqq_trend_long_1d: %s signal at %s (entry=%s sl=%s tp=%s confidence=%.3f)",
        side, symbol, pkg["entry"], pkg["sl"], pkg["tp"], pkg["confidence"],
    )
    pkg_meta = pkg.get("meta") or {}
    try:
        log_signal(_stamp_regime({
            "event": "tqqq_trend_long_1d_eval",
            "strategy": "tqqq_trend_long_1d",
            "symbol": symbol,
            "timeframe": timeframe,
            "side": side,
            "entry": pkg["entry"],
            "stop_loss": pkg["sl"],
            "take_profit": pkg["tp"],
            "confidence": pkg["confidence"],
        }, candles_df))
    except Exception:  # noqa: BLE001
        logger.exception("tqqq_trend_long_1d: dedicated audit emit failed")

    sig = {
        "symbol": symbol,
        "side": side,
        "price": pkg["entry"],
        "entry_price": pkg["entry"],
        "stop_loss": pkg["sl"],
        "take_profit": pkg["tp"],
        "pattern": "tqqq_trend_long_1d",
        "meta": {
            **pkg_meta,
            "strategy_name": "tqqq_trend_long_1d",
            "confidence": pkg["confidence"],
            "direction": pkg["direction"],
        },
    }
    _emit_shadow_preds("tqqq_trend_long_1d", sig, cfg_yaml, symbol, timeframe=timeframe, candles_df=candles_df)
    _stamp_regime_on_meta(sig.setdefault("meta", {}), candles_df, symbol=symbol, timeframe=timeframe)
    return _with_signal_package("tqqq_trend_long_1d", sig)


def qld_trend_long_1d_signal_builder(settings: dict) -> Dict[str, Any]:
    """QLD (2x Nasdaq-100) daily LONG-ONLY trend-follower.

    2x-leveraged sibling of ``qqq_trend_long_1d`` / ``tqqq_trend_long_1d`` —
    same ``trend_donchian`` unit + params, US-session gate, LONG-ONLY. QLD
    tracks 2x the daily Nasdaq-100 (lower decay than the 3x TQQQ).
    Backtested on the actual QLD price series: net +12.7R OOS 2019-2026,
    `paper_ready` (docs/research/leveraged-etf-research-2026-06-30.md).
    Runs on ``alpaca_paper`` (PAPER money). Honours the YAML ``enabled`` flag.
    """
    from src.units.strategies import load_strategy_config
    from src.units.strategies.trend_donchian import order_package
    from src.runtime.market_data import fetch_candles
    from src.runtime.market_hours import is_market_open

    try:
        strategies_cfg = load_strategy_config()
    except Exception:  # noqa: BLE001 - never fail-open on a config error
        strategies_cfg = {}
    cfg_yaml = strategies_cfg.get("qld_trend_long_1d", {}) or {}

    symbol = settings.get("SYMBOL", settings.get("symbol", "QLD"))

    if not bool(cfg_yaml.get("enabled", False)):
        logger.info("qld_trend_long_1d: strategy disabled in config/strategies.yaml - returning side=none")
        return _with_signal_package("qld_trend_long_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "qld_trend_long_1d", "reason": "disabled_in_yaml"},
        })

    try:
        us_open = is_market_open("us_equity")
    except Exception:  # noqa: BLE001 — gate is best-effort, never strands
        us_open = True
    if not us_open:
        logger.info("qld_trend_long_1d: US market closed - side=none")
        return _with_signal_package("qld_trend_long_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "qld_trend_long_1d", "reason": "us_market_closed"},
        })

    timeframe = str(cfg_yaml.get("timeframe") or "1d")

    exchange = _build_killzone_exchange(settings)
    candles_df = fetch_candles(symbol, timeframe, exchange_client=exchange, limit=200)
    if candles_df is None:
        raise RuntimeError(
            f"qld_trend_long_1d: no candle data returned for symbol={symbol} "
            f"timeframe={timeframe}. Check that the Alpaca connection is "
            "configured and the symbol is valid."
        )

    _publish_liquidity_state(symbol, candles_df)

    cfg: Dict[str, Any] = {"symbol": symbol, "timeframe": timeframe, **cfg_yaml}
    cfg["strategy_label"] = "qld_trend_long_1d"

    try:
        pkg = order_package(cfg, candles_df=candles_df)
    except ValueError as exc:
        logger.info("qld_trend_long_1d: no actionable signal (%s)", exc)
        try:
            log_signal(_stamp_regime({
                "event": "qld_trend_long_1d_eval",
                "strategy": "qld_trend_long_1d",
                "symbol": symbol,
                "timeframe": timeframe,
                "side": "none",
                "reason": str(exc),
            }, candles_df))
        except Exception:  # noqa: BLE001
            logger.exception("qld_trend_long_1d: dedicated audit emit failed")
        return _with_signal_package("qld_trend_long_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "qld_trend_long_1d", "reason": str(exc)},
        })

    if pkg["direction"] != "long":
        logger.info("qld_trend_long_1d: short signal suppressed (long-only strategy)")
        try:
            log_signal(_stamp_regime({
                "event": "qld_trend_long_1d_eval",
                "strategy": "qld_trend_long_1d",
                "symbol": symbol,
                "timeframe": timeframe,
                "side": "none",
                "reason": "short_suppressed_long_only",
            }, candles_df))
        except Exception:  # noqa: BLE001
            logger.exception("qld_trend_long_1d: dedicated audit emit failed")
        return _with_signal_package("qld_trend_long_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "qld_trend_long_1d", "reason": "short_suppressed_long_only"},
        })

    side = "buy" if pkg["direction"] == "long" else "sell"
    logger.info(
        "qld_trend_long_1d: %s signal at %s (entry=%s sl=%s tp=%s confidence=%.3f)",
        side, symbol, pkg["entry"], pkg["sl"], pkg["tp"], pkg["confidence"],
    )
    pkg_meta = pkg.get("meta") or {}
    try:
        log_signal(_stamp_regime({
            "event": "qld_trend_long_1d_eval",
            "strategy": "qld_trend_long_1d",
            "symbol": symbol,
            "timeframe": timeframe,
            "side": side,
            "entry": pkg["entry"],
            "stop_loss": pkg["sl"],
            "take_profit": pkg["tp"],
            "confidence": pkg["confidence"],
        }, candles_df))
    except Exception:  # noqa: BLE001
        logger.exception("qld_trend_long_1d: dedicated audit emit failed")

    sig = {
        "symbol": symbol,
        "side": side,
        "price": pkg["entry"],
        "entry_price": pkg["entry"],
        "stop_loss": pkg["sl"],
        "take_profit": pkg["tp"],
        "pattern": "qld_trend_long_1d",
        "meta": {
            **pkg_meta,
            "strategy_name": "qld_trend_long_1d",
            "confidence": pkg["confidence"],
            "direction": pkg["direction"],
        },
    }
    _emit_shadow_preds("qld_trend_long_1d", sig, cfg_yaml, symbol, timeframe=timeframe, candles_df=candles_df)
    _stamp_regime_on_meta(sig.setdefault("meta", {}), candles_df, symbol=symbol, timeframe=timeframe)
    return _with_signal_package("qld_trend_long_1d", sig)


def gld_pullback_1d_signal_builder(settings: dict) -> Dict[str, Any]:
    """GLD daily HTF-pullback (bidirectional) (M15 Phase 4 buildout, S-M15-PHASE4).

    The ETF replacement for the MGC futures leg (Phase-0: train +4.9R / OOS +19.7R). Runs on the ``alpaca_paper`` account (PAPER money; bracket
    orders carry broker-side SL/TP). Reuses the
    ``src.units.strategies.htf_pullback_trend_2h.order_package`` unit.

    US-SESSION GATE: evaluation is skipped outside the US cash session
    (``market_hours.is_market_open("us_equity")`` → side=none, reason
    ``us_market_closed``) so closed-market candles never produce
    entries. Fail-permissive on gate errors. Honours the YAML
    ``enabled`` flag as the single source of truth.
    """
    from src.units.strategies import load_strategy_config
    from src.units.strategies.htf_pullback_trend_2h import order_package
    from src.runtime.market_data import fetch_candles
    from src.runtime.market_hours import is_market_open

    try:
        strategies_cfg = load_strategy_config()
    except Exception:  # noqa: BLE001 - never fail-open on a config error
        strategies_cfg = {}
    cfg_yaml = strategies_cfg.get("gld_pullback_1d", {}) or {}

    symbol = settings.get("SYMBOL", settings.get("symbol", "GLD"))

    if not bool(cfg_yaml.get("enabled", False)):
        logger.info("gld_pullback_1d: strategy disabled in config/strategies.yaml - returning side=none")
        return _with_signal_package("gld_pullback_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "gld_pullback_1d", "reason": "disabled_in_yaml"},
        })

    try:
        us_open = is_market_open("us_equity")
    except Exception:  # noqa: BLE001 — gate is best-effort, never strands
        us_open = True
    if not us_open:
        logger.info("gld_pullback_1d: US market closed - side=none")
        return _with_signal_package("gld_pullback_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "gld_pullback_1d", "reason": "us_market_closed"},
        })

    timeframe = str(cfg_yaml.get("timeframe") or "1d")

    exchange = _build_killzone_exchange(settings)
    candles_df = fetch_candles(symbol, timeframe, exchange_client=exchange, limit=200)
    if candles_df is None:
        raise RuntimeError(
            f"gld_pullback_1d: no candle data returned for symbol={symbol} "
            f"timeframe={timeframe}. Check that the Alpaca connection is "
            "configured and the symbol is valid."
        )

    _publish_liquidity_state(symbol, candles_df)

    cfg: Dict[str, Any] = {"symbol": symbol, "timeframe": timeframe, **cfg_yaml}
    cfg["strategy_label"] = "gld_pullback_1d"

    try:
        pkg = order_package(cfg, candles_df=candles_df)
    except ValueError as exc:
        logger.info("gld_pullback_1d: no actionable signal (%s)", exc)
        try:
            log_signal(_stamp_regime({
                "event": "gld_pullback_1d_eval",
                "strategy": "gld_pullback_1d",
                "symbol": symbol,
                "timeframe": timeframe,
                "side": "none",
                "reason": str(exc),
            }, candles_df))
        except Exception:  # noqa: BLE001
            logger.exception("gld_pullback_1d: dedicated audit emit failed")
        return _with_signal_package("gld_pullback_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "gld_pullback_1d", "reason": str(exc)},
        })

    side = "buy" if pkg["direction"] == "long" else "sell"
    logger.info(
        "gld_pullback_1d: %s signal at %s (entry=%s sl=%s tp=%s confidence=%.3f)",
        side, symbol, pkg["entry"], pkg["sl"], pkg["tp"], pkg["confidence"],
    )
    pkg_meta = pkg.get("meta") or {}
    try:
        log_signal(_stamp_regime({
            "event": "gld_pullback_1d_eval",
            "strategy": "gld_pullback_1d",
            "symbol": symbol,
            "timeframe": timeframe,
            "side": side,
            "entry": pkg["entry"],
            "stop_loss": pkg["sl"],
            "take_profit": pkg["tp"],
            "confidence": pkg["confidence"],
        }, candles_df))
    except Exception:  # noqa: BLE001
        logger.exception("gld_pullback_1d: dedicated audit emit failed")

    sig = {
        "symbol": symbol,
        "side": side,
        "price": pkg["entry"],
        "entry_price": pkg["entry"],
        "stop_loss": pkg["sl"],
        "take_profit": pkg["tp"],
        "pattern": "gld_pullback_1d",
        "meta": {
            **pkg_meta,
            "strategy_name": "gld_pullback_1d",
            "confidence": pkg["confidence"],
            "direction": pkg["direction"],
        },
    }
    _emit_shadow_preds("gld_pullback_1d", sig, cfg_yaml, symbol, timeframe=timeframe, candles_df=candles_df)
    _stamp_regime_on_meta(sig.setdefault("meta", {}), candles_df, symbol=symbol, timeframe=timeframe)
    return _with_signal_package("gld_pullback_1d", sig)


def iaum_pullback_1d_signal_builder(settings: dict) -> Dict[str, Any]:
    """IAUM daily HTF-pullback (bidirectional) (cheap-share GLD proxy).

    iShares Gold Trust Micro ETF (IAUM) pullback leg — a cheap-share (~$41)
    proxy for GLD, the gold sibling of gld_pullback_1d. Runs on the
    ``alpaca_paper`` account (PAPER money; bracket orders carry broker-side
    SL/TP). Reuses the
    ``src.units.strategies.htf_pullback_trend_2h.order_package`` unit
    (trades both directions — no long-only gate).

    US-SESSION GATE: evaluation is skipped outside the US cash session
    (``market_hours.is_market_open("us_equity")`` → side=none, reason
    ``us_market_closed``) so closed-market candles never produce
    entries. Fail-permissive on gate errors. Honours the YAML
    ``enabled`` flag as the single source of truth.
    """
    from src.units.strategies import load_strategy_config
    from src.units.strategies.htf_pullback_trend_2h import order_package
    from src.runtime.market_data import fetch_candles
    from src.runtime.market_hours import is_market_open

    try:
        strategies_cfg = load_strategy_config()
    except Exception:  # noqa: BLE001 - never fail-open on a config error
        strategies_cfg = {}
    cfg_yaml = strategies_cfg.get("iaum_pullback_1d", {}) or {}

    symbol = settings.get("SYMBOL", settings.get("symbol", "IAUM"))

    if not bool(cfg_yaml.get("enabled", False)):
        logger.info("iaum_pullback_1d: strategy disabled in config/strategies.yaml - returning side=none")
        return _with_signal_package("iaum_pullback_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "iaum_pullback_1d", "reason": "disabled_in_yaml"},
        })

    try:
        us_open = is_market_open("us_equity")
    except Exception:  # noqa: BLE001 — gate is best-effort, never strands
        us_open = True
    if not us_open:
        logger.info("iaum_pullback_1d: US market closed - side=none")
        return _with_signal_package("iaum_pullback_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "iaum_pullback_1d", "reason": "us_market_closed"},
        })

    timeframe = str(cfg_yaml.get("timeframe") or "1d")

    exchange = _build_killzone_exchange(settings)
    candles_df = fetch_candles(symbol, timeframe, exchange_client=exchange, limit=200)
    if candles_df is None:
        raise RuntimeError(
            f"iaum_pullback_1d: no candle data returned for symbol={symbol} "
            f"timeframe={timeframe}. Check that the Alpaca connection is "
            "configured and the symbol is valid."
        )

    _publish_liquidity_state(symbol, candles_df)

    cfg: Dict[str, Any] = {"symbol": symbol, "timeframe": timeframe, **cfg_yaml}
    cfg["strategy_label"] = "iaum_pullback_1d"

    try:
        pkg = order_package(cfg, candles_df=candles_df)
    except ValueError as exc:
        logger.info("iaum_pullback_1d: no actionable signal (%s)", exc)
        try:
            log_signal(_stamp_regime({
                "event": "iaum_pullback_1d_eval",
                "strategy": "iaum_pullback_1d",
                "symbol": symbol,
                "timeframe": timeframe,
                "side": "none",
                "reason": str(exc),
            }, candles_df))
        except Exception:  # noqa: BLE001
            logger.exception("iaum_pullback_1d: dedicated audit emit failed")
        return _with_signal_package("iaum_pullback_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "iaum_pullback_1d", "reason": str(exc)},
        })

    side = "buy" if pkg["direction"] == "long" else "sell"
    logger.info(
        "iaum_pullback_1d: %s signal at %s (entry=%s sl=%s tp=%s confidence=%.3f)",
        side, symbol, pkg["entry"], pkg["sl"], pkg["tp"], pkg["confidence"],
    )
    pkg_meta = pkg.get("meta") or {}
    try:
        log_signal(_stamp_regime({
            "event": "iaum_pullback_1d_eval",
            "strategy": "iaum_pullback_1d",
            "symbol": symbol,
            "timeframe": timeframe,
            "side": side,
            "entry": pkg["entry"],
            "stop_loss": pkg["sl"],
            "take_profit": pkg["tp"],
            "confidence": pkg["confidence"],
        }, candles_df))
    except Exception:  # noqa: BLE001
        logger.exception("iaum_pullback_1d: dedicated audit emit failed")

    sig = {
        "symbol": symbol,
        "side": side,
        "price": pkg["entry"],
        "entry_price": pkg["entry"],
        "stop_loss": pkg["sl"],
        "take_profit": pkg["tp"],
        "pattern": "iaum_pullback_1d",
        "meta": {
            **pkg_meta,
            "strategy_name": "iaum_pullback_1d",
            "confidence": pkg["confidence"],
            "direction": pkg["direction"],
        },
    }
    _emit_shadow_preds("iaum_pullback_1d", sig, cfg_yaml, symbol, timeframe=timeframe, candles_df=candles_df)
    _stamp_regime_on_meta(sig.setdefault("meta", {}), candles_df, symbol=symbol, timeframe=timeframe)
    return _with_signal_package("iaum_pullback_1d", sig)


def tlt_pullback_1d_signal_builder(settings: dict) -> Dict[str, Any]:
    """TLT daily HTF-pullback (bidirectional) (ETF-breadth daily sweep 2026-06-20).

    20+ year Treasury bond ETF pullback leg — the long-duration bond sibling
    of gld_pullback_1d. ETF-breadth daily sweep (2026-06-20): paper_ready +
    fee-robust (pooled book Sharpe 3.88, all holdouts positive). Runs on the
    ``alpaca_paper`` account (PAPER money; bracket orders carry broker-side
    SL/TP). Reuses the ``src.units.strategies.htf_pullback_trend_2h.order_package``
    unit (trades both directions — no long-only gate).

    US-SESSION GATE: evaluation is skipped outside the US cash session
    (``market_hours.is_market_open("us_equity")`` → side=none, reason
    ``us_market_closed``) so closed-market candles never produce
    entries. Fail-permissive on gate errors. Honours the YAML
    ``enabled`` flag as the single source of truth.
    """
    from src.units.strategies import load_strategy_config
    from src.units.strategies.htf_pullback_trend_2h import order_package
    from src.runtime.market_data import fetch_candles
    from src.runtime.market_hours import is_market_open

    try:
        strategies_cfg = load_strategy_config()
    except Exception:  # noqa: BLE001 - never fail-open on a config error
        strategies_cfg = {}
    cfg_yaml = strategies_cfg.get("tlt_pullback_1d", {}) or {}

    symbol = settings.get("SYMBOL", settings.get("symbol", "TLT"))

    if not bool(cfg_yaml.get("enabled", False)):
        logger.info("tlt_pullback_1d: strategy disabled in config/strategies.yaml - returning side=none")
        return _with_signal_package("tlt_pullback_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "tlt_pullback_1d", "reason": "disabled_in_yaml"},
        })

    try:
        us_open = is_market_open("us_equity")
    except Exception:  # noqa: BLE001 — gate is best-effort, never strands
        us_open = True
    if not us_open:
        logger.info("tlt_pullback_1d: US market closed - side=none")
        return _with_signal_package("tlt_pullback_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "tlt_pullback_1d", "reason": "us_market_closed"},
        })

    timeframe = str(cfg_yaml.get("timeframe") or "1d")

    exchange = _build_killzone_exchange(settings)
    candles_df = fetch_candles(symbol, timeframe, exchange_client=exchange, limit=200)
    if candles_df is None:
        raise RuntimeError(
            f"tlt_pullback_1d: no candle data returned for symbol={symbol} "
            f"timeframe={timeframe}. Check that the Alpaca connection is "
            "configured and the symbol is valid."
        )

    _publish_liquidity_state(symbol, candles_df)

    cfg: Dict[str, Any] = {"symbol": symbol, "timeframe": timeframe, **cfg_yaml}
    cfg["strategy_label"] = "tlt_pullback_1d"

    try:
        pkg = order_package(cfg, candles_df=candles_df)
    except ValueError as exc:
        logger.info("tlt_pullback_1d: no actionable signal (%s)", exc)
        try:
            log_signal(_stamp_regime({
                "event": "tlt_pullback_1d_eval",
                "strategy": "tlt_pullback_1d",
                "symbol": symbol,
                "timeframe": timeframe,
                "side": "none",
                "reason": str(exc),
            }, candles_df))
        except Exception:  # noqa: BLE001
            logger.exception("tlt_pullback_1d: dedicated audit emit failed")
        return _with_signal_package("tlt_pullback_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "tlt_pullback_1d", "reason": str(exc)},
        })

    side = "buy" if pkg["direction"] == "long" else "sell"
    logger.info(
        "tlt_pullback_1d: %s signal at %s (entry=%s sl=%s tp=%s confidence=%.3f)",
        side, symbol, pkg["entry"], pkg["sl"], pkg["tp"], pkg["confidence"],
    )
    pkg_meta = pkg.get("meta") or {}
    try:
        log_signal(_stamp_regime({
            "event": "tlt_pullback_1d_eval",
            "strategy": "tlt_pullback_1d",
            "symbol": symbol,
            "timeframe": timeframe,
            "side": side,
            "entry": pkg["entry"],
            "stop_loss": pkg["sl"],
            "take_profit": pkg["tp"],
            "confidence": pkg["confidence"],
        }, candles_df))
    except Exception:  # noqa: BLE001
        logger.exception("tlt_pullback_1d: dedicated audit emit failed")

    sig = {
        "symbol": symbol,
        "side": side,
        "price": pkg["entry"],
        "entry_price": pkg["entry"],
        "stop_loss": pkg["sl"],
        "take_profit": pkg["tp"],
        "pattern": "tlt_pullback_1d",
        "meta": {
            **pkg_meta,
            "strategy_name": "tlt_pullback_1d",
            "confidence": pkg["confidence"],
            "direction": pkg["direction"],
        },
    }
    _emit_shadow_preds("tlt_pullback_1d", sig, cfg_yaml, symbol, timeframe=timeframe, candles_df=candles_df)
    _stamp_regime_on_meta(sig.setdefault("meta", {}), candles_df, symbol=symbol, timeframe=timeframe)
    return _with_signal_package("tlt_pullback_1d", sig)


def slv_pullback_1d_signal_builder(settings: dict) -> Dict[str, Any]:
    """SLV daily HTF-pullback (bidirectional) (Tier-3 wiring 2026-06-28).

    SLV (iShares Silver Trust) daily pullback — the silver sibling of
    gld_pullback_1d (a gold-correlated but lower-price diversifier). Same
    htf_pullback_trend_2h unit and parameters as gld/tlt; trades both
    directions (no long-only gate). Routed to alpaca_paper + alpaca_live
    (Tier-3 approved 2026-06-27; builder wired 2026-06-28, SRQ-20260627-SLV).
    Whole-share bracket orders carry broker-side SL/TP.

    US-SESSION GATE: evaluation is skipped outside the US cash session
    (``market_hours.is_market_open("us_equity")`` -> side=none, reason
    ``us_market_closed``) so closed-market candles never produce entries.
    Fail-permissive on gate errors. Honours the YAML ``enabled`` flag as the
    single source of truth.
    """
    from src.units.strategies import load_strategy_config
    from src.units.strategies.htf_pullback_trend_2h import order_package
    from src.runtime.market_data import fetch_candles
    from src.runtime.market_hours import is_market_open

    try:
        strategies_cfg = load_strategy_config()
    except Exception:  # noqa: BLE001 - never fail-open on a config error
        strategies_cfg = {}
    cfg_yaml = strategies_cfg.get("slv_pullback_1d", {}) or {}

    symbol = settings.get("SYMBOL", settings.get("symbol", "SLV"))

    if not bool(cfg_yaml.get("enabled", False)):
        logger.info("slv_pullback_1d: strategy disabled in config/strategies.yaml - returning side=none")
        return _with_signal_package("slv_pullback_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "slv_pullback_1d", "reason": "disabled_in_yaml"},
        })

    try:
        us_open = is_market_open("us_equity")
    except Exception:  # noqa: BLE001 — gate is best-effort, never strands
        us_open = True
    if not us_open:
        logger.info("slv_pullback_1d: US market closed - side=none")
        return _with_signal_package("slv_pullback_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "slv_pullback_1d", "reason": "us_market_closed"},
        })

    timeframe = str(cfg_yaml.get("timeframe") or "1d")

    exchange = _build_killzone_exchange(settings)
    candles_df = fetch_candles(symbol, timeframe, exchange_client=exchange, limit=200)
    if candles_df is None:
        raise RuntimeError(
            f"slv_pullback_1d: no candle data returned for symbol={symbol} "
            f"timeframe={timeframe}. Check that the Alpaca connection is "
            "configured and the symbol is valid."
        )

    _publish_liquidity_state(symbol, candles_df)

    cfg: Dict[str, Any] = {"symbol": symbol, "timeframe": timeframe, **cfg_yaml}
    cfg["strategy_label"] = "slv_pullback_1d"

    try:
        pkg = order_package(cfg, candles_df=candles_df)
    except ValueError as exc:
        logger.info("slv_pullback_1d: no actionable signal (%s)", exc)
        try:
            log_signal(_stamp_regime({
                "event": "slv_pullback_1d_eval",
                "strategy": "slv_pullback_1d",
                "symbol": symbol,
                "timeframe": timeframe,
                "side": "none",
                "reason": str(exc),
            }, candles_df))
        except Exception:  # noqa: BLE001
            logger.exception("slv_pullback_1d: dedicated audit emit failed")
        return _with_signal_package("slv_pullback_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "slv_pullback_1d", "reason": str(exc)},
        })

    side = "buy" if pkg["direction"] == "long" else "sell"
    logger.info(
        "slv_pullback_1d: %s signal at %s (entry=%s sl=%s tp=%s confidence=%.3f)",
        side, symbol, pkg["entry"], pkg["sl"], pkg["tp"], pkg["confidence"],
    )
    pkg_meta = pkg.get("meta") or {}
    try:
        log_signal(_stamp_regime({
            "event": "slv_pullback_1d_eval",
            "strategy": "slv_pullback_1d",
            "symbol": symbol,
            "timeframe": timeframe,
            "side": side,
            "entry": pkg["entry"],
            "stop_loss": pkg["sl"],
            "take_profit": pkg["tp"],
            "confidence": pkg["confidence"],
        }, candles_df))
    except Exception:  # noqa: BLE001
        logger.exception("slv_pullback_1d: dedicated audit emit failed")

    sig = {
        "symbol": symbol,
        "side": side,
        "price": pkg["entry"],
        "entry_price": pkg["entry"],
        "stop_loss": pkg["sl"],
        "take_profit": pkg["tp"],
        "pattern": "slv_pullback_1d",
        "meta": {
            **pkg_meta,
            "strategy_name": "slv_pullback_1d",
            "confidence": pkg["confidence"],
            "direction": pkg["direction"],
        },
    }
    _emit_shadow_preds("slv_pullback_1d", sig, cfg_yaml, symbol, timeframe=timeframe, candles_df=candles_df)
    _stamp_regime_on_meta(sig.setdefault("meta", {}), candles_df, symbol=symbol, timeframe=timeframe)
    return _with_signal_package("slv_pullback_1d", sig)


def gdx_pullback_1d_signal_builder(settings: dict) -> Dict[str, Any]:
    """GDX daily HTF-pullback (bidirectional) (Tier-3 wiring 2026-06-28).

    GDX (VanEck Gold Miners ETF) daily pullback — the gold-miners sibling of
    gld_pullback_1d / slv_pullback_1d (~$43/share, fits the alpaca_live budget
    at 0.3% risk). Same htf_pullback_trend_2h unit and parameters as gld;
    trades both directions (no long-only gate). Routed to alpaca_paper +
    alpaca_live (Tier-3 approved 2026-06-27; builder wired 2026-06-28,
    SRQ-20260627-GDX). Whole-share bracket orders carry broker-side SL/TP.

    US-SESSION GATE: evaluation is skipped outside the US cash session
    (``market_hours.is_market_open("us_equity")`` -> side=none, reason
    ``us_market_closed``) so closed-market candles never produce entries.
    Fail-permissive on gate errors. Honours the YAML ``enabled`` flag as the
    single source of truth.
    """
    from src.units.strategies import load_strategy_config
    from src.units.strategies.htf_pullback_trend_2h import order_package
    from src.runtime.market_data import fetch_candles
    from src.runtime.market_hours import is_market_open

    try:
        strategies_cfg = load_strategy_config()
    except Exception:  # noqa: BLE001 - never fail-open on a config error
        strategies_cfg = {}
    cfg_yaml = strategies_cfg.get("gdx_pullback_1d", {}) or {}

    symbol = settings.get("SYMBOL", settings.get("symbol", "GDX"))

    if not bool(cfg_yaml.get("enabled", False)):
        logger.info("gdx_pullback_1d: strategy disabled in config/strategies.yaml - returning side=none")
        return _with_signal_package("gdx_pullback_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "gdx_pullback_1d", "reason": "disabled_in_yaml"},
        })

    try:
        us_open = is_market_open("us_equity")
    except Exception:  # noqa: BLE001 — gate is best-effort, never strands
        us_open = True
    if not us_open:
        logger.info("gdx_pullback_1d: US market closed - side=none")
        return _with_signal_package("gdx_pullback_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "gdx_pullback_1d", "reason": "us_market_closed"},
        })

    timeframe = str(cfg_yaml.get("timeframe") or "1d")

    exchange = _build_killzone_exchange(settings)
    candles_df = fetch_candles(symbol, timeframe, exchange_client=exchange, limit=200)
    if candles_df is None:
        raise RuntimeError(
            f"gdx_pullback_1d: no candle data returned for symbol={symbol} "
            f"timeframe={timeframe}. Check that the Alpaca connection is "
            "configured and the symbol is valid."
        )

    _publish_liquidity_state(symbol, candles_df)

    cfg: Dict[str, Any] = {"symbol": symbol, "timeframe": timeframe, **cfg_yaml}
    cfg["strategy_label"] = "gdx_pullback_1d"

    try:
        pkg = order_package(cfg, candles_df=candles_df)
    except ValueError as exc:
        logger.info("gdx_pullback_1d: no actionable signal (%s)", exc)
        try:
            log_signal(_stamp_regime({
                "event": "gdx_pullback_1d_eval",
                "strategy": "gdx_pullback_1d",
                "symbol": symbol,
                "timeframe": timeframe,
                "side": "none",
                "reason": str(exc),
            }, candles_df))
        except Exception:  # noqa: BLE001
            logger.exception("gdx_pullback_1d: dedicated audit emit failed")
        return _with_signal_package("gdx_pullback_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "gdx_pullback_1d", "reason": str(exc)},
        })

    side = "buy" if pkg["direction"] == "long" else "sell"
    logger.info(
        "gdx_pullback_1d: %s signal at %s (entry=%s sl=%s tp=%s confidence=%.3f)",
        side, symbol, pkg["entry"], pkg["sl"], pkg["tp"], pkg["confidence"],
    )
    pkg_meta = pkg.get("meta") or {}
    try:
        log_signal(_stamp_regime({
            "event": "gdx_pullback_1d_eval",
            "strategy": "gdx_pullback_1d",
            "symbol": symbol,
            "timeframe": timeframe,
            "side": side,
            "entry": pkg["entry"],
            "stop_loss": pkg["sl"],
            "take_profit": pkg["tp"],
            "confidence": pkg["confidence"],
        }, candles_df))
    except Exception:  # noqa: BLE001
        logger.exception("gdx_pullback_1d: dedicated audit emit failed")

    sig = {
        "symbol": symbol,
        "side": side,
        "price": pkg["entry"],
        "entry_price": pkg["entry"],
        "stop_loss": pkg["sl"],
        "take_profit": pkg["tp"],
        "pattern": "gdx_pullback_1d",
        "meta": {
            **pkg_meta,
            "strategy_name": "gdx_pullback_1d",
            "confidence": pkg["confidence"],
            "direction": pkg["direction"],
        },
    }
    _emit_shadow_preds("gdx_pullback_1d", sig, cfg_yaml, symbol, timeframe=timeframe, candles_df=candles_df)
    _stamp_regime_on_meta(sig.setdefault("meta", {}), candles_df, symbol=symbol, timeframe=timeframe)
    return _with_signal_package("gdx_pullback_1d", sig)


def ief_pullback_1d_signal_builder(settings: dict) -> Dict[str, Any]:
    """IEF daily HTF-pullback (bidirectional) (ETF-breadth daily sweep 2026-06-20).

    7-10 year Treasury bond ETF pullback leg — the intermediate-duration bond
    sibling of gld_pullback_1d / tlt_pullback_1d. ETF-breadth daily sweep
    (2026-06-20): paper_ready + fee-robust (pooled book Sharpe 3.88, all
    holdouts positive). Runs on the ``alpaca_paper`` account (PAPER money;
    bracket orders carry broker-side SL/TP). Reuses the
    ``src.units.strategies.htf_pullback_trend_2h.order_package`` unit (trades
    both directions — no long-only gate).

    US-SESSION GATE: evaluation is skipped outside the US cash session
    (``market_hours.is_market_open("us_equity")`` → side=none, reason
    ``us_market_closed``) so closed-market candles never produce
    entries. Fail-permissive on gate errors. Honours the YAML
    ``enabled`` flag as the single source of truth.
    """
    from src.units.strategies import load_strategy_config
    from src.units.strategies.htf_pullback_trend_2h import order_package
    from src.runtime.market_data import fetch_candles
    from src.runtime.market_hours import is_market_open

    try:
        strategies_cfg = load_strategy_config()
    except Exception:  # noqa: BLE001 - never fail-open on a config error
        strategies_cfg = {}
    cfg_yaml = strategies_cfg.get("ief_pullback_1d", {}) or {}

    symbol = settings.get("SYMBOL", settings.get("symbol", "IEF"))

    if not bool(cfg_yaml.get("enabled", False)):
        logger.info("ief_pullback_1d: strategy disabled in config/strategies.yaml - returning side=none")
        return _with_signal_package("ief_pullback_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "ief_pullback_1d", "reason": "disabled_in_yaml"},
        })

    try:
        us_open = is_market_open("us_equity")
    except Exception:  # noqa: BLE001 — gate is best-effort, never strands
        us_open = True
    if not us_open:
        logger.info("ief_pullback_1d: US market closed - side=none")
        return _with_signal_package("ief_pullback_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "ief_pullback_1d", "reason": "us_market_closed"},
        })

    timeframe = str(cfg_yaml.get("timeframe") or "1d")

    exchange = _build_killzone_exchange(settings)
    candles_df = fetch_candles(symbol, timeframe, exchange_client=exchange, limit=200)
    if candles_df is None:
        raise RuntimeError(
            f"ief_pullback_1d: no candle data returned for symbol={symbol} "
            f"timeframe={timeframe}. Check that the Alpaca connection is "
            "configured and the symbol is valid."
        )

    _publish_liquidity_state(symbol, candles_df)

    cfg: Dict[str, Any] = {"symbol": symbol, "timeframe": timeframe, **cfg_yaml}
    cfg["strategy_label"] = "ief_pullback_1d"

    try:
        pkg = order_package(cfg, candles_df=candles_df)
    except ValueError as exc:
        logger.info("ief_pullback_1d: no actionable signal (%s)", exc)
        try:
            log_signal(_stamp_regime({
                "event": "ief_pullback_1d_eval",
                "strategy": "ief_pullback_1d",
                "symbol": symbol,
                "timeframe": timeframe,
                "side": "none",
                "reason": str(exc),
            }, candles_df))
        except Exception:  # noqa: BLE001
            logger.exception("ief_pullback_1d: dedicated audit emit failed")
        return _with_signal_package("ief_pullback_1d", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "ief_pullback_1d", "reason": str(exc)},
        })

    side = "buy" if pkg["direction"] == "long" else "sell"
    logger.info(
        "ief_pullback_1d: %s signal at %s (entry=%s sl=%s tp=%s confidence=%.3f)",
        side, symbol, pkg["entry"], pkg["sl"], pkg["tp"], pkg["confidence"],
    )
    pkg_meta = pkg.get("meta") or {}
    try:
        log_signal(_stamp_regime({
            "event": "ief_pullback_1d_eval",
            "strategy": "ief_pullback_1d",
            "symbol": symbol,
            "timeframe": timeframe,
            "side": side,
            "entry": pkg["entry"],
            "stop_loss": pkg["sl"],
            "take_profit": pkg["tp"],
            "confidence": pkg["confidence"],
        }, candles_df))
    except Exception:  # noqa: BLE001
        logger.exception("ief_pullback_1d: dedicated audit emit failed")

    sig = {
        "symbol": symbol,
        "side": side,
        "price": pkg["entry"],
        "entry_price": pkg["entry"],
        "stop_loss": pkg["sl"],
        "take_profit": pkg["tp"],
        "pattern": "ief_pullback_1d",
        "meta": {
            **pkg_meta,
            "strategy_name": "ief_pullback_1d",
            "confidence": pkg["confidence"],
            "direction": pkg["direction"],
        },
    }
    _emit_shadow_preds("ief_pullback_1d", sig, cfg_yaml, symbol, timeframe=timeframe, candles_df=candles_df)
    _stamp_regime_on_meta(sig.setdefault("meta", {}), candles_df, symbol=symbol, timeframe=timeframe)
    return _with_signal_package("ief_pullback_1d", sig)


def gld_pullback_1h_signal_builder(settings: dict) -> Dict[str, Any]:
    """GLD INTRADAY (1h) HTF-pullback (bidirectional) (intraday ETF sweep 2026-06-20).

    Intraday (1h) sibling of gld_pullback_1d — the same gold-ETF bidirectional
    HTF-pullback logic on the 1-hour timeframe. PROVENANCE: intraday 1h ETF
    sweep (2026-06-20, § 0e) — GLD pullback trail4 was live_ready (+78.9R,
    2x-fee +61.5R; see docs/research/expansion-backtesting-research-2026-06-20.md).
    Pilot for the intraday ETF-breadth sleeve. Runs on the ``alpaca_paper``
    account (PAPER money; bracket orders carry broker-side SL/TP). Reuses the
    ``src.units.strategies.htf_pullback_trend_2h.order_package`` unit (trades
    both directions — no long-only gate).

    US-SESSION GATE: evaluation is skipped outside the US cash session
    (``market_hours.is_market_open("us_equity")`` → side=none, reason
    ``us_market_closed``) so closed-market candles never produce
    entries. Fail-permissive on gate errors. Honours the YAML
    ``enabled`` flag as the single source of truth.
    """
    from src.units.strategies import load_strategy_config
    from src.units.strategies.htf_pullback_trend_2h import order_package
    from src.runtime.market_data import fetch_candles
    from src.runtime.market_hours import is_market_open

    try:
        strategies_cfg = load_strategy_config()
    except Exception:  # noqa: BLE001 - never fail-open on a config error
        strategies_cfg = {}
    cfg_yaml = strategies_cfg.get("gld_pullback_1h", {}) or {}

    symbol = settings.get("SYMBOL", settings.get("symbol", "GLD"))

    if not bool(cfg_yaml.get("enabled", False)):
        logger.info("gld_pullback_1h: strategy disabled in config/strategies.yaml - returning side=none")
        return _with_signal_package("gld_pullback_1h", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "gld_pullback_1h", "reason": "disabled_in_yaml"},
        })

    try:
        us_open = is_market_open("us_equity")
    except Exception:  # noqa: BLE001 — gate is best-effort, never strands
        us_open = True
    if not us_open:
        logger.info("gld_pullback_1h: US market closed - side=none")
        return _with_signal_package("gld_pullback_1h", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "gld_pullback_1h", "reason": "us_market_closed"},
        })

    timeframe = str(cfg_yaml.get("timeframe") or "1h")

    exchange = _build_killzone_exchange(settings)
    candles_df = fetch_candles(symbol, timeframe, exchange_client=exchange, limit=200)
    if candles_df is None:
        raise RuntimeError(
            f"gld_pullback_1h: no candle data returned for symbol={symbol} "
            f"timeframe={timeframe}. Check that the Alpaca connection is "
            "configured and the symbol is valid."
        )

    _publish_liquidity_state(symbol, candles_df)

    cfg: Dict[str, Any] = {"symbol": symbol, "timeframe": timeframe, **cfg_yaml}
    cfg["strategy_label"] = "gld_pullback_1h"

    try:
        pkg = order_package(cfg, candles_df=candles_df)
    except ValueError as exc:
        logger.info("gld_pullback_1h: no actionable signal (%s)", exc)
        try:
            log_signal(_stamp_regime({
                "event": "gld_pullback_1h_eval",
                "strategy": "gld_pullback_1h",
                "symbol": symbol,
                "timeframe": timeframe,
                "side": "none",
                "reason": str(exc),
            }, candles_df))
        except Exception:  # noqa: BLE001
            logger.exception("gld_pullback_1h: dedicated audit emit failed")
        return _with_signal_package("gld_pullback_1h", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "gld_pullback_1h", "reason": str(exc)},
        })

    side = "buy" if pkg["direction"] == "long" else "sell"
    logger.info(
        "gld_pullback_1h: %s signal at %s (entry=%s sl=%s tp=%s confidence=%.3f)",
        side, symbol, pkg["entry"], pkg["sl"], pkg["tp"], pkg["confidence"],
    )
    pkg_meta = pkg.get("meta") or {}
    try:
        log_signal(_stamp_regime({
            "event": "gld_pullback_1h_eval",
            "strategy": "gld_pullback_1h",
            "symbol": symbol,
            "timeframe": timeframe,
            "side": side,
            "entry": pkg["entry"],
            "stop_loss": pkg["sl"],
            "take_profit": pkg["tp"],
            "confidence": pkg["confidence"],
        }, candles_df))
    except Exception:  # noqa: BLE001
        logger.exception("gld_pullback_1h: dedicated audit emit failed")

    sig = {
        "symbol": symbol,
        "side": side,
        "price": pkg["entry"],
        "entry_price": pkg["entry"],
        "stop_loss": pkg["sl"],
        "take_profit": pkg["tp"],
        "pattern": "gld_pullback_1h",
        "meta": {
            **pkg_meta,
            "strategy_name": "gld_pullback_1h",
            "confidence": pkg["confidence"],
            "direction": pkg["direction"],
        },
    }
    _emit_shadow_preds("gld_pullback_1h", sig, cfg_yaml, symbol, timeframe=timeframe, candles_df=candles_df)
    _stamp_regime_on_meta(sig.setdefault("meta", {}), candles_df, symbol=symbol, timeframe=timeframe)
    return _with_signal_package("gld_pullback_1h", sig)


def slv_trend_1h_signal_builder(settings: dict) -> Dict[str, Any]:
    """SLV INTRADAY (1h) BIDIRECTIONAL Donchian trend-follower (intraday ETF sweep 2026-06-20).

    Silver-ETF intraday (1h) Donchian trend leg. Structurally a clone of
    spy_trend_long_1d (the ``trend_donchian`` unit) BUT trades BOTH directions
    — silver trends down as well as up, so there is NO long-only suppression
    here (buy for long, sell for short). PROVENANCE: intraday 1h ETF sweep
    (2026-06-20, § 0e) — SLV trend both_donch24 was live_ready (+85.5R, 2x-fee
    +77R; see docs/research/expansion-backtesting-research-2026-06-20.md). Pilot
    for the intraday ETF-breadth sleeve. Runs on the ``alpaca_paper`` account
    (PAPER money; bracket orders carry broker-side SL/TP). Reuses the
    ``src.units.strategies.trend_donchian.order_package`` unit.

    US-SESSION GATE: evaluation is skipped outside the US cash session
    (``market_hours.is_market_open("us_equity")`` → side=none, reason
    ``us_market_closed``) so closed-market candles never produce
    entries. Fail-permissive on gate errors. Honours the YAML
    ``enabled`` flag as the single source of truth.
    """
    from src.units.strategies import load_strategy_config
    from src.units.strategies.trend_donchian import order_package
    from src.runtime.market_data import fetch_candles
    from src.runtime.market_hours import is_market_open

    try:
        strategies_cfg = load_strategy_config()
    except Exception:  # noqa: BLE001 - never fail-open on a config error
        strategies_cfg = {}
    cfg_yaml = strategies_cfg.get("slv_trend_1h", {}) or {}

    symbol = settings.get("SYMBOL", settings.get("symbol", "SLV"))

    if not bool(cfg_yaml.get("enabled", False)):
        logger.info("slv_trend_1h: strategy disabled in config/strategies.yaml - returning side=none")
        return _with_signal_package("slv_trend_1h", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "slv_trend_1h", "reason": "disabled_in_yaml"},
        })

    try:
        us_open = is_market_open("us_equity")
    except Exception:  # noqa: BLE001 — gate is best-effort, never strands
        us_open = True
    if not us_open:
        logger.info("slv_trend_1h: US market closed - side=none")
        return _with_signal_package("slv_trend_1h", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "slv_trend_1h", "reason": "us_market_closed"},
        })

    timeframe = str(cfg_yaml.get("timeframe") or "1h")

    exchange = _build_killzone_exchange(settings)
    candles_df = fetch_candles(symbol, timeframe, exchange_client=exchange, limit=200)
    if candles_df is None:
        raise RuntimeError(
            f"slv_trend_1h: no candle data returned for symbol={symbol} "
            f"timeframe={timeframe}. Check that the Alpaca connection is "
            "configured and the symbol is valid."
        )

    _publish_liquidity_state(symbol, candles_df)

    cfg: Dict[str, Any] = {"symbol": symbol, "timeframe": timeframe, **cfg_yaml}
    cfg["strategy_label"] = "slv_trend_1h"

    try:
        pkg = order_package(cfg, candles_df=candles_df)
    except ValueError as exc:
        logger.info("slv_trend_1h: no actionable signal (%s)", exc)
        try:
            log_signal(_stamp_regime({
                "event": "slv_trend_1h_eval",
                "strategy": "slv_trend_1h",
                "symbol": symbol,
                "timeframe": timeframe,
                "side": "none",
                "reason": str(exc),
            }, candles_df))
        except Exception:  # noqa: BLE001
            logger.exception("slv_trend_1h: dedicated audit emit failed")
        return _with_signal_package("slv_trend_1h", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "slv_trend_1h", "reason": str(exc)},
        })

    # BIDIRECTIONAL: no long-only suppression — silver trends both ways, so
    # both the long and short sides are traded (the validated short side was
    # net-positive in the intraday 1h sweep).
    side = "buy" if pkg["direction"] == "long" else "sell"
    logger.info(
        "slv_trend_1h: %s signal at %s (entry=%s sl=%s tp=%s confidence=%.3f)",
        side, symbol, pkg["entry"], pkg["sl"], pkg["tp"], pkg["confidence"],
    )
    pkg_meta = pkg.get("meta") or {}
    try:
        log_signal(_stamp_regime({
            "event": "slv_trend_1h_eval",
            "strategy": "slv_trend_1h",
            "symbol": symbol,
            "timeframe": timeframe,
            "side": side,
            "entry": pkg["entry"],
            "stop_loss": pkg["sl"],
            "take_profit": pkg["tp"],
            "confidence": pkg["confidence"],
        }, candles_df))
    except Exception:  # noqa: BLE001
        logger.exception("slv_trend_1h: dedicated audit emit failed")

    sig = {
        "symbol": symbol,
        "side": side,
        "price": pkg["entry"],
        "entry_price": pkg["entry"],
        "stop_loss": pkg["sl"],
        "take_profit": pkg["tp"],
        "pattern": "slv_trend_1h",
        "meta": {
            **pkg_meta,
            "strategy_name": "slv_trend_1h",
            "confidence": pkg["confidence"],
            "direction": pkg["direction"],
        },
    }
    _emit_shadow_preds("slv_trend_1h", sig, cfg_yaml, symbol, timeframe=timeframe, candles_df=candles_df)
    _stamp_regime_on_meta(sig.setdefault("meta", {}), candles_df, symbol=symbol, timeframe=timeframe)
    return _with_signal_package("slv_trend_1h", sig)


def spy_pullback_1h_signal_builder(settings: dict) -> Dict[str, Any]:
    """SPY INTRADAY (1h) HTF-pullback (bidirectional) (intraday ETF sweep 2026-06-20).

    Intraday (1h) S&P-500-ETF bidirectional HTF-pullback leg — structurally the
    gld_pullback_1h sibling on SPY. PROVENANCE: intraday 1h ETF sweep
    (2026-06-20, § 0e) — SPY pullback frac618 was +42R (2x-fee +30.6R; see
    docs/research/expansion-backtesting-research-2026-06-20.md). Round 2b
    completing the intraday ETF-breadth sleeve. Runs on the ``alpaca_paper``
    account (PAPER money; bracket orders carry broker-side SL/TP). Reuses the
    ``src.units.strategies.htf_pullback_trend_2h.order_package`` unit (trades
    both directions — no long-only gate).

    US-SESSION GATE: evaluation is skipped outside the US cash session
    (``market_hours.is_market_open("us_equity")`` → side=none, reason
    ``us_market_closed``) so closed-market candles never produce
    entries. Fail-permissive on gate errors. Honours the YAML
    ``enabled`` flag as the single source of truth.
    """
    from src.units.strategies import load_strategy_config
    from src.units.strategies.htf_pullback_trend_2h import order_package
    from src.runtime.market_data import fetch_candles
    from src.runtime.market_hours import is_market_open

    try:
        strategies_cfg = load_strategy_config()
    except Exception:  # noqa: BLE001 - never fail-open on a config error
        strategies_cfg = {}
    cfg_yaml = strategies_cfg.get("spy_pullback_1h", {}) or {}

    symbol = settings.get("SYMBOL", settings.get("symbol", "SPY"))

    if not bool(cfg_yaml.get("enabled", False)):
        logger.info("spy_pullback_1h: strategy disabled in config/strategies.yaml - returning side=none")
        return _with_signal_package("spy_pullback_1h", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "spy_pullback_1h", "reason": "disabled_in_yaml"},
        })

    try:
        us_open = is_market_open("us_equity")
    except Exception:  # noqa: BLE001 — gate is best-effort, never strands
        us_open = True
    if not us_open:
        logger.info("spy_pullback_1h: US market closed - side=none")
        return _with_signal_package("spy_pullback_1h", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "spy_pullback_1h", "reason": "us_market_closed"},
        })

    timeframe = str(cfg_yaml.get("timeframe") or "1h")

    exchange = _build_killzone_exchange(settings)
    candles_df = fetch_candles(symbol, timeframe, exchange_client=exchange, limit=200)
    if candles_df is None:
        raise RuntimeError(
            f"spy_pullback_1h: no candle data returned for symbol={symbol} "
            f"timeframe={timeframe}. Check that the Alpaca connection is "
            "configured and the symbol is valid."
        )

    _publish_liquidity_state(symbol, candles_df)

    cfg: Dict[str, Any] = {"symbol": symbol, "timeframe": timeframe, **cfg_yaml}
    cfg["strategy_label"] = "spy_pullback_1h"

    try:
        pkg = order_package(cfg, candles_df=candles_df)
    except ValueError as exc:
        logger.info("spy_pullback_1h: no actionable signal (%s)", exc)
        try:
            log_signal(_stamp_regime({
                "event": "spy_pullback_1h_eval",
                "strategy": "spy_pullback_1h",
                "symbol": symbol,
                "timeframe": timeframe,
                "side": "none",
                "reason": str(exc),
            }, candles_df))
        except Exception:  # noqa: BLE001
            logger.exception("spy_pullback_1h: dedicated audit emit failed")
        return _with_signal_package("spy_pullback_1h", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "spy_pullback_1h", "reason": str(exc)},
        })

    side = "buy" if pkg["direction"] == "long" else "sell"
    logger.info(
        "spy_pullback_1h: %s signal at %s (entry=%s sl=%s tp=%s confidence=%.3f)",
        side, symbol, pkg["entry"], pkg["sl"], pkg["tp"], pkg["confidence"],
    )
    pkg_meta = pkg.get("meta") or {}
    try:
        log_signal(_stamp_regime({
            "event": "spy_pullback_1h_eval",
            "strategy": "spy_pullback_1h",
            "symbol": symbol,
            "timeframe": timeframe,
            "side": side,
            "entry": pkg["entry"],
            "stop_loss": pkg["sl"],
            "take_profit": pkg["tp"],
            "confidence": pkg["confidence"],
        }, candles_df))
    except Exception:  # noqa: BLE001
        logger.exception("spy_pullback_1h: dedicated audit emit failed")

    sig = {
        "symbol": symbol,
        "side": side,
        "price": pkg["entry"],
        "entry_price": pkg["entry"],
        "stop_loss": pkg["sl"],
        "take_profit": pkg["tp"],
        "pattern": "spy_pullback_1h",
        "meta": {
            **pkg_meta,
            "strategy_name": "spy_pullback_1h",
            "confidence": pkg["confidence"],
            "direction": pkg["direction"],
        },
    }
    _emit_shadow_preds("spy_pullback_1h", sig, cfg_yaml, symbol, timeframe=timeframe, candles_df=candles_df)
    _stamp_regime_on_meta(sig.setdefault("meta", {}), candles_df, symbol=symbol, timeframe=timeframe)
    return _with_signal_package("spy_pullback_1h", sig)


def qqq_pullback_1h_signal_builder(settings: dict) -> Dict[str, Any]:
    """QQQ INTRADAY (1h) HTF-pullback (bidirectional) (intraday ETF sweep 2026-06-20).

    Intraday (1h) Nasdaq-100-ETF bidirectional HTF-pullback leg — the
    gld_pullback_1h sibling on QQQ (same frac618 params as spy_pullback_1h).
    PROVENANCE: intraday 1h ETF sweep (2026-06-20, § 0e) — QQQ pullback frac618
    was +45.3R (2x-fee +36.7R; see
    docs/research/expansion-backtesting-research-2026-06-20.md). Round 2b
    completing the intraday ETF-breadth sleeve. Runs on the ``alpaca_paper``
    account (PAPER money; bracket orders carry broker-side SL/TP). Reuses the
    ``src.units.strategies.htf_pullback_trend_2h.order_package`` unit (trades
    both directions — no long-only gate).

    US-SESSION GATE: evaluation is skipped outside the US cash session
    (``market_hours.is_market_open("us_equity")`` → side=none, reason
    ``us_market_closed``) so closed-market candles never produce
    entries. Fail-permissive on gate errors. Honours the YAML
    ``enabled`` flag as the single source of truth.
    """
    from src.units.strategies import load_strategy_config
    from src.units.strategies.htf_pullback_trend_2h import order_package
    from src.runtime.market_data import fetch_candles
    from src.runtime.market_hours import is_market_open

    try:
        strategies_cfg = load_strategy_config()
    except Exception:  # noqa: BLE001 - never fail-open on a config error
        strategies_cfg = {}
    cfg_yaml = strategies_cfg.get("qqq_pullback_1h", {}) or {}

    symbol = settings.get("SYMBOL", settings.get("symbol", "QQQ"))

    if not bool(cfg_yaml.get("enabled", False)):
        logger.info("qqq_pullback_1h: strategy disabled in config/strategies.yaml - returning side=none")
        return _with_signal_package("qqq_pullback_1h", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "qqq_pullback_1h", "reason": "disabled_in_yaml"},
        })

    try:
        us_open = is_market_open("us_equity")
    except Exception:  # noqa: BLE001 — gate is best-effort, never strands
        us_open = True
    if not us_open:
        logger.info("qqq_pullback_1h: US market closed - side=none")
        return _with_signal_package("qqq_pullback_1h", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "qqq_pullback_1h", "reason": "us_market_closed"},
        })

    timeframe = str(cfg_yaml.get("timeframe") or "1h")

    exchange = _build_killzone_exchange(settings)
    candles_df = fetch_candles(symbol, timeframe, exchange_client=exchange, limit=200)
    if candles_df is None:
        raise RuntimeError(
            f"qqq_pullback_1h: no candle data returned for symbol={symbol} "
            f"timeframe={timeframe}. Check that the Alpaca connection is "
            "configured and the symbol is valid."
        )

    _publish_liquidity_state(symbol, candles_df)

    cfg: Dict[str, Any] = {"symbol": symbol, "timeframe": timeframe, **cfg_yaml}
    cfg["strategy_label"] = "qqq_pullback_1h"

    try:
        pkg = order_package(cfg, candles_df=candles_df)
    except ValueError as exc:
        logger.info("qqq_pullback_1h: no actionable signal (%s)", exc)
        try:
            log_signal(_stamp_regime({
                "event": "qqq_pullback_1h_eval",
                "strategy": "qqq_pullback_1h",
                "symbol": symbol,
                "timeframe": timeframe,
                "side": "none",
                "reason": str(exc),
            }, candles_df))
        except Exception:  # noqa: BLE001
            logger.exception("qqq_pullback_1h: dedicated audit emit failed")
        return _with_signal_package("qqq_pullback_1h", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "qqq_pullback_1h", "reason": str(exc)},
        })

    side = "buy" if pkg["direction"] == "long" else "sell"
    logger.info(
        "qqq_pullback_1h: %s signal at %s (entry=%s sl=%s tp=%s confidence=%.3f)",
        side, symbol, pkg["entry"], pkg["sl"], pkg["tp"], pkg["confidence"],
    )
    pkg_meta = pkg.get("meta") or {}
    try:
        log_signal(_stamp_regime({
            "event": "qqq_pullback_1h_eval",
            "strategy": "qqq_pullback_1h",
            "symbol": symbol,
            "timeframe": timeframe,
            "side": side,
            "entry": pkg["entry"],
            "stop_loss": pkg["sl"],
            "take_profit": pkg["tp"],
            "confidence": pkg["confidence"],
        }, candles_df))
    except Exception:  # noqa: BLE001
        logger.exception("qqq_pullback_1h: dedicated audit emit failed")

    sig = {
        "symbol": symbol,
        "side": side,
        "price": pkg["entry"],
        "entry_price": pkg["entry"],
        "stop_loss": pkg["sl"],
        "take_profit": pkg["tp"],
        "pattern": "qqq_pullback_1h",
        "meta": {
            **pkg_meta,
            "strategy_name": "qqq_pullback_1h",
            "confidence": pkg["confidence"],
            "direction": pkg["direction"],
        },
    }
    _emit_shadow_preds("qqq_pullback_1h", sig, cfg_yaml, symbol, timeframe=timeframe, candles_df=candles_df)
    _stamp_regime_on_meta(sig.setdefault("meta", {}), candles_df, symbol=symbol, timeframe=timeframe)
    return _with_signal_package("qqq_pullback_1h", sig)


def tlt_pullback_1h_signal_builder(settings: dict) -> Dict[str, Any]:
    """TLT INTRADAY (1h) HTF-pullback (bidirectional) (intraday ETF sweep 2026-06-20).

    Intraday (1h) long-duration Treasury-bond-ETF bidirectional HTF-pullback leg
    — the gld_pullback_1h sibling on TLT (frac 0.5, trail 4.0). PROVENANCE:
    intraday 1h ETF sweep (2026-06-20, § 0e) — TLT pullback trail4 was +50.6R
    (2x-fee +34R; see
    docs/research/expansion-backtesting-research-2026-06-20.md). Round 2b
    completing the intraday ETF-breadth sleeve. Runs on the ``alpaca_paper``
    account (PAPER money; bracket orders carry broker-side SL/TP). Reuses the
    ``src.units.strategies.htf_pullback_trend_2h.order_package`` unit (trades
    both directions — no long-only gate).

    US-SESSION GATE: evaluation is skipped outside the US cash session
    (``market_hours.is_market_open("us_equity")`` → side=none, reason
    ``us_market_closed``) so closed-market candles never produce
    entries. Fail-permissive on gate errors. Honours the YAML
    ``enabled`` flag as the single source of truth.
    """
    from src.units.strategies import load_strategy_config
    from src.units.strategies.htf_pullback_trend_2h import order_package
    from src.runtime.market_data import fetch_candles
    from src.runtime.market_hours import is_market_open

    try:
        strategies_cfg = load_strategy_config()
    except Exception:  # noqa: BLE001 - never fail-open on a config error
        strategies_cfg = {}
    cfg_yaml = strategies_cfg.get("tlt_pullback_1h", {}) or {}

    symbol = settings.get("SYMBOL", settings.get("symbol", "TLT"))

    if not bool(cfg_yaml.get("enabled", False)):
        logger.info("tlt_pullback_1h: strategy disabled in config/strategies.yaml - returning side=none")
        return _with_signal_package("tlt_pullback_1h", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "tlt_pullback_1h", "reason": "disabled_in_yaml"},
        })

    try:
        us_open = is_market_open("us_equity")
    except Exception:  # noqa: BLE001 — gate is best-effort, never strands
        us_open = True
    if not us_open:
        logger.info("tlt_pullback_1h: US market closed - side=none")
        return _with_signal_package("tlt_pullback_1h", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "tlt_pullback_1h", "reason": "us_market_closed"},
        })

    timeframe = str(cfg_yaml.get("timeframe") or "1h")

    exchange = _build_killzone_exchange(settings)
    candles_df = fetch_candles(symbol, timeframe, exchange_client=exchange, limit=200)
    if candles_df is None:
        raise RuntimeError(
            f"tlt_pullback_1h: no candle data returned for symbol={symbol} "
            f"timeframe={timeframe}. Check that the Alpaca connection is "
            "configured and the symbol is valid."
        )

    _publish_liquidity_state(symbol, candles_df)

    cfg: Dict[str, Any] = {"symbol": symbol, "timeframe": timeframe, **cfg_yaml}
    cfg["strategy_label"] = "tlt_pullback_1h"

    try:
        pkg = order_package(cfg, candles_df=candles_df)
    except ValueError as exc:
        logger.info("tlt_pullback_1h: no actionable signal (%s)", exc)
        try:
            log_signal(_stamp_regime({
                "event": "tlt_pullback_1h_eval",
                "strategy": "tlt_pullback_1h",
                "symbol": symbol,
                "timeframe": timeframe,
                "side": "none",
                "reason": str(exc),
            }, candles_df))
        except Exception:  # noqa: BLE001
            logger.exception("tlt_pullback_1h: dedicated audit emit failed")
        return _with_signal_package("tlt_pullback_1h", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "tlt_pullback_1h", "reason": str(exc)},
        })

    side = "buy" if pkg["direction"] == "long" else "sell"
    logger.info(
        "tlt_pullback_1h: %s signal at %s (entry=%s sl=%s tp=%s confidence=%.3f)",
        side, symbol, pkg["entry"], pkg["sl"], pkg["tp"], pkg["confidence"],
    )
    pkg_meta = pkg.get("meta") or {}
    try:
        log_signal(_stamp_regime({
            "event": "tlt_pullback_1h_eval",
            "strategy": "tlt_pullback_1h",
            "symbol": symbol,
            "timeframe": timeframe,
            "side": side,
            "entry": pkg["entry"],
            "stop_loss": pkg["sl"],
            "take_profit": pkg["tp"],
            "confidence": pkg["confidence"],
        }, candles_df))
    except Exception:  # noqa: BLE001
        logger.exception("tlt_pullback_1h: dedicated audit emit failed")

    sig = {
        "symbol": symbol,
        "side": side,
        "price": pkg["entry"],
        "entry_price": pkg["entry"],
        "stop_loss": pkg["sl"],
        "take_profit": pkg["tp"],
        "pattern": "tlt_pullback_1h",
        "meta": {
            **pkg_meta,
            "strategy_name": "tlt_pullback_1h",
            "confidence": pkg["confidence"],
            "direction": pkg["direction"],
        },
    }
    _emit_shadow_preds("tlt_pullback_1h", sig, cfg_yaml, symbol, timeframe=timeframe, candles_df=candles_df)
    _stamp_regime_on_meta(sig.setdefault("meta", {}), candles_df, symbol=symbol, timeframe=timeframe)
    return _with_signal_package("tlt_pullback_1h", sig)


def uso_trend_1h_signal_builder(settings: dict) -> Dict[str, Any]:
    """USO INTRADAY (1h) LONG-ONLY Donchian trend-follower (intraday ETF sweep 2026-06-20).

    Crude-oil-ETF intraday (1h) Donchian trend leg. Structurally a clone of
    spy_trend_long_1d / iwm_trend_long_1d (the ``trend_donchian`` unit) and
    LONG-ONLY — the short-suppression block is KEPT. PROVENANCE: intraday 1h ETF
    sweep (2026-06-20, § 0e) — USO trend donch24 LONG-ONLY was live_ready
    (+39.5R, 2x-fee +35.2R; the both-sides variant was REJECTED, so this cell is
    long-only; see docs/research/expansion-backtesting-research-2026-06-20.md).
    Round 2b completing the intraday ETF-breadth sleeve. Runs on the
    ``alpaca_paper`` account (PAPER money; bracket orders carry broker-side
    SL/TP). Reuses the ``src.units.strategies.trend_donchian.order_package``
    unit. LONG-ONLY (shorts suppressed).

    US-SESSION GATE: evaluation is skipped outside the US cash session
    (``market_hours.is_market_open("us_equity")`` → side=none, reason
    ``us_market_closed``) so closed-market candles never produce
    entries. Fail-permissive on gate errors. Honours the YAML
    ``enabled`` flag as the single source of truth.
    """
    from src.units.strategies import load_strategy_config
    from src.units.strategies.trend_donchian import order_package
    from src.runtime.market_data import fetch_candles
    from src.runtime.market_hours import is_market_open

    try:
        strategies_cfg = load_strategy_config()
    except Exception:  # noqa: BLE001 - never fail-open on a config error
        strategies_cfg = {}
    cfg_yaml = strategies_cfg.get("uso_trend_1h", {}) or {}

    symbol = settings.get("SYMBOL", settings.get("symbol", "USO"))

    if not bool(cfg_yaml.get("enabled", False)):
        logger.info("uso_trend_1h: strategy disabled in config/strategies.yaml - returning side=none")
        return _with_signal_package("uso_trend_1h", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "uso_trend_1h", "reason": "disabled_in_yaml"},
        })

    try:
        us_open = is_market_open("us_equity")
    except Exception:  # noqa: BLE001 — gate is best-effort, never strands
        us_open = True
    if not us_open:
        logger.info("uso_trend_1h: US market closed - side=none")
        return _with_signal_package("uso_trend_1h", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "uso_trend_1h", "reason": "us_market_closed"},
        })

    timeframe = str(cfg_yaml.get("timeframe") or "1h")

    exchange = _build_killzone_exchange(settings)
    candles_df = fetch_candles(symbol, timeframe, exchange_client=exchange, limit=200)
    if candles_df is None:
        raise RuntimeError(
            f"uso_trend_1h: no candle data returned for symbol={symbol} "
            f"timeframe={timeframe}. Check that the Alpaca connection is "
            "configured and the symbol is valid."
        )

    _publish_liquidity_state(symbol, candles_df)

    cfg: Dict[str, Any] = {"symbol": symbol, "timeframe": timeframe, **cfg_yaml}
    cfg["strategy_label"] = "uso_trend_1h"

    try:
        pkg = order_package(cfg, candles_df=candles_df)
    except ValueError as exc:
        logger.info("uso_trend_1h: no actionable signal (%s)", exc)
        try:
            log_signal(_stamp_regime({
                "event": "uso_trend_1h_eval",
                "strategy": "uso_trend_1h",
                "symbol": symbol,
                "timeframe": timeframe,
                "side": "none",
                "reason": str(exc),
            }, candles_df))
        except Exception:  # noqa: BLE001
            logger.exception("uso_trend_1h: dedicated audit emit failed")
        return _with_signal_package("uso_trend_1h", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "uso_trend_1h", "reason": str(exc)},
        })

    # LONG-ONLY gate: the validated USO edge is the long side (mirrors
    # spy_trend_long_1d / iwm_trend_long_1d; the both-sides variant was REJECTED
    # in the intraday 1h sweep § 0e).
    if pkg["direction"] != "long":
        logger.info("uso_trend_1h: short signal suppressed (long-only strategy)")
        try:
            log_signal(_stamp_regime({
                "event": "uso_trend_1h_eval",
                "strategy": "uso_trend_1h",
                "symbol": symbol,
                "timeframe": timeframe,
                "side": "none",
                "reason": "short_suppressed_long_only",
            }, candles_df))
        except Exception:  # noqa: BLE001
            logger.exception("uso_trend_1h: dedicated audit emit failed")
        return _with_signal_package("uso_trend_1h", {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": "uso_trend_1h", "reason": "short_suppressed_long_only"},
        })

    side = "buy" if pkg["direction"] == "long" else "sell"
    logger.info(
        "uso_trend_1h: %s signal at %s (entry=%s sl=%s tp=%s confidence=%.3f)",
        side, symbol, pkg["entry"], pkg["sl"], pkg["tp"], pkg["confidence"],
    )
    pkg_meta = pkg.get("meta") or {}
    try:
        log_signal(_stamp_regime({
            "event": "uso_trend_1h_eval",
            "strategy": "uso_trend_1h",
            "symbol": symbol,
            "timeframe": timeframe,
            "side": side,
            "entry": pkg["entry"],
            "stop_loss": pkg["sl"],
            "take_profit": pkg["tp"],
            "confidence": pkg["confidence"],
        }, candles_df))
    except Exception:  # noqa: BLE001
        logger.exception("uso_trend_1h: dedicated audit emit failed")

    sig = {
        "symbol": symbol,
        "side": side,
        "price": pkg["entry"],
        "entry_price": pkg["entry"],
        "stop_loss": pkg["sl"],
        "take_profit": pkg["tp"],
        "pattern": "uso_trend_1h",
        "meta": {
            **pkg_meta,
            "strategy_name": "uso_trend_1h",
            "confidence": pkg["confidence"],
            "direction": pkg["direction"],
        },
    }
    _emit_shadow_preds("uso_trend_1h", sig, cfg_yaml, symbol, timeframe=timeframe, candles_df=candles_df)
    _stamp_regime_on_meta(sig.setdefault("meta", {}), candles_df, symbol=symbol, timeframe=timeframe)
    return _with_signal_package("uso_trend_1h", sig)


def _htf_pullback_variant_builder(
    name: str, settings: dict, *, default_symbol: str = "",
) -> Dict[str, Any]:
    """Shared builder for the htf_pullback_trend_2h alt-cell variants.

    Symbol-generic refactor (2026-06-18) of the original
    ``eth_pullback_2h_signal_builder``: reads the named ``<name>`` config
    block, pins the traded symbol from that block's ``symbols:`` (a
    single-instrument crypto instance, like the trend_donchian variant
    builder), fetches that symbol's candles, runs the
    ``src.units.strategies.htf_pullback_trend_2h.order_package`` unit at the
    block's params, emits shadow predictions, and honours ``enabled`` as the
    single source of truth. Behaviour-preserving for ``eth_pullback_2h``
    (the symbol resolves the same way — its ``symbols: [ETHUSDT]`` block pins
    ETHUSDT, with the ``settings``/``default_symbol`` chain preserved as the
    fallback for an un-pinned call). Backs ``eth_pullback_2h`` plus the four
    WS-C ``paper_ready`` alt cells (SOL/XRP/ADA/AVAX 2h) on bybit_1 demo.
    """
    from src.units.strategies import load_strategy_config
    from src.units.strategies.htf_pullback_trend_2h import order_package
    from src.runtime.market_data import fetch_candles

    try:
        strategies_cfg = load_strategy_config()
    except Exception:  # noqa: BLE001 - never fail-open on a config error
        strategies_cfg = {}
    cfg_yaml = strategies_cfg.get(name, {}) or {}

    syms = cfg_yaml.get("symbols") or []
    symbol = (
        str(syms[0]) if syms
        else settings.get("SYMBOL", settings.get("symbol", default_symbol))
    )

    if not bool(cfg_yaml.get("enabled", False)):
        logger.info("%s: strategy disabled in config/strategies.yaml - returning side=none", name)
        return _with_signal_package(name, {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": name, "reason": "disabled_in_yaml"},
        })

    timeframe = str(cfg_yaml.get("timeframe") or "2h")

    exchange = _build_killzone_exchange(settings)
    candles_df = fetch_candles(symbol, timeframe, exchange_client=exchange, limit=200)
    if candles_df is None:
        raise RuntimeError(
            f"{name}: no candle data returned for symbol={symbol} "
            f"timeframe={timeframe}. Check that the Bybit connection is "
            "configured and the symbol is valid."
        )

    _publish_liquidity_state(symbol, candles_df)

    cfg: Dict[str, Any] = {"symbol": symbol, "timeframe": timeframe, **cfg_yaml}
    cfg["strategy_label"] = name

    try:
        pkg = order_package(cfg, candles_df=candles_df)
    except ValueError as exc:
        logger.info("%s: no actionable signal (%s)", name, exc)
        try:
            log_signal(_stamp_regime({
                "event": f"{name}_eval",
                "strategy": name,
                "symbol": symbol,
                "timeframe": timeframe,
                "side": "none",
                "reason": str(exc),
            }, candles_df))
        except Exception:  # noqa: BLE001
            logger.exception("%s: dedicated audit emit failed", name)
        return _with_signal_package(name, {
            "symbol": symbol,
            "side": "none",
            "meta": {"strategy_name": name, "reason": str(exc)},
        })

    side = "buy" if pkg["direction"] == "long" else "sell"
    logger.info(
        "%s: %s signal at %s (entry=%s sl=%s tp=%s confidence=%.3f)",
        name, side, symbol, pkg["entry"], pkg["sl"], pkg["tp"], pkg["confidence"],
    )
    pkg_meta = pkg.get("meta") or {}
    try:
        log_signal(_stamp_regime({
            "event": f"{name}_eval",
            "strategy": name,
            "symbol": symbol,
            "timeframe": timeframe,
            "side": side,
            "entry": pkg["entry"],
            "stop_loss": pkg["sl"],
            "take_profit": pkg["tp"],
            "confidence": pkg["confidence"],
        }, candles_df))
    except Exception:  # noqa: BLE001
        logger.exception("%s: dedicated audit emit failed", name)

    sig = {
        "symbol": symbol,
        "side": side,
        "price": pkg["entry"],
        "entry_price": pkg["entry"],
        "stop_loss": pkg["sl"],
        "take_profit": pkg["tp"],
        "pattern": name,
        "meta": {
            **pkg_meta,
            "strategy_name": name,
            "confidence": pkg["confidence"],
            "direction": pkg["direction"],
        },
    }
    _emit_shadow_preds(name, sig, cfg_yaml, symbol, timeframe=timeframe, candles_df=candles_df)
    _stamp_regime_on_meta(sig.setdefault("meta", {}), candles_df, symbol=symbol, timeframe=timeframe)
    return _with_signal_package(name, sig)


def eth_pullback_2h_signal_builder(settings: dict) -> Dict[str, Any]:
    """ETH/USDT 2h HTF-pullback (bidirectional) — M15 WS-C alt sleeve.

    The strongest cell of the WS-C alt generalization sweep
    (docs/research/m15-ws-c-alt-sweep-2026-06-11.md): net of 7.5 bps,
    train +35.4R / OOS +33.5R with the matrix's best OOS expectancy
    (+0.36R/trade, 93 trades). Reuses the
    ``src.units.strategies.htf_pullback_trend_2h.order_package`` unit at
    the SAME params as the live BTC leg (lookback 40/10, frac 0.5,
    stop 2.5x, trail 5.0x). Routed ONLY to bybit_1 (Bybit demo — paper
    money); per the paper-accounts-execute policy it ships
    ``execution: live`` there. Crypto trades 24/7 — no session gate.

    CORRELATION CAVEAT: ETH correlates ~0.7–0.9 with BTC; this leg buys
    frequency, not diversification — size assuming concurrent drawdown
    with the BTC roster. Honours the YAML ``enabled`` flag as the single
    source of truth. Delegates to the symbol-generic
    ``_htf_pullback_variant_builder`` (2026-06-18 refactor; behaviour-
    preserving — its ``symbols: [ETHUSDT]`` block pins the same symbol).
    """
    return _htf_pullback_variant_builder(
        "eth_pullback_2h", settings, default_symbol="ETHUSDT")


def eth_pullback_prop_2h_signal_builder(settings: dict) -> Dict[str, Any]:
    """SWAP-ROBUST prop variant of eth_pullback_2h for Breakout (DRAFT, Tier-3).

    Same ETHUSDT 2h pullback unit + live base params, but with swap-robust exits
    (``tp_r: 6.0`` / ``trail_mult: 3.5`` vs the live 50/5.0) so the days-to-weeks
    holds don't bleed to Breakout's flat 0.09%/day swap. Routed ONLY to
    ``breakout_1`` as ``execution: shadow`` (observe-only — logs order packages,
    never emits a prop ticket). Funded-EV gate: full-period 12-mo EV +$538
    @72.7% P(net>0), walk-forward 4/4 folds EV-positive — but realised post-swap
    is regime-dependent (negative in 2/4 folds), so this is a soak candidate, NOT
    a live-money promotion. Promotion past shadow is the operator-gated Tier-3
    switch. Eval: docs/research/eth-pullback-prop-swap-aware-2026-06-25.md.
    Delegates to the shared ``_htf_pullback_variant_builder`` (reads the
    ``eth_pullback_prop_2h`` config block, which pins the swap-robust exits).
    """
    return _htf_pullback_variant_builder(
        "eth_pullback_prop_2h", settings, default_symbol="ETHUSDT")


# ── pullback_2h alt cells — bybit_1 DEMO soak (paper_ready, WS-C-validated) ─────
# Four symbol-pinned htf_pullback_trend_2h instances on the 2h candle (SOL/XRP/
# ADA/AVAX), routed to bybit_1 (Bybit demo — paper money) for decision/ML soak.
# The M15 WS-C k-fold sweep classified the htf_pullback_2h cell `paper_ready`:
# net-of-fee positive overall + survives 2x fees, failing only the strict
# every-fold gate on the recent regime (SRQ-20260618-002). Demo-only,
# observe-for-soak — NOT live-money-ready (real-money bybit_2 stays a separate
# Tier-3 gate). Each mirrors eth_pullback_2h's params EXACTLY (lookback 40/10,
# frac 0.5, stop 2.5x, trail 5.0x, min_confidence 0.0) — only the symbol differs;
# all delegate to the shared _htf_pullback_variant_builder (2026-06-18, Tier-3).
def sol_pullback_2h_signal_builder(settings: dict) -> Dict[str, Any]:
    """SOLUSDT 2h HTF-pullback — bybit_1 demo soak (paper_ready)."""
    return _htf_pullback_variant_builder(
        "sol_pullback_2h", settings, default_symbol="SOLUSDT")


def xrp_pullback_2h_signal_builder(settings: dict) -> Dict[str, Any]:
    """XRPUSDT 2h HTF-pullback — bybit_1 demo soak (paper_ready)."""
    return _htf_pullback_variant_builder(
        "xrp_pullback_2h", settings, default_symbol="XRPUSDT")


def ada_pullback_2h_signal_builder(settings: dict) -> Dict[str, Any]:
    """ADAUSDT 2h HTF-pullback — bybit_1 demo soak (paper_ready)."""
    return _htf_pullback_variant_builder(
        "ada_pullback_2h", settings, default_symbol="ADAUSDT")


def avax_pullback_2h_signal_builder(settings: dict) -> Dict[str, Any]:
    """AVAXUSDT 2h HTF-pullback — bybit_1 demo soak (paper_ready)."""
    return _htf_pullback_variant_builder(
        "avax_pullback_2h", settings, default_symbol="AVAXUSDT")


# ── Monitor-unit tags (aliased strategies) ─────────────────────────────────────
# Aliased strategies are a distinct config instance that REUSES a base unit via
# its signal builder (the WS-A metals + M15 equity/fx sleeves, ict_scalp_5m on
# the ict_scalp unit). The order-monitor imports a strategy's module by NAME to
# call monitor(); these strategies have no same-name module, so we tag each
# builder with the base unit module that owns its monitor() — co-located with
# the builders (the source of truth for which unit they reuse).
# pipeline.monitor_unit_for() reads this off the builder registry; plain
# strategies carry no tag and resolve to their own module. The drift guard
# tests/test_strategy_monitor_unit_resolution.py fails CI if a registered
# strategy ends up with no resolvable monitor().
for _builder, _monitor_unit in (
    (ict_scalp_signal_builder, "ict_scalp"),
    (trend_donchian_1h_signal_builder, "trend_donchian"),
    (trend_donchian_sol_signal_builder, "trend_donchian"),
    (trend_donchian_eth_signal_builder, "trend_donchian"),
    # SWAP-ROBUST prop exit variants — breakout_1 shadow soak (Unit C, DRAFT Tier-3, 2026-06-29).
    (trend_donchian_sol_prop_signal_builder, "trend_donchian"),
    (trend_donchian_eth_prop_signal_builder, "trend_donchian"),
    # trend_4h alt cells — bybit_1 demo soak (paper_ready, 2026-06-18).
    (trend_donchian_eth_4h_signal_builder, "trend_donchian"),
    (trend_donchian_sol_4h_signal_builder, "trend_donchian"),
    (trend_donchian_xrp_4h_signal_builder, "trend_donchian"),
    (trend_donchian_ada_4h_signal_builder, "trend_donchian"),
    (trend_donchian_avax_4h_signal_builder, "trend_donchian"),
    (mes_trend_long_1d_signal_builder, "trend_donchian"),
    (mgc_trend_1h_signal_builder, "trend_donchian"),
    (xauusd_trend_1h_signal_builder, "trend_donchian"),
    (spy_trend_long_1d_signal_builder, "trend_donchian"),
    (qqq_trend_long_1d_signal_builder, "trend_donchian"),
    # tqqq_trend_long_1d / qld_trend_long_1d — leveraged Nasdaq-100 ETF trend
    # cells (2026-06-30), TQQQ (3x) + QLD (2x) siblings of qqq (reuse the
    # trend_donchian unit).
    (tqqq_trend_long_1d_signal_builder, "trend_donchian"),
    (qld_trend_long_1d_signal_builder, "trend_donchian"),
    # iwm_trend_long_1d — ETF-breadth daily sweep (2026-06-20), small-cap
    # trend sibling of spy/qqq (reuses the trend_donchian unit).
    (iwm_trend_long_1d_signal_builder, "trend_donchian"),
    # splg_trend_long_1d / scha_trend_long_1d — sub-$100 proxy trend cells
    # (2026-07-07): SPLG (S&P 500 proxy for spy) + SCHA (small-cap proxy for
    # iwm), both reuse the trend_donchian unit.
    (splg_trend_long_1d_signal_builder, "trend_donchian"),
    (scha_trend_long_1d_signal_builder, "trend_donchian"),
    (mgc_pullback_1d_signal_builder, "htf_pullback_trend_2h"),
    (mhg_pullback_1d_signal_builder, "htf_pullback_trend_2h"),
    (gld_pullback_1d_signal_builder, "htf_pullback_trend_2h"),
    # tlt_pullback_1d / ief_pullback_1d — ETF-breadth daily sweep (2026-06-20),
    # bond-pullback siblings of gld (reuse the htf_pullback_trend_2h unit).
    (tlt_pullback_1d_signal_builder, "htf_pullback_trend_2h"),
    (ief_pullback_1d_signal_builder, "htf_pullback_trend_2h"),
    # slv_pullback_1d / gdx_pullback_1d — gold/silver-complex pullback siblings of
    # gld (reuse the htf_pullback_trend_2h unit); wired 2026-06-28 (Tier-3) after
    # the audit found them enabled+live in YAML but missing a builder.
    (slv_pullback_1d_signal_builder, "htf_pullback_trend_2h"),
    (gdx_pullback_1d_signal_builder, "htf_pullback_trend_2h"),
    # iaum_pullback_1d — sub-$100 gold proxy for gld (2026-07-07); reuses the
    # htf_pullback_trend_2h unit.
    (iaum_pullback_1d_signal_builder, "htf_pullback_trend_2h"),
    # gld_pullback_1h / slv_trend_1h — intraday 1h ETF pilot (2026-06-20 § 0e):
    # GLD 1h bidirectional pullback (htf_pullback_trend_2h unit) + SLV 1h
    # bidirectional Donchian trend (trend_donchian unit, both-sides).
    (gld_pullback_1h_signal_builder, "htf_pullback_trend_2h"),
    (slv_trend_1h_signal_builder, "trend_donchian"),
    # intraday ETF rollout 2b (2026-06-20 § 0e) — completes the intraday ETF
    # sleeve: SPY/QQQ/TLT 1h bidirectional pullback (htf_pullback_trend_2h unit)
    # + USO 1h LONG-ONLY Donchian trend (trend_donchian unit).
    (spy_pullback_1h_signal_builder, "htf_pullback_trend_2h"),
    (qqq_pullback_1h_signal_builder, "htf_pullback_trend_2h"),
    (tlt_pullback_1h_signal_builder, "htf_pullback_trend_2h"),
    (uso_trend_1h_signal_builder, "trend_donchian"),
    (eth_pullback_2h_signal_builder, "htf_pullback_trend_2h"),
    # swap-robust prop variant — breakout_1 shadow soak (DRAFT, Tier-3, 2026-06-25).
    (eth_pullback_prop_2h_signal_builder, "htf_pullback_trend_2h"),
    # pullback_2h alt cells — bybit_1 demo soak (paper_ready, 2026-06-18).
    (sol_pullback_2h_signal_builder, "htf_pullback_trend_2h"),
    (xrp_pullback_2h_signal_builder, "htf_pullback_trend_2h"),
    (ada_pullback_2h_signal_builder, "htf_pullback_trend_2h"),
    (avax_pullback_2h_signal_builder, "htf_pullback_trend_2h"),
):
    _builder.monitor_unit = _monitor_unit
