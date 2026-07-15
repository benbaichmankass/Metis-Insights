#!/usr/bin/env python3
"""Chop-scalp capital-efficiency study (research orchestrator, Tier-1).

Answers the operator's question (2026-07-15): can a strategy that SCALPS THROUGH
CHOP — reading range boundaries on a higher timeframe and catching bounces on a
faster one — be net-positive, AND is it CAPITAL-EFFICIENT? "Efficient" = it
earns per unit of trade-time (net_R per position-day) and, despite idling
between chops, beats (a) holding a longer position (buy-and-hold) and (b) sitting
on cash — measured as return per CALENDAR day, which pays for the idle time.

It orchestrates, on ONE feed, a fair head-to-head:
  * a grid of the multi-TF ``chop_scalp`` harness (scripts/backtest_chop_scalp.py);
  * the existing single-TF range strategy ``fvg_range`` (scripts/backtest_fvg_range.py)
    at its live params, the incumbent range member;
  * buy-and-hold and cash reference lines over the same window;
  * a chop-tape characterization (how much of the tape is actually chop).

Every strategy is scored from its own ``--emit-trades`` JSONL (each row carries
net_r + hold_bars + mfe_r), so all capital-efficiency metrics are computed by
ONE function, identically, with no per-harness special-casing. Net-of-fee.
Walk-forward: pass ``--oos-split`` to also report an out-of-sample slice.

  python scripts/research/chop_scalp_study.py \
      --data /home/ubuntu/ict-trader-data/btc_5m.parquet --symbol BTCUSDT \
      --ltf 5m --oos-split 2025-01-01 --out /tmp/chop_btc.json --md /tmp/chop_btc.md

Research only (Tier-1). Never touches config or live paths.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts import backtest_chop_scalp as cs  # noqa: E402
from scripts import backtest_fvg_range as fr  # noqa: E402

RISK_FRAC = 0.01  # 1% equity risked per trade, to express net-R in %-return terms


# --------------------------------------------------------------------------
# Chop-scalp config grid (the multi-TF harness). Kept small so a multi-year 5m
# sweep finishes inside a relay window.
# --------------------------------------------------------------------------

def _chop_grid(htf_rules: List[str]) -> List[Dict[str, Any]]:
    grid: List[Dict[str, Any]] = []
    for htf in htf_rules:
        for exit_style in ("far", "mid"):
            grid.append({"label": f"chop_scalp/{htf}/{exit_style}/wick",
                         "htf_rule": htf, "exit_style": exit_style, "require_fvg": False})
        # An FVG-confirmed variant on the far target (stricter, fewer trades).
        grid.append({"label": f"chop_scalp/{htf}/far/fvg",
                     "htf_rule": htf, "exit_style": "far", "require_fvg": True})
    return grid


# --------------------------------------------------------------------------
# Uniform capital-efficiency metrics from a per-trade JSONL
# --------------------------------------------------------------------------

def _metrics(rows: List[dict], tf_seconds: int, window_days: float,
             risk_frac: float = RISK_FRAC) -> Dict[str, Any]:
    n = len(rows)
    if n == 0:
        return {"trades": 0, "win_pct": None, "net_total_r": 0.0, "net_exp_r": None,
                "max_dd_r": 0.0, "pos_days": 0.0, "net_r_per_pos_day": None,
                "mean_hold_h": None, "roundtrippers_pct": None, "exposure_pct": 0.0,
                "total_return_pct": 0.0, "ret_per_calendar_day_pct": 0.0,
                "ret_per_deployed_day_pct": None}
    net = [float(r["net_r"]) for r in rows]
    holds = [int(r.get("hold_bars", 0)) for r in rows]
    wins = sum(1 for x in net if x > 0)
    net_total = float(sum(net))
    cum = peak = mdd = 0.0
    for x in net:
        cum += x
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    pos_days = sum(holds) * tf_seconds / 86400.0
    rt = sum(1 for r in rows if float(r.get("mfe_r", 0.0)) >= 1.0 and float(r["net_r"]) <= 0.0)
    total_ret_pct = net_total * risk_frac * 100.0
    return {
        "trades": n,
        "win_pct": round(100 * wins / n, 2),
        "net_total_r": round(net_total, 3),
        "net_exp_r": round(net_total / n, 4),
        "max_dd_r": round(mdd, 3),
        "pos_days": round(pos_days, 2),
        "net_r_per_pos_day": round(net_total / pos_days, 4) if pos_days > 0 else None,
        "mean_hold_h": round(sum(holds) / n * tf_seconds / 3600.0, 3),
        "roundtrippers_pct": round(100 * rt / n, 2),
        "exposure_pct": round(100 * pos_days / window_days, 2) if window_days > 0 else None,
        "total_return_pct": round(total_ret_pct, 3),
        "ret_per_calendar_day_pct": round(total_ret_pct / window_days, 5) if window_days > 0 else None,
        "ret_per_deployed_day_pct": round(total_ret_pct / pos_days, 5) if pos_days > 0 else None,
    }


def _read_jsonl(path: str) -> List[dict]:
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


# --------------------------------------------------------------------------
# Baselines + tape characterization
# --------------------------------------------------------------------------

def _window_days(df: pd.DataFrame) -> float:
    if len(df) < 2:
        return 0.0
    return (df["timestamp"].iloc[-1] - df["timestamp"].iloc[0]).total_seconds() / 86400.0


def _buy_hold(df: pd.DataFrame, window_days: float) -> Dict[str, Any]:
    if len(df) < 2:
        return {"trades": None, "total_return_pct": 0.0, "ret_per_calendar_day_pct": 0.0,
                "exposure_pct": 100.0, "net_r_per_pos_day": None}
    ret = (float(df["close"].iloc[-1]) / float(df["close"].iloc[0]) - 1.0) * 100.0
    return {"trades": None, "total_return_pct": round(ret, 3),
            "ret_per_calendar_day_pct": round(ret / window_days, 5) if window_days > 0 else None,
            "exposure_pct": 100.0, "net_r_per_pos_day": None,
            "note": "held 1 unit the whole window; return is price drift, not risk-normalised"}


def _cash_line() -> Dict[str, Any]:
    return {"trades": 0, "total_return_pct": 0.0, "ret_per_calendar_day_pct": 0.0,
            "exposure_pct": 0.0, "net_r_per_pos_day": 0.0,
            "note": "sit on cash through the chop — the do-nothing baseline"}


def _chop_tape(df: pd.DataFrame, htf_rules: List[str], adx_period: int,
               adx_max: float, range_lookback: int) -> Dict[str, Any]:
    """How much of the tape is chop (HTF ADX < adx_max) and how wide the range
    typically is — the opportunity size for a chop strategy."""
    out: Dict[str, Any] = {}
    for htf in htf_rules:
        h = cs._resample(df, htf)
        if len(h) < adx_period + 2:
            out[htf] = {"htf_bars": len(h), "chop_frac_pct": None}
            continue
        adx = cs._adx(h, adx_period)
        rng_hi = h["high"].rolling(range_lookback).max()
        rng_lo = h["low"].rolling(range_lookback).min()
        width_pct = ((rng_hi - rng_lo) / h["close"]).replace([np.inf, -np.inf], np.nan)
        valid = adx.dropna()
        out[htf] = {
            "htf_bars": int(len(h)),
            "chop_frac_pct": round(100 * float((valid < adx_max).mean()), 2) if len(valid) else None,
            "median_adx": round(float(valid.median()), 2) if len(valid) else None,
            "median_range_width_pct": round(float(width_pct.dropna().median()) * 100, 3)
            if width_pct.notna().any() else None,
        }
    return out


# --------------------------------------------------------------------------
# Run one strategy config → uniform metrics (via its emit-trades JSONL)
# --------------------------------------------------------------------------

def _run_chop_cfg(df: pd.DataFrame, symbol: str, ltf: str, cfg: Dict[str, Any],
                  window_days: float, tmpdir: str) -> Dict[str, Any]:
    emit = str(Path(tmpdir) / (cfg["label"].replace("/", "_") + ".jsonl"))
    summary = cs.run_backtest(
        df, htf_rule=cfg["htf_rule"], timeframe=ltf, symbol=symbol,
        range_lookback=48, atr_period=14, adx_period=14, adx_max=20.0,
        min_width_pct=0.015, max_width_pct=0.12, touch_tol_pct=0.002,
        min_touches=3, third_frac=0.34, wick_tol_frac=0.05,
        require_fvg=cfg["require_fvg"], fvg_search=24, min_fvg_size_bps=2.0,
        atr_stop_buffer=0.25, exit_style=cfg["exit_style"], tp_r=1.5,
        timeout_bars=48, cooldown_bars=1, emit_path=emit)
    rows = _read_jsonl(emit)
    m = _metrics(rows, cs._tf_seconds(ltf), window_days)
    m["label"] = cfg["label"]
    m["by_outcome"] = summary.get("by_outcome")
    return m


def _run_fvg_baseline(df: pd.DataFrame, symbol: str, window_days: float,
                      tmpdir: str) -> Dict[str, Any]:
    """The incumbent single-TF range strategy at its live-validated params
    (15m / range_lookback 48 / touches 4 / ADX<20 / far-boundary / 0.25 ATR)."""
    htf = cs._resample(df, "15m")
    emit = str(Path(tmpdir) / "fvg_range_15m.jsonl")
    summary = fr.run_backtest(
        htf, range_lookback=48, atr_period=14, adx_period=14, adx_max=20.0,
        min_width_pct=0.015, max_width_pct=0.12, touch_tol_pct=0.002,
        min_touches=4, third_frac=0.34, fvg_search=24, min_fvg_size_bps=2.0,
        atr_stop_buffer=0.25, exit_style="far", tp_r=1.5, timeout_bars=48,
        cooldown_bars=1, timeframe="15m", symbol=symbol, emit_path=emit)
    rows = _read_jsonl(emit)
    m = _metrics(rows, cs._tf_seconds("15m"), window_days)
    m["label"] = "fvg_range/15m/far (incumbent, live params)"
    m["by_outcome"] = summary.get("by_outcome")
    return m


# --------------------------------------------------------------------------
# Study over a window
# --------------------------------------------------------------------------

def _study_window(df: pd.DataFrame, symbol: str, ltf: str, htf_rules: List[str],
                  tag: str) -> Dict[str, Any]:
    wd = _window_days(df)
    with tempfile.TemporaryDirectory() as td:
        strat_rows = [_run_fvg_baseline(df, symbol, wd, td)]
        for cfg in _chop_grid(htf_rules):
            strat_rows.append(_run_chop_cfg(df, symbol, ltf, cfg, wd, td))
    return {
        "tag": tag,
        "data_start": str(df["timestamp"].iloc[0]) if len(df) else None,
        "data_end": str(df["timestamp"].iloc[-1]) if len(df) else None,
        "window_days": round(wd, 1),
        "chop_tape": _chop_tape(df, htf_rules, 14, 20.0, 48),
        "buy_hold": _buy_hold(df, wd),
        "cash": _cash_line(),
        "strategies": sorted(strat_rows, key=lambda r: (r["net_r_per_pos_day"] is None,
                                                        -(r["net_r_per_pos_day"] or -1e9))),
    }


def _md_table(win: Dict[str, Any]) -> str:
    lines = [f"### {win['tag']}  ({win['data_start']} → {win['data_end']}, {win['window_days']}d)",
             "", "Chop tape: " + ", ".join(
                 f"{k}: {v.get('chop_frac_pct')}% chop (med ADX {v.get('median_adx')}, "
                 f"med width {v.get('median_range_width_pct')}%)"
                 for k, v in win["chop_tape"].items()), "",
             "| strategy | trades | win% | net_R | net_exp | maxDD_R | **net_R/pos-day** | mean_hold_h | RT% | exposure% | ret/cal-day% |",
             "|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|"]
    for s in win["strategies"]:
        lines.append(
            f"| {s['label']} | {s['trades']} | {s['win_pct']} | {s['net_total_r']} | "
            f"{s['net_exp_r']} | {s['max_dd_r']} | **{s['net_r_per_pos_day']}** | "
            f"{s['mean_hold_h']} | {s['roundtrippers_pct']} | {s['exposure_pct']} | "
            f"{s['ret_per_calendar_day_pct']} |")
    bh = win["buy_hold"]
    lines.append(f"| buy_hold | — | — | — | — | — | — | — | — | {bh['exposure_pct']} | {bh['ret_per_calendar_day_pct']} |")
    lines.append("| cash | 0 | — | 0 | — | 0 | 0 | — | — | 0 | 0 |")
    return "\n".join(lines)


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(description="Chop-scalp capital-efficiency study (net-of-fee).")
    p.add_argument("--data", required=True, help="OHLCV csv/parquet base feed (>= LTF resolution).")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--ltf", default="5m", help="LTF (entry) timeframe of the base feed.")
    p.add_argument("--htf-rules", default="15m,1h", help="Comma-separated HTF boundary timeframes.")
    p.add_argument("--resample", default=None, help="Resample base to this LTF first (if finer).")
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--oos-split", default=None,
                   help="ISO date; also report IS (<split) and OOS (>=split) windows.")
    p.add_argument("--out", default=None, help="Write full JSON here.")
    p.add_argument("--md", default=None, help="Write the markdown comparison here.")
    args = p.parse_args(argv[1:])

    htf_rules = [x.strip() for x in args.htf_rules.split(",") if x.strip()]
    df = cs._load_candles(args.data)
    if args.resample:
        df = cs._resample(df, args.resample)
    df = cs._date_filter(df, args.start, args.end)
    if len(df) < 200:
        print(f"ERROR: only {len(df)} bars after filtering — too few.", file=sys.stderr)
        return 1

    windows = [_study_window(df, args.symbol, args.ltf, htf_rules, "FULL")]
    if args.oos_split:
        split = pd.Timestamp(args.oos_split, tz="UTC")
        is_df = df[df["timestamp"] < split].reset_index(drop=True)
        oos_df = df[df["timestamp"] >= split].reset_index(drop=True)
        if len(is_df) >= 200:
            windows.append(_study_window(is_df, args.symbol, args.ltf, htf_rules, "IN-SAMPLE"))
        if len(oos_df) >= 200:
            windows.append(_study_window(oos_df, args.symbol, args.ltf, htf_rules, "OUT-OF-SAMPLE"))

    result = {"symbol": args.symbol, "ltf": args.ltf, "htf_rules": htf_rules,
              "data": args.data, "risk_frac": RISK_FRAC, "windows": windows}
    md = f"## Chop-scalp capital-efficiency study — {args.symbol} (LTF {args.ltf})\n\n" + \
         "\n\n".join(_md_table(w) for w in windows)
    print(md)
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=2, default=str))
        print(f"\nJSON -> {args.out}", file=sys.stderr)
    if args.md:
        Path(args.md).write_text(md)
        print(f"MD   -> {args.md}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
