#!/usr/bin/env python3
"""Funding-sentiment contrarian backtest (S-STRAT-IMPROVE-S9, complement hunt).

A DIFFERENT return source than the price-channel strategies (trend /
fade): trade extreme perpetual funding as a crowded-positioning signal.
When funding is extremely POSITIVE, longs are paying shorts heavily =
crowded longs = reversal/flush risk -> fade SHORT. When extremely
NEGATIVE (rare on BTC), crowded shorts -> LONG. Exit on a Chandelier ATR
trail (the wide-stop, let-winners-run lever that worked for trend/fade)
so a short rides the long-squeeze flush.

The thesis for COMPLEMENTARITY: extreme-funding events cluster at tops /
before corrections, so this should make money in the selloffs that the
long-biased trend + fade strategies don't cover — i.e. uncorrelated (or
anti-correlated) with them, providing downside diversification. The
risk: it shorts into a secular bull, so it bleeds in relentless grind-ups
with persistently high funding and no flush.

Entry  : merge funding (8h settlements) onto the price bars (merge_asof
         backward, no lookahead). SHORT when funding >= +fund_thresh;
         LONG when funding <= -fund_thresh (if --both).
Stop   : entry ∓ atr_stop_mult × ATR(atr_period) — WIDE + fee-efficient.
Exit   : Chandelier ATR trail (trail_mult). SL-first intrabar. Timeout.

Net-of-fee, long/short split, by-year, month-over-month consistency.
Research only (Tier-1). Emits portfolio_combine-compatible per-trade
{entry_time, net_r} JSONL.
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
    funding: float


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


def _load_funding(path: str) -> pd.DataFrame:
    f = pd.read_csv(path)
    f["timestamp"] = pd.to_datetime(f["timestamp"], utc=True, errors="coerce")
    f["funding_rate"] = f["funding_rate"].astype(float)
    return f.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)


def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    out = (df.set_index("timestamp")
           .resample(rule, label="right", closed="right")
           .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
           .dropna().reset_index())
    return out


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


def _attach_funding(df: pd.DataFrame, funding: pd.DataFrame) -> pd.Series:
    """Most-recent funding as of each bar's timestamp (backward as-of, no
    lookahead — the funding settled at/before the bar close is known at
    close)."""
    merged = pd.merge_asof(
        df[["timestamp"]].sort_values("timestamp"),
        funding.rename(columns={"funding_rate": "_fr"}),
        on="timestamp", direction="backward",
    )
    return merged["_fr"].reset_index(drop=True)


def run_backtest(df: pd.DataFrame, funding: pd.DataFrame, *, fund_thresh: float,
                 both_sides: bool, atr_period: int, atr_stop_mult: float,
                 trail_mult: float, timeout_bars: int, cooldown_bars: int,
                 timeframe: str, symbol: str,
                 emit_path: Optional[str] = None) -> Dict[str, Any]:
    df = df.reset_index(drop=True)
    df["atr"] = _atr(df, atr_period)
    df["fr"] = _attach_funding(df, funding)
    trades: List[Trade] = []
    n = len(df)
    i = atr_period + 1
    next_idx = i
    while i < n - 1:
        if i < next_idx:
            i += 1
            continue
        atr = float(df["atr"].iloc[i])
        fr = df["fr"].iloc[i]
        if atr <= 0 or pd.isna(fr):
            i += 1
            continue
        fr = float(fr)
        direction: Optional[str] = None
        if fr >= fund_thresh:
            direction = "short"          # crowded longs -> fade short
        elif both_sides and fr <= -fund_thresh:
            direction = "long"           # crowded shorts -> long
        if direction is None:
            i += 1
            continue
        entry = float(df["close"].iloc[i])
        sl = entry + atr_stop_mult * atr if direction == "short" else entry - atr_stop_mult * atr
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
            if direction == "short":
                if bh >= trail:
                    exit_price, exit_idx = trail, j
                    exit_reason = "trail_stop" if trail < sl else "stop"
                    break
                ext = min(ext, bl)
                trail = min(trail, ext + trail_mult * atr)
                mfe = max(mfe, (entry - ext) / risk)
            else:
                if bl <= trail:
                    exit_price, exit_idx = trail, j
                    exit_reason = "trail_stop" if trail > sl else "stop"
                    break
                ext = max(ext, bh)
                trail = max(trail, ext - trail_mult * atr)
                mfe = max(mfe, (ext - entry) / risk)
        if exit_price is None:
            exit_price = float(df["close"].iloc[exit_idx])
        r = ((exit_price - entry) / risk if direction == "long"
             else (entry - exit_price) / risk)
        trades.append(Trade(
            entry_index=i, entry_time=df["timestamp"].iloc[i], direction=direction,
            entry=entry, sl=sl, risk=risk, exit_index=exit_idx,
            exit_time=df["timestamp"].iloc[exit_idx], exit_price=exit_price,
            outcome=exit_reason, r_multiple=round(r, 4), mfe_r=round(mfe, 3),
            funding=fr))
        next_idx = exit_idx + 1 + cooldown_bars
        i = next_idx
    if emit_path:
        Path(emit_path).parent.mkdir(parents=True, exist_ok=True)
        with open(emit_path, "w", encoding="utf-8") as fh:
            for t in trades:
                fr_fee = _fee_r(t)
                fh.write(json.dumps({
                    "strategy": "funding_fade", "entry_time": str(t.entry_time),
                    "direction": t.direction, "gross_r": t.r_multiple,
                    "net_r": round(t.r_multiple - fr_fee, 4)}, default=str) + "\n")
    return _summarize(trades, df, timeframe=timeframe, symbol=symbol,
                      params={"fund_thresh": fund_thresh, "both_sides": both_sides,
                              "atr_stop_mult": atr_stop_mult, "trail_mult": trail_mult})


def _fee_r(t: Trade) -> float:
    if not t.exit_price or t.risk <= 0:
        return 0.0
    return (FEE_BPS_ROUNDTRIP / 10_000.0) * ((t.entry + t.exit_price) / 2.0) / t.risk


def _summarize(trades: List[Trade], df: pd.DataFrame, *, timeframe: str,
               symbol: str, params: Dict[str, Any]) -> Dict[str, Any]:
    n = len(trades)
    base: Dict[str, Any] = {
        "strategy": "funding_fade", "symbol": symbol, "timeframe": timeframe,
        "params": params, "total_trades": n, "fee_bps_roundtrip": FEE_BPS_ROUNDTRIP,
        "data_start": str(df["timestamp"].iloc[0]) if len(df) else None,
        "data_end": str(df["timestamp"].iloc[-1]) if len(df) else None,
        "run_date": str(date.today())}
    if n == 0:
        base.update({"win_rate_pct": 0.0, "total_r": 0.0, "net_total_r": 0.0,
                     "net_expectancy_r": 0.0, "total_fee_r": 0.0,
                     "trades_long": 0, "trades_short": 0,
                     "net_total_r_long": 0.0, "net_total_r_short": 0.0,
                     "max_drawdown_r": 0.0, "by_outcome": {}, "by_year": {}})
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
        (t.entry_time, t.r_multiple - _fee_r(t)) for t in trades
    )
    base.update({
        "win_rate_pct": round(100 * len(wins) / n, 2),
        "total_r": round(sum(rs), 4),
        "trades_long": len(longs),
        "trades_short": len(shorts),
        "total_fee_r": round(sum(_fee_r(t) for t in trades), 4),
        "net_total_r": round(sum(net), 4),
        "net_total_r_long": round(sum(t.r_multiple - _fee_r(t) for t in longs), 4),
        "net_total_r_short": round(sum(t.r_multiple - _fee_r(t) for t in shorts), 4),
        "net_expectancy_r": round(sum(net) / n, 4),
        "avg_win_r": round(sum(wins) / len(wins), 4) if wins else 0.0,
        "max_mfe_r": round(max(t.mfe_r for t in trades), 3),
        "max_drawdown_r": round(mdd, 4), "by_outcome": by, "by_year": by_year,
        "consistency": consistency})
    return base


def _fmt(s: Dict[str, Any]) -> str:
    lines = [f"funding_fade — {s['symbol']} {s['timeframe']} {s.get('params')}",
             f"  data {s.get('data_start')} -> {s.get('data_end')}  trades={s['total_trades']}"]
    if s["total_trades"]:
        lines += [
            f"  win_rate={s['win_rate_pct']}%  gross_r={s['total_r']} "
            f"(L {s.get('net_total_r_long')}/S {s.get('net_total_r_short')} net)",
            f"  net_r={s['net_total_r']} (net_exp {s['net_expectancy_r']}, "
            f"fee_r {s['total_fee_r']}, L/S {s['trades_long']}/{s['trades_short']})",
            f"  avg_win_r={s.get('avg_win_r')} max_mfe_r={s.get('max_mfe_r')} "
            f"maxdd_r={s['max_drawdown_r']} by={s['by_outcome']}",
            f"  by_year={s.get('by_year')}"]
        c = s.get("consistency") or {}
        if c:
            lines.append(
                f"  consistency: months={c.get('months')} "
                f"pos={c.get('pct_months_positive')}% ratio={c.get('consistency_ratio')} "
                f"worst={c.get('worst_month_r')} top_month_share={c.get('top_month_share')}"
            )
    return "\n".join(lines)


def main(argv: List[str]) -> int:
    global FEE_BPS_ROUNDTRIP
    p = argparse.ArgumentParser(description="Funding-sentiment contrarian backtest.")
    p.add_argument("--data", default=os.environ.get("BACKTEST_DATA_PATH", "data/backtest_candles.csv"))
    p.add_argument("--funding", default="data/funding_BTCUSDT.csv")
    p.add_argument("--timeframe", default="8h")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--resample", default="8h", help="Resample price to this rule (align to 8h funding).")
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--fund-thresh", type=float, default=0.0005,
                   help="Abs per-8h funding fraction to trigger (0.0005 = 0.05%).")
    p.add_argument("--both", action="store_true", help="Also LONG on funding <= -thresh.")
    p.add_argument("--atr-period", type=int, default=14)
    p.add_argument("--atr-stop-mult", type=float, default=2.5)
    p.add_argument("--trail-mult", type=float, default=3.5)
    p.add_argument("--timeout-bars", type=int, default=24)
    p.add_argument("--cooldown-bars", type=int, default=1)
    p.add_argument("--fee-bps-roundtrip", type=float, default=FEE_BPS_ROUNDTRIP)
    p.add_argument("--json", dest="json_out", default=None)
    p.add_argument("--emit-trades", default=None, metavar="PATH")
    args = p.parse_args(argv[1:])
    FEE_BPS_ROUNDTRIP = args.fee_bps_roundtrip
    try:
        df = _load_candles(args.data)
        if args.resample:
            df = _resample(df, args.resample)
        df = _date_filter(df, args.start, args.end)
        funding = _load_funding(args.funding)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: load failed: {exc}", file=sys.stderr)
        return 1
    s = run_backtest(df, funding, fund_thresh=args.fund_thresh, both_sides=args.both,
                     atr_period=args.atr_period, atr_stop_mult=args.atr_stop_mult,
                     trail_mult=args.trail_mult, timeout_bars=args.timeout_bars,
                     cooldown_bars=args.cooldown_bars, timeframe=args.timeframe,
                     symbol=args.symbol, emit_path=args.emit_trades)
    print(_fmt(s))
    if args.json_out:
        payload = json.dumps(s, indent=2, default=str)
        if args.json_out == "-":
            print(payload)
        else:
            Path(args.json_out).write_text(payload)
            print(f"JSON -> {args.json_out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
