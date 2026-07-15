#!/usr/bin/env python3
"""Cointegration-stability monitor for the M22 pairs sleeve (research/Tier-1).

The D2 finding — market-neutral crypto cointegration pairs at 1h — carries its
risk in ONE place: a **cointegration break** (the spread stops mean-reverting and
trends away), which is what produces the observed maxDD tail. This tool measures
whether a pair's spread is *stably* mean-reverting, and how fast, using only the
same log-spread the backtest trades — numpy only, no statsmodels dep.

Diagnostics per pair (one invocation = one A/B pair):
  * **half-life** of mean reversion (bars → hours): OLS of Δspread on the lagged
    spread gives the AR(1) coefficient λ (Δs = λ·s_{t-1}+…); half-life =
    −ln2 / ln(1+λ). A short, stable half-life is the health signal; λ ≥ 0
    (no reversion) is a broken pair.
  * **rolling half-life**: recompute over rolling windows → median + IQR + the
    fraction of windows with a valid (mean-reverting, finite, < cap) half-life.
    A pair that is only *sometimes* cointegrated shows a low valid-fraction.
  * **hedge-β drift**: std/|mean| of the rolling hedge ratio — a drifting β means
    the cointegrating relationship itself is moving (a soft break signal).
  * **spread-bounded %**: fraction of bars with |z| < `z_cap` — a stationary,
    reverting spread stays bounded; a broken one runs away.

Observe-only; proposes nothing. A live pairs sleeve would consult these to
DEMOTE a pair whose half-life blows out or whose valid-fraction collapses (the
divergence-tail guard the D2 finding flagged as a remaining gap).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

_TF_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600,
               "2h": 7200, "4h": 14400, "1d": 86400}


def _load_candles(path: str) -> pd.DataFrame:
    df = pd.read_parquet(path) if path.endswith(".parquet") else pd.read_csv(path)
    cols = {c.lower(): c for c in df.columns}
    need = ["timestamp", "open", "high", "low", "close"]
    df = df.rename(columns={cols[c]: c for c in need if c in cols and cols[c] != c})
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    return df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)


def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    return (df.set_index("timestamp")
            .resample(rule, label="right", closed="right")
            .agg({"close": "last"}).dropna().reset_index())


def _align(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    m = pd.merge(a[["timestamp", "close"]].rename(columns={"close": "close_a"}),
                 b[["timestamp", "close"]].rename(columns={"close": "close_b"}),
                 on="timestamp", how="inner")
    return m[(m["close_a"] > 0) & (m["close_b"] > 0)].reset_index(drop=True)


def _rolling_beta(la: np.ndarray, lb: np.ndarray, window: int) -> np.ndarray:
    """Rolling OLS slope of la on lb (cov/var), shifted 1 (leakage-safe)."""
    sa = pd.Series(la)
    sb = pd.Series(lb)
    cov = sa.rolling(window).cov(sb)
    var = sb.rolling(window).var()
    beta = (cov / var).replace([np.inf, -np.inf], np.nan).shift(1).fillna(1.0)
    return beta.to_numpy()


def _half_life_bars(spread: np.ndarray) -> Optional[float]:
    """AR(1) half-life via OLS of Δs on s_{t-1}: Δs = c + λ·s_{t-1}. HL only
    exists for a mean-reverting series (−1 < λ < 0)."""
    s = np.asarray(spread, dtype=float)
    s = s[np.isfinite(s)]
    if s.size < 20:
        return None
    s_lag = s[:-1]
    ds = np.diff(s)
    # OLS with intercept
    x = np.column_stack([np.ones_like(s_lag), s_lag])
    try:
        coef, *_ = np.linalg.lstsq(x, ds, rcond=None)
    except np.linalg.LinAlgError:
        return None
    lam = float(coef[1])
    if lam >= 0 or (1.0 + lam) <= 0:
        return None  # not mean-reverting
    hl = -np.log(2.0) / np.log(1.0 + lam)
    return hl if np.isfinite(hl) and hl > 0 else None


def analyze(path_a: str, path_b: str, *, resample: str, lookback: int,
            window: int, z_cap: float, hl_cap_bars: float) -> Dict[str, Any]:
    a = _resample(_load_candles(path_a), resample)
    b = _resample(_load_candles(path_b), resample)
    m = _align(a, b)
    n = len(m)
    tf_sec = _TF_SECONDS.get(resample, 3600)
    hrs = tf_sec / 3600.0
    if n <= max(lookback, window) + 5:
        return {"n_bars": n, "error": "insufficient aligned bars"}
    la = np.log(m["close_a"].to_numpy())
    lb = np.log(m["close_b"].to_numpy())
    beta = _rolling_beta(la, lb, lookback)
    spread = la - beta * lb
    zmean = pd.Series(spread).rolling(lookback).mean().to_numpy()
    zstd = pd.Series(spread).rolling(lookback).std().to_numpy()
    with np.errstate(invalid="ignore", divide="ignore"):
        z = (spread - zmean) / zstd
    z_valid = z[np.isfinite(z)]
    bounded_pct = float(100.0 * np.mean(np.abs(z_valid) < z_cap)) if z_valid.size else None

    global_hl = _half_life_bars(spread)
    # rolling half-life
    hls: List[float] = []
    valid = 0
    total = 0
    step = max(window // 4, 1)
    for start in range(0, n - window, step):
        total += 1
        hl = _half_life_bars(spread[start:start + window])
        if hl is not None and hl <= hl_cap_bars:
            valid += 1
            hls.append(hl)
    beta_fin = beta[np.isfinite(beta)]
    beta_drift = (float(np.std(beta_fin) / abs(np.mean(beta_fin)))
                  if beta_fin.size and np.mean(beta_fin) != 0 else None)
    return {
        "n_bars": n, "resample": resample, "lookback": lookback, "window": window,
        "global_half_life_hours": round(global_hl * hrs, 2) if global_hl else None,
        "rolling_hl_median_hours": round(float(np.median(hls)) * hrs, 2) if hls else None,
        "rolling_hl_iqr_hours": (round((float(np.percentile(hls, 75)) -
                                        float(np.percentile(hls, 25))) * hrs, 2)
                                 if len(hls) >= 4 else None),
        "rolling_hl_valid_pct": round(100.0 * valid / total, 1) if total else None,
        "hedge_beta_drift": round(beta_drift, 3) if beta_drift is not None else None,
        "spread_bounded_pct": round(bounded_pct, 1) if bounded_pct is not None else None,
    }


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(description="Cointegration-stability diagnostics for a pair.")
    p.add_argument("--data-a", required=True)
    p.add_argument("--data-b", required=True)
    p.add_argument("--symbol-a", default="A")
    p.add_argument("--symbol-b", default="B")
    p.add_argument("--resample", default="1h")
    p.add_argument("--lookback", type=int, default=15)
    p.add_argument("--window", type=int, default=720, help="rolling-window bars for HL stability (720=30d @1h)")
    p.add_argument("--z-cap", type=float, default=4.0)
    p.add_argument("--hl-cap-bars", type=float, default=200.0)
    p.add_argument("--json", dest="json_out", default=None)
    args = p.parse_args(argv[1:])
    try:
        out = analyze(args.data_a, args.data_b, resample=args.resample,
                      lookback=args.lookback, window=args.window,
                      z_cap=args.z_cap, hl_cap_bars=args.hl_cap_bars)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    out["pair"] = f"{args.symbol_a}/{args.symbol_b}"
    print(f"cointegration-stability — {out['pair']} {args.resample}")
    for k in ("n_bars", "global_half_life_hours", "rolling_hl_median_hours",
              "rolling_hl_iqr_hours", "rolling_hl_valid_pct", "hedge_beta_drift",
              "spread_bounded_pct"):
        print(f"  {k}={out.get(k)}")
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
