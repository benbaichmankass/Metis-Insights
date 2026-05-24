#!/usr/bin/env python3
"""Trend entry-quality ML filter — proof of concept (S9 models-in-the-loop).

Models-in-the-loop, self-contained: trains a win-probability classifier on
the trend backtest's TRADE LEDGER (donchian-breakout entries + entry-context
features -> won/lost over the full history), then backtests the model-
FILTERED trend on an untouched OOS window (take only entries whose
predicted P(win) >= threshold). Reports filtered vs unfiltered
expectancy/net/winrate so we can see whether the model lifts the edge —
the whole point of an entry filter on the live trend.

No lookahead: time-ordered split — fit on entries strictly before
--split-date, evaluate on entries on/after it. Reuses the validated
``scripts/backtest_trend.run_backtest`` (via its emit_path) for the
ledger so the trades match the live strategy exactly. Research only.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.backtest_trend import (  # noqa: E402
    _atr, _load_candles, _resample, run_backtest,
)

FEATS = ["dir_long", "breakout_depth_atr", "atr_pct", "dc_width_atr",
         "adx", "ret5", "ret20", "hour", "dow"]


def _adx(df: pd.DataFrame, period: int) -> pd.Series:
    h, low, c = df["high"], df["low"], df["close"]
    up = h.diff(); down = -low.diff()
    plus_dm = ((up > down) & (up > 0)) * up.clip(lower=0)
    minus_dm = ((down > up) & (down > 0)) * down.clip(lower=0)
    pc = c.shift(1)
    tr = pd.concat([(h - low), (h - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    a = 1.0 / period
    atr = tr.ewm(alpha=a, adjust=False).mean()
    pdi = 100 * plus_dm.ewm(alpha=a, adjust=False).mean() / atr.replace(0, pd.NA)
    mdi = 100 * minus_dm.ewm(alpha=a, adjust=False).mean() / atr.replace(0, pd.NA)
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, pd.NA)
    return dx.ewm(alpha=a, adjust=False).mean()


def main(argv):
    ap = argparse.ArgumentParser(description="Trend ML entry filter PoC")
    ap.add_argument("--data", default="data/backtest_BTCUSDT_5m.csv")
    ap.add_argument("--resample", default="2h")
    ap.add_argument("--timeframe", default="2h")
    ap.add_argument("--donchian", type=int, default=20)
    ap.add_argument("--atr-period", type=int, default=14)
    ap.add_argument("--atr-stop-mult", type=float, default=2.5)
    ap.add_argument("--trail-mult", type=float, default=3.5)
    ap.add_argument("--timeout-bars", type=int, default=200)
    ap.add_argument("--cooldown-bars", type=int, default=1)
    ap.add_argument("--split-date", default="2024-01-01")
    ap.add_argument("--thresholds", default="0.35,0.4,0.45,0.5,0.55,0.6")
    a = ap.parse_args(argv[1:])

    raw = _load_candles(a.data)
    if a.resample:
        raw = _resample(raw, a.resample)

    # Feature frame (same windows the strategy uses; shifted -> no lookahead).
    f = raw.copy().reset_index(drop=True)
    f["atr"] = _atr(f, a.atr_period)
    f["dc_hi"] = f["high"].rolling(a.donchian).max().shift(1)
    f["dc_lo"] = f["low"].rolling(a.donchian).min().shift(1)
    f["adx"] = _adx(f, a.atr_period).shift(1)
    f["ret5"] = f["close"].pct_change(5)
    f["ret20"] = f["close"].pct_change(20)
    f["t"] = pd.to_datetime(f["timestamp"], utc=True)

    # Trade ledger from the validated backtest (entry_time, direction, net_r).
    tmp = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False).name
    run_backtest(raw.copy(), donchian=a.donchian, atr_period=a.atr_period,
                 atr_stop_mult=a.atr_stop_mult, trail_mult=a.trail_mult,
                 timeout_bars=a.timeout_bars, cooldown_bars=a.cooldown_bars,
                 timeframe=a.timeframe, symbol="BTCUSDT", emit_path=tmp)
    led = pd.DataFrame([json.loads(line) for line in open(tmp)])
    led["entry_time"] = pd.to_datetime(led["entry_time"], utc=True)

    m = led.merge(f, left_on="entry_time", right_on="t", how="left")
    m["dir_long"] = (m["direction"] == "long").astype(int)
    m["breakout_depth_atr"] = np.where(
        m["dir_long"] == 1, (m["close"] - m["dc_hi"]) / m["atr"],
        (m["dc_lo"] - m["close"]) / m["atr"])
    m["atr_pct"] = m["atr"] / m["close"]
    m["dc_width_atr"] = (m["dc_hi"] - m["dc_lo"]) / m["atr"]
    m["hour"] = m["entry_time"].dt.hour
    m["dow"] = m["entry_time"].dt.dayofweek
    m["won"] = (m["net_r"] > 0).astype(int)
    m = m.dropna(subset=FEATS).reset_index(drop=True)

    split = pd.Timestamp(a.split_date, tz="UTC")
    tr = m[m["entry_time"] < split]
    oo = m[m["entry_time"] >= split].copy()
    print(f"train trades={len(tr)} (win {tr['won'].mean():.3f}) | "
          f"OOS trades={len(oo)} (win {oo['won'].mean():.3f})")
    print(f"OOS UNFILTERED: net_r={oo['net_r'].sum():.1f} "
          f"exp={oo['net_r'].mean():.4f} n={len(oo)}")

    try:
        from sklearn.ensemble import GradientBoostingClassifier
    except Exception as exc:  # noqa: BLE001
        print(f"sklearn unavailable: {exc}")
        return 1
    clf = GradientBoostingClassifier(random_state=0, n_estimators=120, max_depth=3)
    clf.fit(tr[FEATS], tr["won"])
    oo["p"] = clf.predict_proba(oo[FEATS])[:, 1]
    imp = sorted(zip(FEATS, clf.feature_importances_), key=lambda x: -x[1])
    print("feat_importances:", [(k, round(v, 3)) for k, v in imp])
    for th in [float(x) for x in a.thresholds.split(",")]:
        sel = oo[oo["p"] >= th]
        if len(sel) == 0:
            print(f"  thresh={th}: 0 trades kept")
            continue
        print(f"  thresh={th}: kept {len(sel)}/{len(oo)} "
              f"net_r={sel['net_r'].sum():.1f} exp={sel['net_r'].mean():.4f} "
              f"win={sel['won'].mean():.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
