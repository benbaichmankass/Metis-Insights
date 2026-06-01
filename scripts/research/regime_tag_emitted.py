#!/usr/bin/env python3
"""Regime x direction net-R from ANY harness's emitted per-trade JSONL.

The companion to ``regime_matrix.py``. That tool drives the Donchian-trend
engine in-process; this one is **engine-agnostic** — it post-processes the
``--emit-trades`` JSONL that ``backtest_fade.py`` / ``backtest_squeeze.py`` /
``backtest_fvg_range.py`` / ``scripts/research/backtest_trend.py`` (and the
vwap harness) already write, so the whole roster goes through one tagger.

Why a post-processor: each harness simulates its own exits/fees and writes one
row per trade ``{strategy, entry_time, direction, gross_r, net_r, confidence}``
with ``net_r`` ALREADY fee-adjusted. We only need to label each trade by the
ADX regime *at its entry bar* and aggregate — the same regime primitive the
live strategies gate on (chop <20, transitional 20-25, trending >=25).

CRITICAL (the reconciliation lesson, see docs/research/session-handoff-2026-06-01.md):
the harness MUST be driven with the strategy's EXACT live params from
config/strategies.yaml before emitting — wrong params give a misleading matrix.
And ``--resample`` must be the strategy's live timeframe so the regime label is
computed on the same bars the strategy trades.

Research only (Tier-1). Reads OHLCV CSV / Parquet / JSONL + a trades JSONL.

Usage:
    python scripts/research/regime_tag_emitted.py \
        --trades /tmp/fade_trades.jsonl \
        --data data/btc_1h_multiyear.csv \
        --resample 4h --label fade_breakout_4h
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from typing import Any, Dict, List

import pandas as pd

# Reuse the committed engines (same dir) regardless of cwd: the loader/resampler
# from the trend engine, the ADX + regime primitives from regime_matrix.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest_trend import _load, _resample  # type: ignore  # noqa: E402
from regime_matrix import _adx, _regime, regime_distribution  # type: ignore  # noqa: E402


def _read_trades(path: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            # vwap emits ``net_pnl_r``; normalise to ``net_r``.
            if "net_r" not in d and "net_pnl_r" in d:
                d["net_r"] = d["net_pnl_r"]
            rows.append(d)
    return rows


def tag_emitted_by_regime(trades: List[Dict[str, Any]], adx: pd.Series,
                          df: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
    """Bucket each emitted trade's (already-net) R by the ADX regime at entry.

    Mirrors ``regime_matrix.tag_trades_by_regime`` but consumes plain dicts with
    a precomputed ``net_r`` rather than engine Trade objects, so it works for any
    harness's JSONL.
    """
    ts = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    by: Dict[str, Dict[str, Any]] = {}
    skipped = 0
    for t in trades:
        et = pd.to_datetime(t.get("entry_time"), utc=True, errors="coerce")
        if et is pd.NaT:
            skipped += 1
            continue
        idx = ts.searchsorted(et, side="right") - 1  # nearest bar at/just-before entry
        a = float(adx.iloc[idx]) if 0 <= idx < len(adx) else float("nan")
        reg = _regime(a)
        direction = str(t.get("direction", "?")).lower()
        net = float(t.get("net_r", 0.0))
        slot = by.setdefault(reg, {"trades": 0, "wins": 0, "net_r": 0.0,
                                   "long_r": 0.0, "short_r": 0.0,
                                   "long_n": 0, "short_n": 0})
        slot["trades"] += 1
        slot["wins"] += 1 if net > 0 else 0
        slot["net_r"] = round(slot["net_r"] + net, 4)
        if direction == "short":
            slot["short_r"] = round(slot["short_r"] + net, 4)
            slot["short_n"] += 1
        else:
            slot["long_r"] = round(slot["long_r"] + net, 4)
            slot["long_n"] += 1
    for reg, s in by.items():
        s["win_pct"] = round(100 * s["wins"] / s["trades"], 1) if s["trades"] else 0.0
        s["exp_r"] = round(s["net_r"] / s["trades"], 4) if s["trades"] else 0.0
    if skipped:
        by["_skipped_no_entry_time"] = {"trades": skipped}
    return by


def _totals(by: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    longs = sum(s.get("long_r", 0.0) for r, s in by.items() if not r.startswith("_"))
    shorts = sum(s.get("short_r", 0.0) for r, s in by.items() if not r.startswith("_"))
    net = sum(s.get("net_r", 0.0) for r, s in by.items() if not r.startswith("_"))
    n = sum(s.get("trades", 0) for r, s in by.items() if not r.startswith("_"))
    return {"trades": n, "net_r": round(net, 3),
            "long_r": round(longs, 3), "short_r": round(shorts, 3)}


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(description="Regime x direction net-R from emitted trade JSONL.")
    p.add_argument("--trades", required=True, help="per-trade JSONL from a harness's --emit-trades")
    p.add_argument("--data", required=True, help="OHLCV the harness ran on (CSV/Parquet/JSONL)")
    p.add_argument("--resample", default="1h", help="strategy's LIVE timeframe (regime is computed on these bars)")
    p.add_argument("--adx-period", type=int, default=14)
    p.add_argument("--label", default="strategy")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON for roster aggregation")
    a = p.parse_args(argv)

    df = _load(a.data)
    if a.resample:
        df = _resample(df, a.resample)
    adx = _adx(df, a.adx_period)
    trades = _read_trades(a.trades)

    by = tag_emitted_by_regime(trades, adx, df)
    dist = regime_distribution(adx)
    totals = _totals(by)

    if a.json:
        print(json.dumps({"label": a.label, "resample": a.resample,
                          "bars": int(len(df)), "regime_base_rate_pct": dist["pct"],
                          "by_regime": by, "totals": totals}, default=str))
        return 0

    print(f"strategy={a.label} tf={a.resample} trades={totals['trades']} "
          f"net_r={totals['net_r']} (long {totals['long_r']} / short {totals['short_r']})")
    print(f"data {df['timestamp'].iloc[0]} -> {df['timestamp'].iloc[-1]} bars={len(df)}")
    print(f"regime base-rate (bars): chop={dist['pct']['chop']}% "
          f"transitional={dist['pct']['transitional']}% trending={dist['pct']['trending']}%")
    print("--- by regime (entry regime) x direction ---")
    print(f"  {'regime':13} {'trades':>6} {'win%':>6} {'net_r':>9} "
          f"{'long_r':>9}({'n':>4}) {'short_r':>9}({'n':>4})")
    for reg in ("trending", "transitional", "chop", "unknown"):
        s = by.get(reg)
        if not s:
            continue
        print(f"  {reg:13} {s['trades']:6} {s['win_pct']:6} {s['net_r']:9} "
              f"{s['long_r']:9}({s['long_n']:4}) {s['short_r']:9}({s['short_n']:4})")
    if "_skipped_no_entry_time" in by:
        print(f"  (skipped {by['_skipped_no_entry_time']['trades']} trades with unparseable entry_time)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
