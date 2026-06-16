#!/usr/bin/env python3
"""Vectorized full-feed signal generators for the HF research candidates +
fast R-replay — RESEARCH-ONLY tuning accelerator.

The per-bar ``order_package`` scan in ``hf_solo_sim.py`` is faithful but far
too slow for a parameter sweep (it re-slices a 260-bar window and recomputes
ATR/ADX/VWAP every bar → minutes per config). This module computes the same
gates VECTORIZED over the full feed in one pass, so a grid sweeps in seconds.

FAITHFULNESS: each vectorized generator is validated against the canonical
``src.units.strategies.<module>.order_package`` on a sample window
(``--validate``) — the two must agree on the signal set before any tuning
conclusion is drawn. The vectorized path is ONLY used for coarse IS tuning;
the FROZEN config is re-verified through the REAL engine
(``scripts/backtest_system.py``) for the numbers in the NOTE.

Tier-1 research tooling — does not import or alter any live-order path.
"""
from __future__ import annotations

import argparse
import itertools
import sys
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

FEE_BPS_ROUNDTRIP = 7.5


def _load(path: str) -> pd.DataFrame:
    df = pd.read_parquet(path) if path.endswith(".parquet") else pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    return df.dropna(subset=["timestamp"]).reset_index(drop=True)


def _resample(df, rule):
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if "volume" in df.columns:
        agg["volume"] = "sum"
    return (df.set_index("timestamp").resample(rule, label="right", closed="right")
            .agg(agg).dropna().reset_index())


def _date_filter(df, start, end):
    if start:
        df = df[df["timestamp"] >= pd.Timestamp(start, tz="UTC")]
    if end:
        df = df[df["timestamp"] <= pd.Timestamp(end, tz="UTC")]
    return df.reset_index(drop=True)


