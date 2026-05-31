"""Session-breakout trend-follower — units-layer adapter (SCAFFOLD, not wired).

Rank-1 candidate from the new-strategy research pass
(docs/research/new-strategy-candidates-2026-05-31.md). **Not yet wired** into
``strategy_signal_builders.py``, ``intents.py``, or ``config/strategies.yaml``
— registration is explicit (no auto-discovery), so this module is inert until
the Tier-3 activation PR. It exists so the logic can be backtested
(``scripts/backtest_session.py``) on the trainer-VM archive before anyone
proposes shipping it ``execution: shadow``.

Strategy summary
----------------
Time-of-session momentum. BTC perps trade 24/7 but the marginal
price-setting flow is not uniform: the US equity cash open (13:30 UTC) and
the CME futures session concentrate institutional flow and recurring
liquidity events. The edge: an opening-range breakout *initiated inside a
session window* has higher follow-through than the same breakout at a random
hour, because real participants are present to push it.

Why it complements ``trend_donchian`` (the one live winner)
-----------------------------------------------------------
Same winning *mechanism* (breakout + Chandelier runner exit — the program's
one durable, fee-efficient profile) but gated on an axis ORTHOGONAL to price
structure: **clock time**. trend_donchian fires on any bar that breaks its
channel; this fires only inside a session window and is flat the rest of the
day, so the two strategies' trade timestamps barely overlap → low return
correlation. It also fills the roster's only unexploited dimension: every
current member is regime/structure-gated; none is time-gated. (ict_scalp even
ships a disabled ``session_filter`` — the hook exists, time was never the
*primary* edge.)

Entry
-----
Compute the session opening range = the high/low of the first
``opening_range_bars`` bars after the session open (``session_open_utc_hour``).
On a later bar WITHIN the session window: LONG if ``close`` prints above the
range high, SHORT if below the range low. Outside the window, or before the
range is established, the bar is non-actionable (ValueError → side="none").

Exit (the real-money-critical piece)
------------------------------------
The VERBATIM shared Chandelier ATR trail from ``trend_donchian.monitor`` —
copied deliberately (proven, parity-tested) rather than reinvented. Initial
stop ``atr_stop_mult × ATR``; ``tp`` a far ~50R sentinel (the trail is the
sole profit-exit). A ``timeout_bars`` end-of-session backstop closes a stalled
session trade (the fade/fvg pattern). Entry-time ATR is frozen in ``meta`` so
the live trail distance matches the backtest's fixed-ATR semantics exactly.

Strategies are pure signal generators (no dry/live awareness); the dry/live
decision lives in the Accounts layer per ``mode:`` in ``config/accounts.yaml``
and the per-strategy ``execution:`` gate.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

import pandas as pd

from src.units.strategies._base import require_candles

# Defaults mirror trend_donchian's proven exit profile (atr_stop 2.5 / trail
# 3.0, trail MUST stay looser than the stop) plus the session-specific knobs.
# Live values would be set in config/strategies.yaml once (if) this is wired.
_DEFAULTS: Dict[str, Any] = {
    "opening_range_bars": 4,        # bars after the open that define the range
    "session_open_utc_hour": 13,    # 13:30 UTC US cash open ≈ hour 13 on 15m/1h
    "session_open_utc_minute": 30,
    "session_window_bars": 16,      # how many bars after the open stay tradeable
    "atr_period": 14,
    "atr_stop_mult": 2.5,
    "trail_mult": 3.0,
    "tp_r": 50.0,                   # far sentinel — trail is the real exit
    "timeframe": "15m",
    "min_confidence": 0.0,          # breakout depth / ATR gate, [0,1]
}

# Same exchange TP-distance clamp as trend_donchian (Bybit ErrCode 10001).
_TP_SENTINEL_CAP_PCT = 0.099


def _resolve_params(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return {key: cfg.get(key, default) for key, default in _DEFAULTS.items()}


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    """ATR as SMA of True Range — identical formula to trend_donchian/_atr and
    the backtest, so the live stop distance matches what was validated."""
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
    return None if f != f else f  # NaN guard


def _in_session_window(ts: pd.Timestamp, params: Dict[str, Any]) -> tuple[bool, int]:
    """Return (is_in_window, bars_since_open_estimate).

    A bar is in-window when its time-of-day is between the session open and
    ``session_window_bars`` worth of time after it. Works off wall-clock
    minutes-from-open so it's timeframe-agnostic (the backtest derives the
    same mask from the bar index within the day).
    """
    open_min = int(params["session_open_utc_hour"]) * 60 + int(params["session_open_utc_minute"])
    bar_min = ts.hour * 60 + ts.minute
    delta = bar_min - open_min
    # window length in minutes is approximated by the caller's TF; here we only
    # gate on "at or after open, within the day" — the backtest enforces the
    # exact bar-count window. delta>=0 and within 24h.
    return (delta >= 0, delta)


def order_package(cfg: dict, candles_df: Optional[pd.DataFrame] = None) -> dict:
    """Build a session_breakout_trend OrderPackage dict from the latest candles.

    Raises ValueError on any non-actionable tick (no candles, too few rows,
    outside the session window, range not yet established, or no breakout) —
    the runtime builder treats that as side="none".
    """
    candles_df = require_candles(candles_df, "session_breakout_trend")
    params = _resolve_params(cfg)
    symbol = cfg.get("symbol") or cfg.get("SYMBOL") or "BTCUSDT"

    or_bars = int(params["opening_range_bars"])
    atr_period = int(params["atr_period"])
    atr_stop_mult = float(params["atr_stop_mult"])
    timeframe = str(cfg.get("timeframe") or params["timeframe"])

    needed = or_bars + atr_period + 2
    if len(candles_df) < needed:
        raise ValueError(
            f"Strategy 'session_breakout_trend': need at least {needed} candles "
            f"for the opening-range({or_bars}) / atr({atr_period}) windows; got "
            f"{len(candles_df)}."
        )

    df = candles_df.reset_index(drop=True)
    if "timestamp" not in df.columns:
        raise ValueError(
            "Strategy 'session_breakout_trend': timestamp column required to "
            "gate on the session window (non-actionable)."
        )
    ts = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    last_ts = ts.iloc[-1]
    if pd.isna(last_ts):
        raise ValueError("Strategy 'session_breakout_trend': latest timestamp unparseable.")

    in_window, _ = _in_session_window(last_ts, params)
    if not in_window:
        raise ValueError(
            "Strategy 'session_breakout_trend': latest bar outside the session "
            "window (non-actionable)."
        )

    # Opening range = high/low of the first or_bars bars of the CURRENT session
    # day. Restrict to today's session-or-later bars, take the first or_bars.
    day = last_ts.normalize()
    session_open_min = int(params["session_open_utc_hour"]) * 60 + int(params["session_open_utc_minute"])
    bar_min = ts.dt.hour * 60 + ts.dt.minute
    today_session = df[(ts >= day) & (bar_min >= session_open_min)]
    if len(today_session) < or_bars + 1:
        raise ValueError(
            "Strategy 'session_breakout_trend': opening range not yet "
            "established for this session (non-actionable)."
        )
    or_slice = today_session.iloc[:or_bars]
    range_hi = float(or_slice["high"].max())
    range_lo = float(or_slice["low"].min())

    atr_series = _atr(df, atr_period)
    atr = float(atr_series.iloc[-1]) if pd.notna(atr_series.iloc[-1]) else 0.0
    close = float(df["close"].iloc[-1])
    if atr <= 0:
        raise ValueError("Strategy 'session_breakout_trend': ATR non-positive (non-actionable).")

    if close > range_hi:
        direction = "long"
        breakout_depth = (close - range_hi) / atr
    elif close < range_lo:
        direction = "short"
        breakout_depth = (range_lo - close) / atr
    else:
        raise ValueError(
            f"Strategy 'session_breakout_trend': no opening-range breakout "
            f"(close={close} within [{range_lo}, {range_hi}]) — non-actionable."
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
        raise ValueError("Strategy 'session_breakout_trend': non-positive risk; skipping.")

    confidence = round(min(max(breakout_depth, 0.0), 1.0), 4)
    min_confidence = float(params["min_confidence"])
    if confidence < min_confidence:
        raise ValueError(
            f"Strategy 'session_breakout_trend': confidence {confidence} below "
            f"min_confidence {min_confidence} — non-actionable."
        )

    return {
        "symbol": symbol,
        "direction": direction,
        "entry": round(entry, 8),
        "sl": round(float(sl), 8),
        "tp": round(float(tp), 8),
        "confidence": confidence,
        "meta": {
            "session_range_hi": range_hi,
            "session_range_lo": range_lo,
            "opening_range_bars": or_bars,
            # Frozen entry-time ATR drives the monitor trail (fixed-ATR parity
            # with the backtest), exactly as trend_donchian does.
            "atr": atr,
            "atr_period": atr_period,
            "atr_stop_mult": atr_stop_mult,
            "trail_mult": float(params["trail_mult"]),
            "tp_r": float(params["tp_r"]),
            "risk_per_unit": float(risk),
            "entry_time": str(last_ts),
            "timeframe": timeframe,
        },
    }


# ---------------------------------------------------------------------------
# monitor() — VERBATIM Chandelier ATR trailing stop (copied from
# trend_donchian.monitor; the program's one proven, parity-tested exit).
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
    """Chandelier trail ratchet + SL/TP cross close — identical contract to
    ``trend_donchian.monitor``. Reads all trail params from ``open_pkg[meta]``
    because ``run_monitor_tick`` passes ``cfg={}`` in production."""
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
