"""Per-strategy signal builder functions — extracted from pipeline.py (PR-6).

Each builder fetches candles, calls the strategy unit, and returns a
pipeline-shape signal dict: {symbol, side, price, stop_loss, take_profit,
meta}. No qty — sizing is the per-account RiskManager's job (S-026 G1).

The module-level ``_build_killzone_exchange`` shim is kept as a named
attribute so tests can monkeypatch it: ``monkeypatch.setattr(
strategy_signal_builders, "_build_killzone_exchange", ...)``.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import pandas as pd

from src.utils.signal_audit_logger import log_signal

logger = logging.getLogger(__name__)


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
        meta} where side ∈ {"buy", "sell", "none"}. S-026 G1: no qty —
        sizing is the per-account RiskManager's job.
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
            log_signal(
                {
                    "event": "turtle_soup_eval",
                    "strategy": "turtle_soup",
                    "symbol": symbol,
                    "side": "none",
                    "reason": str(exc),
                    "stage_rejections": stage_rejections,
                }
            )
        except Exception:  # noqa: BLE001
            logger.exception("Turtle Soup: dedicated audit emit failed")
        meta: Dict[str, Any] = {
            "strategy_name": "turtle_soup",
            "reason": str(exc),
        }
        if stage_rejections is not None:
            meta["stage_rejections"] = stage_rejections
        return {
            "symbol": symbol,
            "side": "none",
            "meta": meta,
        }

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
        log_signal(
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
        )
    except Exception:  # noqa: BLE001
        logger.exception("Turtle Soup: dedicated audit emit failed")
    return {
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
        return {
            "symbol": symbol,
            "side": "none",
            "meta": {
                "strategy_name": "ict_scalp_5m",
                "reason": "disabled_in_yaml",
            },
        }

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

    cfg: Dict[str, Any] = {"symbol": symbol, "timeframe": timeframe, **ict_cfg}

    try:
        pkg = order_package(cfg, candles_df=candles_df)
    except ValueError as exc:
        logger.info("ict_scalp_5m: no actionable signal (%s)", exc)
        try:
            log_signal({
                "event": "ict_scalp_eval",
                "strategy": "ict_scalp_5m",
                "symbol": symbol,
                "timeframe": timeframe,
                "side": "none",
                "reason": str(exc),
            })
        except Exception:  # noqa: BLE001
            logger.exception("ict_scalp_5m: dedicated audit emit failed")
        return {
            "symbol": symbol,
            "side": "none",
            "meta": {
                "strategy_name": "ict_scalp_5m",
                "reason": str(exc),
            },
        }

    side = "buy" if pkg["direction"] == "long" else "sell"
    logger.info(
        "ict_scalp_5m: %s signal at %s (entry=%s sl=%s tp=%s confidence=%.3f)",
        side, symbol, pkg["entry"], pkg["sl"], pkg["tp"], pkg["confidence"],
    )
    pkg_meta = pkg.get("meta") or {}
    try:
        log_signal({
            "event": "ict_scalp_eval",
            "strategy": "ict_scalp_5m",
            "symbol": symbol,
            "timeframe": timeframe,
            "side": side,
            "entry": pkg["entry"],
            "stop_loss": pkg["sl"],
            "take_profit": pkg["tp"],
            "confidence": pkg["confidence"],
        })
    except Exception:  # noqa: BLE001
        logger.exception("ict_scalp_5m: dedicated audit emit failed")

    return {
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
        log_signal({
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
        })
    except Exception:  # noqa: BLE001
        logger.exception("VWAP: dedicated audit emit failed")
    return sig
