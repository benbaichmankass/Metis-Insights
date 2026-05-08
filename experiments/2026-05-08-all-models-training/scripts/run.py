"""All-models training run — VWAP + Turtle Soup A/B and parameter sweeps.

See ../PLAN.md for the hypothesis grid and adoption gates.

Outputs:
    ../results/SUMMARY.md           — human-readable per-variant + sweep tables
    ../results/all_metrics.json     — every metric this run produced
    ../results/walk_forward.json    — in-sample / out-of-sample split numbers
    ../results/<variant>/metrics.json
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

HERE = Path(__file__).resolve().parent.parent
DATA = HERE / "data" / "btc_5m.parquet"
OUT = HERE / "results"
OUT.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Data prep
# ---------------------------------------------------------------------------

def load_5m() -> pd.DataFrame:
    df = pd.read_parquet(DATA)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    s = df.set_index("timestamp")
    out = pd.DataFrame({
        "open":   s["open"].resample(rule).first(),
        "high":   s["high"].resample(rule).max(),
        "low":    s["low"].resample(rule).min(),
        "close":  s["close"].resample(rule).last(),
        "volume": s["volume"].resample(rule).sum(),
    }).dropna().reset_index()
    return out


# ---------------------------------------------------------------------------
# Backtest engine — first-touch SL/TP, sliding window
# ---------------------------------------------------------------------------

@dataclass
class Metrics:
    trades: int = 0
    win_rate: float = 0.0
    expectancy_r: float = 0.0
    sharpe: float = 0.0
    max_dd_r: float = 0.0
    avg_hold_bars: float = 0.0


def _metrics_from_rmults(r_mults: List[float], holds: List[int]) -> Metrics:
    if not r_mults:
        return Metrics()
    s = pd.Series(r_mults)
    eq = s.cumsum()
    dd = float((eq - eq.cummax()).min())
    sharpe = float(s.mean() / s.std() * np.sqrt(len(s))) if s.std() > 0 else 0.0
    return Metrics(
        trades=len(s),
        win_rate=float((s > 0).mean()),
        expectancy_r=float(s.mean()),
        sharpe=sharpe,
        max_dd_r=dd,
        avg_hold_bars=float(np.mean(holds)) if holds else 0.0,
    )


def _exit_first_touch(direction: str, entry: float, sl: float, tp: float, future: np.ndarray) -> tuple[float, str, int]:
    """future: (n, 2) array of (high, low). Returns (exit_price, reason, hold_bars)."""
    n = len(future)
    if direction == "long":
        for i in range(n):
            high, low = future[i, 0], future[i, 1]
            if low <= sl:
                return sl, "sl", i + 1
            if high >= tp:
                return tp, "tp", i + 1
    else:
        for i in range(n):
            high, low = future[i, 0], future[i, 1]
            if high >= sl:
                return sl, "sl", i + 1
            if low <= tp:
                return tp, "tp", i + 1
    # Timeout — close at last bar's close (we use future[-1, 1] as proxy for low)
    # Actually we need close. Caller will pass the closes array separately.
    return float("nan"), "timeout", n


def backtest(
    candles: pd.DataFrame,
    build_signal: Callable[[int, pd.DataFrame], Optional[Dict]],
    lookback: int,
    max_hold: int,
    step: int = 12,
    htf: Optional[pd.DataFrame] = None,  # forwarded to build_signal as kw
) -> Metrics:
    """Walk through candles. At every `step` bars, call build_signal(i, df).

    build_signal returns {direction, entry, sl, tp} or None.
    """
    n = len(candles)
    highs = candles["high"].to_numpy()
    lows = candles["low"].to_numpy()
    closes = candles["close"].to_numpy()
    r_mults: List[float] = []
    holds: List[int] = []

    last_exit_idx = lookback - 1
    i = lookback
    while i < n - max_hold - 1:
        if i <= last_exit_idx:
            i += 1
            continue
        sig = build_signal(i, candles)
        if sig is None:
            i += step
            continue
        d = sig["direction"]
        entry, sl, tp = sig["entry"], sig["sl"], sig["tp"]
        risk = abs(entry - sl)
        if risk <= 0:
            i += step
            continue
        # Walk forward
        j = i
        end = min(i + max_hold, n - 1)
        exit_price = float(closes[end])
        reason = "timeout"
        hold = end - i
        if d == "long":
            for k in range(i + 1, end + 1):
                if lows[k] <= sl:
                    exit_price, reason, hold = sl, "sl", k - i
                    break
                if highs[k] >= tp:
                    exit_price, reason, hold = tp, "tp", k - i
                    break
        else:
            for k in range(i + 1, end + 1):
                if highs[k] >= sl:
                    exit_price, reason, hold = sl, "sl", k - i
                    break
                if lows[k] <= tp:
                    exit_price, reason, hold = tp, "tp", k - i
                    break
        if d == "long":
            r_mult = (exit_price - entry) / risk
        else:
            r_mult = (entry - exit_price) / risk
        r_mults.append(float(r_mult))
        holds.append(int(hold))
        last_exit_idx = i + hold
        i += max(step, hold + 1)
    return _metrics_from_rmults(r_mults, holds)


# ---------------------------------------------------------------------------
# VWAP signal logic — replicates production semantics in vectorised form
# ---------------------------------------------------------------------------

ENTRY_THR = 1.0
SL_MULT = 0.5
SESSION_MIN_BARS = 50
LOOKBACK_5M = 120
MAX_HOLD_5M = 96
STEP_5M = 12  # check every hour


def _make_vwap_signal_fn(
    candles: pd.DataFrame,
    *,
    entry_thr: float = ENTRY_THR,
    sl_mult: float = SL_MULT,
    htf_close_lookup: Optional[Callable[[pd.Timestamp], tuple[float, float]]] = None,
    htf_band: Optional[float] = None,
) -> Callable[[int, pd.DataFrame], Optional[Dict]]:
    """Return a build_signal(i, df) closure that mirrors `build_vwap_signal`.

    Signal anchors at UTC midnight when the session slice has >= SESSION_MIN_BARS
    bars (Phase-1 production behaviour); otherwise falls back to the rolling
    `lookback` window.

    Optional HTF filter: if htf_close_lookup + htf_band given, blocks LONGs when
    htf_close < ema * (1 - band) and SHORTs when htf_close > ema * (1 + band).
    """
    ts_pd = pd.to_datetime(candles["timestamp"], utc=True)
    ts_naive = ts_pd.dt.tz_localize(None).to_numpy()  # ns
    o = candles["open"].to_numpy()
    h = candles["high"].to_numpy()
    l = candles["low"].to_numpy()
    c = candles["close"].to_numpy()
    v = candles["volume"].to_numpy()

    # Precompute UTC-midnight session-start index for each bar (the first index
    # whose timestamp >= floor(ts_i, 'D')). All comparisons in tz-naive ns.
    midnight_naive = ts_pd.dt.floor("D").dt.tz_localize(None).to_numpy()
    session_start_idx = np.searchsorted(ts_naive, midnight_naive, side="left")
    ts_pd_iloc = ts_pd

    def _build(i: int, _df: pd.DataFrame) -> Optional[Dict]:
        # Session-anchored slice
        s_idx = int(session_start_idx[i])
        if (i - s_idx + 1) >= SESSION_MIN_BARS and v[s_idx:i + 1].sum() > 0:
            sl_lo = s_idx
        else:
            sl_lo = max(0, i - LOOKBACK_5M + 1)
        if v[sl_lo:i + 1].sum() <= 0:
            return None
        sub_h = h[sl_lo:i + 1]
        sub_l = l[sl_lo:i + 1]
        sub_c = c[sl_lo:i + 1]
        sub_v = v[sl_lo:i + 1]
        tp = (sub_h + sub_l + sub_c) / 3.0
        vwap = float((tp * sub_v).sum() / sub_v.sum())
        std = float(tp.std(ddof=1)) if len(tp) > 1 else 0.0
        if std <= 0:
            return None
        price = float(c[i])
        deviation = (price - vwap) / std
        if deviation <= -entry_thr:
            direction, sl_px = "long", price - sl_mult * std
            tp_px = vwap
        elif deviation >= entry_thr:
            direction, sl_px = "short", price + sl_mult * std
            tp_px = vwap
        else:
            return None

        # Optional HTF gate
        if htf_close_lookup is not None and htf_band is not None:
            close_htf, ema = htf_close_lookup(ts_naive[i])
            if not np.isnan(close_htf):
                if direction == "long" and close_htf < ema * (1 - htf_band):
                    return None
                if direction == "short" and close_htf > ema * (1 + htf_band):
                    return None

        return {"direction": direction, "entry": price, "sl": sl_px, "tp": tp_px}

    return _build


def make_htf_lookup(htf: pd.DataFrame, ema_period: int) -> Callable[[pd.Timestamp], tuple[float, float]]:
    """Return a function ts -> (htf_close_at_or_before_ts, ema_at_that_bar).

    EMA computed *before* the lookup is created so it's deterministic. Bar
    forward-fills when ts falls between bars (the standard "use the most recent
    completed HTF bar" semantics).
    """
    h = htf.copy().sort_values("timestamp").reset_index(drop=True)
    h["ema"] = h["close"].ewm(span=ema_period, adjust=False).mean()
    htf_ts = pd.to_datetime(h["timestamp"], utc=True).dt.tz_localize(None).to_numpy()
    htf_close = h["close"].to_numpy()
    htf_ema = h["ema"].to_numpy()

    def _lookup(ts_np) -> tuple[float, float]:
        # ts_np must be tz-naive datetime64[ns]
        idx = int(np.searchsorted(htf_ts, ts_np, side="right") - 1)
        if idx < 0 or np.isnan(htf_ema[idx]):
            return float("nan"), float("nan")
        return float(htf_close[idx]), float(htf_ema[idx])

    return _lookup


# ---------------------------------------------------------------------------
# Turtle Soup signal logic
# ---------------------------------------------------------------------------

T_DEFAULTS = {
    "sweep_lookback": 60,
    "min_body_to_range": 0.60,
    "min_sweep_buffer_bps": 12,
    "atr_period": 14,
    "atr_stop_mult": 0.35,
    "tp1_at_r": 1.25,
}

LOOKBACK_15M = 130
MAX_HOLD_15M = 80
STEP_15M = 4  # every hour at 15m


def _make_turtle_signal_fn(
    candles: pd.DataFrame,
    *,
    params: Optional[Dict] = None,
    htf_close_lookup: Optional[Callable] = None,
    htf_align_ema_period: Optional[int] = None,
    atr_regime_min: Optional[float] = None,
) -> Callable:
    """Vectorised turtle_soup signal precompute + per-i lookup."""
    p = {**T_DEFAULTS, **(params or {})}
    h = candles["high"].to_numpy()
    l = candles["low"].to_numpy()
    o = candles["open"].to_numpy()
    c = candles["close"].to_numpy()

    # ATR (Wilder-ish, simple rolling mean of TR)
    prev_c = np.roll(c, 1); prev_c[0] = c[0]
    tr = np.maximum.reduce([h - l, np.abs(h - prev_c), np.abs(l - prev_c)])
    atr_period = int(p["atr_period"])
    atr = pd.Series(tr).rolling(atr_period, min_periods=atr_period).mean().to_numpy()

    # Prior swing references over `lookback` bars (excluding current bar)
    lb = int(p["sweep_lookback"])
    s_h = pd.Series(h)
    s_l = pd.Series(l)
    prev_high_ref = s_h.rolling(lb).max().shift(1).to_numpy()
    prev_low_ref = s_l.rolling(lb).min().shift(1).to_numpy()

    rng = h - l
    body = np.abs(c - o)
    body_to_range = np.where(rng > 0, body / rng, 0.0)
    sweep_buffer_bps = float(p["min_sweep_buffer_bps"])
    sweep_buffer = np.maximum(c * (sweep_buffer_bps / 10000.0), np.nan_to_num(atr, nan=0.0) * 0.05)

    bullish_setup = (
        (l < (prev_low_ref - sweep_buffer))
        & (c > prev_low_ref)
        & (body_to_range >= float(p["min_body_to_range"]))
    )
    bearish_setup = (
        (h > (prev_high_ref + sweep_buffer))
        & (c < prev_high_ref)
        & (body_to_range >= float(p["min_body_to_range"]))
    )

    atr_stop_mult = float(p["atr_stop_mult"])
    tp1_at_r = float(p["tp1_at_r"])

    ts_naive = pd.to_datetime(candles["timestamp"], utc=True).dt.tz_localize(None).to_numpy()

    def _build(i: int, _df: pd.DataFrame) -> Optional[Dict]:
        if i < lb or np.isnan(atr[i]):
            return None
        if atr_regime_min is not None and (atr[i] / c[i]) < atr_regime_min:
            return None

        if bool(bullish_setup[i]):
            sweep_extreme = float(l[i])
            level = float(prev_low_ref[i])
            entry = float(c[i])
            sl = min(sweep_extreme, level) - float(atr[i]) * atr_stop_mult
            risk = entry - sl
            direction = "long"
        elif bool(bearish_setup[i]):
            sweep_extreme = float(h[i])
            level = float(prev_high_ref[i])
            entry = float(c[i])
            sl = max(sweep_extreme, level) + float(atr[i]) * atr_stop_mult
            risk = sl - entry
            direction = "short"
        else:
            return None

        if risk <= 0:
            return None

        # HTF alignment (only-with-trend)
        if htf_close_lookup is not None and htf_align_ema_period is not None:
            close_htf, ema = htf_close_lookup(ts_naive[i])
            if not np.isnan(close_htf):
                if direction == "long" and close_htf < ema:
                    return None
                if direction == "short" and close_htf > ema:
                    return None

        if direction == "long":
            tp_px = entry + tp1_at_r * risk
        else:
            tp_px = entry - tp1_at_r * risk
        return {"direction": direction, "entry": entry, "sl": sl, "tp": tp_px}

    return _build


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def fmt_metrics(m: Metrics) -> str:
    return (f"trades={m.trades:>5}  win={m.win_rate:.2%}  "
            f"E[R]={m.expectancy_r:+.4f}  Sharpe={m.sharpe:+.2f}  "
            f"DD={m.max_dd_r:+.1f}R  hold={m.avg_hold_bars:.1f}b")


def split_70_30(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    cut = int(len(df) * 0.7)
    in_s = df.iloc[:cut].reset_index(drop=True)
    oos = df.iloc[cut:].reset_index(drop=True)
    return in_s, oos


def main() -> None:
    t0 = time.time()
    print(f"loading {DATA} ...")
    btc5 = load_5m()
    print(f"  {len(btc5):,} bars  {btc5['timestamp'].iloc[0]} -> {btc5['timestamp'].iloc[-1]}")

    btc15 = resample(btc5, "15min")
    btc1h = resample(btc5, "1h")
    btc4h = resample(btc5, "4h")
    btc1d = resample(btc5, "1D")
    print(f"  resampled: 15m={len(btc15):,}  1h={len(btc1h):,}  4h={len(btc4h):,}  1d={len(btc1d):,}")

    htf_4h_ema200 = make_htf_lookup(btc4h, 200)
    htf_1h_ema200 = make_htf_lookup(btc1h, 200)
    htf_1d_ema50 = make_htf_lookup(btc1d, 50)
    htf_4h_ema50_15m = make_htf_lookup(btc4h, 50)  # for turtle T6

    in_s_5m, oos_5m = split_70_30(btc5)
    in_s_15m, oos_15m = split_70_30(btc15)

    all_results: Dict = {"vwap": {}, "turtle_soup": {}}

    # =========================================================================
    # VWAP
    # =========================================================================
    print("\n" + "=" * 72)
    print("VWAP — BTCUSDT 5m, anchored VWAP + 1.0σ entry + 0.5σ SL")
    print("=" * 72)

    def run_vwap(name: str, fn_factory: Callable[[pd.DataFrame], Callable], df: pd.DataFrame) -> Metrics:
        f = fn_factory(df)
        m = backtest(df, f, LOOKBACK_5M, MAX_HOLD_5M, STEP_5M)
        return m

    # V0 baseline (full-period)
    print(f"\n[V0 baseline]      ", end="", flush=True)
    v0 = run_vwap("V0", lambda d: _make_vwap_signal_fn(d), btc5)
    print(fmt_metrics(v0))
    all_results["vwap"]["V0_baseline"] = asdict(v0)

    # V1 — H6 Phase-2 HTF gate
    print(f"[V1 HTF 4h±1%]     ", end="", flush=True)
    v1 = run_vwap("V1", lambda d: _make_vwap_signal_fn(d, htf_close_lookup=htf_4h_ema200, htf_band=0.01), btc5)
    print(fmt_metrics(v1))
    all_results["vwap"]["V1_htf_4h_ema200_band0.01"] = asdict(v1)

    # V2 — band sweep
    print("\n[V2 HTF band sweep]")
    v2_sweep = {}
    for band in (0.005, 0.010, 0.015, 0.020, 0.030):
        m = run_vwap(f"V2_{band}", lambda d, b=band: _make_vwap_signal_fn(d, htf_close_lookup=htf_4h_ema200, htf_band=b), btc5)
        v2_sweep[f"{band:.3f}"] = asdict(m)
        print(f"  band={band:.3f}: {fmt_metrics(m)}")
    all_results["vwap"]["V2_band_sweep"] = v2_sweep

    # V3 — HTF timeframe variant
    print("\n[V3 HTF timeframe variant @ band=0.01]")
    v3 = {}
    for label, lookup in [("1h_ema200", htf_1h_ema200), ("4h_ema200", htf_4h_ema200), ("1d_ema50", htf_1d_ema50)]:
        m = run_vwap(label, lambda d, lk=lookup: _make_vwap_signal_fn(d, htf_close_lookup=lk, htf_band=0.01), btc5)
        v3[label] = asdict(m)
        print(f"  {label}: {fmt_metrics(m)}")
    all_results["vwap"]["V3_htf_tf"] = v3

    # V5 — SL multiplier sweep
    print("\n[V5 SL multiplier sweep, baseline (no HTF)]")
    v5 = {}
    for mult in (0.4, 0.5, 0.6, 0.75, 1.0):
        m = run_vwap(f"V5_{mult}", lambda d, mm=mult: _make_vwap_signal_fn(d, sl_mult=mm), btc5)
        v5[f"{mult:.2f}"] = asdict(m)
        print(f"  sl_mult={mult:.2f}: {fmt_metrics(m)}")
    all_results["vwap"]["V5_sl_sweep"] = v5

    # V6 — entry threshold sweep
    print("\n[V6 entry threshold sweep, baseline (no HTF)]")
    v6 = {}
    for thr in (0.8, 1.0, 1.2, 1.5, 2.0):
        m = run_vwap(f"V6_{thr}", lambda d, tt=thr: _make_vwap_signal_fn(d, entry_thr=tt), btc5)
        v6[f"{thr:.2f}"] = asdict(m)
        print(f"  thr={thr:.2f}: {fmt_metrics(m)}")
    all_results["vwap"]["V6_thr_sweep"] = v6

    # Walk-forward for V0 and V1 (the two production candidates)
    print("\n[VWAP walk-forward 70/30]")
    wf = {}
    for name, mk in [("V0_baseline", lambda d: _make_vwap_signal_fn(d)),
                     ("V1_htf",      lambda d: _make_vwap_signal_fn(d, htf_close_lookup=htf_4h_ema200, htf_band=0.01))]:
        in_m = backtest(in_s_5m, mk(in_s_5m), LOOKBACK_5M, MAX_HOLD_5M, STEP_5M)
        oo_m = backtest(oos_5m, mk(oos_5m), LOOKBACK_5M, MAX_HOLD_5M, STEP_5M)
        wf[name] = {"in_sample": asdict(in_m), "out_of_sample": asdict(oo_m)}
        print(f"  {name}: IS  {fmt_metrics(in_m)}")
        print(f"  {name}: OOS {fmt_metrics(oo_m)}")
    all_results["vwap"]["walk_forward"] = wf

    # =========================================================================
    # Turtle Soup
    # =========================================================================
    print("\n" + "=" * 72)
    print("Turtle Soup — BTCUSDT 15m, sweep+reversal, ATR stop, 1.25R TP")
    print("=" * 72)

    def run_turtle(name: str, fn_factory: Callable[[pd.DataFrame], Callable], df: pd.DataFrame) -> Metrics:
        f = fn_factory(df)
        return backtest(df, f, LOOKBACK_15M, MAX_HOLD_15M, STEP_15M)

    # T0 baseline (defaults)
    print(f"\n[T0 baseline]      ", end="", flush=True)
    t0_m = run_turtle("T0", lambda d: _make_turtle_signal_fn(d), btc15)
    print(fmt_metrics(t0_m))
    all_results["turtle_soup"]["T0_baseline"] = asdict(t0_m)

    # T1 — sweep_lookback
    print("\n[T1 sweep_lookback]")
    t1 = {}
    for lb in (30, 45, 60, 90, 120):
        m = run_turtle(f"T1_{lb}", lambda d, _lb=lb: _make_turtle_signal_fn(d, params={"sweep_lookback": _lb}), btc15)
        t1[str(lb)] = asdict(m)
        print(f"  lookback={lb:>3}: {fmt_metrics(m)}")
    all_results["turtle_soup"]["T1_lookback"] = t1

    # T2 — min_body_to_range
    print("\n[T2 min_body_to_range]")
    t2 = {}
    for r in (0.50, 0.55, 0.60, 0.65, 0.70, 0.75):
        m = run_turtle(f"T2_{r}", lambda d, _r=r: _make_turtle_signal_fn(d, params={"min_body_to_range": _r}), btc15)
        t2[f"{r:.2f}"] = asdict(m)
        print(f"  body_to_range={r:.2f}: {fmt_metrics(m)}")
    all_results["turtle_soup"]["T2_body"] = t2

    # T3 — min_sweep_buffer_bps
    print("\n[T3 min_sweep_buffer_bps]")
    t3 = {}
    for b in (4, 8, 12, 18, 25):
        m = run_turtle(f"T3_{b}", lambda d, _b=b: _make_turtle_signal_fn(d, params={"min_sweep_buffer_bps": _b}), btc15)
        t3[str(b)] = asdict(m)
        print(f"  buffer_bps={b:>2}: {fmt_metrics(m)}")
    all_results["turtle_soup"]["T3_buffer"] = t3

    # T4 — atr_stop_mult
    print("\n[T4 atr_stop_mult]")
    t4 = {}
    for mm in (0.25, 0.30, 0.35, 0.45, 0.60):
        m = run_turtle(f"T4_{mm}", lambda d, _mm=mm: _make_turtle_signal_fn(d, params={"atr_stop_mult": _mm}), btc15)
        t4[f"{mm:.2f}"] = asdict(m)
        print(f"  atr_mult={mm:.2f}: {fmt_metrics(m)}")
    all_results["turtle_soup"]["T4_atr_stop"] = t4

    # T5 — tp1_at_r
    print("\n[T5 tp1_at_r]")
    t5 = {}
    for tp in (1.0, 1.25, 1.50, 2.0):
        m = run_turtle(f"T5_{tp}", lambda d, _tp=tp: _make_turtle_signal_fn(d, params={"tp1_at_r": _tp}), btc15)
        t5[f"{tp:.2f}"] = asdict(m)
        print(f"  tp1_R={tp:.2f}: {fmt_metrics(m)}")
    all_results["turtle_soup"]["T5_tp"] = t5

    # T6 — HTF 4h EMA-50 alignment
    print(f"\n[T6 HTF 4h EMA-50 align]   ", end="", flush=True)
    t6 = run_turtle("T6", lambda d: _make_turtle_signal_fn(d, htf_close_lookup=htf_4h_ema50_15m, htf_align_ema_period=50), btc15)
    print(fmt_metrics(t6))
    all_results["turtle_soup"]["T6_htf_align"] = asdict(t6)

    # T7 — ATR regime filter
    print("\n[T7 ATR regime]")
    t7 = {}
    for thr in (0.0, 0.0025, 0.005, 0.0075, 0.010):
        m = run_turtle(f"T7_{thr}", lambda d, _thr=thr: _make_turtle_signal_fn(d, atr_regime_min=_thr), btc15)
        t7[f"{thr:.4f}"] = asdict(m)
        label = "off" if thr == 0 else f"{thr:.4f}"
        print(f"  atr_min={label}: {fmt_metrics(m)}")
    all_results["turtle_soup"]["T7_atr_regime"] = t7

    # Walk-forward for top-2 turtle candidates (decided after we see results)
    # Run a fixed pair: T0 baseline + T6 (HTF align) since we have a strong prior trend-aligned strategies benefit
    print("\n[Turtle walk-forward 70/30]")
    twf = {}
    for name, mk in [("T0_baseline", lambda d: _make_turtle_signal_fn(d)),
                     ("T6_htf_align", lambda d: _make_turtle_signal_fn(d, htf_close_lookup=htf_4h_ema50_15m, htf_align_ema_period=50))]:
        in_m = backtest(in_s_15m, mk(in_s_15m), LOOKBACK_15M, MAX_HOLD_15M, STEP_15M)
        oo_m = backtest(oos_15m, mk(oos_15m), LOOKBACK_15M, MAX_HOLD_15M, STEP_15M)
        twf[name] = {"in_sample": asdict(in_m), "out_of_sample": asdict(oo_m)}
        print(f"  {name}: IS  {fmt_metrics(in_m)}")
        print(f"  {name}: OOS {fmt_metrics(oo_m)}")
    all_results["turtle_soup"]["walk_forward"] = twf

    # =========================================================================
    # Persist + summary
    # =========================================================================
    (OUT / "all_metrics.json").write_text(json.dumps(all_results, indent=2, default=str))
    print(f"\nWrote {OUT/'all_metrics.json'}")
    print(f"\nWall-clock: {(time.time()-t0)/60:.2f} min")


if __name__ == "__main__":
    main()
