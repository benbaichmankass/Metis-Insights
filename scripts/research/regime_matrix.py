#!/usr/bin/env python3
"""Regime x strategy performance matrix (regime-aware-routing groundwork).

Operator direction 2026-06-01: complementarity is not just across symbols but
across *regimes* — we want to know which strategy earns in which regime so a
router can lean the roster toward the strategies that fit the current regime.

This is the evidence-foundation step: label every bar by its ADX regime
(chop / transitional / trending — the same ADX primitive the live strategies
already gate on), run a strategy's backtest, tag each trade by the regime *at
its entry*, and aggregate net-R / trades / win% per regime. The output is one
row of the matrix; run it per strategy to build the full grid.

v1 drives the Donchian-trend family via the committed backtest_trend.py engine
(the re-tuned live config is 1h / donchian 20 / trail 5.0). The
``tag_trades_by_regime`` helper is engine-agnostic so the fade / squeeze / fvg
harnesses can be wired in next (each just supplies its Trade list + the OHLCV
frame). Research only (Tier-1); reads OHLCV CSV / Parquet / JSONL.
"""
from __future__ import annotations
import argparse
import os
import sys
from typing import Any, Dict, List

import pandas as pd

# Reuse the committed trend engine (same dir) regardless of cwd.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest_trend import _load, _resample, backtest  # type: ignore  # noqa: E402

FEE_BPS_ROUNDTRIP = 7.5

# ADX regime cut-points. <20 = chop/range (mean-reversion territory),
# 20-25 = transitional, >=25 = trending (trend/breakout territory). These
# mirror the ADX gates the live strategies already use (fade/fvg require
# ADX<20; trend/breakout require the trending side).
_CHOP_MAX = 20.0
_TREND_MIN = 25.0


def _adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder's ADX(period). Returns a Series aligned to df.index."""
    h, lo, c = df["high"], df["low"], df["close"]
    up = h.diff()
    down = -lo.diff()
    plus_dm = ((up > down) & (up > 0)) * up
    minus_dm = ((down > up) & (down > 0)) * down
    pc = c.shift(1)
    tr = pd.concat([(h - lo), (h - pc).abs(), (lo - pc).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr.replace(0, pd.NA)
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr.replace(0, pd.NA)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, pd.NA)
    return dx.ewm(alpha=1 / period, adjust=False).mean()


def _regime(adx_value: float) -> str:
    if adx_value != adx_value:  # NaN
        return "unknown"
    if adx_value < _CHOP_MAX:
        return "chop"
    if adx_value < _TREND_MIN:
        return "transitional"
    return "trending"


def _fee_r(t) -> float:
    return (t.entry * (FEE_BPS_ROUNDTRIP / 10000.0)) / t.risk if t.risk else 0.0


def tag_trades_by_regime(trades: List[Any], adx: pd.Series,
                         df: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
    """Bucket each trade's net-R by the ADX regime at its ENTRY bar.

    Engine-agnostic: ``trades`` is any list of objects exposing
    ``entry_time`` + the R/fee fields backtest_trend.Trade carries. ``adx`` is
    indexed the same as ``df`` (the resampled OHLCV the backtest ran on).
    """
    ts = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    by: Dict[str, Dict[str, Any]] = {}
    for t in trades:
        et = pd.to_datetime(getattr(t, "entry_time", None), utc=True, errors="coerce")
        # nearest bar at/just-before entry
        idx = ts.searchsorted(et, side="right") - 1
        a = float(adx.iloc[idx]) if 0 <= idx < len(adx) else float("nan")
        reg = _regime(a)
        direction = str(getattr(t, "direction", "?")).lower()
        net = t.r_multiple - _fee_r(t)
        slot = by.setdefault(reg, {"trades": 0, "wins": 0, "net_r": 0.0,
                                   "long_r": 0.0, "short_r": 0.0,
                                   "long_n": 0, "short_n": 0})
        slot["trades"] += 1
        slot["wins"] += 1 if net > 0 else 0
        slot["net_r"] = round(slot["net_r"] + net, 4)
        if direction == "short":
            slot["short_r"] = round(slot["short_r"] + net, 4); slot["short_n"] += 1
        else:
            slot["long_r"] = round(slot["long_r"] + net, 4); slot["long_n"] += 1
    for reg, s in by.items():
        s["win_pct"] = round(100 * s["wins"] / s["trades"], 1) if s["trades"] else 0.0
        s["exp_r"] = round(s["net_r"] / s["trades"], 4) if s["trades"] else 0.0
    return by


def regime_distribution(adx: pd.Series) -> Dict[str, Any]:
    """How much of the sample sits in each regime (the base rates)."""
    regs = adx.map(_regime)
    n = int(regs.notna().sum())
    out = {r: int((regs == r).sum()) for r in ("chop", "transitional", "trending", "unknown")}
    out["pct"] = {r: (round(100 * out[r] / n, 1) if n else 0.0)
                  for r in ("chop", "transitional", "trending")}
    return out


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(description="Regime x strategy performance (ADX-bucketed).")
    p.add_argument("--data", required=True)
    p.add_argument("--strategy", default="trend_donchian")
    p.add_argument("--resample", default="1h")
    p.add_argument("--donchian", type=int, default=20)
    p.add_argument("--atr-period", type=int, default=14)
    p.add_argument("--atr-stop-mult", type=float, default=2.5)
    p.add_argument("--trail-mult", type=float, default=5.0)
    p.add_argument("--adx-period", type=int, default=14)
    p.add_argument("--min-confidence", type=float, default=0.0,
                   help="breakout-depth/ATR gate, mirrors the live unit (0.30 live)")
    p.add_argument("--long-only", action="store_true")
    a = p.parse_args(argv)

    df = _load(a.data)
    if a.resample:
        df = _resample(df, a.resample)
    adx = _adx(df, a.adx_period)

    trades = backtest(df, a.donchian, a.atr_period, a.atr_stop_mult,
                      a.trail_mult, 0, a.long_only, a.min_confidence)
    by = tag_trades_by_regime(trades, adx, df)
    dist = regime_distribution(adx)

    print(f"strategy={a.strategy} tf={a.resample} dc={a.donchian} trail={a.trail_mult} "
          f"long_only={a.long_only}")
    print(f"data {df['timestamp'].iloc[0]} -> {df['timestamp'].iloc[-1]} bars={len(df)}")
    print(f"regime base-rate (bars): chop={dist['pct']['chop']}% "
          f"transitional={dist['pct']['transitional']}% trending={dist['pct']['trending']}%")
    print(f"total trades={len(trades)} net_r={round(sum(t.r_multiple - _fee_r(t) for t in trades),3)}")
    print("--- by regime (entry regime) x direction ---")
    print(f"  {'regime':13} {'trades':>6} {'win%':>6} {'net_r':>9} "
          f"{'long_r':>9}({'n':>4}) {'short_r':>9}({'n':>4})")
    for reg in ("trending", "transitional", "chop", "unknown"):
        s = by.get(reg)
        if not s:
            continue
        print(f"  {reg:13} {s['trades']:6} {s['win_pct']:6} {s['net_r']:9} "
              f"{s['long_r']:9}({s['long_n']:4}) {s['short_r']:9}({s['short_n']:4})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
