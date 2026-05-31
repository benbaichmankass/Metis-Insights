#!/usr/bin/env python3
"""HTF trend-pullback continuation backtest (net-of-fee).

Mirrors ``src/units/strategies/htf_pullback_trend_2h.py`` so the Rank-2
research candidate can be validated on the trainer-VM BTC archive before
anyone proposes wiring it ``execution: shadow``. Cloned from
``scripts/backtest_trend.py`` — same Chandelier-trail exit, fee model, and
walk-forward windowing — with the entry swapped for trend-filter + pullback +
confirmation.

Entry  : HTF trend filter (close vs Donchian-``trend_lookback`` midline,
         prior bars only, no lookahead) AND a pullback into the lower/upper
         ``pullback_frac`` of the recent ``pullback_lookback`` range AND a
         confirmation bar (close back in the trend direction).
Stop   : entry ∓ atr_stop_mult × ATR.
Exit   : Chandelier ATR trail (let the continuation run — NO tight target),
         SL-first intrabar; timeout backstop.

Special test this harness enables: because pullback entries are SAME-SIDE as
trend_donchian in a trend, ``--emit-trades`` lets portfolio_combine confirm the
two are flip-safe (same-side max-qty, not opposite-side churn) — the structural
property that justifies this candidate.
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

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

FEE_BPS_ROUNDTRIP = 7.5


@dataclass
class Trade:
    entry_time: Any
    direction: str
    entry: float
    sl: float
    risk: float
    exit_time: Any
    exit_price: float
    outcome: str
    r_multiple: float
    mfe_r: float
    confidence: float = 0.0


def _load_candles(path: str) -> pd.DataFrame:
    df = pd.read_parquet(path) if path.endswith(".parquet") else pd.read_csv(path)
    cols = {c.lower(): c for c in df.columns}
    need = ["timestamp", "open", "high", "low", "close"]
    df = df.rename(columns={cols[c]: c for c in need if c in cols and cols[c] != c})
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    return df.dropna(subset=["timestamp"]).reset_index(drop=True)


def _norm_rule(rule: str) -> str:
    """Normalize a CLI resample rule for newer pandas (>=2.2): bare 'm'
    minute alias must be 'min'. '15m' -> '15min', '2h' -> '2h'."""
    r = rule.strip().lower()
    if r.endswith("m") and not r.endswith("min"):
        return r[:-1] + "min"
    return r


def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    return (df.set_index("timestamp")
            .resample(_norm_rule(rule), label="right", closed="right")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
            .dropna().reset_index())


def _date_filter(df: pd.DataFrame, start: Optional[str], end: Optional[str]) -> pd.DataFrame:
    if start:
        df = df[df["timestamp"] >= pd.Timestamp(start, tz="UTC")]
    if end:
        df = df[df["timestamp"] <= pd.Timestamp(end, tz="UTC")]
    return df.reset_index(drop=True)


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    h, low, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([(h - low), (h - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=1).mean()


def run_backtest(df: pd.DataFrame, *, trend_lookback: int, pullback_lookback: int,
                 pullback_frac: float, atr_period: int, atr_stop_mult: float,
                 trail_mult: float, timeout_bars: int, cooldown_bars: int,
                 timeframe: str, symbol: str, min_confidence: float = 0.0,
                 emit_path: Optional[str] = None) -> Dict[str, Any]:
    df = df.reset_index(drop=True)
    df["atr"] = _atr(df, atr_period)
    dc_hi = df["high"].rolling(trend_lookback).max().shift(1)
    dc_lo = df["low"].rolling(trend_lookback).min().shift(1)
    df["mid"] = (dc_hi + dc_lo) / 2.0
    df["pr_hi"] = df["high"].rolling(pullback_lookback).max().shift(1)
    df["pr_lo"] = df["low"].rolling(pullback_lookback).min().shift(1)
    trades: List[Trade] = []
    n = len(df)
    i = trend_lookback + atr_period + 1
    next_idx = i
    while i < n - 1:
        if i < next_idx:
            i += 1
            continue
        atr = float(df["atr"].iloc[i])
        mid, rhi, rlo = df["mid"].iloc[i], df["pr_hi"].iloc[i], df["pr_lo"].iloc[i]
        if atr <= 0 or pd.isna(mid) or pd.isna(rhi) or pd.isna(rlo):
            i += 1
            continue
        mid, rhi, rlo = float(mid), float(rhi), float(rlo)
        rng = rhi - rlo
        if rng <= 0:
            i += 1
            continue
        c = float(df["close"].iloc[i])
        prev_c = float(df["close"].iloc[i - 1])
        pos = (c - rlo) / rng
        direction: Optional[str] = None
        depth = 0.0
        if c > mid and pos <= pullback_frac and c > prev_c:
            direction, depth = "long", (c - mid) / atr
        elif c < mid and pos >= (1 - pullback_frac) and c < prev_c:
            direction, depth = "short", (mid - c) / atr
        if direction is None:
            i += 1
            continue
        confidence = round(min(max(depth, 0.0), 1.0), 4)
        if confidence < min_confidence:
            i += 1
            continue
        entry = c
        sl = entry - atr_stop_mult * atr if direction == "long" else entry + atr_stop_mult * atr
        risk = abs(entry - sl)
        if risk <= 0:
            i += 1
            continue
        ext, trail = entry, sl
        exit_price: Optional[float] = None
        exit_reason = "timeout"
        exit_idx = min(i + timeout_bars, n - 1)
        mfe = 0.0
        for j in range(i + 1, min(i + timeout_bars + 1, n)):
            bh, bl = float(df["high"].iloc[j]), float(df["low"].iloc[j])
            if direction == "long":
                if bl <= trail:
                    exit_price, exit_idx = trail, j
                    exit_reason = "trail_stop" if trail > sl else "stop"
                    break
                ext = max(ext, bh)
                trail = max(trail, ext - trail_mult * atr)
                mfe = max(mfe, (ext - entry) / risk)
            else:
                if bh >= trail:
                    exit_price, exit_idx = trail, j
                    exit_reason = "trail_stop" if trail < sl else "stop"
                    break
                ext = min(ext, bl)
                trail = min(trail, ext + trail_mult * atr)
                mfe = max(mfe, (entry - ext) / risk)
        if exit_price is None:
            exit_price = float(df["close"].iloc[exit_idx])
        r = ((exit_price - entry) / risk if direction == "long"
             else (entry - exit_price) / risk)
        trades.append(Trade(
            entry_time=df["timestamp"].iloc[i], direction=direction, entry=entry,
            sl=sl, risk=risk, exit_time=df["timestamp"].iloc[exit_idx],
            exit_price=exit_price, outcome=exit_reason, r_multiple=round(r, 4),
            mfe_r=round(mfe, 3), confidence=confidence))
        next_idx = exit_idx + 1 + cooldown_bars
        i = next_idx

    if emit_path:
        Path(emit_path).parent.mkdir(parents=True, exist_ok=True)
        with open(emit_path, "w", encoding="utf-8") as fh:
            for t in trades:
                fr = _fee_r(t)
                fh.write(json.dumps({
                    "strategy": "htf_pullback_trend_2h", "entry_time": str(t.entry_time),
                    "direction": t.direction, "gross_r": t.r_multiple,
                    "net_r": round(t.r_multiple - fr, 4), "confidence": t.confidence},
                    default=str) + "\n")
    return _summarize(trades, df, timeframe=timeframe, symbol=symbol, params={
        "trend_lookback": trend_lookback, "pullback_lookback": pullback_lookback,
        "pullback_frac": pullback_frac, "atr_stop_mult": atr_stop_mult,
        "trail_mult": trail_mult, "min_confidence": min_confidence})


def _fee_r(t: Trade) -> float:
    if not t.exit_price or t.risk <= 0:
        return 0.0
    return (FEE_BPS_ROUNDTRIP / 10_000.0) * ((t.entry + t.exit_price) / 2.0) / t.risk


def _summarize(trades: List[Trade], df: pd.DataFrame, *, timeframe: str,
               symbol: str, params: Dict[str, Any]) -> Dict[str, Any]:
    n = len(trades)
    base: Dict[str, Any] = {
        "strategy": "htf_pullback_trend_2h", "symbol": symbol, "timeframe": timeframe,
        "params": params, "total_trades": n, "fee_bps_roundtrip": FEE_BPS_ROUNDTRIP,
        "data_start": str(df["timestamp"].iloc[0]) if len(df) else None,
        "data_end": str(df["timestamp"].iloc[-1]) if len(df) else None,
        "run_date": str(date.today())}
    if n == 0:
        base.update({"win_rate_pct": 0.0, "net_total_r": 0.0, "net_expectancy_r": 0.0,
                     "trades_long": 0, "trades_short": 0, "max_drawdown_r": 0.0,
                     "by_outcome": {}, "by_year": {}})
        return base
    rs = [t.r_multiple for t in trades]
    net = [t.r_multiple - _fee_r(t) for t in trades]
    wins = [r for r in rs if r > 0]
    longs = [t for t in trades if t.direction == "long"]
    shorts = [t for t in trades if t.direction == "short"]
    cum = peak = mdd = 0.0
    for r in net:
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
    base.update({
        "win_rate_pct": round(100 * len(wins) / n, 2),
        "total_r": round(sum(rs), 4), "net_total_r": round(sum(net), 4),
        "net_total_r_long": round(sum(t.r_multiple - _fee_r(t) for t in longs), 4),
        "net_total_r_short": round(sum(t.r_multiple - _fee_r(t) for t in shorts), 4),
        "net_expectancy_r": round(sum(net) / n, 4),
        "trades_long": len(longs), "trades_short": len(shorts),
        "avg_win_r": round(sum(wins) / len(wins), 4) if wins else 0.0,
        "max_mfe_r": round(max(t.mfe_r for t in trades), 3),
        "max_drawdown_r": round(mdd, 4), "by_outcome": by, "by_year": by_year})
    return base


def _fmt(s: Dict[str, Any]) -> str:
    lines = [f"htf_pullback_trend_2h — {s['symbol']} {s['timeframe']} {s.get('params')}",
             f"  data {s.get('data_start')} -> {s.get('data_end')}  trades={s['total_trades']}"]
    if s["total_trades"]:
        lines += [
            f"  win_rate={s['win_rate_pct']}%  net_r={s['net_total_r']} "
            f"(exp {s['net_expectancy_r']}, L/S {s['trades_long']}/{s['trades_short']})",
            f"  avg_win_r={s.get('avg_win_r')} maxdd_r={s['max_drawdown_r']} by={s['by_outcome']}",
            f"  by_year={s.get('by_year')}"]
    return "\n".join(lines)


def main(argv: List[str]) -> int:
    global FEE_BPS_ROUNDTRIP
    p = argparse.ArgumentParser(description="HTF trend-pullback backtest (net-of-fee).")
    p.add_argument("--data", default=os.environ.get("BACKTEST_DATA_PATH", "data/backtest_candles.csv"))
    p.add_argument("--timeframe", default="2h")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--resample", default=None, help="Resample to this rule first (e.g. 2h, 4h).")
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--trend-lookback", type=int, default=50)
    p.add_argument("--pullback-lookback", type=int, default=10)
    p.add_argument("--pullback-frac", type=float, default=0.33)
    p.add_argument("--atr-period", type=int, default=14)
    p.add_argument("--atr-stop-mult", type=float, default=2.5)
    p.add_argument("--trail-mult", type=float, default=3.0)
    p.add_argument("--timeout-bars", type=int, default=200)
    p.add_argument("--cooldown-bars", type=int, default=1)
    p.add_argument("--fee-bps-roundtrip", type=float, default=FEE_BPS_ROUNDTRIP)
    p.add_argument("--min-confidence", type=float, default=0.0)
    p.add_argument("--json", dest="json_out", default=None)
    p.add_argument("--emit-trades", default=None, metavar="PATH")
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
        df, trend_lookback=args.trend_lookback, pullback_lookback=args.pullback_lookback,
        pullback_frac=args.pullback_frac, atr_period=args.atr_period,
        atr_stop_mult=args.atr_stop_mult, trail_mult=args.trail_mult,
        timeout_bars=args.timeout_bars, cooldown_bars=args.cooldown_bars,
        timeframe=args.timeframe, symbol=args.symbol, min_confidence=args.min_confidence,
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
