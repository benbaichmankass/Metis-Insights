#!/usr/bin/env python3
"""FVG range / mean-reversion backtest (S-STRAT-IMPROVE, complementary-strategy R&D).

The under-served regime in the current roster is the clean, persistent
HORIZONTAL range: price oscillating between static support & resistance,
where the *bounce continues* (mean reversion to the range interior).
trend_donchian rides breakouts, squeeze_breakout_4h trades the expansion,
fade_breakout_4h fades a *failed* breakout, turtle_soup fades a sweep, and
vwap reverts to a *drifting* anchor (gated against trend, no net edge). None
of them trade a confirmed static-S/R range bounce. ict_scalp_5m uses FVGs but
DIRECTIONALLY (sweep -> displacement -> FVG continuation in the breakout
direction) — the OPPOSITE intent of this strategy, which uses an unfilled FVG
as a mean-reversion S/R level INSIDE a confirmed range.

THE HYPOTHESIS (operator-directed 2026-05-30): inside a confirmed horizontal
range (low ADX = chop, sane width, both boundaries touched >=2x), an UNFILLED
Fair Value Gap sitting in the lower third (long) / upper third (short) of the
range is a high-probability bounce level. Enter on a wick-rejection at the gap
(price wicks INTO the gap and CLOSES back OUT of it with a matching-direction
body — the same confirmation mechanic ict_scalp uses, but for reversion not
continuation), stop ATR-buffered beyond the gap / range boundary (a range BREAK
invalidates the thesis), target the range midline (mid) or the opposite boundary
(far). If this is net-positive net-of-fee in the chop regime where the
trend-followers are flat, it is a genuine diversifier — the missing range member.

Entry  : Confirmed range over the prior `range_lookback` bars (resistance =
         rolling max, support = rolling min), gated by ADX < `adx_max` (chop)
         + width bounds + each boundary touched >= `min_touches` times. Find
         the most recent UNFILLED FVG of the matching direction whose midpoint
         sits in the lower/upper `third_frac` of the range. Fire on a
         wick-rejection at that gap on the current bar.
           long  : bullish FVG (high[k-2] < low[k]) in lower third, intact
                   (no bar since formation closed below the gap floor); current
                   bar low <= gap_high (wicks in) AND close > gap_high (rejects)
                   AND bullish body.
           short : mirror with a bearish FVG (low[k-2] > high[k]) in upper third.
Stop   : beyond the gap AND the range boundary — long: min(gap_low, support) -
         buffer x ATR; short: max(gap_high, resistance) + buffer x ATR. A close
         past the boundary invalidates the range (regime shift to trend), and
         the stop sits past the boundary so that break closes the trade.
Exit   : --exit-style mid (range midline) | far (opposite boundary) | tp1r
         (fixed R). SL-first intrabar (conservative). Timeout backstop
         (time-decay if the range stalls).

Net-of-fee, long/short split, by-outcome, per-calendar-year, and
month-over-month consistency — same readout shape as scripts/backtest_fade.py
and the rest of the program. Research only (Tier-1), not wired into live.
Reads an OHLCV CSV/Parquet (optionally --resample to a higher TF; optionally
--start/--end for walk-forward windows).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

FEE_BPS_ROUNDTRIP = 7.5
_EXIT_STYLES = ("mid", "far", "tp1r")


@dataclass
class Trade:
    entry_index: int
    entry_time: Any
    direction: str
    entry: float
    sl: float
    risk: float
    exit_index: int
    exit_time: Any
    exit_price: float
    outcome: str
    r_multiple: float
    mfe_r: float
    confidence: float


# --------------------------------------------------------------------------
# Data loading (identical contract to scripts/backtest_fade.py)
# --------------------------------------------------------------------------


def _load_candles(path: str) -> pd.DataFrame:
    if path.endswith(".parquet"):
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path)
    cols = {c.lower(): c for c in df.columns}
    need = ["timestamp", "open", "high", "low", "close"]
    df = df.rename(columns={cols[c]: c for c in need if c in cols and cols[c] != c})
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    return df.dropna(subset=["timestamp"]).reset_index(drop=True)


def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    # pandas 3.0 dropped the lowercase 'm' minutes alias (wants 'min'); normalise
    # so a live timeframe like "15m" still resamples (hours 'h' stay valid).
    r = rule.strip().lower()
    if r.endswith("m") and not r.endswith("min"):
        rule = r[:-1] + "min"
    out = (df.set_index("timestamp")
           .resample(rule, label="right", closed="right")
           .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
           .dropna().reset_index())
    return out


def _date_filter(df: pd.DataFrame, start: Optional[str], end: Optional[str]) -> pd.DataFrame:
    if start:
        df = df[df["timestamp"] >= pd.Timestamp(start, tz="UTC")]
    if end:
        df = df[df["timestamp"] <= pd.Timestamp(end, tz="UTC")]
    return df.reset_index(drop=True)


# --------------------------------------------------------------------------
# Indicators
# --------------------------------------------------------------------------


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    h, low, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([(h - low), (h - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=1).mean()


def _adx(df: pd.DataFrame, period: int) -> pd.Series:
    """Wilder's ADX — identical formula to scripts/backtest_fade.py::_adx and
    src/units/strategies/fade_breakout_4h.py::_adx so the live regime gate
    matches the validated one. Low ADX = chop (where a range lives)."""
    h, low, c = df["high"], df["low"], df["close"]
    up = h.diff()
    down = -low.diff()
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


# --------------------------------------------------------------------------
# Core backtest
# --------------------------------------------------------------------------


def run_backtest(df: pd.DataFrame, *, range_lookback: int, atr_period: int,
                 adx_period: int, adx_max: float, min_width_pct: float,
                 max_width_pct: float, touch_tol_pct: float, min_touches: int,
                 third_frac: float, fvg_search: int, min_fvg_size_bps: float,
                 atr_stop_buffer: float, exit_style: str, tp_r: float,
                 timeout_bars: int, cooldown_bars: int, timeframe: str,
                 symbol: str, min_confidence: float = 0.0,
                 stale_exit_bars: int = 0, stale_exit_below_r: float = 0.0,
                 giveback_min_mfe_r: float = 0.0, giveback_r: float = 1.0,
                 emit_path: Optional[str] = None) -> Dict[str, Any]:
    if exit_style not in _EXIT_STYLES:
        raise ValueError(f"exit_style must be one of {_EXIT_STYLES}")
    df = df.reset_index(drop=True)
    df["atr"] = _atr(df, atr_period)
    # Range boundaries from the PRIOR `range_lookback` bars only (shift(1)) —
    # no lookahead; the current bar's own action decides the bounce.
    df["range_hi"] = df["high"].rolling(range_lookback).max().shift(1)
    df["range_lo"] = df["low"].rolling(range_lookback).min().shift(1)
    df["adx"] = _adx(df, adx_period).shift(1)

    # numpy views for a fast hot loop (180k+ 15m bars over 5y).
    o = df["open"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    lo_arr = df["low"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)
    atr_arr = df["atr"].to_numpy(dtype=float)
    rhi = df["range_hi"].to_numpy(dtype=float)
    rlo = df["range_lo"].to_numpy(dtype=float)
    adx_arr = df["adx"].to_numpy(dtype=float)
    ts = df["timestamp"]

    trades: List[Trade] = []
    n = len(df)
    start_i = max(range_lookback, atr_period, adx_period) + 2
    i = start_i
    next_idx = i
    while i < n - 1:
        if i < next_idx:
            i += 1
            continue
        atr = atr_arr[i]
        R = rhi[i]
        S = rlo[i]
        if not (atr > 0) or np.isnan(R) or np.isnan(S):
            i += 1
            continue
        width = R - S
        price = c[i]
        # Width bounds (fraction of price) — reject degenerate (too tight to
        # clear fees) and runaway (not a range) channels.
        wfrac = width / price if price > 0 else 0.0
        if wfrac < min_width_pct or wfrac > max_width_pct:
            i += 1
            continue
        # Regime gate: only trade chop (low ADX). A range needs no trend.
        adx_i = adx_arr[i]
        if np.isnan(adx_i) or adx_i >= adx_max:
            i += 1
            continue
        # Price must be INSIDE the range (not already broken out).
        if price <= S or price >= R:
            i += 1
            continue

        lower_third = S + width * third_frac
        upper_third = R - width * third_frac
        # Location gate decides candidate direction: bottom -> long, top -> short.
        if price <= lower_third:
            direction = "long"
        elif price >= upper_third:
            direction = "short"
        else:
            i += 1
            continue

        # Boundary-touch confirmation: each side touched >= min_touches over the
        # prior range_lookback window (an oscillating range, not a one-touch
        # spike). tol is a fraction of price.
        w0 = i - range_lookback
        tol = price * touch_tol_pct
        win_hi = h[w0:i]
        win_lo = lo_arr[w0:i]
        touches_R = int(np.count_nonzero(win_hi >= (R - tol)))
        touches_S = int(np.count_nonzero(win_lo <= (S + tol)))
        if touches_R < min_touches or touches_S < min_touches:
            i += 1
            continue

        # Find the most recent UNFILLED FVG of the matching direction whose
        # midpoint sits in the lower/upper third. Search the last fvg_search
        # bars. Bullish 3-candle FVG: high[k-2] < low[k] (gap [high[k-2],
        # low[k]]). Bearish: low[k-2] > high[k].
        fvg = _find_range_fvg(
            h, lo_arr, c, i,
            direction=direction, fvg_search=fvg_search,
            min_fvg_size_bps=min_fvg_size_bps,
            S=S, R=R, width=width, third_frac=third_frac,
        )
        if fvg is None:
            i += 1
            continue
        g_lo, g_hi = fvg

        # Wick-rejection on the current bar at the gap (same mechanic as
        # ict_scalp's mitigation_mode="wick_rejection", inverted intent):
        #   long : low wicks into/below gap_high AND close back ABOVE gap_high
        #          AND bullish body.
        #   short: high wicks into/above gap_low AND close back BELOW gap_low
        #          AND bearish body.
        bull_body = c[i] > o[i]
        bear_body = c[i] < o[i]
        if direction == "long":
            wicked_in = lo_arr[i] <= g_hi
            closed_out = c[i] > g_hi
            if not (wicked_in and closed_out and bull_body):
                i += 1
                continue
        else:
            wicked_in = h[i] >= g_lo
            closed_out = c[i] < g_lo
            if not (wicked_in and closed_out and bear_body):
                i += 1
                continue

        entry = c[i]
        if direction == "long":
            sl = min(g_lo, S) - atr_stop_buffer * atr
            risk = entry - sl
        else:
            sl = max(g_hi, R) + atr_stop_buffer * atr
            risk = sl - entry
        if risk <= 0:
            i += 1
            continue

        # Confidence: blend location depth (closer to boundary = better) and
        # FVG size normalized by ATR, clamped [0,1]. Gives a live-parity
        # min_confidence lever (mirrors fade/trend).
        if direction == "long":
            loc = (lower_third - price) / (width * third_frac) if width > 0 else 0.0
        else:
            loc = (price - upper_third) / (width * third_frac) if width > 0 else 0.0
        loc = min(max(loc, 0.0), 1.0)
        fvg_size_norm = min((g_hi - g_lo) / atr, 1.0) if atr > 0 else 0.0
        confidence = round(min(0.6 * loc + 0.4 * fvg_size_norm, 1.0), 4)
        if confidence < min_confidence:
            i += 1
            continue

        # Target.
        if exit_style == "mid":
            target = (R + S) / 2.0
        elif exit_style == "far":
            target = R if direction == "long" else S
        else:  # tp1r
            target = entry + tp_r * risk if direction == "long" else entry - tp_r * risk
        # Degenerate-target guard.
        if direction == "long" and target <= entry:
            i += 1
            continue
        if direction == "short" and target >= entry:
            i += 1
            continue

        # Walk forward: SL-first intrabar (conservative), then target, then
        # timeout (time-decay).
        exit_price: Optional[float] = None
        exit_reason = "timeout"
        exit_idx = min(i + timeout_bars, n - 1)
        mfe = 0.0
        ext = entry
        for j in range(i + 1, min(i + timeout_bars + 1, n)):
            bh, bl = h[j], lo_arr[j]
            if direction == "long":
                if bl <= sl:
                    exit_price, exit_idx, exit_reason = sl, j, "stop"
                    break
                if bh >= target:
                    exit_price, exit_idx, exit_reason = target, j, "target"
                    break
                ext = max(ext, bh)
                mfe = max(mfe, (ext - entry) / risk)
            else:
                if bh >= sl:
                    exit_price, exit_idx, exit_reason = sl, j, "stop"
                    break
                if bl <= target:
                    exit_price, exit_idx, exit_reason = target, j, "target"
                    break
                ext = min(ext, bl)
                mfe = max(mfe, (entry - ext) / risk)
            # M20 exit levers (default 0 = off, byte-identical): checked at
            # bar close, never pre-empting the intrabar stop/target above —
            # same precedence as scripts/research/backtest_trend.py.
            r_close = ((c[j] - entry) / risk if direction == "long"
                       else (entry - c[j]) / risk)
            lever_stale = (stale_exit_bars > 0 and (j - i) >= stale_exit_bars
                           and r_close < stale_exit_below_r)
            lever_gb = (giveback_min_mfe_r > 0.0 and mfe >= giveback_min_mfe_r
                        and (mfe - r_close) >= giveback_r)
            if lever_stale or lever_gb:
                exit_price, exit_idx = float(c[j]), j
                exit_reason = "stale_stop" if lever_stale else "giveback_stop"
                break
        if exit_price is None:
            exit_price = c[exit_idx]
        r = ((exit_price - entry) / risk if direction == "long"
             else (entry - exit_price) / risk)
        trades.append(Trade(
            entry_index=i, entry_time=ts.iloc[i], direction=direction,
            entry=entry, sl=sl, risk=risk, exit_index=exit_idx,
            exit_time=ts.iloc[exit_idx], exit_price=exit_price,
            outcome=exit_reason, r_multiple=round(r, 4), mfe_r=round(mfe, 3),
            confidence=confidence))
        next_idx = exit_idx + 1 + cooldown_bars
        i = next_idx

    if emit_path:
        Path(emit_path).parent.mkdir(parents=True, exist_ok=True)
        with open(emit_path, "w", encoding="utf-8") as fh:
            for t in trades:
                fr = _fee_r(t)
                fh.write(json.dumps({
                    "strategy": "fvg_range", "entry_time": str(t.entry_time),
                    "direction": t.direction, "gross_r": t.r_multiple,
                    "net_r": round(t.r_multiple - fr, 4),
                    "confidence": t.confidence}, default=str) + "\n")
    return _summarize(trades, df, timeframe=timeframe, symbol=symbol,
                      params={"range_lookback": range_lookback, "adx_max": adx_max,
                              "min_width_pct": min_width_pct, "max_width_pct": max_width_pct,
                              "min_touches": min_touches, "third_frac": third_frac,
                              "fvg_search": fvg_search, "min_fvg_size_bps": min_fvg_size_bps,
                              "atr_stop_buffer": atr_stop_buffer, "exit_style": exit_style,
                              "tp_r": tp_r, "timeout_bars": timeout_bars,
                              "min_confidence": min_confidence})


def _find_range_fvg(h, lo_arr, c, i, *, direction, fvg_search, min_fvg_size_bps,
                    S, R, width, third_frac):
    """Most recent UNFILLED FVG of `direction` whose midpoint sits in the
    lower/upper `third_frac` of the range, scanning the last `fvg_search` bars.

    Returns (gap_low, gap_high) or None. "Unfilled" = since the gap formed at
    bar k, no later bar (k+1..i-1) has CLOSED through the gap's far edge:
      bullish gap (support): no later close < gap_low (still an intact floor).
      bearish gap (resistance): no later close > gap_high (still an intact cap).
    """
    lo_search = max(2, i - fvg_search)
    lower_third = S + width * third_frac
    upper_third = R - width * third_frac
    for k in range(i, lo_search - 1, -1):
        if direction == "long":
            g_lo = h[k - 2]
            g_hi = lo_arr[k]
            if not (g_lo < g_hi):
                continue
            size_bps = (g_hi - g_lo) / c[k] * 10_000.0 if c[k] > 0 else 0.0
            if size_bps < min_fvg_size_bps:
                continue
            mid = (g_lo + g_hi) / 2.0
            if mid > lower_third:          # gap not in the lower third
                continue
            # intact floor: no later bar closed below the gap floor
            if k < i and np.any(c[k + 1:i] < g_lo):
                continue
            return float(g_lo), float(g_hi)
        else:
            g_hi = lo_arr[k - 2]
            g_lo = h[k]
            if not (g_hi > g_lo):
                continue
            size_bps = (g_hi - g_lo) / c[k] * 10_000.0 if c[k] > 0 else 0.0
            if size_bps < min_fvg_size_bps:
                continue
            mid = (g_lo + g_hi) / 2.0
            if mid < upper_third:          # gap not in the upper third
                continue
            if k < i and np.any(c[k + 1:i] > g_hi):
                continue
            return float(g_lo), float(g_hi)
    return None


def _fee_r(t: Trade) -> float:
    if not t.exit_price or t.risk <= 0:
        return 0.0
    return (FEE_BPS_ROUNDTRIP / 10_000.0) * ((t.entry + t.exit_price) / 2.0) / t.risk


def _monthly_consistency(pairs) -> Optional[Dict[str, Any]]:
    """Local fallback for scripts.ops.consistency.monthly_consistency (which
    ships on the program branch only). Month-over-month net-R consistency."""
    by_month: Dict[str, float] = {}
    for ts_val, r in pairs:
        key = pd.Timestamp(ts_val).strftime("%Y-%m")
        by_month[key] = by_month.get(key, 0.0) + float(r)
    if not by_month:
        return None
    vals = list(by_month.values())
    months = len(vals)
    pos = sum(1 for v in vals if v > 0)
    mean = float(np.mean(vals))
    std = float(np.std(vals))
    # max consecutive negative months
    max_neg = cur = 0
    for v in sorted(by_month):  # chronological
        if by_month[v] < 0:
            cur += 1
            max_neg = max(max_neg, cur)
        else:
            cur = 0
    total = sum(vals)
    top_share = (max(vals) / total) if total > 0 else 0.0
    return {
        "months": months,
        "pct_months_positive": round(100 * pos / months, 1),
        "consistency_ratio": round(mean / std, 3) if std > 0 else 0.0,
        "monthly_mean_r": round(mean, 3),
        "monthly_std_r": round(std, 3),
        "worst_month_r": round(min(vals), 3),
        "max_consecutive_negative_months": max_neg,
        "top_month_share": round(top_share, 3),
    }


def _summarize(trades: List[Trade], df: pd.DataFrame, *, timeframe: str,
               symbol: str, params: Dict[str, Any]) -> Dict[str, Any]:
    n = len(trades)
    base: Dict[str, Any] = {
        "strategy": "fvg_range", "symbol": symbol, "timeframe": timeframe,
        "params": params, "total_trades": n, "fee_bps_roundtrip": FEE_BPS_ROUNDTRIP,
        "data_start": str(df["timestamp"].iloc[0]) if len(df) else None,
        "data_end": str(df["timestamp"].iloc[-1]) if len(df) else None,
        "run_date": str(date.today())}
    if n == 0:
        base.update({"win_rate_pct": 0.0, "total_r": 0.0, "net_total_r": 0.0,
                     "net_expectancy_r": 0.0, "total_fee_r": 0.0,
                     "trades_long": 0, "trades_short": 0,
                     "net_total_r_long": 0.0, "net_total_r_short": 0.0,
                     "max_drawdown_r": 0.0, "by_outcome": {}, "by_year": {},
                     "consistency": None})
        return base
    rs = [t.r_multiple for t in trades]
    net = [t.r_multiple - _fee_r(t) for t in trades]
    wins = [r for r in rs if r > 0]
    longs = [t for t in trades if t.direction == "long"]
    shorts = [t for t in trades if t.direction == "short"]
    cum = peak = mdd = 0.0
    for r in rs:
        cum += r
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    by: Dict[str, int] = {}
    for t in trades:
        by[t.outcome] = by.get(t.outcome, 0) + 1
    by_year: Dict[str, Dict[str, Any]] = {}
    for t in trades:
        yr = str(pd.Timestamp(t.entry_time).year)
        slot = by_year.setdefault(yr, {"trades": 0, "net_r": 0.0})
        slot["trades"] += 1
        slot["net_r"] = round(slot["net_r"] + (t.r_multiple - _fee_r(t)), 4)
    try:
        from scripts.ops.consistency import monthly_consistency
        consistency = monthly_consistency(
            (t.entry_time, t.r_multiple - _fee_r(t)) for t in trades)
    except ImportError:
        consistency = _monthly_consistency(
            (t.entry_time, t.r_multiple - _fee_r(t)) for t in trades)
    base.update({
        "win_rate_pct": round(100 * len(wins) / n, 2),
        "total_r": round(sum(rs), 4),
        "trades_long": len(longs),
        "trades_short": len(shorts),
        "total_r_long": round(sum(t.r_multiple for t in longs), 4),
        "total_r_short": round(sum(t.r_multiple for t in shorts), 4),
        "total_fee_r": round(sum(_fee_r(t) for t in trades), 4),
        "net_total_r": round(sum(net), 4),
        "net_total_r_long": round(sum(t.r_multiple - _fee_r(t) for t in longs), 4),
        "net_total_r_short": round(sum(t.r_multiple - _fee_r(t) for t in shorts), 4),
        "net_expectancy_r": round(sum(net) / n, 4),
        "avg_win_r": round(sum(wins) / len(wins), 4) if wins else 0.0,
        "max_mfe_r": round(max(t.mfe_r for t in trades), 3),
        "max_drawdown_r": round(mdd, 4), "by_outcome": by, "by_year": by_year,
        "consistency": consistency})
    return base


def _fmt(s: Dict[str, Any]) -> str:
    lines = [f"fvg_range — {s['symbol']} {s['timeframe']} {s.get('params')}",
             f"  data {s.get('data_start')} -> {s.get('data_end')}  trades={s['total_trades']}"]
    if s["total_trades"]:
        lines += [
            f"  win_rate={s['win_rate_pct']}%  gross_r={s['total_r']} "
            f"(L {s.get('total_r_long')}/S {s.get('total_r_short')})",
            f"  net_r={s['net_total_r']} (net_exp {s['net_expectancy_r']}, "
            f"fee_r {s['total_fee_r']}, net L/S {s.get('net_total_r_long')}/{s.get('net_total_r_short')})",
            f"  avg_win_r={s.get('avg_win_r')} max_mfe_r={s.get('max_mfe_r')} "
            f"maxdd_r={s['max_drawdown_r']} by={s['by_outcome']}",
            f"  by_year={s.get('by_year')}"]
        cc = s.get("consistency") or {}
        if cc:
            lines.append(
                f"  consistency: months={cc.get('months')} "
                f"pos={cc.get('pct_months_positive')}% "
                f"ratio={cc.get('consistency_ratio')} "
                f"(mean {cc.get('monthly_mean_r')}/std {cc.get('monthly_std_r')}) "
                f"worst={cc.get('worst_month_r')} "
                f"max_neg_streak={cc.get('max_consecutive_negative_months')} "
                f"top_month_share={cc.get('top_month_share')}")
    return "\n".join(lines)


def main(argv: List[str]) -> int:
    global FEE_BPS_ROUNDTRIP
    p = argparse.ArgumentParser(description="FVG range / mean-reversion backtest (net-of-fee).")
    p.add_argument("--data", default=os.environ.get("BACKTEST_DATA_PATH", "data/backtest_candles.csv"))
    p.add_argument("--timeframe", default="15m")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--resample", default=None, help="Resample to this rule first (e.g. 15m, 30m).")
    p.add_argument("--start", default=None, help="Walk-forward window start (ISO date, inclusive).")
    p.add_argument("--end", default=None, help="Walk-forward window end (ISO date, inclusive).")
    p.add_argument("--range-lookback", type=int, default=48,
                   help="Bars defining the prior-window range (resistance/support).")
    p.add_argument("--atr-period", type=int, default=14)
    p.add_argument("--adx-period", type=int, default=14)
    p.add_argument("--adx-max", type=float, default=20.0,
                   help="Regime gate: only trade when ADX < this (chop/range).")
    p.add_argument("--min-width-pct", type=float, default=0.015,
                   help="Min range width as fraction of price (fee-clearance floor).")
    p.add_argument("--max-width-pct", type=float, default=0.12,
                   help="Max range width as fraction of price (reject runaway 'ranges').")
    p.add_argument("--touch-tol-pct", type=float, default=0.002,
                   help="Boundary-touch tolerance as fraction of price.")
    p.add_argument("--min-touches", type=int, default=2,
                   help="Min touches of EACH boundary in the window (oscillating range).")
    p.add_argument("--third-frac", type=float, default=0.34,
                   help="Lower/upper fraction of the range that admits long/short entries.")
    p.add_argument("--fvg-search", type=int, default=24,
                   help="Bars back to scan for an unfilled matching FVG.")
    p.add_argument("--min-fvg-size-bps", type=float, default=2.0)
    p.add_argument("--atr-stop-buffer", type=float, default=0.5,
                   help="Stop placed buffer x ATR beyond the gap / range boundary.")
    p.add_argument("--exit-style", choices=_EXIT_STYLES, default="mid",
                   help="mid=range midline | far=opposite boundary | tp1r=fixed R.")
    p.add_argument("--tp-r", type=float, default=1.5, help="R target for --exit-style tp1r.")
    p.add_argument("--timeout-bars", type=int, default=48,
                   help="Time-decay backstop: close at market after N bars.")
    p.add_argument("--cooldown-bars", type=int, default=1)
    p.add_argument("--fee-bps-roundtrip", type=float, default=FEE_BPS_ROUNDTRIP)
    p.add_argument("--min-confidence", type=float, default=0.0)
    p.add_argument("--stale-exit-bars", type=int, default=0,
                   help="M20 stale-stop: close at bar close after N bars if still below --stale-exit-below-r (0=off).")
    p.add_argument("--stale-exit-below-r", type=float, default=0.0)
    p.add_argument("--giveback-min-mfe-r", type=float, default=0.0,
                   help="M20 giveback-stop: arm once peak open profit reaches this many R (0=off).")
    p.add_argument("--giveback-r", type=float, default=1.0,
                   help="Close at bar close once the trade gives back this many R from its peak.")
    p.add_argument("--json", dest="json_out", default=None)
    p.add_argument("--emit-trades", default=None, metavar="PATH",
                   help="Write per-trade {entry_time, net_r, confidence} JSONL for portfolio_combine.")
    args = p.parse_args(argv[1:])
    FEE_BPS_ROUNDTRIP = args.fee_bps_roundtrip
    try:
        df = _load_candles(args.data)
        if args.resample:
            df = _resample(df, args.resample)
        df = _date_filter(df, args.start, args.end)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: load failed: {exc}", file=sys.stderr)
        return 1
    out = run_backtest(
        df, range_lookback=args.range_lookback, atr_period=args.atr_period,
        adx_period=args.adx_period, adx_max=args.adx_max,
        min_width_pct=args.min_width_pct, max_width_pct=args.max_width_pct,
        touch_tol_pct=args.touch_tol_pct, min_touches=args.min_touches,
        third_frac=args.third_frac, fvg_search=args.fvg_search,
        min_fvg_size_bps=args.min_fvg_size_bps, atr_stop_buffer=args.atr_stop_buffer,
        exit_style=args.exit_style, tp_r=args.tp_r, timeout_bars=args.timeout_bars,
        cooldown_bars=args.cooldown_bars, timeframe=args.timeframe,
        symbol=args.symbol, min_confidence=args.min_confidence,
        stale_exit_bars=args.stale_exit_bars,
        stale_exit_below_r=args.stale_exit_below_r,
        giveback_min_mfe_r=args.giveback_min_mfe_r,
        giveback_r=args.giveback_r,
        emit_path=args.emit_trades)
    print(_fmt(out))
    if args.json_out:
        payload = json.dumps(out, indent=2, default=str)
        if args.json_out == "-":
            print(payload)
        else:
            Path(args.json_out).write_text(payload)
            print(f"JSON -> {args.json_out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
