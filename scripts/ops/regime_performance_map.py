#!/usr/bin/env python3
"""Regime-conditional performance map — Step 1 of regime-conditional strategy
weighting (docs/research/regime-conditional-strategy-weighting-DESIGN.md).

Buckets a strategy's per-trade backtest outcomes by the REGIME at entry —
ADX(14) band x realised-vol tercile, both computed NO-LOOKAHEAD from the same
candles — and reports the edge per regime. Answers the falsifiable gating
question: does the strategy have a *predictable* regime in which it is reliably
+EV, and how concentrated is its edge?

Inputs: an OHLCV candle CSV (optionally --resample) + an `--emit-trades` JSONL
(rows carry ``entry_time`` + ``net_r``, as written by scripts/backtest_*.py).
Tier-1 research tooling — reads files, writes a report; no live path.

Usage:
    python3 scripts/ops/regime_performance_map.py --data data/ETHUSDT_15m.csv \
        --resample 4h --emit trades.jsonl --label trend_ETH_4h
"""
from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import pandas as pd


def _load_candles(path: str, resample: str | None) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.lower() for c in df.columns]
    tcol = "timestamp" if "timestamp" in df.columns else df.columns[0]
    df[tcol] = pd.to_datetime(df[tcol], utc=True, errors="coerce")
    df = df.dropna(subset=[tcol]).set_index(tcol).sort_index()
    if resample:
        df = df.resample(resample).agg(
            {"open": "first", "high": "max", "low": "min",
             "close": "last", "volume": "sum"}
        ).dropna()
    return df


def _adx(df: pd.DataFrame, period: int) -> pd.Series:
    h, low, c = df["high"], df["low"], df["close"]
    up, dn = h.diff(), -low.diff()
    plus = ((up > dn) & (up > 0)).astype(float) * up.clip(lower=0)
    minus = ((dn > up) & (dn > 0)).astype(float) * dn.clip(lower=0)
    tr = pd.concat([h - low, (h - c.shift()).abs(), (low - c.shift()).abs()], axis=1).max(axis=1)
    a = 1.0 / period
    atr = tr.ewm(alpha=a, adjust=False, min_periods=period).mean()
    pdi = 100.0 * plus.ewm(alpha=a, adjust=False, min_periods=period).mean() / atr
    mdi = 100.0 * minus.ewm(alpha=a, adjust=False, min_periods=period).mean() / atr
    dx = 100.0 * (pdi - mdi).abs() / (pdi + mdi).replace(0.0, np.nan)
    return dx.ewm(alpha=a, adjust=False, min_periods=period).mean()


_ADX_BANDS = ["<15", "15-25", "25-35", ">35"]


def _adx_band(x: float) -> str | None:
    if pd.isna(x):
        return None
    return "<15" if x < 15 else "15-25" if x < 25 else "25-35" if x < 35 else ">35"


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True)
    p.add_argument("--resample", default=None)
    p.add_argument("--emit", required=True)
    p.add_argument("--label", default="")
    p.add_argument("--adx-period", type=int, default=14)
    p.add_argument("--vol-window", type=int, default=20)
    p.add_argument("--min-bucket", type=int, default=15, help="min trades for a bucket to count toward the verdict")
    p.add_argument("--json", dest="json_out", default=None)
    a = p.parse_args(argv)

    df = _load_candles(a.data, a.resample)
    df["adx"] = _adx(df, a.adx_period)
    lr = np.log(df["close"] / df["close"].shift())
    df["vol"] = lr.rolling(a.vol_window).std()
    df["adx_b"] = df["adx"].map(_adx_band)
    q1, q2 = df["vol"].quantile([1 / 3, 2 / 3])
    df["vol_b"] = df["vol"].map(
        lambda x: None if pd.isna(x) else ("loVol" if x < q1 else "midVol" if x < q2 else "hiVol")
    )

    trades = [json.loads(line) for line in open(a.emit) if line.strip()]
    rows = []
    for t in trades:
        et = pd.to_datetime(t.get("entry_time"), utc=True, errors="coerce")
        if pd.isna(et):
            continue
        idx = df.index.searchsorted(et, side="right") - 1  # bar closed AT/BEFORE entry — no lookahead
        if idx < 0 or idx >= len(df):
            continue
        bar = df.iloc[idx]
        rows.append({"net_r": float(t.get("net_r", 0.0)), "adx_b": bar["adx_b"], "vol_b": bar["vol_b"]})
    R = pd.DataFrame(rows)
    label = a.label or a.emit
    if R.empty:
        print(f"=== {label}: no trades joined to candles ===")
        return 0

    total = float(R["net_r"].sum())
    out: dict = {"label": label, "n_trades": int(len(R)), "total_net_r": round(total, 2), "by_adx": {}, "by_adx_vol": {}}
    print(f"\n=== {label}: {len(R)} trades, total net_r {total:.1f} ===")
    print("-- by ADX regime --")
    for b in _ADX_BANDS:
        s = R[R["adx_b"] == b]
        if s.empty:
            continue
        wr = 100 * (s["net_r"] > 0).mean()
        share = 100 * s["net_r"].sum() / total if total else 0.0
        out["by_adx"][b] = {"n": int(len(s)), "net_r": round(float(s["net_r"].sum()), 1), "mean": round(float(s["net_r"].mean()), 3), "wr": round(float(wr), 0)}
        print(f"  ADX {b:6} n={len(s):4} netR={s['net_r'].sum():8.1f} mean={s['net_r'].mean():+.3f} wr={wr:4.0f}% share={share:5.0f}%")
    print(f"-- by ADX x Vol (n>={a.min_bucket}) --")
    for ab in _ADX_BANDS:
        for vb in ("loVol", "midVol", "hiVol"):
            s = R[(R["adx_b"] == ab) & (R["vol_b"] == vb)]
            if len(s) < a.min_bucket:
                continue
            out["by_adx_vol"][f"{ab}/{vb}"] = {"n": int(len(s)), "net_r": round(float(s["net_r"].sum()), 1), "mean": round(float(s["net_r"].mean()), 3)}
            print(f"  {ab:6} {vb:6} n={len(s):4} netR={s['net_r'].sum():8.1f} mean={s['net_r'].mean():+.3f} wr={100*(s['net_r']>0).mean():4.0f}%")

    # Verdict: which regimes are +EV with a real sample (reported even when the
    # strategy is net-NEGATIVE overall — finding a strategy's good regime inside
    # an otherwise-mediocre book is the entire point), and how concentrated.
    fav = [b for b in _ADX_BANDS
           if b in out["by_adx"] and out["by_adx"][b]["mean"] > 0 and out["by_adx"][b]["n"] >= a.min_bucket]
    if out["by_adx"]:
        top = max(out["by_adx"], key=lambda b: out["by_adx"][b]["net_r"])
        top_share = round(100 * out["by_adx"][top]["net_r"] / total, 0) if total else None
        out["verdict"] = {"favorable_adx_regimes": fav, "best_band": top, "best_band_share_pct": top_share,
                          "net_negative_overall": bool(total <= 0)}
        share_txt = f" | edge concentrated in {top} ({top_share:.0f}% of total)" if total else f" | best band {top}"
        print(f"VERDICT: +EV ADX regimes (mean>0, n>={a.min_bucket}): {fav or 'NONE'}{share_txt}"
              + (" [strategy is net-NEGATIVE overall — these regimes are where it'd be worth listening]" if total <= 0 and fav else ""))
    if a.json_out:
        with open(a.json_out, "w") as fh:
            fh.write(json.dumps(out, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
