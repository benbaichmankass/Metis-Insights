#!/usr/bin/env python3
"""Regime-weight portfolio overlay — Step 2 (matrix) of regime-conditional
strategy weighting (docs/research/regime-conditional-strategy-weighting-DESIGN.md).

Tests, as a MATRIX of variations (not one config — the lesson from the ADX
overfit), whether weighting each strategy's per-trade contribution by its
**regime favourability — learned on a TRAIN period only** — improves the
aggregate (portfolio) net R on a **held-out** period vs the un-weighted book.

Matrix axes:
  * regime definition: ``adx`` (ADX band) | ``adxvol`` (ADX band x vol tercile)
  * weight scheme:     baseline (w=1) | hard_sign | graded | winrate

For each (regime_def, scheme): fit w_s(regime) on train (per cell x regime
bucket), apply to the holdout trades, sum w*net_r across all cells. The winner
is the scheme that beats baseline ON HOLDOUT with a small train->holdout
degradation (generalises); a scheme that only wins on train is overfit.

Inputs: a manifest JSON ``[{"label","data","resample","emit"}, ...]``.
Tier-1 research tooling — reads files, writes a report. No live path.
"""
from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import pandas as pd

_ADX_BANDS = ["<15", "15-25", "25-35", ">35"]


def _load_candles(path: str, resample: str | None) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.lower() for c in df.columns]
    tcol = "timestamp" if "timestamp" in df.columns else df.columns[0]
    df[tcol] = pd.to_datetime(df[tcol], utc=True, errors="coerce")
    df = df.dropna(subset=[tcol]).set_index(tcol).sort_index()
    if resample:
        df = df.resample(resample).agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        ).dropna()
    return df


def _adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
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


def _adx_band(x: float):
    if pd.isna(x):
        return None
    return "<15" if x < 15 else "15-25" if x < 25 else "25-35" if x < 35 else ">35"


def _build_trades(manifest: list[dict], vol_window: int = 20) -> pd.DataFrame:
    """Per-trade rows: cell, time, net_r, adx_b, vol_b (regime at entry, no-lookahead)."""
    rows = []
    for cell in manifest:
        df = _load_candles(cell["data"], cell.get("resample"))
        df["adx"] = _adx(df)
        df["adx_b"] = df["adx"].map(_adx_band)
        lr = np.log(df["close"] / df["close"].shift())
        df["vol"] = lr.rolling(vol_window).std()
        q1, q2 = df["vol"].quantile([1 / 3, 2 / 3])
        for t in (json.loads(line) for line in open(cell["emit"]) if line.strip()):
            et = pd.to_datetime(t.get("entry_time"), utc=True, errors="coerce")
            if pd.isna(et):
                continue
            idx = df.index.searchsorted(et, side="right") - 1
            if idx < 0 or idx >= len(df):
                continue
            bar = df.iloc[idx]
            vb = None if pd.isna(bar["vol"]) else ("loVol" if bar["vol"] < q1 else "midVol" if bar["vol"] < q2 else "hiVol")
            rows.append({"cell": cell["label"], "family": str(cell["label"]).split("_")[0],
                         "time": et, "net_r": float(t.get("net_r", 0.0)),
                         "adx_b": bar["adx_b"], "vol_b": vb})
    return pd.DataFrame(rows)


def _bucket(row, regime_def: str):
    if regime_def == "adx":
        return row["adx_b"]
    return f"{row['adx_b']}/{row['vol_b']}"


def _fit_weights(train: pd.DataFrame, regime_def: str, scheme: str, min_n: int, group_col: str = "cell"):
    """Return {(group_val, bucket): w in [0,1]} learned on the TRAIN slice only.

    group_col selects the granularity the weight is fit at: ``cell`` (per
    strategy x symbol x band — most parameters, most overfit-prone) or
    ``family`` (one weight per family x band, e.g. "pullback in chop" — far
    fewer params, the less-overfit variant the Step-2 results pointed to).
    """
    w = {}
    train = train.assign(bucket=train.apply(lambda r: _bucket(r, regime_def), axis=1))
    g = train.groupby([group_col, "bucket"])["net_r"]
    means, wins, counts = g.mean(), g.apply(lambda s: (s > 0).mean()), g.size()
    pos_means = means[means > 0]
    scale = pos_means.quantile(0.8) if not pos_means.empty else 1.0  # robust upper for grading
    for key in means.index:
        n, m, wr = counts[key], means[key], wins[key]
        if scheme == "baseline":
            w[key] = 1.0
        elif n < min_n:
            w[key] = 1.0  # too few train samples to judge -> don't gate (conservative)
        elif scheme == "hard_sign":
            w[key] = 1.0 if m > 0 else 0.0
        elif scheme == "graded":
            w[key] = float(np.clip(m / scale, 0.0, 1.0)) if scale else (1.0 if m > 0 else 0.0)
        elif scheme == "winrate":
            w[key] = float(np.clip(2.0 * wr - 0.8, 0.0, 1.0))  # wr 0.4->0, 0.9->1
        else:
            w[key] = 1.0
    return w


