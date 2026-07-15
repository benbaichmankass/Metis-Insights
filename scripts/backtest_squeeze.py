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
    # Live-parity confidence (squeeze_breakout_4h.order_package):
    # |close - basis| / ATR clamped [0,1]. Enables a min_confidence sweep.
    confidence: float = 0.0


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
    # pandas 3.0 dropped the lowercase 'm' minutes alias (wants 'min'); normalise
    # so a minute timeframe like "5m"/"15m" still resamples (hours 'h' stay valid).
    r = rule.strip().lower()
    if r.endswith("m") and not r.endswith("min"):
        rule = r[:-1] + "min"
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
                 emit_path: Optional[str] = None,
                 min_confidence: float = 0.0,
                 stale_exit_bars: int = 0, stale_exit_below_r: float = 0.0,
                 giveback_min_mfe_r: float = 0.0,
                 giveback_r: float = 1.0) -> Dict[str, Any]:
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
        # Live-parity confidence: distance of price from the BB basis / ATR,
        # clamped [0,1]. Gate entry on it so the sweep mirrors a live floor.
        confidence = round(min(max(abs(c - float(basis_i)) / atr, 0.0), 1.0), 4)
        if confidence < min_confidence:
            i += 1
            continue
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
            # M20 exit levers (default 0 = off, byte-identical): checked at
            # bar close, never pre-empting the intrabar trail hit above —
            # same precedence as scripts/research/backtest_trend.py.
            cl = float(df["close"].iloc[j])
            r_close = ((cl - entry) / risk if direction == "long"
                       else (entry - cl) / risk)
            stale = (stale_exit_bars > 0 and (j - i) >= stale_exit_bars
                     and r_close < stale_exit_below_r)
            gb = (giveback_min_mfe_r > 0.0 and mfe >= giveback_min_mfe_r
                  and (mfe - r_close) >= giveback_r)
            if stale or gb:
                exit_price, exit_idx = cl, j
                exit_reason = "stale_stop" if stale else "giveback_stop"
                break
        if exit_price is None:
            exit_price = float(df["close"].iloc[exit_idx])
        r = ((exit_price - entry) / risk if direction == "long"
             else (entry - exit_price) / risk)
        trades.append(Trade(
            entry_index=i, entry_time=df["timestamp"].iloc[i], direction=direction,
            entry=entry, sl=sl, risk=risk, exit_index=exit_idx,
            exit_time=df["timestamp"].iloc[exit_idx], exit_price=exit_price,
            outcome=exit_reason, r_multiple=round(r, 4), mfe_r=round(mfe, 3),
            confidence=confidence))
        next_idx = exit_idx + 1 + cooldown_bars
        i = next_idx
    if emit_path:
        Path(emit_path).parent.mkdir(parents=True, exist_ok=True)
        with open(emit_path, "w", encoding="utf-8") as fh:
            for t in trades:
                fr = _fee_r(t)
                fh.write(json.dumps({
                    "strategy": "squeeze_breakout", "entry_time": str(t.entry_time),
                    "direction": t.direction, "gross_r": t.r_multiple,
                    "net_r": round(t.r_multiple - fr, 4),
                    "confidence": t.confidence}, default=str) + "\n")
    return _summarize(trades, df, timeframe=timeframe, symbol=symbol,
                      params={"bb_period": bb_period, "bb_std": bb_std,
                              "kc_mult": kc_mult, "atr_stop_mult": atr_stop_mult,
                              "trail_mult": trail_mult, "min_confidence": min_confidence})


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
    try:
        from scripts.ops.consistency import monthly_consistency
        consistency = monthly_consistency(
            (t.entry_time, t.r_multiple - _fee_r(t)) for t in trades)
    except ImportError:
        consistency = None
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


def _parse_grid(spec: str) -> List[float]:
    spec = spec.strip()
    if ":" in spec:
        lo, hi, step = (float(x) for x in spec.split(":"))
        out, v = [], lo
        while v <= hi + 1e-9:
            out.append(round(v, 6))
            v += step
        return out
    return [float(x) for x in spec.split(",") if x.strip() != ""]


