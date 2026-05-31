"""HTF trend-pullback continuation — units-layer adapter (SCAFFOLD, not wired).

Rank-2 candidate from the new-strategy research pass
(docs/research/new-strategy-candidates-2026-05-31.md). **Not yet wired** into
``strategy_signal_builders.py``, ``intents.py``, or ``config/strategies.yaml``
(registration is explicit; no auto-discovery) — inert until the Tier-3
activation PR. Backtested via ``scripts/backtest_pullback.py`` first.

Strategy summary
----------------
Trend-continuation via a mean-reversion ENTRY. In an established trend,
pullbacks to a dynamic level (Donchian midline / EMA) overshoot on retail
panic and revert in the trend direction. You capture the trend payoff but
enter on weakness (better R:R) instead of chasing the breakout bar.

Why it complements ``trend_donchian`` — the flip-safety argument
----------------------------------------------------------------
This is the deliberate anti-correlation-by-construction play, and its key
property is STRUCTURAL, not a backtest hope:

  trend_donchian and squeeze enter on STRENGTH (breakout/expansion). This
  enters on WEAKNESS within the SAME trend. So when trend_donchian is mid-
  trade riding a runner up, this strategy wants to ADD on the retrace — the
  same side. Because both want the same side in a trend, their conflicts in
  the intent layer are SAME-SIDE (resolved to max-qty, no flip) rather than
  OPPOSITE-SIDE (flip-churn).

That directly honours the #1 system finding (flip-churn is the dominant cost;
``FLIP_POLICY=hold`` is now the live default). A pullback member cannot
re-introduce the churn that ``fade``/``turtle`` caused — it is flip-safe
against the live winner by construction. The risk it must clear is the
opposite: being so correlated with trend that it adds no diversification —
which is exactly what the backtest's corr-vs-trend gate checks.

Entry
-----
Require an HTF uptrend (close > Donchian-``trend_lookback`` midline, i.e. the
midline rising / price above it) AND a short-term pullback (close has pulled
back into the lower ``pullback_frac`` of the recent ``pullback_lookback``
range). Enter LONG on a reversal-confirmation bar (a bullish close off the
pullback low). Symmetric for downtrends/shorts. Anything else is
non-actionable (ValueError → side="none").

Exit
----
The VERBATIM shared Chandelier ATR trail (copied from trend_donchian) — let
the continuation run; NOT a tight target (the program's iron law: every
tight-target strategy died on BTC fees). Far ~50R ``tp`` sentinel +
``timeout_bars`` backstop. Frozen entry-time ATR in ``meta``.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

import pandas as pd

from src.units.strategies._base import require_candles

_DEFAULTS: Dict[str, Any] = {
    "trend_lookback": 50,        # Donchian window whose midline defines the trend
    "pullback_lookback": 10,     # recent-range window for the pullback test
    "pullback_frac": 0.33,       # close must be in the lower (long) third of it
    "atr_period": 14,
    "atr_stop_mult": 2.5,
    "trail_mult": 3.0,
    "tp_r": 50.0,
    "timeframe": "2h",
    "min_confidence": 0.0,
}

_TP_SENTINEL_CAP_PCT = 0.099


def _resolve_params(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return {key: cfg.get(key, default) for key, default in _DEFAULTS.items()}


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    h, low, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([(h - low), (h - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=1).mean()


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def order_package(cfg: dict, candles_df: Optional[pd.DataFrame] = None) -> dict:
    """Build an htf_pullback_trend_2h OrderPackage dict. Raises ValueError on
    any non-actionable tick (no trend, no pullback, no confirmation, etc.)."""
    candles_df = require_candles(candles_df, "htf_pullback_trend_2h")
    params = _resolve_params(cfg)
    symbol = cfg.get("symbol") or cfg.get("SYMBOL") or "BTCUSDT"

    trend_lb = int(params["trend_lookback"])
    pull_lb = int(params["pullback_lookback"])
    pull_frac = float(params["pullback_frac"])
    atr_period = int(params["atr_period"])
    atr_stop_mult = float(params["atr_stop_mult"])
    timeframe = str(cfg.get("timeframe") or params["timeframe"])

    needed = trend_lb + atr_period + 2
    if len(candles_df) < needed:
        raise ValueError(
            f"Strategy 'htf_pullback_trend_2h': need at least {needed} candles "
            f"for the trend({trend_lb}) / atr({atr_period}) windows; got "
            f"{len(candles_df)}."
        )

    df = candles_df.reset_index(drop=True)
    atr_series = _atr(df, atr_period)
    # Trend filter: Donchian midline of the prior trend_lb bars (shift(1), no
    # lookahead). Price above a rising midline = uptrend; below = downtrend.
    dc_hi = df["high"].rolling(trend_lb).max().shift(1)
    dc_lo = df["low"].rolling(trend_lb).min().shift(1)
    midline = (dc_hi + dc_lo) / 2.0
    # Recent range for the pullback test (prior pull_lb bars, shift(1)).
    pr_hi = df["high"].rolling(pull_lb).max().shift(1)
    pr_lo = df["low"].rolling(pull_lb).min().shift(1)

    atr = float(atr_series.iloc[-1]) if pd.notna(atr_series.iloc[-1]) else 0.0
    close = float(df["close"].iloc[-1])
    prev_close = float(df["close"].iloc[-2])
    mid = midline.iloc[-1]
    rhi, rlo = pr_hi.iloc[-1], pr_lo.iloc[-1]
    if atr <= 0 or pd.isna(mid) or pd.isna(rhi) or pd.isna(rlo):
        raise ValueError("Strategy 'htf_pullback_trend_2h': indicators undefined (non-actionable).")
    mid, rhi, rlo = float(mid), float(rhi), float(rlo)
    rng = rhi - rlo
    if rng <= 0:
        raise ValueError("Strategy 'htf_pullback_trend_2h': degenerate recent range (non-actionable).")

    # Position within the recent range, 0=at low .. 1=at high.
    pos_in_range = (close - rlo) / rng
    uptrend = close > mid
    downtrend = close < mid

    direction: Optional[str] = None
    if uptrend and pos_in_range <= pull_frac and close > prev_close:
        # Pullback into the lower third of the range + a bullish confirmation bar.
        direction = "long"
        # Confidence: how strong the trend is (distance above midline / ATR).
        depth = (close - mid) / atr
    elif downtrend and pos_in_range >= (1 - pull_frac) and close < prev_close:
        direction = "short"
        depth = (mid - close) / atr
    else:
        raise ValueError(
            "Strategy 'htf_pullback_trend_2h': no trend-pullback-confirmation "
            "setup on the latest bar (non-actionable)."
        )

    entry = close
    if direction == "long":
        sl = entry - atr_stop_mult * atr
        risk = entry - sl
        tp = min(entry * (1 + _TP_SENTINEL_CAP_PCT), entry + float(params["tp_r"]) * risk)
    else:
        sl = entry + atr_stop_mult * atr
        risk = sl - entry
        tp = max(entry * (1 - _TP_SENTINEL_CAP_PCT), entry - float(params["tp_r"]) * risk)
    if risk <= 0:
        raise ValueError("Strategy 'htf_pullback_trend_2h': non-positive risk; skipping.")

    confidence = round(min(max(depth, 0.0), 1.0), 4)
    min_confidence = float(params["min_confidence"])
    if confidence < min_confidence:
        raise ValueError(
            f"Strategy 'htf_pullback_trend_2h': confidence {confidence} below "
            f"min_confidence {min_confidence} — non-actionable."
        )

    try:
        entry_time = str(df["timestamp"].iloc[-1])
    except (KeyError, IndexError):
        entry_time = None

    return {
        "symbol": symbol,
        "direction": direction,
        "entry": round(entry, 8),
        "sl": round(float(sl), 8),
        "tp": round(float(tp), 8),
        "confidence": confidence,
        "meta": {
            "trend_midline": mid,
            "pullback_pos_in_range": round(pos_in_range, 4),
            "atr": atr,
            "atr_period": atr_period,
            "atr_stop_mult": atr_stop_mult,
            "trail_mult": float(params["trail_mult"]),
            "tp_r": float(params["tp_r"]),
            "risk_per_unit": float(risk),
            "entry_time": entry_time,
            "timeframe": timeframe,
        },
    }


# ---------------------------------------------------------------------------
# monitor() — VERBATIM Chandelier ATR trail (copied from trend_donchian).
# ---------------------------------------------------------------------------
def _since_entry(candles_df: pd.DataFrame, open_pkg: Dict[str, Any]) -> pd.DataFrame:
    meta = open_pkg.get("meta") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta) if meta else {}
        except Exception:  # noqa: BLE001
            meta = {}
    entry_ts = (meta.get("entry_time") if isinstance(meta, dict) else None) or \
        open_pkg.get("created_at")
    if entry_ts is None or "timestamp" not in getattr(candles_df, "columns", []):
        return candles_df
    try:
        ts = pd.to_datetime(candles_df["timestamp"], utc=True, errors="coerce")
        cutoff = pd.to_datetime(entry_ts, utc=True, errors="coerce")
        if pd.isna(cutoff):
            return candles_df
        filtered = candles_df[ts >= cutoff]
        return filtered if len(filtered) > 0 else candles_df
    except Exception:  # noqa: BLE001
        return candles_df


def monitor(cfg, candles_df, open_pkg):
    """Identical contract to ``trend_donchian.monitor`` — see that module."""
    if candles_df is None or len(candles_df) == 0:
        return None
    try:
        current_price = float(candles_df["close"].iloc[-1])
    except (KeyError, IndexError, ValueError, TypeError):
        return None
    try:
        sl = float(open_pkg["sl"])
        direction = str(open_pkg["direction"]).lower()
    except (KeyError, TypeError, ValueError):
        return None
    if direction not in ("long", "short"):
        return None

    meta = open_pkg.get("meta") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta) if meta else {}
        except Exception:  # noqa: BLE001
            meta = {}
    if not isinstance(meta, dict):
        meta = {}
    cfg_dict = cfg if isinstance(cfg, dict) else {}

    if direction == "long" and current_price <= sl:
        return {"action": "close", "reason": "sl_cross", "exit_price": current_price}
    if direction == "short" and current_price >= sl:
        return {"action": "close", "reason": "sl_cross", "exit_price": current_price}

    tp = _coerce_float(open_pkg.get("tp"))
    if tp is not None:
        if direction == "long" and current_price >= tp:
            return {"action": "close", "reason": "tp_cross", "exit_price": current_price}
        if direction == "short" and current_price <= tp:
            return {"action": "close", "reason": "tp_cross", "exit_price": current_price}

    atr = _coerce_float(meta.get("atr"))
    if atr is None or atr <= 0:
        period = int(meta.get("atr_period") or cfg_dict.get("atr_period") or _DEFAULTS["atr_period"])
        try:
            atr = float(_atr(candles_df, period).iloc[-1])
        except Exception:  # noqa: BLE001
            return None
    if atr is None or atr <= 0:
        return None

    trail_mult = (
        _coerce_float(meta.get("trail_mult"))
        or _coerce_float(cfg_dict.get("trail_mult"))
        or float(_DEFAULTS["trail_mult"])
    )
    window = _since_entry(candles_df, open_pkg)
    try:
        if direction == "long":
            ext = float(window["high"].max())
            candidate = ext - trail_mult * atr
            if candidate > sl and candidate < current_price:
                return {"sl": round(candidate, 8)}
        else:
            ext = float(window["low"].min())
            candidate = ext + trail_mult * atr
            if candidate < sl and candidate > current_price:
                return {"sl": round(candidate, 8)}
    except (KeyError, ValueError, TypeError):
        return None
    return None
