"""
ICT signal-builder factory (M7 Phase 2.4).

This module composes the pieces built earlier in the M7 Phase 2 sprint
(``ICTSignalsAnalyzer`` for FVG / Order Blocks / kill-zones plus
``htf_trend_bias`` for direction confluence) into a single **pure**
function that returns a signal dict in the shape the runtime pipeline
expects::

    {"symbol": str, "side": "buy" | "sell" | "none",
     "qty": float, "meta": dict}

The builder does **not** fetch market data, place orders, or write to
the database. Wiring it into ``src/runtime/pipeline.py`` (registering
``"ict"`` in ``_STRATEGY_BUILDERS``) is intentionally deferred to its
own checkpoint — see ``docs/claude/checkpoints/CHECKPOINT_LOG.md``.

Gates (all must pass for an actionable signal)
----------------------------------------------

1. **HTF trend bias**: ``htf_trend_bias()`` on the supplied trend frame
   (``htf_df`` if provided, else the same candles frame) must be
   ``"bullish"`` or ``"bearish"``. ``"neutral"`` short-circuits to
   ``side="none"``.

2. **Kill-zone gate** (optional): if ``settings["ICT_REQUIRE_KILLZONE"]``
   is truthy (default ``True``), the most recent candle's timestamp must
   fall inside at least one of the ``ICTSignalsAnalyzer`` kill-zones
   (asia / london / new_york). Out-of-zone ticks return ``side="none"``.

3. **Entry trigger**: the most recent **unfilled** FVG aligned with the
   trend bias, OR the most recent Order Block aligned with the trend
   bias. If neither exists, ``side="none"`` with ``reason=
   "no_aligned_zone"``.

When all gates pass, ``side`` is ``"buy"`` for bullish bias and
``"sell"`` for bearish bias, and ``qty`` is taken from
``settings["MAX_QTY"]`` / ``settings["max_qty"]`` (default ``1.0``).

The returned ``meta`` always includes ``strategy_name="ict"`` plus
diagnostic fields so the pipeline's signal-writer can persist what
was detected even when no trade is taken.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from src.core.signals import ICTSignalsAnalyzer
from src.ict_detection.trend import htf_trend_bias


_DEFAULT_SYMBOL = "BTCUSDT"


def _coerce_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in {"true", "1", "yes", "y", "on"}:
        return True
    if s in {"false", "0", "no", "n", "off"}:
        return False
    return default


def _settings_get(settings: dict, *keys: str, default: Any = None) -> Any:
    for k in keys:
        if k in settings and settings[k] is not None:
            return settings[k]
    return default


def _resolve_qty(settings: dict) -> float:
    raw = _settings_get(settings, "MAX_QTY", "max_qty", default=1.0)
    try:
        qty = float(raw)
    except (TypeError, ValueError):
        qty = 1.0
    if qty <= 0:
        qty = 1.0
    return qty


def _resolve_symbol(settings: dict) -> str:
    return str(_settings_get(settings, "SYMBOL", "symbol", default=_DEFAULT_SYMBOL))


def _latest_killzone(kill_zones: dict) -> Optional[str]:
    """Return the name of the kill-zone that is active on the most recent
    timestamp present across the kill-zone masks, or ``None``."""
    if not kill_zones:
        return None
    # All zones share the same DatetimeIndex; pick any to find the last ts.
    any_zone = next(iter(kill_zones.values()))
    if not any_zone:
        return None
    last_ts = max(any_zone.keys())
    for name, mask in kill_zones.items():
        if mask.get(last_ts):
            return name
    return None


def _pick_entry_zone(
    fvgs: list,
    order_blocks: list,
    bias: str,
) -> Optional[Dict[str, Any]]:
    """
    Select the most recent entry zone aligned with *bias*.

    Preference order: unfilled FVG first (more reactive ICT entry), then
    Order Block. ``None`` when nothing aligns.
    """
    target = "bullish" if bias == "bullish" else "bearish"

    aligned_fvgs = [
        f for f in (fvgs or [])
        if isinstance(f, dict)
        and f.get("type") == target
        and not f.get("filled", False)
    ]
    if aligned_fvgs:
        # FVG dicts carry ``end_time`` (most recent first when ties broken
        # by detection order). Use ``end_time`` to pick the latest.
        latest = max(
            aligned_fvgs,
            key=lambda f: f.get("end_time") or f.get("start_time") or 0,
        )
        return {"kind": "fvg", "zone": latest}

    aligned_obs = [
        ob for ob in (order_blocks or [])
        if isinstance(ob, dict) and ob.get("type") == target
    ]
    if aligned_obs:
        latest = max(
            aligned_obs,
            key=lambda ob: ob.get("timestamp") or 0,
        )
        return {"kind": "ob", "zone": latest}

    return None


def build_ict_signal(
    candles_df: pd.DataFrame,
    settings: Optional[dict] = None,
    htf_df: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    """
    Build an ICT signal dict from OHLCV candles.

    Parameters
    ----------
    candles_df : pd.DataFrame
        OHLCV frame with a DatetimeIndex (UTC or tz-naive). Used for
        FVG / OB / kill-zone detection. Must have columns
        ``open, high, low, close, volume``.
    settings : dict, optional
        Pipeline-style settings dict. Recognised keys:

        - ``SYMBOL`` / ``symbol``: market symbol label (default ``"BTCUSDT"``).
        - ``MAX_QTY`` / ``max_qty``: trade size when actionable (default ``1.0``).
        - ``ICT_FVG_MIN_GAP``: forwarded to ``ICTSignalsAnalyzer``.
        - ``ICT_OB_LOOKBACK``: forwarded to ``ICTSignalsAnalyzer``.
        - ``ICT_OB_BODY_MIN_PCT``: forwarded to ``ICTSignalsAnalyzer``.
        - ``ICT_SWING_BARS``: forwarded to ``ICTSignalsAnalyzer``.
        - ``ICT_TREND_FAST`` / ``ICT_TREND_SLOW``: EMA spans for the trend
          gate (defaults ``20`` / ``50``).
        - ``ICT_TREND_SOURCE``: column name for the trend EMA (default
          ``"close"``).
        - ``ICT_REQUIRE_KILLZONE``: bool; when truthy (default ``True``)
          the most recent candle must be inside a kill-zone for the
          signal to fire.
    htf_df : pd.DataFrame, optional
        Higher-timeframe OHLCV for the trend gate. When ``None`` the
        function falls back to ``candles_df``. Either frame is accepted
        unchanged — ``htf_trend_bias`` does not resample.

    Returns
    -------
    dict
        ``{symbol, side, qty, meta}`` where ``side ∈ {"buy", "sell", "none"}``.
        ``meta`` always contains ``strategy_name="ict"`` plus diagnostic
        fields (``trend_bias``, ``kill_zone``, ``fvgs``, ``order_blocks``,
        ``reason`` when ``side="none"``).
    """
    settings = dict(settings or {})
    symbol = _resolve_symbol(settings)
    qty = _resolve_qty(settings)

    base_meta: Dict[str, Any] = {
        "strategy_name": "ict",
        "symbol": symbol,
    }

    def _flat(reason: str, **extra) -> Dict[str, Any]:
        meta = {**base_meta, "reason": reason}
        meta.update(extra)
        return {"symbol": symbol, "side": "none", "qty": 0.0, "meta": meta}

    if candles_df is None or len(candles_df) == 0:
        return _flat("empty_candles")

    # Trend gate ------------------------------------------------------
    trend_frame = htf_df if htf_df is not None else candles_df
    trend_source = str(
        _settings_get(settings, "ICT_TREND_SOURCE", default="close")
    )
    trend_fast = int(_settings_get(settings, "ICT_TREND_FAST", default=20))
    trend_slow = int(_settings_get(settings, "ICT_TREND_SLOW", default=50))

    if trend_source not in trend_frame.columns:
        return _flat(
            "trend_source_missing",
            trend_source=trend_source,
        )

    bias = htf_trend_bias(
        trend_frame,
        fast=trend_fast,
        slow=trend_slow,
        source=trend_source,
    )
    base_meta["trend_bias"] = bias

    if bias == "neutral":
        return _flat("trend_neutral", trend_bias=bias)

    # ICT analyzer (FVG + OB + kill-zones) ----------------------------
    analyzer = ICTSignalsAnalyzer(
        symbol=symbol,
        fvg_min_gap=float(_settings_get(settings, "ICT_FVG_MIN_GAP", default=0.0)),
        ob_lookback=int(_settings_get(settings, "ICT_OB_LOOKBACK", default=20)),
        ob_body_min_pct=float(
            _settings_get(settings, "ICT_OB_BODY_MIN_PCT", default=0.0)
        ),
        swing_bars=int(_settings_get(settings, "ICT_SWING_BARS", default=5)),
    )

    try:
        signals = analyzer.analyze(candles_df)
    except Exception as exc:  # pragma: no cover - defensive
        return _flat("analyzer_error", error=str(exc))

    fvgs = signals.get("fvgs") or []
    order_blocks = signals.get("order_blocks") or []
    kill_zones = signals.get("kill_zones") or {}

    # Kill-zone gate --------------------------------------------------
    require_kz = _coerce_bool(
        _settings_get(settings, "ICT_REQUIRE_KILLZONE", default=True),
        default=True,
    )
    active_kz = _latest_killzone(kill_zones)
    base_meta["kill_zone"] = active_kz

    if require_kz and active_kz is None:
        return _flat(
            "killzone_inactive",
            trend_bias=bias,
            kill_zone=None,
            fvgs=fvgs,
            order_blocks=order_blocks,
        )

    # Entry trigger ---------------------------------------------------
    trigger = _pick_entry_zone(fvgs, order_blocks, bias)
    if trigger is None:
        return _flat(
            "no_aligned_zone",
            trend_bias=bias,
            kill_zone=active_kz,
            fvgs=fvgs,
            order_blocks=order_blocks,
        )

    side = "buy" if bias == "bullish" else "sell"
    meta = {
        **base_meta,
        "kill_zone": active_kz,
        "trigger_kind": trigger["kind"],
        "trigger_zone": trigger["zone"],
        "fvgs": fvgs,
        "order_blocks": order_blocks,
    }

    return {
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "meta": meta,
    }


__all__ = ["build_ict_signal"]