def _apply(slice_df: pd.DataFrame, weights: dict, regime_def: str, group_col: str = "cell") -> pd.Series:
    def w_of(r):
        if not weights:
            return 1.0
        return weights.get((r[group_col], _bucket(r, regime_def)), 1.0)  # unseen bucket -> 1.0
    return slice_df["net_r"] * slice_df.apply(w_of, axis=1)


def _metrics(weighted: pd.Series) -> dict:
    n = len(weighted)
    tot = float(weighted.sum())
    mean = float(weighted.mean()) if n else 0.0
    std = float(weighted.std(ddof=1)) if n > 1 else 0.0
    sharpe = (mean / std * np.sqrt(n)) if std else 0.0  # per-trade Sharpe, scaled
    cum = weighted.cumsum()
    dd = float((cum.cummax() - cum).max()) if n else 0.0
    return {"n": int(n), "net_r": round(tot, 1), "sharpe": round(float(sharpe), 2), "max_dd_r": round(dd, 1)}


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", required=True)
    p.add_argument("--cutoff", default="2025-01-01", help="train < cutoff <= holdout (UTC date)")
    p.add_argument("--min-n", type=int, default=20, help="min train samples in a bucket to gate it")
    p.add_argument("--group", choices=("cell", "family"), default="cell",
                   help="granularity the weight is fit at: per cell (default) or per family (fewer params, less overfit-prone)")
    p.add_argument("--json", dest="json_out", default=None)
    a = p.parse_args(argv)

    manifest = json.load(open(a.manifest))
    T = _build_trades(manifest)
    if T.empty:
        print("no trades built")
        return 1
    cutoff = pd.to_datetime(a.cutoff, utc=True)
    train, holdout = T[T["time"] < cutoff], T[T["time"] >= cutoff]
    print(f"trades: {len(T)} total | train {len(train)} (<{a.cutoff}) | holdout {len(holdout)} (>={a.cutoff})")
    base_train = float(train["net_r"].sum())
    base_hold = float(holdout["net_r"].sum())
    print(f"baseline (un-weighted): train netR={base_train:.1f}  holdout netR={base_hold:.1f}\n")

    results = []
    print(f"weight granularity: {a.group}\n")
    print(f"{'regime_def':9} {'scheme':10} | {'TRAIN netR':>10} | {'HOLDOUT netR':>12} {'vs base':>8} {'sharpe':>7} {'maxDD':>7} | {'train->hold degrade':>20}")
    for regime_def in ("adx", "adxvol"):
        for scheme in ("baseline", "hard_sign", "graded", "winrate"):
            w = _fit_weights(train, regime_def, scheme, a.min_n, a.group)
            tr = _metrics(_apply(train, w, regime_def, a.group))
            ho = _metrics(_apply(holdout, w, regime_def, a.group))
            vs = ho["net_r"] - base_hold
            degrade = round((tr["net_r"] / base_train if base_train else 0) - (ho["net_r"] / base_hold if base_hold else 0), 2)
            results.append({"regime_def": regime_def, "scheme": scheme, "train": tr, "holdout": ho,
                            "holdout_vs_baseline": round(vs, 1), "train_minus_holdout_ratio": degrade})
            flag = "  <== beats baseline OOS" if (scheme != "baseline" and vs > 0) else ""
            print(f"{regime_def:9} {scheme:10} | {tr['net_r']:>10.1f} | {ho['net_r']:>12.1f} {vs:>+8.1f} {ho['sharpe']:>7.2f} {ho['max_dd_r']:>7.1f} | {degrade:>20}{flag}")
    if a.json_out:
        json.dump({"cutoff": a.cutoff, "group": a.group,
                   "baseline": {"train": base_train, "holdout": base_hold}, "matrix": results},
                  open(a.json_out, "w"), indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
