#!/usr/bin/env python3
"""Multi-timeframe chop-scalp backtest — range-boundary bounces (research, Tier-1).

Motivation (operator-directed 2026-07-15): a strategy geared to SCALP THROUGH
CHOP. The existing range member ``fvg_range_15m`` (verbatim port in
``scripts/backtest_fvg_range.py``) resolves its range and enters on the SAME
timeframe (15m). This harness is its faster, explicitly MULTI-TIMEFRAME cousin:

  * read the chop/range BOUNDARIES on a HIGHER timeframe (``--htf-rule``, e.g.
    15m or 1h) — rolling support/resistance + an ADX chop gate + width bounds +
    a boundary-touch confirmation, exactly the ``fvg_range`` geometry;
  * catch the BOUNCE on a faster LOWER timeframe (``--timeframe`` / base feed,
    e.g. 1m or 5m) — a wick-rejection back off the HTF boundary (optionally
    confirmed by an unfilled LTF FVG, the same mechanic as ict_scalp inverted);
  * target the OPPOSITE HTF boundary (full-range reversion), the midline, or a
    fixed R; stop just past the HTF boundary (a range BREAK invalidates).

The research question is CAPITAL EFFICIENCY, not big wins: a chop-scalp wins
small but holds briefly, so it is measured on **PnL per unit of trade-time**
(``net_r_per_pos_day`` — the same metric the exit-refinement gate uses,
``scripts/ml/train_exit_head.py::agg``) alongside hold time and roundtrippers,
so its efficiency can be compared head-to-head against the slower reverters,
buy-and-hold, and sitting on cash through the chop.

Lookahead discipline (the load-bearing correctness property of a multi-TF
harness): HTF features are attached to each LTF bar with ``merge_asof`` in the
**backward** direction on the (right-labelled) HTF close timestamp, so an LTF
entry bar only ever sees an HTF bar that has ALREADY CLOSED at or before it.
The HTF rolling window is inclusive of that closed bar (no shift needed — the
merge enforces closure), so the boundaries reflect the most recently completed
HTF bar and nothing in the future.

Net-of-fee, long/short split, by-outcome, by-year, month-over-month
consistency — same readout shape as ``scripts/backtest_fvg_range.py`` — PLUS a
capital-efficiency block. Research only (Tier-1); never wired into live. Reads
an OHLCV CSV/Parquet (the base LTF feed; ``--resample`` first if the feed is
finer than the desired LTF).
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
    hold_bars: int
    confidence: float


# --------------------------------------------------------------------------
# Data loading (identical contract to scripts/backtest_fvg_range.py)
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


def _norm_rule(rule: str) -> str:
    """Normalise a timeframe/resample rule so pandas 3.0 accepts it (it dropped
    the lowercase 'm' minutes alias in favour of 'min'; hours 'h' stay valid)."""
    r = rule.strip().lower()
    if r.endswith("m") and not r.endswith("min"):
        return r[:-1] + "min"
    return r


def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    out = (df.set_index("timestamp")
           .resample(_norm_rule(rule), label="right", closed="right")
           .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
           .dropna().reset_index())
    return out


def _date_filter(df: pd.DataFrame, start: Optional[str], end: Optional[str]) -> pd.DataFrame:
    if start:
        df = df[df["timestamp"] >= pd.Timestamp(start, tz="UTC")]
    if end:
        df = df[df["timestamp"] <= pd.Timestamp(end, tz="UTC")]
    return df.reset_index(drop=True)


def _tf_seconds(tf: str) -> int:
    """Seconds per bar for a timeframe string like 1m/5m/15m/1h/4h/1d."""
    r = tf.strip().lower()
    unit = r[-1]
    try:
        n = int(r[:-1] or "1")
    except ValueError:
        return 60
    return n * {"m": 60, "h": 3600, "d": 86400, "w": 604800}.get(unit, 60)


# --------------------------------------------------------------------------
# Indicators (identical formulas to scripts/backtest_fvg_range.py so a live
# port stays verbatim and the HTF regime gate matches the validated one).
# --------------------------------------------------------------------------


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    h, low, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([(h - low), (h - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=1).mean()


def _adx(df: pd.DataFrame, period: int) -> pd.Series:
    """Wilder's ADX — identical to scripts/backtest_fvg_range.py::_adx. Low
    ADX = chop (where a range lives)."""
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


def _rolling_touches(highs: np.ndarray, lows: np.ndarray, hi: np.ndarray,
                     lo: np.ndarray, lookback: int, tol_frac: float) -> tuple:
    """Per-HTF-bar count of how many of the prior ``lookback`` bars touched the
    resistance / support (within ``tol_frac`` of price). Vectorised-ish; the
    HTF frame is small (thousands of bars) so a python loop is fine."""
    n = len(highs)
    tR = np.zeros(n, dtype=int)
    tS = np.zeros(n, dtype=int)
    for i in range(n):
        if i < lookback or np.isnan(hi[i]) or np.isnan(lo[i]):
            continue
        w_hi = highs[i - lookback + 1:i + 1]
        w_lo = lows[i - lookback + 1:i + 1]
        tol = ((hi[i] + lo[i]) / 2.0) * tol_frac
        tR[i] = int(np.count_nonzero(w_hi >= (hi[i] - tol)))
        tS[i] = int(np.count_nonzero(w_lo <= (lo[i] + tol)))
    return tR, tS


def _build_htf_features(base: pd.DataFrame, *, htf_rule: str, range_lookback: int,
                        adx_period: int, touch_tol_pct: float) -> pd.DataFrame:
    """Resample the base feed to the HTF and compute the range boundaries +
    regime per HTF bar. Rolling windows are INCLUSIVE of the current (closed)
    HTF bar; the backward merge_asof in run_backtest guarantees an LTF entry
    only ever reads an HTF bar that closed at/before it, so there is no
    lookahead despite the inclusive window."""
    htf = _resample(base, htf_rule)
    htf["htf_hi"] = htf["high"].rolling(range_lookback).max()
    htf["htf_lo"] = htf["low"].rolling(range_lookback).min()
    htf["htf_adx"] = _adx(htf, adx_period)
    tR, tS = _rolling_touches(
        htf["high"].to_numpy(float), htf["low"].to_numpy(float),
        htf["htf_hi"].to_numpy(float), htf["htf_lo"].to_numpy(float),
        range_lookback, touch_tol_pct)
    htf["htf_touch_hi"] = tR
    htf["htf_touch_lo"] = tS
    return htf[["timestamp", "htf_hi", "htf_lo", "htf_adx",
                "htf_touch_hi", "htf_touch_lo"]]


# --------------------------------------------------------------------------
# Core backtest
# --------------------------------------------------------------------------


def run_backtest(df: pd.DataFrame, *, htf_rule: str, timeframe: str, symbol: str,
                 range_lookback: int, atr_period: int, adx_period: int,
                 adx_max: float, min_width_pct: float, max_width_pct: float,
                 touch_tol_pct: float, min_touches: int, third_frac: float,
                 wick_tol_frac: float, require_fvg: bool, fvg_search: int,
                 min_fvg_size_bps: float, atr_stop_buffer: float,
                 exit_style: str, tp_r: float, timeout_bars: int,
                 cooldown_bars: int, min_confidence: float = 0.0,
                 emit_path: Optional[str] = None) -> Dict[str, Any]:
    if exit_style not in _EXIT_STYLES:
        raise ValueError(f"exit_style must be one of {_EXIT_STYLES}")
    df = df.reset_index(drop=True)
    df["atr"] = _atr(df, atr_period)

    # HTF boundaries/regime, attached to each LTF bar via a BACKWARD merge_asof
    # on the right-labelled HTF close time — the lookahead-safety property.
    htf_feat = _build_htf_features(
        df, htf_rule=htf_rule, range_lookback=range_lookback,
        adx_period=adx_period, touch_tol_pct=touch_tol_pct)
    merged = pd.merge_asof(
        df.sort_values("timestamp"), htf_feat.sort_values("timestamp"),
        on="timestamp", direction="backward")

    o = merged["open"].to_numpy(float)
    h = merged["high"].to_numpy(float)
    lo_arr = merged["low"].to_numpy(float)
    c = merged["close"].to_numpy(float)
    atr_arr = merged["atr"].to_numpy(float)
    rhi = merged["htf_hi"].to_numpy(float)
    rlo = merged["htf_lo"].to_numpy(float)
    adx_arr = merged["htf_adx"].to_numpy(float)
    thi = merged["htf_touch_hi"].to_numpy(float)
    tlo = merged["htf_touch_lo"].to_numpy(float)
    ts = merged["timestamp"]

    trades: List[Trade] = []
    n = len(merged)
    start_i = max(atr_period, 3) + 1
    i = start_i
    next_idx = i
    while i < n - 1:
        if i < next_idx:
            i += 1
            continue
        atr = atr_arr[i]
        R = rhi[i]
        S = rlo[i]
        if not (atr > 0) or np.isnan(R) or np.isnan(S) or R <= S:
            i += 1
            continue
        width = R - S
        price = c[i]
        wfrac = width / price if price > 0 else 0.0
        if wfrac < min_width_pct or wfrac > max_width_pct:
            i += 1
            continue
        # HTF regime must be chop.
        adx_i = adx_arr[i]
        if np.isnan(adx_i) or adx_i >= adx_max:
            i += 1
            continue
        # HTF range confirmed: each boundary touched >= min_touches.
        if thi[i] < min_touches or tlo[i] < min_touches:
            i += 1
            continue
        # Price inside the HTF range.
        if price <= S or price >= R:
            i += 1
            continue

        lower_third = S + width * third_frac
        upper_third = R - width * third_frac
        if price <= lower_third:
            direction = "long"
        elif price >= upper_third:
            direction = "short"
        else:
            i += 1
            continue

        # Bounce trigger on the LTF bar at the HTF boundary: wick to/through the
        # boundary and CLOSE back inside with a matching body (the fvg_range
        # wick-rejection mechanic, anchored to the HTF boundary instead of an
        # FVG). ``wick_tol_frac`` is how deep (as a fraction of range width) the
        # wick must reach toward/past the boundary.
        bull_body = c[i] > o[i]
        bear_body = c[i] < o[i]
        if direction == "long":
            wicked_in = lo_arr[i] <= S + width * wick_tol_frac
            closed_in = c[i] > S
            if not (wicked_in and closed_in and bull_body):
                i += 1
                continue
        else:
            wicked_in = h[i] >= R - width * wick_tol_frac
            closed_in = c[i] < R
            if not (wicked_in and closed_in and bear_body):
                i += 1
                continue

        # Optional LTF FVG confirmation (same unfilled-gap test as fvg_range,
        # scanning the last fvg_search LTF bars in the boundary third).
        if require_fvg:
            fvg = _find_range_fvg(
                h, lo_arr, c, i, direction=direction, fvg_search=fvg_search,
                min_fvg_size_bps=min_fvg_size_bps, S=S, R=R, width=width,
                third_frac=third_frac)
            if fvg is None:
                i += 1
                continue

        entry = c[i]
        if direction == "long":
            sl = S - atr_stop_buffer * atr
            risk = entry - sl
        else:
            sl = R + atr_stop_buffer * atr
            risk = sl - entry
        if risk <= 0:
            i += 1
            continue

        # Confidence: location depth toward the boundary + wick reach past it.
        if direction == "long":
            loc = (lower_third - price) / (width * third_frac) if width > 0 else 0.0
            wick_depth = (S + width * wick_tol_frac - lo_arr[i]) / (width * wick_tol_frac) \
                if wick_tol_frac > 0 else 0.0
        else:
            loc = (price - upper_third) / (width * third_frac) if width > 0 else 0.0
            wick_depth = (h[i] - (R - width * wick_tol_frac)) / (width * wick_tol_frac) \
                if wick_tol_frac > 0 else 0.0
        loc = min(max(loc, 0.0), 1.0)
        wick_depth = min(max(wick_depth, 0.0), 1.0)
        confidence = round(min(0.6 * loc + 0.4 * wick_depth, 1.0), 4)
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
        if (direction == "long" and target <= entry) or \
           (direction == "short" and target >= entry):
            i += 1
            continue

        # Walk forward: SL-first intrabar (conservative), then target, then
        # timeout (time-decay) — all on the LTF frame.
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
        if exit_price is None:
            exit_price = c[exit_idx]
        r = ((exit_price - entry) / risk if direction == "long"
             else (entry - exit_price) / risk)
        trades.append(Trade(
            entry_index=i, entry_time=ts.iloc[i], direction=direction,
            entry=entry, sl=sl, risk=risk, exit_index=exit_idx,
            exit_time=ts.iloc[exit_idx], exit_price=exit_price,
            outcome=exit_reason, r_multiple=round(r, 4), mfe_r=round(mfe, 3),
            hold_bars=exit_idx - i, confidence=confidence))
        next_idx = exit_idx + 1 + cooldown_bars
        i = next_idx

    if emit_path:
        Path(emit_path).parent.mkdir(parents=True, exist_ok=True)
        with open(emit_path, "w", encoding="utf-8") as fh:
            for t in trades:
                fr = _fee_r(t)
                fh.write(json.dumps({
                    "strategy": "chop_scalp", "entry_time": str(t.entry_time),
                    "direction": t.direction, "gross_r": t.r_multiple,
                    "net_r": round(t.r_multiple - fr, 4),
                    "hold_bars": t.hold_bars, "mfe_r": t.mfe_r,
                    "confidence": t.confidence}, default=str) + "\n")
    return _summarize(
        trades, merged, timeframe=timeframe, symbol=symbol,
        tf_seconds=_tf_seconds(timeframe),
        params={"htf_rule": htf_rule, "range_lookback": range_lookback,
                "adx_max": adx_max, "min_width_pct": min_width_pct,
                "max_width_pct": max_width_pct, "min_touches": min_touches,
                "third_frac": third_frac, "wick_tol_frac": wick_tol_frac,
                "require_fvg": require_fvg, "atr_stop_buffer": atr_stop_buffer,
                "exit_style": exit_style, "tp_r": tp_r,
                "timeout_bars": timeout_bars, "min_confidence": min_confidence})


def _find_range_fvg(h, lo_arr, c, i, *, direction, fvg_search, min_fvg_size_bps,
                    S, R, width, third_frac):
    """Most recent UNFILLED FVG of `direction` in the lower/upper third — a
    verbatim copy of scripts/backtest_fvg_range.py::_find_range_fvg so the
    optional LTF FVG confirmation matches the validated range logic."""
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
            if (g_lo + g_hi) / 2.0 > lower_third:
                continue
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
            if (g_lo + g_hi) / 2.0 < upper_third:
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
    """Local fallback for scripts.ops.consistency.monthly_consistency (program
    branch only). Month-over-month net-R consistency."""
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
    max_neg = cur = 0
    for v in sorted(by_month):
        if by_month[v] < 0:
            cur += 1
            max_neg = max(max_neg, cur)
        else:
            cur = 0
    total = sum(vals)
    return {
        "months": months,
        "pct_months_positive": round(100 * pos / months, 1),
        "consistency_ratio": round(mean / std, 3) if std > 0 else 0.0,
        "monthly_mean_r": round(mean, 3), "monthly_std_r": round(std, 3),
        "worst_month_r": round(min(vals), 3),
        "max_consecutive_negative_months": max_neg,
        "top_month_share": round((max(vals) / total) if total > 0 else 0.0, 3),
    }


def _summarize(trades: List[Trade], df: pd.DataFrame, *, timeframe: str,
               symbol: str, tf_seconds: int, params: Dict[str, Any]) -> Dict[str, Any]:
    n = len(trades)
    base: Dict[str, Any] = {
        "strategy": "chop_scalp", "symbol": symbol, "timeframe": timeframe,
        "params": params, "total_trades": n, "fee_bps_roundtrip": FEE_BPS_ROUNDTRIP,
        "data_start": str(df["timestamp"].iloc[0]) if len(df) else None,
        "data_end": str(df["timestamp"].iloc[-1]) if len(df) else None,
        "run_date": str(date.today())}
    if n == 0:
        base.update({"win_rate_pct": 0.0, "total_r": 0.0, "net_total_r": 0.0,
                     "net_expectancy_r": 0.0, "total_fee_r": 0.0,
                     "trades_long": 0, "trades_short": 0, "max_drawdown_r": 0.0,
                     "by_outcome": {}, "by_year": {}, "consistency": None,
                     "capital_efficiency": {"net_r_per_pos_day": None,
                                            "position_days": 0.0, "mean_hold_bars": 0.0,
                                            "mean_hold_hours": 0.0, "roundtrippers_pct": 0.0}})
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

    # ---- Capital-efficiency block (the point of the study) ----
    # net_r_per_pos_day = net_R / position-days, position-days = Σ(hold_bars) ×
    # tf_seconds / 86400 — the same metric as scripts/ml/train_exit_head.py::agg.
    total_hold_bars = sum(t.hold_bars for t in trades)
    position_days = total_hold_bars * tf_seconds / 86400.0
    net_total_r = sum(net)
    roundtrippers = sum(1 for t in trades if t.mfe_r >= 1.0 and (t.r_multiple - _fee_r(t)) <= 0.0)
    cap_eff = {
        "net_r_per_pos_day": round(net_total_r / position_days, 4) if position_days > 0 else None,
        "position_days": round(position_days, 3),
        "mean_hold_bars": round(total_hold_bars / n, 2),
        "mean_hold_hours": round(total_hold_bars / n * tf_seconds / 3600.0, 3),
        "roundtrippers_pct": round(100 * roundtrippers / n, 2),
    }

    base.update({
        "win_rate_pct": round(100 * len(wins) / n, 2),
        "total_r": round(sum(rs), 4),
        "trades_long": len(longs), "trades_short": len(shorts),
        "total_r_long": round(sum(t.r_multiple for t in longs), 4),
        "total_r_short": round(sum(t.r_multiple for t in shorts), 4),
        "total_fee_r": round(sum(_fee_r(t) for t in trades), 4),
        "net_total_r": round(net_total_r, 4),
        "net_total_r_long": round(sum(t.r_multiple - _fee_r(t) for t in longs), 4),
        "net_total_r_short": round(sum(t.r_multiple - _fee_r(t) for t in shorts), 4),
        "net_expectancy_r": round(net_total_r / n, 4),
        "avg_win_r": round(sum(wins) / len(wins), 4) if wins else 0.0,
        "max_mfe_r": round(max(t.mfe_r for t in trades), 3),
        "max_drawdown_r": round(mdd, 4), "by_outcome": by, "by_year": by_year,
        "consistency": consistency, "capital_efficiency": cap_eff})
    return base


def _fmt(s: Dict[str, Any]) -> str:
    lines = [f"chop_scalp — {s['symbol']} {s['timeframe']} (HTF {s['params'].get('htf_rule')})",
             f"  params {s.get('params')}",
             f"  data {s.get('data_start')} -> {s.get('data_end')}  trades={s['total_trades']}"]
    if s["total_trades"]:
        ce = s.get("capital_efficiency", {})
        lines += [
            f"  win_rate={s['win_rate_pct']}%  gross_r={s['total_r']} "
            f"(L {s.get('total_r_long')}/S {s.get('total_r_short')})",
            f"  net_r={s['net_total_r']} (net_exp {s['net_expectancy_r']}, "
            f"fee_r {s['total_fee_r']}, net L/S {s.get('net_total_r_long')}/{s.get('net_total_r_short')})",
            f"  avg_win_r={s.get('avg_win_r')} max_mfe_r={s.get('max_mfe_r')} "
            f"maxdd_r={s['max_drawdown_r']} by={s['by_outcome']}",
            f"  CAP-EFF: net_r_per_pos_day={ce.get('net_r_per_pos_day')} "
            f"(pos_days {ce.get('position_days')}) mean_hold={ce.get('mean_hold_hours')}h "
            f"({ce.get('mean_hold_bars')} bars) roundtrippers={ce.get('roundtrippers_pct')}%",
            f"  by_year={s.get('by_year')}"]
        cc = s.get("consistency") or {}
        if cc:
            lines.append(
                f"  consistency: months={cc.get('months')} "
                f"pos={cc.get('pct_months_positive')}% ratio={cc.get('consistency_ratio')} "
                f"worst={cc.get('worst_month_r')}")
    return "\n".join(lines)


def main(argv: List[str]) -> int:
    global FEE_BPS_ROUNDTRIP
    p = argparse.ArgumentParser(
        description="Multi-timeframe chop-scalp backtest (range-boundary bounces, net-of-fee).")
    p.add_argument("--data", default=os.environ.get("BACKTEST_DATA_PATH", "data/backtest_candles.csv"))
    p.add_argument("--timeframe", default="5m", help="LTF (entry) timeframe label, e.g. 1m/5m.")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--resample", default=None,
                   help="Resample the base feed to this LTF rule first (e.g. 5m).")
    p.add_argument("--htf-rule", default="1h",
                   help="Higher timeframe that defines the range boundaries + regime (e.g. 15m, 1h).")
    p.add_argument("--start", default=None, help="Walk-forward window start (ISO date, inclusive).")
    p.add_argument("--end", default=None, help="Walk-forward window end (ISO date, inclusive).")
    p.add_argument("--range-lookback", type=int, default=48,
                   help="HTF bars defining the prior-window range (resistance/support).")
    p.add_argument("--atr-period", type=int, default=14, help="ATR period on the LTF (stop sizing).")
    p.add_argument("--adx-period", type=int, default=14, help="ADX period on the HTF (regime gate).")
    p.add_argument("--adx-max", type=float, default=20.0,
                   help="HTF regime gate: only trade when HTF ADX < this (chop/range).")
    p.add_argument("--min-width-pct", type=float, default=0.015,
                   help="Min HTF range width as fraction of price (fee-clearance floor).")
    p.add_argument("--max-width-pct", type=float, default=0.12,
                   help="Max HTF range width as fraction of price (reject runaway 'ranges').")
    p.add_argument("--touch-tol-pct", type=float, default=0.002,
                   help="HTF boundary-touch tolerance as fraction of price.")
    p.add_argument("--min-touches", type=int, default=3,
                   help="Min touches of EACH HTF boundary in the window (oscillating range).")
    p.add_argument("--third-frac", type=float, default=0.34,
                   help="Lower/upper fraction of the HTF range that admits long/short entries.")
    p.add_argument("--wick-tol-frac", type=float, default=0.05,
                   help="How deep (fraction of range width) the LTF wick must reach toward the boundary.")
    p.add_argument("--require-fvg", action="store_true",
                   help="Also require an unfilled LTF FVG at the boundary (stricter, fewer trades).")
    p.add_argument("--fvg-search", type=int, default=24)
    p.add_argument("--min-fvg-size-bps", type=float, default=2.0)
    p.add_argument("--atr-stop-buffer", type=float, default=0.25,
                   help="Stop placed buffer x ATR beyond the HTF boundary.")
    p.add_argument("--exit-style", choices=_EXIT_STYLES, default="far",
                   help="far=opposite boundary | mid=range midline | tp1r=fixed R.")
    p.add_argument("--tp-r", type=float, default=1.5, help="R target for --exit-style tp1r.")
    p.add_argument("--timeout-bars", type=int, default=48,
                   help="Time-decay backstop: close at market after N LTF bars.")
    p.add_argument("--cooldown-bars", type=int, default=1)
    p.add_argument("--fee-bps-roundtrip", type=float, default=FEE_BPS_ROUNDTRIP)
    p.add_argument("--min-confidence", type=float, default=0.0)
    p.add_argument("--json", dest="json_out", default=None)
    p.add_argument("--emit-trades", default=None, metavar="PATH",
                   help="Write per-trade {entry_time, net_r, hold_bars, confidence} JSONL.")
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
        df, htf_rule=args.htf_rule, timeframe=args.timeframe, symbol=args.symbol,
        range_lookback=args.range_lookback, atr_period=args.atr_period,
        adx_period=args.adx_period, adx_max=args.adx_max,
        min_width_pct=args.min_width_pct, max_width_pct=args.max_width_pct,
        touch_tol_pct=args.touch_tol_pct, min_touches=args.min_touches,
        third_frac=args.third_frac, wick_tol_frac=args.wick_tol_frac,
        require_fvg=args.require_fvg, fvg_search=args.fvg_search,
        min_fvg_size_bps=args.min_fvg_size_bps, atr_stop_buffer=args.atr_stop_buffer,
        exit_style=args.exit_style, tp_r=args.tp_r, timeout_bars=args.timeout_bars,
        cooldown_bars=args.cooldown_bars, min_confidence=args.min_confidence,
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
