"""HF displacement-continuation (research candidate A) — RESEARCH-ONLY.

Candidate A of the high-frequency prop-pass research effort
(``docs/research/hf-prop-strategy-research-plan-2026-06-16.md``). This is a
research strategy module exposing the engine contract
``order_package(cfg, candles_df)`` + ``monitor(cfg, candles_df, open_pkg)``
(the ``scripts/backtest_system.py::ROSTER`` shape). It is **registered in the
research harness ROSTER only** — it is NOT wired into ``config/strategies.yaml``
and never touches the live order path. Promotion to live is a separate
Tier-3 step gated on a clean OOS prop-gate pass.

Thesis
------
``ict_scalp_5m`` proved a real NEGATIVE edge on the 2023→2026 BTC 5m feed
(37% WR @ 1.5R → −0.075 R/trade, ``runtime_logs/prop_eval/2026-06-16-expanded/
NOTE.md``). The research plan's family-A hypothesis: keep ict_scalp's
sweep→displacement→FVG continuation geometry but PRUNE to a profitable
subset so WR lifts 37% → ≥45% even at the cost of trade count. The pruning
levers, all attacking the −0.075 R:

1. **Hard HTF trend-alignment gate** — only take continuations aligned with a
   higher-timeframe (1h/4h) EMA bias. (ict_scalp's HTF gate exists but is a
   soft EMA-cross; here it is a non-optional structural gate computed in-module
   from the same feed, so the engine need not inject it.)
2. **Killzone-only** — restrict to the London + NY ICT kill-zones (UTC hours).
   Continuation setups outside the active sessions are the low-quality tail.
3. **Minimum ATR-relative displacement strength** — a steeper displacement
   floor than ict_scalp's 1.3 (the "tepid displacement" cohort it admits is
   the dominant timeout/stop driver).
4. **ATR-scaled SL/TP** — stop a configurable ATR multiple beyond the swept
   extreme; target a configurable R. Tuned on IS only.

Same pure-signal-generator contract as the rest of ``src/units/strategies/``
(no dry/live awareness, no qty).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd


# Defaults are the FROZEN IS-tuned config (see the research NOTE). Any caller
# may override via cfg.get(<name>) — the harness passes --override STRAT.key=val.
_DEFAULTS: Dict[str, Any] = {
    "timeframe": "5m",
    # --- sweep / structure (ported from ict_scalp) ---
    "sweep_lookback_bars": 12,
    "swing_lookback_bars": 20,
    "atr_period": 14,
    "sweep_buffer_bps": 5.0,
    # --- displacement (FROZEN IS-best, see NOTE: disp 1.3 / btr 0.65 was the
    #     least-negative IS cell; a steeper floor only cut frequency without
    #     fixing the negative edge) ---
    "displacement_atr_mult": 1.3,
    "min_displacement_body_to_range": 0.65,
    # --- FVG ---
    "min_fvg_size_bps": 2.0,
    # --- HTF trend-alignment gate (HARD, in-module) ---
    # When enabled, the order_package requires cfg["htf_close"]/cfg["htf_ema"]
    # (injected by the harness via generate_signal_stream, see below) and
    # blocks any setup whose direction opposes the HTF EMA bias. Unlike
    # ict_scalp this is non-optional when enabled — a missing HTF value is
    # treated as "no trade" (the gate fails closed), not a silent skip, so
    # the pruning is real.
    "htf_trend_filter_enabled": True,
    "htf_filter_timeframe": "1h",
    "htf_filter_ema_period": 50,    # FROZEN IS-best
    # --- killzone session gate (London 07-10, NY 12-16 UTC by default) ---
    "session_filter_enabled": True,
    # Two windows: [start, end) UTC hours. London + NY kill-zones.
    "killzone_windows": "7-10,12-16",
    # --- risk model (ATR-scaled) ---
    "atr_sl_buffer_mult": 0.25,
    "tp_at_r": 1.0,                 # FROZEN IS-best (1.0 was the least-negative
                                    # R-target; higher R lowered WR faster than it
                                    # raised payoff because next-bar fill gaps
                                    # through the tight ICT stop > 1R)
}


def _resolve_params(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return {key: cfg.get(key, default) for key, default in _DEFAULTS.items()}


def _add_atr(df: pd.DataFrame, period: int) -> pd.DataFrame:
    out = df.copy()
    prev_close = out["close"].shift(1)
    tr = pd.concat(
        [
            (out["high"] - out["low"]).abs(),
            (out["high"] - prev_close).abs(),
            (out["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out["atr"] = tr.rolling(period, min_periods=period).mean()
    return out


def _detect_sweep(df, *, lookback_bars, swing_lookback, sweep_buffer_bps):
    """Most recent liquidity sweep within lookback_bars (pierce + revert)."""
    n = len(df)
    if n < swing_lookback + 2:
        return {"direction": None}
    prev_low_ref = df["low"].rolling(swing_lookback).min().shift(1)
    prev_high_ref = df["high"].rolling(swing_lookback).max().shift(1)
    buffer = df["close"] * (sweep_buffer_bps / 10_000.0)
    bull_setup = (df["low"] < (prev_low_ref - buffer)) & (df["close"] > prev_low_ref)
    bear_setup = (df["high"] > (prev_high_ref + buffer)) & (df["close"] < prev_high_ref)
    start = max(0, n - lookback_bars)
    for k in range(n - 1, start - 1, -1):
        if bool(bull_setup.iloc[k]):
            return {"direction": "long", "index": int(k),
                    "level": float(prev_low_ref.iloc[k]), "extreme": float(df["low"].iloc[k])}
        if bool(bear_setup.iloc[k]):
            return {"direction": "short", "index": int(k),
                    "level": float(prev_high_ref.iloc[k]), "extreme": float(df["high"].iloc[k])}
    return {"direction": None}


def _detect_displacement(df, *, sweep_idx, direction, atr_mult, min_body_to_range):
    n = len(df)
    if sweep_idx >= n - 1:
        return None
    for idx in range(sweep_idx + 1, n):
        atr = float(df["atr"].iloc[idx]) if pd.notna(df["atr"].iloc[idx]) else 0.0
        if atr <= 0:
            continue
        op = float(df["open"].iloc[idx]); cl = float(df["close"].iloc[idx])
        hi = float(df["high"].iloc[idx]); lo = float(df["low"].iloc[idx])
        body = abs(cl - op); rng = max(hi - lo, 1e-12)
        if body < atr_mult * atr:
            continue
        if (body / rng) < min_body_to_range:
            continue
        if direction == "long" and cl <= op:
            continue
        if direction == "short" and cl >= op:
            continue
        return {"index": int(idx), "body": body, "body_to_range": float(body / rng), "atr_at_bar": atr}
    return None


def _detect_fvg_in_leg(df, *, start_idx, direction, min_size_bps):
    n = len(df); last = None
    lo_start = max(start_idx, 2)
    for i in range(lo_start, n):
        ref_price = float(df["close"].iloc[i])
        min_size = ref_price * (min_size_bps / 10_000.0)
        h_im2 = float(df["high"].iloc[i - 2]); l_im2 = float(df["low"].iloc[i - 2])
        l_i = float(df["low"].iloc[i]); h_i = float(df["high"].iloc[i])
        if direction == "long" and h_im2 < l_i:
            size = l_i - h_im2
            if size >= min_size:
                last = {"index": int(i), "low": float(h_im2), "high": float(l_i), "size": float(size)}
        elif direction == "short" and l_im2 > h_i:
            size = l_im2 - h_i
            if size >= min_size:
                last = {"index": int(i), "low": float(h_i), "high": float(l_im2), "size": float(size)}
    return last


def _parse_windows(spec: str):
    """Parse 'a-b,c-d' UTC-hour windows into a list of (start,end) tuples."""
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


def _in_killzone(df, *, enabled, windows_spec) -> bool:
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
        return True  # un-timestamped fixtures: don't block
    for start, end in windows:
        if start <= end:
            if start <= hour < end:
                return True
        else:  # wrap-around
            if hour >= start or hour < end:
                return True
    return False


def order_package(cfg: dict, candles_df: Optional[pd.DataFrame] = None) -> dict:
    """Build an HF displacement-continuation order package dict.

    Raises ValueError (non-actionable) when candles are absent/too few, the
    last bar is outside the killzone, no sweep/displacement/FVG/mitigation is
    present, or the setup opposes the HTF bias.
    """
    if candles_df is None or (hasattr(candles_df, "empty") and candles_df.empty):
        raise ValueError("hf_displacement_cont: candles_df required.")
    params = _resolve_params(cfg)
    symbol = cfg.get("symbol") or "BTCUSDT"
    timeframe = str(cfg.get("timeframe") or params["timeframe"])

    required_cols = {"open", "high", "low", "close"}
    if required_cols - set(candles_df.columns):
        raise ValueError("hf_displacement_cont: missing OHLC columns.")

    needed = max(int(params["swing_lookback_bars"]), int(params["atr_period"]),
                 int(params["sweep_lookback_bars"])) + 5
    if len(candles_df) < needed:
        raise ValueError("hf_displacement_cont: too few candles.")

    # Killzone gate FIRST (cheap, prunes most bars).
    if not _in_killzone(candles_df, enabled=bool(params["session_filter_enabled"]),
                        windows_spec=params["killzone_windows"]):
        raise ValueError("hf_displacement_cont: last bar outside killzone.")

    df = _add_atr(candles_df, int(params["atr_period"]))

    sweep = _detect_sweep(df, lookback_bars=int(params["sweep_lookback_bars"]),
                          swing_lookback=int(params["swing_lookback_bars"]),
                          sweep_buffer_bps=float(params["sweep_buffer_bps"]))
    if sweep.get("direction") is None:
        raise ValueError("hf_displacement_cont: no sweep.")
    direction = sweep["direction"]; sweep_idx = int(sweep["index"])

    # HARD HTF trend-alignment gate (fails CLOSED when enabled + missing).
    if bool(params["htf_trend_filter_enabled"]):
        htf_close = cfg.get("htf_close"); htf_ema = cfg.get("htf_ema")
        if htf_close is None or htf_ema is None:
            raise ValueError("hf_displacement_cont: HTF bias unavailable — gate fails closed.")
        try:
            hc, he = float(htf_close), float(htf_ema)
        except (TypeError, ValueError):
            raise ValueError("hf_displacement_cont: HTF bias non-numeric.")
        if direction == "long" and hc <= he:
            raise ValueError("hf_displacement_cont: HTF bias bearish; block long.")
        if direction == "short" and hc >= he:
            raise ValueError("hf_displacement_cont: HTF bias bullish; block short.")

    displacement = _detect_displacement(df, sweep_idx=sweep_idx, direction=direction,
                                        atr_mult=float(params["displacement_atr_mult"]),
                                        min_body_to_range=float(params["min_displacement_body_to_range"]))
    if displacement is None:
        raise ValueError("hf_displacement_cont: no qualifying displacement.")

    fvg = _detect_fvg_in_leg(df, start_idx=sweep_idx, direction=direction,
                             min_size_bps=float(params["min_fvg_size_bps"]))
    if fvg is None:
        raise ValueError("hf_displacement_cont: no FVG in leg.")

    # Mitigation: wick-rejection at the FVG (same confirmation as ict_scalp v2).
    last_idx = len(df) - 1
    lo_o = float(df["open"].iloc[last_idx]); lo_c = float(df["close"].iloc[last_idx])
    lo_h = float(df["high"].iloc[last_idx]); lo_l = float(df["low"].iloc[last_idx])
    bull_body = lo_c > lo_o; bear_body = lo_c < lo_o
    if direction == "long":
        if not (lo_l <= fvg["high"] and lo_c > fvg["high"] and bull_body):
            raise ValueError("hf_displacement_cont: no long wick-rejection at FVG.")
    else:
        if not (lo_h >= fvg["low"] and lo_c < fvg["low"] and bear_body):
            raise ValueError("hf_displacement_cont: no short wick-rejection at FVG.")

    # Risk model — ATR-scaled stop beyond the swept extreme, R-multiple TP.
    entry = lo_c
    atr_now = float(df["atr"].iloc[last_idx]) if pd.notna(df["atr"].iloc[last_idx]) else 0.0
    sl_buffer = float(params["atr_sl_buffer_mult"]) * atr_now
    if direction == "long":
        sl = sweep["extreme"] - sl_buffer; risk = entry - sl
    else:
        sl = sweep["extreme"] + sl_buffer; risk = sl - entry
    if risk <= 0:
        raise ValueError("hf_displacement_cont: non-positive risk.")
    tp_at_r = float(params["tp_at_r"])
    tp = entry + tp_at_r * risk if direction == "long" else entry - tp_at_r * risk

    body_to_range = float(displacement["body_to_range"])
    sweep_depth_atr = abs(sweep["extreme"] - sweep["level"]) / atr_now if atr_now > 0 else 0.0
    fvg_size_norm = min(float(fvg["size"]) / max(atr_now, 1e-9), 1.0) if atr_now > 0 else 0.0
    confidence = round(min(0.4 * body_to_range + 0.3 * min(sweep_depth_atr, 1.0) + 0.3 * fvg_size_norm, 1.0), 4)

    return {
        "symbol": symbol,
        "direction": direction,
        "entry": round(entry, 8),
        "sl": round(float(sl), 8),
        "tp": round(float(tp), 8),
        "confidence": confidence,
        "meta": {
            "strategy_name": "hf_displacement_cont",
            "timeframe": timeframe,
            "sweep_level": float(sweep["level"]),
            "sweep_extreme": float(sweep["extreme"]),
            "displacement_body_to_range": body_to_range,
            "fvg_low": float(fvg["low"]),
            "fvg_high": float(fvg["high"]),
            "fvg_size": float(fvg["size"]),
            "atr": atr_now,
            "risk_per_unit": float(risk),
        },
    }


def monitor(cfg, candles_df, open_pkg):
    """Break-even-after-1R, same contract as ict_scalp / turtle_soup."""
    from src.units.strategies._base import monitor_breakeven_sl
    if candles_df is None:
        return None
    cfg_dict = cfg if isinstance(cfg, dict) else {}
    try:
        be_offset_bps = float(cfg_dict.get("be_offset_bps", 0.0))
    except (TypeError, ValueError):
        be_offset_bps = 0.0
    if be_offset_bps < 0:
        be_offset_bps = 0.0
    return monitor_breakeven_sl(open_pkg, candles_df, be_offset_bps=be_offset_bps)