def _atr(df, period):
    h, low, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([(h - low), (h - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


def _adx(df, period):
    h, low, c = df["high"], df["low"], df["close"]
    up = h.diff(); down = -low.diff()
    plus_dm = ((up > down) & (up > 0)) * up.clip(lower=0)
    minus_dm = ((down > up) & (down > 0)) * down.clip(lower=0)
    pc = c.shift(1)
    tr = pd.concat([(h - low), (h - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    a = 1.0 / period
    atr = tr.ewm(alpha=a, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=a, adjust=False).mean() / atr.replace(0, float("nan"))
    minus_di = 100 * minus_dm.ewm(alpha=a, adjust=False).mean() / atr.replace(0, float("nan"))
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, float("nan"))
    return dx.ewm(alpha=a, adjust=False).mean()


# ---------------------------------------------------------------------------
# Candidate B — VWAP-band mean-reversion (vectorized)
# ---------------------------------------------------------------------------
def signals_vwap_revert(df5: pd.DataFrame, p: Dict[str, Any]) -> pd.DataFrame:
    df = df5.reset_index(drop=True).copy()
    atr = _atr(df, int(p["atr_period"]))
    adx = _adx(df, int(p["adx_period"])).shift(1)
    tp_price = (df["high"] + df["low"] + df["close"]) / 3.0
    lb = int(p["vwap_lookback"])
    if "volume" in df.columns and df["volume"].fillna(0).abs().sum() > 0:
        vol = df["volume"].fillna(0.0)
        vwap = (tp_price * vol).rolling(lb, min_periods=lb).sum() / \
               vol.rolling(lb, min_periods=lb).sum().replace(0, float("nan"))
    else:
        vwap = df["close"].rolling(lb, min_periods=lb).mean()
    dev = df["close"] - vwap
    band_std = dev.rolling(int(p["band_std_lookback"]), min_periods=int(p["band_std_lookback"])).std()

    o = df["open"].to_numpy(float); c = df["close"].to_numpy(float)
    h = df["high"].to_numpy(float); lo = df["low"].to_numpy(float)
    atr_a = atr.to_numpy(float); adx_a = adx.to_numpy(float)
    vw = vwap.to_numpy(float); bs = band_std.to_numpy(float)
    bk = float(p["band_k"]); adx_max = float(p["adx_max"])
    upper = vw + bk * bs; lower = vw - bk * bs

    valid = (atr_a > 0) & ~np.isnan(vw) & ~np.isnan(bs) & (bs > 0) & \
            ~np.isnan(adx_a) & (adx_a < adx_max)
    bull_body = c > o; bear_body = c < o
    short_sig = valid & (h >= upper) & (c < upper) & bear_body
    long_sig = valid & (lo <= lower) & (c > lower) & bull_body
    # if both (rare), prefer the one whose excursion is larger; resolve by upper first
    short_sig = short_sig & ~long_sig

    sl_buf = float(p["atr_stop_buffer"])
    min_stop_atr = float(p["min_stop_atr"]); min_stop_pct = float(p["min_stop_pct"])
    tp_frac = float(p["tp_anchor_frac"]); min_tp_r = float(p["min_tp_r"])

    rows = []
    idxs = np.where(long_sig | short_sig)[0]
    for i in idxs:
        if long_sig[i]:
            side = "long"; exc = lo[i]
            min_stop = max(min_stop_atr * atr_a[i], min_stop_pct * c[i])
            sl = min(exc - sl_buf * atr_a[i], c[i] - min_stop)
            risk = c[i] - sl
            if risk <= 0:
                continue
            anchor_tp = c[i] + tp_frac * (vw[i] - c[i]); floor_tp = c[i] + min_tp_r * risk
            tp = max(anchor_tp, floor_tp)
            if tp <= c[i]:
                continue
        else:
            side = "short"; exc = h[i]
            min_stop = max(min_stop_atr * atr_a[i], min_stop_pct * c[i])
            sl = max(exc + sl_buf * atr_a[i], c[i] + min_stop)
            risk = sl - c[i]
            if risk <= 0:
                continue
            anchor_tp = c[i] - tp_frac * (c[i] - vw[i]); floor_tp = c[i] - min_tp_r * risk
            tp = min(anchor_tp, floor_tp)
            if tp >= c[i]:
                continue
        rows.append({"ts": df["timestamp"].iloc[i], "side": side,
                     "entry": float(c[i]), "sl": float(sl), "tp": float(tp), "idx": int(i)})
    return pd.DataFrame(rows, columns=["ts", "side", "entry", "sl", "tp", "idx"])


# ---------------------------------------------------------------------------
# Candidate A — displacement-continuation (vectorized gates)
# ---------------------------------------------------------------------------
def _parse_windows(spec):
    out = []
    for part in str(spec).split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            try:
                out.append((int(a), int(b)))
            except ValueError:
                pass
    return out


def signals_displacement(df5: pd.DataFrame, p: Dict[str, Any], htf_close, htf_ema) -> pd.DataFrame:
    """Vectorized version of hf_displacement_cont. Sweep/displacement/FVG are
    detected with rolling/windowed numpy. Mirrors the module's gate ORDER and
    thresholds. htf_close/htf_ema are per-bar 1h-EMA arrays aligned to df5."""
    df = df5.reset_index(drop=True).copy()
    n = len(df)
    atr = _atr(df, int(p["atr_period"])).to_numpy(float)
    o = df["open"].to_numpy(float); c = df["close"].to_numpy(float)
    h = df["high"].to_numpy(float); lo = df["low"].to_numpy(float)

    swing = int(p["swing_lookback_bars"]); sweep_lb = int(p["sweep_lookback_bars"])
    buf_bps = float(p["sweep_buffer_bps"])
    prev_low = df["low"].rolling(swing).min().shift(1).to_numpy(float)
    prev_high = df["high"].rolling(swing).max().shift(1).to_numpy(float)
    buffer = c * (buf_bps / 10_000.0)
    bull_swept = (lo < (prev_low - buffer)) & (c > prev_low)
    bear_swept = (h > (prev_high + buffer)) & (c < prev_high)

    disp_mult = float(p["displacement_atr_mult"]); min_btr = float(p["min_displacement_body_to_range"])
    body = np.abs(c - o); rng = np.maximum(h - lo, 1e-12)
    bull_disp = (body >= disp_mult * atr) & (body / rng >= min_btr) & (c > o)
    bear_disp = (body >= disp_mult * atr) & (body / rng >= min_btr) & (c < o)

    min_fvg = float(p["min_fvg_size_bps"])
    # bullish FVG at bar i: high[i-2] < low[i]
    bull_fvg = np.zeros(n, bool); bull_fvg_lo = np.full(n, np.nan); bull_fvg_hi = np.full(n, np.nan)
    bear_fvg = np.zeros(n, bool); bear_fvg_lo = np.full(n, np.nan); bear_fvg_hi = np.full(n, np.nan)
    for i in range(2, n):
        if h[i - 2] < lo[i] and (lo[i] - h[i - 2]) >= c[i] * min_fvg / 10_000.0:
            bull_fvg[i] = True; bull_fvg_lo[i] = h[i - 2]; bull_fvg_hi[i] = lo[i]
        if lo[i - 2] > h[i] and (lo[i - 2] - h[i]) >= c[i] * min_fvg / 10_000.0:
            bear_fvg[i] = True; bear_fvg_lo[i] = h[i]; bear_fvg_hi[i] = lo[i - 2]

    htf_on = bool(p["htf_trend_filter_enabled"])
    kz_on = bool(p["session_filter_enabled"])
    windows = _parse_windows(p["killzone_windows"]) if kz_on else []
    hours = df["timestamp"].dt.hour.to_numpy()
    def in_kz(i):
        if not kz_on or not windows:
            return True
        hr = hours[i]
        for s, e in windows:
            if (s <= e and s <= hr < e) or (s > e and (hr >= s or hr < e)):
                return True
        return False

    sl_buf = float(p["atr_sl_buffer_mult"]); tp_r = float(p["tp_at_r"])
    rows = []
    start = max(swing + 2, int(p["atr_period"]) + 2)
    for i in range(start, n):
        if atr[i] <= 0 or np.isnan(atr[i]):
            continue
        if not in_kz(i):
            continue
        # most-recent sweep within sweep_lb (mirror: scan back, freshest first)
        s0 = max(0, i - sweep_lb + 1)
        direction = None; sweep_idx = -1; sweep_level = sweep_extreme = 0.0
        for k in range(i, s0 - 1, -1):
            if bull_swept[k]:
                direction = "long"; sweep_idx = k; sweep_level = prev_low[k]; sweep_extreme = lo[k]; break
            if bear_swept[k]:
                direction = "short"; sweep_idx = k; sweep_level = prev_high[k]; sweep_extreme = h[k]; break
        if direction is None:
            continue
        # HTF hard gate (fails closed)
        if htf_on:
            hc = htf_close[i]; he = htf_ema[i]
            if hc != hc or he != he:
                continue
            if direction == "long" and hc <= he:
                continue
            if direction == "short" and hc >= he:
                continue
        # displacement after sweep
        disp_arr = bull_disp if direction == "long" else bear_disp
        has_disp = np.any(disp_arr[sweep_idx + 1:i + 1]) if sweep_idx + 1 <= i else False
        if not has_disp:
            continue
        # most recent FVG in leg [sweep_idx, i]
        fvg_arr = bull_fvg if direction == "long" else bear_fvg
        leg = np.where(fvg_arr[sweep_idx:i + 1])[0]
        if leg.size == 0:
            continue
        fk = sweep_idx + leg[-1]
        if direction == "long":
            f_lo = bull_fvg_lo[fk]; f_hi = bull_fvg_hi[fk]
        else:
            f_lo = bear_fvg_lo[fk]; f_hi = bear_fvg_hi[fk]
        # mitigation: wick-rejection at FVG on bar i
        bull_body = c[i] > o[i]; bear_body = c[i] < o[i]
        if direction == "long":
            if not (lo[i] <= f_hi and c[i] > f_hi and bull_body):
                continue
            sl = sweep_extreme - sl_buf * atr[i]; risk = c[i] - sl
            if risk <= 0:
                continue
            tp = c[i] + tp_r * risk
        else:
            if not (h[i] >= f_lo and c[i] < f_lo and bear_body):
                continue
            sl = sweep_extreme + sl_buf * atr[i]; risk = sl - c[i]
            if risk <= 0:
                continue
            tp = c[i] - tp_r * risk
        rows.append({"ts": df["timestamp"].iloc[i], "side": direction,
                     "entry": float(c[i]), "sl": float(sl), "tp": float(tp), "idx": int(i)})
    return pd.DataFrame(rows, columns=["ts", "side", "entry", "sl", "tp", "idx"])


def build_htf(df5: pd.DataFrame, base5m: pd.DataFrame, ema_period: int, htf_tf="1h"):
    rule = {"1h": "1h", "4h": "4h"}.get(htf_tf, "1h")
    htf = _resample(base5m, rule)
    htf["ema"] = htf["close"].ewm(span=ema_period, adjust=False).mean()
    htf = htf.dropna(subset=["ema"])
    merged = pd.merge_asof(df5[["timestamp"]].sort_values("timestamp"),
                           htf[["timestamp", "close", "ema"]].rename(columns={"close": "_hc", "ema": "_he"}).sort_values("timestamp"),
                           on="timestamp", direction="backward")
    return merged["_hc"].to_numpy(), merged["_he"].to_numpy()


# ---------------------------------------------------------------------------
# Fast R-replay on the 5m clock (single position, intrabar SL-first then TP)
# ---------------------------------------------------------------------------
def replay(sigs: pd.DataFrame, df5: pd.DataFrame, *, be_after_1r=False, signal_ttl_bars=1):
    c = df5["close"].to_numpy(float); h = df5["high"].to_numpy(float); lo = df5["low"].to_numpy(float)
    n = len(df5)
    sig_at = {int(r["idx"]): r for _, r in sigs.iterrows()}
    fee = FEE_BPS_ROUNDTRIP / 10_000.0
    pos = None; trades = []; exits = {"tp": 0, "sl": 0, "eod": 0}
    pending = None; pend_idx = -10**9
    for i in range(n):
        if i in sig_at:
            pending = sig_at[i]; pend_idx = i
        if pos is not None:
            side, entry, slv, tpv, risk0 = pos
            px = None; reason = None
            if side == "long":
                if lo[i] <= slv: px, reason = slv, "sl"
                elif h[i] >= tpv: px, reason = tpv, "tp"
            else:
                if h[i] >= slv: px, reason = slv, "sl"
                elif lo[i] <= tpv: px, reason = tpv, "tp"
            if px is None and be_after_1r:
                r1 = abs(entry - slv)
                if side == "long" and c[i] >= entry + r1 and slv < entry:
                    slv = entry; pos = (side, entry, slv, tpv, risk0)
                elif side == "short" and c[i] <= entry - r1 and slv > entry:
                    slv = entry; pos = (side, entry, slv, tpv, risk0)
            if px is not None:
                g = (px - entry) if side == "long" else (entry - px)
                trades.append((g - fee * (entry + px)) / risk0); exits[reason] += 1; pos = None
        if pos is None and pending is not None and i == pend_idx:
            side = pending["side"]; fill = c[i]; slv = float(pending["sl"]); tpv = float(pending["tp"])
            risk0 = abs(fill - slv)
            if risk0 > 0:
                pos = (side, fill, slv, tpv, risk0)
    if pos is not None:
        side, entry, slv, tpv, risk0 = pos; px = c[-1]
        g = (px - entry) if side == "long" else (entry - px)
        trades.append((g - fee * (entry + px)) / risk0); exits["eod"] += 1
    arr = np.array(trades) if trades else np.array([0.0])
    return {"trades": len(trades), "win_rate": round(100 * (arr > 0).mean(), 2),
            "E_R": round(float(arr.mean()), 4), "total_R": round(float(arr.sum()), 2),
            "exits": exits, "R": arr}


DEFAULTS_B = {"vwap_lookback": 96, "atr_period": 14, "adx_period": 14, "adx_max": 22.0,
              "band_k": 2.0, "band_std_lookback": 96, "atr_stop_buffer": 0.5,
              "min_stop_atr": 0.75, "min_stop_pct": 0.003, "tp_anchor_frac": 1.0, "min_tp_r": 0.6}
DEFAULTS_A = {"sweep_lookback_bars": 12, "swing_lookback_bars": 20, "atr_period": 14,
              "sweep_buffer_bps": 5.0, "displacement_atr_mult": 2.0,
              "min_displacement_body_to_range": 0.60, "min_fvg_size_bps": 2.0,
              "htf_trend_filter_enabled": True, "htf_filter_ema_period": 50,
              "session_filter_enabled": True, "killzone_windows": "7-10,12-16",
              "atr_sl_buffer_mult": 0.25, "tp_at_r": 1.5}


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="/home/user/ict-trader-data/btc_5m.parquet")
    ap.add_argument("--cand", choices=["A", "B"], required=True)
    ap.add_argument("--start", default=None); ap.add_argument("--end", default=None)
    ap.add_argument("--validate", action="store_true", help="compare vs module on a sample")
    ap.add_argument("--grid", action="store_true")
    args = ap.parse_args(argv[1:])
    base = _load(args.data)
    df5 = _date_filter(_resample(base, "5min"), args.start, args.end)

    if args.cand == "B":
        if args.validate:
            _validate_B(base, df5, args.start, args.end)
            return 0
        if args.grid:
            _grid_B(base, df5)
            return 0
        s = signals_vwap_revert(df5, DEFAULTS_B)
        print("B default:", replay(s, df5)["E_R"], "n", len(s))
    else:
        hc, he = build_htf(df5, base, DEFAULTS_A["htf_filter_ema_period"])
        if args.validate:
            _validate_A(base, df5, hc, he, args.start, args.end)
            return 0
        if args.grid:
            _grid_A(base, df5)
            return 0
        s = signals_displacement(df5, DEFAULTS_A, hc, he)
        print("A default:", replay(s, df5, be_after_1r=True)["E_R"], "n", len(s))
    return 0


def _validate_B(base, df5, start, end):
    import scripts.research.hf_solo_sim as sim
    vec = signals_vwap_revert(df5, DEFAULTS_B)
    mod = sim.gen_signals("hf_vwap_revert", "src.units.strategies.hf_vwap_revert", "5m",
                          base, start=start, end=end, overrides={})
    vts = set(pd.to_datetime(vec["ts"]).astype("int64")); mts = set(pd.to_datetime(mod["ts"]).astype("int64"))
    print(f"B validate: vec={len(vec)} mod={len(mod)} overlap={len(vts & mts)} "
          f"only_vec={len(vts - mts)} only_mod={len(mts - vts)}")


def _validate_A(base, df5, hc, he, start, end):
    import scripts.research.hf_solo_sim as sim
    vec = signals_displacement(df5, DEFAULTS_A, hc, he)
    mod = sim.gen_signals("hf_displacement_cont", "src.units.strategies.hf_displacement_cont", "5m",
                          base, start=start, end=end, overrides={})
    vts = set(pd.to_datetime(vec["ts"]).astype("int64")); mts = set(pd.to_datetime(mod["ts"]).astype("int64"))
    print(f"A validate: vec={len(vec)} mod={len(mod)} overlap={len(vts & mts)} "
          f"only_vec={len(vts - mts)} only_mod={len(mts - vts)}")


def _grid_B(base, df5):
    print(f"B grid IS bars {len(df5)}")
    res = []
    for band_k, adx_max, buf, tp_frac, min_tp_r, vwlb in itertools.product(
            [2.0, 2.5, 3.0, 3.5], [16, 20, 24], [0.3, 0.5, 1.0], [0.7, 1.0], [0.8, 1.0], [96, 144]):
        p = dict(DEFAULTS_B); p.update(band_k=band_k, adx_max=float(adx_max), atr_stop_buffer=buf,
                                       tp_anchor_frac=tp_frac, min_tp_r=min_tp_r, vwap_lookback=vwlb,
                                       band_std_lookback=vwlb)
        s = signals_vwap_revert(df5, p)
        if len(s) < 40:
            continue
        o = replay(s, df5)
        Rc = np.clip(o["R"], -2.0, None)
        res.append((round(float(Rc.mean()), 4), o["E_R"], o["win_rate"], o["trades"],
                    band_k, adx_max, buf, tp_frac, min_tp_r, vwlb))
    res.sort(reverse=True)
    print("E_Rcap  E_R    WR    n    bk adx buf tpf mtr vwlb")
    for r in res[:15]:
        print(f"{r[0]:7.4f} {r[1]:7.4f} {r[2]:5.1f} {r[3]:4d}  {r[4]} {r[5]} {r[6]} {r[7]} {r[8]} {r[9]}")


def _grid_A(base, df5):
    print(f"A grid IS bars {len(df5)}")
    res = []
    htf_cache = {}
    for disp, emaP, kz, tpr, slbuf, minbtr in itertools.product(
            [1.3, 1.6, 2.0], [20, 50], ["7-10,12-16", "0-24"], [1.0, 1.5, 2.0], [0.25], [0.55, 0.65]):
        if emaP not in htf_cache:
            htf_cache[emaP] = build_htf(df5, base, emaP)
        hc, he = htf_cache[emaP]
        p = dict(DEFAULTS_A); p.update(displacement_atr_mult=disp, htf_filter_ema_period=emaP,
                                       killzone_windows=kz, tp_at_r=tpr, atr_sl_buffer_mult=slbuf,
                                       min_displacement_body_to_range=minbtr,
                                       session_filter_enabled=(kz != "0-24"))
        s = signals_displacement(df5, p, hc, he)
        if len(s) < 20:
            continue
        o = replay(s, df5, be_after_1r=True)
        Rc = np.clip(o["R"], -2.0, None)
        res.append((round(float(Rc.mean()), 4), o["E_R"], o["win_rate"], o["trades"],
                    disp, emaP, kz, tpr, minbtr))
    res.sort(reverse=True)
    print("E_Rcap  E_R    WR    n    disp ema kz tpr btr")
    for r in res[:15]:
        print(f"{r[0]:7.4f} {r[1]:7.4f} {r[2]:5.1f} {r[3]:4d}  {r[4]} {r[5]} {r[6]} {r[7]} {r[8]}")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
