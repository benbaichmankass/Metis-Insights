#!/usr/bin/env python3
"""Volatility-squeeze breakout backtest (S-STRAT-IMPROVE-S9, complement hunt).

A different ENTRY TRIGGER than the price-channel strategies (trend / fade):
the TTM-style squeeze. When Bollinger Bands contract INSIDE the Keltner
Channels, volatility is compressed (a "squeeze"); when the BBs expand back
outside the KC, the squeeze "fires" — trade the expansion in the direction
of price vs the basis MA, with the same wide-stop + Chandelier-runner exit
that worked for trend/fade.

Thesis: volatility is mean-reverting and clustered, so compression precedes
expansion — this may catch the START of moves the Donchian breakout misses.
Whether it's a *diversifier* (vs just more momentum exposure correlated with
the trend) is the open question — emits portfolio_combine-compatible
per-trade JSONL for the correlation check.

Entry  : on the bar where the squeeze releases (BB width exits KC), LONG if
         close > basis EMA else SHORT.
Stop   : entry ∓ atr_stop_mult × ATR(atr_period) — WIDE + fee-efficient.
Exit   : Chandelier ATR trail (trail_mult). SL-first intrabar. Timeout.

Net-of-fee, long/short split, by-year, month-over-month consistency.
Research only (Tier-1). Reads OHLCV CSV/Parquet (optionally --resample).
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
    entry_index: int
    entry_time: Any
    direction: str
    entry: float
    sl: float
    risk: float
    exit_index: int
    exit_time: Any
    exit_price: float
    outcome: str
    r_multiple: float
    mfe_r: float


def _load_candles(path: str) -> pd.DataFrame:
    if path.endswith(".parquet"):
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path)
    cols = {c.lower(): c for c in df.columns}
    need = ["timestamp", "open", "high", "low", "close"]
    df = df.rename(columns={cols[c]: c for c in need if c in cols and cols[c] != c})
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    return df.dropna(subset=["timestamp"]).reset_index(drop=True)


def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    return (df.set_index("timestamp")
            .resample(rule, label="right", closed="right")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
            .dropna().reset_index())


def _date_filter(df, start, end):
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


def run_backtest(df: pd.DataFrame, *, bb_period: int, bb_std: float,
                 kc_mult: float, atr_period: int, atr_stop_mult: float,
                 trail_mult: float, timeout_bars: int, cooldown_bars: int,
                 timeframe: str, symbol: str,
                 emit_path: Optional[str] = None) -> Dict[str, Any]:
    df = df.reset_index(drop=True)
    df["atr"] = _atr(df, atr_period)
    basis = df["close"].rolling(bb_period).mean()
    sd = df["close"].rolling(bb_period).std(ddof=0)
    bb_up = basis + bb_std * sd
    bb_lo = basis - bb_std * sd
    kc_up = basis + kc_mult * df["atr"]
    kc_lo = basis - kc_mult * df["atr"]
    # squeeze ON when BBs sit inside the KC; fired on the prior bar (shift)
    # so the entry uses only closed-bar info (no lookahead).
    sqz_on = (bb_up < kc_up) & (bb_lo > kc_lo)
    df["_sqz_prev"] = sqz_on.shift(1)
    df["_sqz_now"] = sqz_on
    df["_basis"] = basis
    trades: List[Trade] = []
    n = len(df)
    i = bb_period + atr_period + 1
    next_idx = i
    while i < n - 1:
        if i < next_idx:
            i += 1
            continue
        atr = float(df["atr"].iloc[i])
        prev = df["_sqz_prev"].iloc[i]
        now = df["_sqz_now"].iloc[i]
        basis_i = df["_basis"].iloc[i]
        if atr <= 0 or pd.isna(prev) or pd.isna(basis_i):
            i += 1
            continue
        # squeeze fires: was ON last bar, OFF now (expansion)
        if not (bool(prev) and not bool(now)):
            i += 1
            continue
        c = float(df["close"].iloc[i])
        direction = "long" if c > float(basis_i) else "short"
        entry = c
        sl = entry - atr_stop_mult * atr if direction == "long" else entry + atr_stop_mult * atr
        risk = abs(entry - sl)
        if risk <= 0:
            i += 1
            continue
        ext = entry
        trail = sl
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
            entry_index=i, entry_time=df["timestamp"].iloc[i], direction=direction,
            entry=entry, sl=sl, risk=risk, exit_index=exit_idx,
            exit_time=df["timestamp"].iloc[exit_idx], exit_price=exit_price,
            outcome=exit_reason, r_multiple=round(r, 4), mfe_r=round(mfe, 3)))
        next_idx = exit_idx + 1 + cooldown_bars
        i = next_idx
    if emit_path:
        Path(emit_path).parent.mkdir(parents=True, exist_ok=True)
        with open(emit_path, "w", encoding="utf-8") as fh:
            for t in trades:
                fr = _fee_r(t)
                fh.write(json.dumps({
                    "strategy": "squeeze_breakout", "entry_time": str(t.entry_time),
                    "direction": t.direction, "exit_time": str(t.exit_time), "gross_r": t.r_multiple,
                    "net_r": round(t.r_multiple - fr, 4)}, default=str) + "\n")
    return _summarize(trades, df, timeframe=timeframe, symbol=symbol,
                      params={"bb_period": bb_period, "bb_std": bb_std,
                              "kc_mult": kc_mult, "atr_stop_mult": atr_stop_mult,
                              "trail_mult": trail_mult})


def _fee_r(t: Trade) -> float:
    if not t.exit_price or t.risk <= 0:
        return 0.0
    return (FEE_BPS_ROUNDTRIP / 10_000.0) * ((t.entry + t.exit_price) / 2.0) / t.risk


def _summarize(trades, df, *, timeframe, symbol, params):
    n = len(trades)
    base: Dict[str, Any] = {
        "strategy": "squeeze_breakout", "symbol": symbol, "timeframe": timeframe,
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
    for r in rs:
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
    from scripts.ops.consistency import monthly_consistency
    consistency = monthly_consistency(
        (t.entry_time, t.r_multiple - _fee_r(t)) for t in trades)
    base.update({
        "win_rate_pct": round(100 * len(wins) / n, 2),
        "net_total_r": round(sum(net), 4),
        "net_total_r_long": round(sum(t.r_multiple - _fee_r(t) for t in longs), 4),
        "net_total_r_short": round(sum(t.r_multiple - _fee_r(t) for t in shorts), 4),
        "net_expectancy_r": round(sum(net) / n, 4),
        "trades_long": len(longs), "trades_short": len(shorts),
        "max_drawdown_r": round(mdd, 4), "by_outcome": by, "by_year": by_year,
        "consistency": consistency})
    return base


def _fmt(s):
    lines = [f"squeeze_breakout — {s['symbol']} {s['timeframe']} {s.get('params')}",
             f"  data {s.get('data_start')} -> {s.get('data_end')}  trades={s['total_trades']}"]
    if s["total_trades"]:
        lines += [
            f"  win_rate={s['win_rate_pct']}%  net_r={s['net_total_r']} "
            f"(exp {s['net_expectancy_r']}, L/S {s['trades_long']}/{s['trades_short']}, "
            f"netL/S {s.get('net_total_r_long')}/{s.get('net_total_r_short')})",
            f"  maxdd_r={s['max_drawdown_r']} by={s['by_outcome']}",
            f"  by_year={s.get('by_year')}"]
        c = s.get("consistency") or {}
        if c:
            lines.append(f"  consistency: pos={c.get('pct_months_positive')}% "
                         f"ratio={c.get('consistency_ratio')} "
                         f"top_month_share={c.get('top_month_share')}")
    return "\n".join(lines)


def main(argv):
    global FEE_BPS_ROUNDTRIP
    p = argparse.ArgumentParser(description="Volatility-squeeze breakout backtest.")
    p.add_argument("--data", default=os.environ.get("BACKTEST_DATA_PATH", "data/backtest_candles.csv"))
    p.add_argument("--timeframe", default="2h")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--resample", default=None)
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--bb-period", type=int, default=20)
    p.add_argument("--bb-std", type=float, default=2.0)
    p.add_argument("--kc-mult", type=float, default=1.5)
    p.add_argument("--atr-period", type=int, default=14)
    p.add_argument("--atr-stop-mult", type=float, default=2.5)
    p.add_argument("--trail-mult", type=float, default=3.5)
    p.add_argument("--timeout-bars", type=int, default=48)
    p.add_argument("--cooldown-bars", type=int, default=1)
    p.add_argument("--fee-bps-roundtrip", type=float, default=FEE_BPS_ROUNDTRIP)
    p.add_argument("--json", dest="json_out", default=None)
    p.add_argument("--emit-trades", default=None)
    a = p.parse_args(argv[1:])
    FEE_BPS_ROUNDTRIP = a.fee_bps_roundtrip
    try:
        df = _load_candles(a.data)
        if a.resample:
            df = _resample(df, a.resample)
        df = _date_filter(df, a.start, a.end)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: load failed: {exc}", file=sys.stderr)
        return 1
    s = run_backtest(df, bb_period=a.bb_period, bb_std=a.bb_std, kc_mult=a.kc_mult,
                     atr_period=a.atr_period, atr_stop_mult=a.atr_stop_mult,
                     trail_mult=a.trail_mult, timeout_bars=a.timeout_bars,
                     cooldown_bars=a.cooldown_bars, timeframe=a.timeframe,
                     symbol=a.symbol, emit_path=a.emit_trades)
    print(_fmt(s))
    if a.json_out:
        payload = json.dumps(s, indent=2, default=str)
        Path(a.json_out).write_text(payload) if a.json_out != "-" else print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
