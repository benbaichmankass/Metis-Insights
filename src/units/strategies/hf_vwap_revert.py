"""HF VWAP/band mean-reversion (research candidate B) — RESEARCH-ONLY.

Candidate B of the high-frequency prop-pass research effort
(``docs/research/hf-prop-strategy-research-plan-2026-06-16.md``). Exposes the
engine contract ``order_package(cfg, candles_df)`` +
``monitor(cfg, candles_df, open_pkg)`` (the
``scripts/backtest_system.py::ROSTER`` shape). **Registered in the research
harness ROSTER only** — NOT in ``config/strategies.yaml``, never on the live
order path. Promotion is a separate Tier-3 step gated on a clean OOS pass.

Thesis (research-plan family B)
-------------------------------
An ORTHOGONAL edge to the roster's all-directional trend/breakout members:
fade a stretched 5m excursion back toward an intraday VWAP anchor, but ONLY in
a ranging (low-ADX) regime — that is exactly the regime where the
trend-followers are flat, so it diversifies away the roster's correlation.
Target a high win rate (~55-60%) at R~1.0 (mean-reversion economics).

A signal fires on the most recent closed bar when ALL of:

1. **Ranging regime** — ADX(``adx_period``) of the prior bar < ``adx_max``.
   A VWAP fade in a trend is the classic "catch a falling knife" loser; the
   ADX gate confines it to chop.
2. **Stretch** — close is at least ``band_k`` rolling-std deviations away from
   the rolling VWAP anchor (a Bollinger-on-VWAP band). Above the upper band →
   short candidate; below the lower band → long candidate.
3. **Wick rejection back toward the anchor** on the current bar: short = the
   bar made a higher high then CLOSED back BELOW the upper band with a bearish
   body; long = mirror at the lower band. (The same wick-rejection confirmation
   ict_scalp/fvg_range use, applied to a band edge — avoids fading into
   continued momentum.)

On a signal:
  * ``entry`` = close of the most recent bar
  * ``sl``    = beyond the excursion extreme by ``atr_stop_buffer`` × ATR
                (a band break = regime change = thesis invalidated)
  * ``tp``    = the VWAP anchor scaled by ``tp_anchor_frac`` (1.0 = full
                reversion to VWAP), but never worse than ``min_tp_r`` × risk
                and the realised R is what the backtest measures.
  * ``confidence`` = blend of stretch depth + how cleanly the bar rejected.

VWAP anchor: a rolling volume-weighted average price over ``vwap_lookback``
bars (a session-agnostic rolling anchor — robust to the resampled feed not
carrying explicit session boundaries; the 5m feed the harness resamples DOES
carry ``volume``). Falls back to a simple rolling mean if volume is absent.

Pure signal generator (no dry/live awareness, no qty).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np
import pandas as pd


_DEFAULTS: Dict[str, Any] = {
    "timeframe": "5m",
    # FROZEN IS-best (see NOTE): band_k 3.0 / adx_max 16 / buf 1.0 /
    # tp_anchor_frac 0.7 / min_tp_r 1.0 / vwlb 144 was the least-negative IS
    # cell — still E_R -0.13 (net-negative), so this freeze documents a clean
    # negative result, not a passing config.
    "vwap_lookback": 144,         # ~12h of 5m bars — an intraday rolling anchor
    "atr_period": 14,
    "adx_period": 14,
    "adx_max": 16.0,             # ranging-only gate (tighter chop)
    "band_k": 3.0,               # std-devs from VWAP that count as "stretched"
    "band_std_lookback": 144,    # rolling-std window for the band
    "atr_stop_buffer": 1.0,      # SL this many ATR beyond the excursion extreme
    # Minimum stop distance, in ATR units AND % of price. A wick-rejection
    # entry (close) can sit microscopically close to the excursion extreme, so
    # the raw risk can be a few bps — which then (a) explodes the R-normalized
    # loss when the next bar gaps through the stop and (b) makes the live
    # fixed-fractional sizer take an enormous position (one gap = account
    # death). Floor the stop distance so the trade has real breathing room.
    "min_stop_atr": 0.75,        # stop must be >= this many ATR from entry
    "min_stop_pct": 0.003,       # ... and >= this fraction of price (0.3%)
    "tp_anchor_frac": 0.7,       # target = VWAP * frac of the gap (FROZEN IS-best)
    "min_tp_r": 1.0,             # floor the target so a near-anchor entry still has room (FROZEN)
    "session_filter_enabled": False,  # mean-reversion runs all-hours by default
    "killzone_windows": "0-24",
}


def _resolve_params(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return {key: cfg.get(key, default) for key, default in _DEFAULTS.items()}


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    h, low, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([(h - low), (h - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


def _adx(df: pd.DataFrame, period: int) -> pd.Series:
    """Wilder's ADX — same formula as fvg_range_15m / fade_breakout_4h."""
    h, low, c = df["high"], df["low"], df["close"]
    up = h.diff(); down = -low.diff()
    plus_dm = ((up > down) & (up > 0)) * up.clip(lower=0)
    minus_dm = ((down > up) & (down > 0)) * down.clip(lower=0)
    pc = c.shift(1)
    tr = pd.concat([(h - low), (h - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    alpha = 1.0 / period
    atr = tr.ewm(alpha=alpha, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=alpha, adjust=False).mean() / atr.replace(0, float("nan"))
    minus_di = 100 * minus_dm.ewm(alpha=alpha, adjust=False).mean() / atr.replace(0, float("nan"))
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, float("nan"))
    return dx.ewm(alpha=alpha, adjust=False).mean()


def _rolling_vwap(df: pd.DataFrame, lookback: int) -> pd.Series:
    """Rolling volume-weighted average of the typical price over ``lookback``
    bars. Falls back to a rolling mean of close when no volume column."""
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    if "volume" in df.columns and df["volume"].fillna(0).abs().sum() > 0:
        vol = df["volume"].fillna(0.0)
        num = (tp * vol).rolling(lookback, min_periods=lookback).sum()
        den = vol.rolling(lookback, min_periods=lookback).sum().replace(0, float("nan"))
        return num / den
    return df["close"].rolling(lookback, min_periods=lookback).mean()


def _parse_windows(spec: str):
    out = []
    for part in str(spec).split(","):
        part = part.strip()
        if not part or "-" not in part:
            continue
        a, b = part.split("-", 1)
        try:
            out.append((int(a), int(b)))
        except ValueError:
            continue
    return out


def _in_session(df, *, enabled, windows_spec) -> bool:
    if not enabled:
        return True
    windows = _parse_windows(windows_spec)
    if not windows:
        return True
    try:
        ts = pd.Timestamp(df["timestamp"].iloc[-1]) if "timestamp" in df.columns else pd.Timestamp(df.index[-1])
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        hour = int(ts.hour)
    except Exception:
        return True
    for start, end in windows:
        if start <= end:
            if start <= hour < end:
                return True
        else:
            if hour >= start or hour < end:
                return True
    return False


def order_package(cfg: dict, candles_df: Optional[pd.DataFrame] = None) -> dict:
    """Build an HF VWAP-band mean-reversion order package dict.

    Raises ValueError (non-actionable) when candles are absent/too few, the
    regime is trending (ADX too high), price isn't stretched past a band, or
    the current bar doesn't reject back toward the anchor.
    """
    if candles_df is None or (hasattr(candles_df, "empty") and candles_df.empty):
        raise ValueError("hf_vwap_revert: candles_df required.")
    params = _resolve_params(cfg)
    symbol = cfg.get("symbol") or "BTCUSDT"
    timeframe = str(cfg.get("timeframe") or params["timeframe"])

    if {"open", "high", "low", "close"} - set(candles_df.columns):
        raise ValueError("hf_vwap_revert: missing OHLC columns.")

    vwap_lookback = int(params["vwap_lookback"])
    atr_period = int(params["atr_period"])
    adx_period = int(params["adx_period"])
    band_std_lookback = int(params["band_std_lookback"])
    needed = max(vwap_lookback, atr_period, adx_period, band_std_lookback) + 3
    if len(candles_df) < needed:
        raise ValueError("hf_vwap_revert: too few candles.")

    if not _in_session(candles_df, enabled=bool(params["session_filter_enabled"]),
                       windows_spec=params["killzone_windows"]):
        raise ValueError("hf_vwap_revert: last bar outside session window.")

    df = candles_df.reset_index(drop=True)
    df["atr"] = _atr(df, atr_period)
    df["adx"] = _adx(df, adx_period).shift(1)   # prior-bar ADX, no lookahead
    df["vwap"] = _rolling_vwap(df, vwap_lookback)
    # Band = rolling std of (close - vwap) — a deviation band centered on VWAP.
    dev = df["close"] - df["vwap"]
    df["band_std"] = dev.rolling(band_std_lookback, min_periods=band_std_lookback).std()

    i = len(df) - 1
    atr = float(df["atr"].iloc[i]) if pd.notna(df["atr"].iloc[i]) else 0.0
    vwap = float(df["vwap"].iloc[i]) if pd.notna(df["vwap"].iloc[i]) else float("nan")
    band_std = float(df["band_std"].iloc[i]) if pd.notna(df["band_std"].iloc[i]) else float("nan")
    adx_i = float(df["adx"].iloc[i]) if pd.notna(df["adx"].iloc[i]) else float("nan")
    if not (atr > 0) or np.isnan(vwap) or np.isnan(band_std) or band_std <= 0:
        raise ValueError("hf_vwap_revert: anchor/band/ATR undefined on latest bar.")

    if np.isnan(adx_i) or adx_i >= float(params["adx_max"]):
        raise ValueError(f"hf_vwap_revert: regime not ranging (ADX={adx_i}).")

    band_k = float(params["band_k"])
    upper = vwap + band_k * band_std
    lower = vwap - band_k * band_std

    o = float(df["open"].iloc[i]); c = float(df["close"].iloc[i])
    h = float(df["high"].iloc[i]); lo = float(df["low"].iloc[i])
    bull_body = c > o; bear_body = c < o

    # Direction + wick-rejection at the stretched band edge.
    if h >= upper:
        # short candidate: poked above upper band then closed back below it
        direction = "short"
        if not (c < upper and bear_body):
            raise ValueError("hf_vwap_revert: upper-band poke but no bearish rejection.")
        excursion_extreme = h
    elif lo <= lower:
        direction = "long"
        if not (c > lower and bull_body):
            raise ValueError("hf_vwap_revert: lower-band poke but no bullish rejection.")
        excursion_extreme = lo
    else:
        raise ValueError("hf_vwap_revert: price not stretched past a band.")

    entry = c
    buf = float(params["atr_stop_buffer"]) * atr
    # Floor the stop distance so a near-extreme entry doesn't produce a
    # microscopic (fee-dominated, gap-fragile, sizing-exploding) risk.
    min_stop = max(float(params["min_stop_atr"]) * atr,
                   float(params["min_stop_pct"]) * entry)
    if direction == "long":
        sl = min(excursion_extreme - buf, entry - min_stop)
        risk = entry - sl
    else:
        sl = max(excursion_extreme + buf, entry + min_stop)
        risk = sl - entry
    if risk <= 0:
        raise ValueError("hf_vwap_revert: non-positive risk.")

    # Target: revert toward the VWAP anchor (scaled), floored at min_tp_r * risk.
    tp_anchor_frac = float(params["tp_anchor_frac"])
    min_tp_r = float(params["min_tp_r"])
    if direction == "long":
        anchor_tp = entry + tp_anchor_frac * (vwap - entry)
        floor_tp = entry + min_tp_r * risk
        tp = max(anchor_tp, floor_tp)
        if tp <= entry:
            raise ValueError("hf_vwap_revert: degenerate long target.")
    else:
        anchor_tp = entry - tp_anchor_frac * (entry - vwap)
        floor_tp = entry - min_tp_r * risk
        tp = min(anchor_tp, floor_tp)
        if tp >= entry:
            raise ValueError("hf_vwap_revert: degenerate short target.")

    stretch = abs(c - vwap) / band_std if band_std > 0 else 0.0
    stretch_norm = min(stretch / (band_k + 2.0), 1.0)
    rng = max(h - lo, 1e-12)
    if direction == "short":
        reject_frac = (h - c) / rng
    else:
        reject_frac = (c - lo) / rng
    confidence = round(min(0.5 * stretch_norm + 0.5 * float(reject_frac), 1.0), 4)

    return {
        "symbol": symbol,
        "direction": direction,
        "entry": round(float(entry), 8),
        "sl": round(float(sl), 8),
        "tp": round(float(tp), 8),
        "confidence": confidence,
        "meta": {
            "strategy_name": "hf_vwap_revert",
            "timeframe": timeframe,
            "vwap": float(vwap),
            "band_upper": float(upper),
            "band_lower": float(lower),
            "band_std": float(band_std),
            "adx": float(adx_i),
            "atr": float(atr),
            "stretch_std": float(stretch),
            "risk_per_unit": float(risk),
        },
    }


def monitor(cfg, candles_df, open_pkg):
    """No active management — the fixed SL (band break) and TP (anchor revert)
    fire naturally. A break-even trail would cut the wick-against-then-revert
    bounces the edge depends on (same reasoning as fvg_range_15m). Returns None.
    """
    return None