def _confidence_sweep(df, grid, kwargs):
    rows = []
    for thr in grid:
        s = run_backtest(df, min_confidence=thr, **kwargs)
        rows.append({"min_confidence": thr, "trades": s["total_trades"],
                     "win_rate_pct": s.get("win_rate_pct", 0.0),
                     "net_total_r": s.get("net_total_r", 0.0),
                     "net_expectancy_r": s.get("net_expectancy_r", 0.0),
                     "max_drawdown_r": s.get("max_drawdown_r", 0.0)})
    best = max(rows, key=lambda r: r["net_total_r"]) if rows else None
    best_exp = max((r for r in rows if r["trades"] >= 20),
                   key=lambda r: r["net_expectancy_r"], default=None)
    return {"strategy": "squeeze_breakout", "symbol": kwargs.get("symbol"),
            "timeframe": kwargs.get("timeframe"),
            "data_start": str(df["timestamp"].iloc[0]) if len(df) else None,
            "data_end": str(df["timestamp"].iloc[-1]) if len(df) else None,
            "grid": rows, "best_by_net_total_r": best,
            "best_by_net_expectancy_r_min20": best_exp}


def _fmt_sweep(sw):
    lines = [f"squeeze_breakout confidence sweep — {sw['symbol']} {sw['timeframe']} "
             f"({sw['data_start']} -> {sw['data_end']})",
             f"  {'min_conf':>8} {'trades':>7} {'WR%':>6} {'net_R':>9} {'net_exp':>8} {'maxDD_R':>8}"]
    for r in sw["grid"]:
        lines.append(f"  {r['min_confidence']:>8.3f} {r['trades']:>7d} {r['win_rate_pct']:>6.1f} "
                     f"{r['net_total_r']:>9.2f} {r['net_expectancy_r']:>8.3f} {r['max_drawdown_r']:>8.2f}")
    b = sw.get("best_by_net_total_r")
    if b:
        lines.append(f"  -> best net_total_R @ min_confidence={b['min_confidence']} "
                     f"(net_R={b['net_total_r']}, trades={b['trades']})")
    be = sw.get("best_by_net_expectancy_r_min20")
    if be:
        lines.append(f"  -> best net_expectancy_R (>=20 trades) @ min_confidence={be['min_confidence']} "
                     f"(exp={be['net_expectancy_r']}, trades={be['trades']})")
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
    p.add_argument("--min-confidence", type=float, default=0.0,
                   help="Skip entries whose live-parity confidence (|close-basis|/ATR) is below this.")
    p.add_argument("--confidence-sweep", default=None, metavar="GRID",
                   help="Sweep min_confidence over GRID ('0:0.5:0.05' or '0,0.1,0.2') and tabulate.")
    p.add_argument("--stale-exit-bars", type=int, default=0,
                   help="M20 stale-stop: close at bar close after N bars if still below --stale-exit-below-r (0=off).")
    p.add_argument("--stale-exit-below-r", type=float, default=0.0)
    p.add_argument("--giveback-min-mfe-r", type=float, default=0.0,
                   help="M20 giveback-stop: arm once peak open profit reaches this many R (0=off).")
    p.add_argument("--giveback-r", type=float, default=1.0,
                   help="Close at bar close once the trade gives back this many R from its peak.")
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
    bt_kwargs = dict(bb_period=a.bb_period, bb_std=a.bb_std, kc_mult=a.kc_mult,
                     atr_period=a.atr_period, atr_stop_mult=a.atr_stop_mult,
                     trail_mult=a.trail_mult, timeout_bars=a.timeout_bars,
                     cooldown_bars=a.cooldown_bars, timeframe=a.timeframe,
                     symbol=a.symbol, stale_exit_bars=a.stale_exit_bars,
                     stale_exit_below_r=a.stale_exit_below_r,
                     giveback_min_mfe_r=a.giveback_min_mfe_r,
                     giveback_r=a.giveback_r)
    if a.confidence_sweep:
        out = _confidence_sweep(df, _parse_grid(a.confidence_sweep), bt_kwargs)
        print(_fmt_sweep(out))
    else:
        out = run_backtest(df, emit_path=a.emit_trades,
                           min_confidence=a.min_confidence, **bt_kwargs)
        print(_fmt(out))
    if a.json_out:
        payload = json.dumps(out, indent=2, default=str)
        Path(a.json_out).write_text(payload) if a.json_out != "-" else print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
