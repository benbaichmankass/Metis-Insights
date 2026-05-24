#!/usr/bin/env python3
"""Failed-breakout fade backtest (S-STRAT-IMPROVE-S9, complementary-strategy R&D).

The literal INVERSE of the Donchian trend-follower
(``scripts/backtest_trend.py``): where the trend-follower BUYS a
*confirmed* Donchian breakout, this FADES a *failed* one — a bar that
pierces beyond the channel (a liquidity grab) but closes back inside
(rejection). It is turtle_soup's sweep-and-revert idea, defined off the
exact same Donchian(N) channel as the live trend strategy so the two
read as mirror images on the same structure.

THE HYPOTHESIS under test (S9, operator-approved 2026-05-24): turtle_soup
is net-NEGATIVE because it takes the fade with a *tight* target — and on
BTC, tight-target strategies (vwap, ict_scalp) die on fee drag. The one
lever that made the trend-follower the program's first net-positive edge
was **asymmetric payoff: wide fee-efficient stops + letting winners
run.** So this harness runs the *same* fade entries under four exit
styles and compares them head-to-head:

  --exit-style tp1r  : fixed 1R target              (tight; turtle_soup-like control)
  --exit-style mid   : target the channel midpoint  (partial reversion)
  --exit-style far   : target the FAR channel band  (full-range reversion runner)
  --exit-style trail : Chandelier ATR trail, no TP  (max runner; the trend exit)

If the runner styles (far/trail) turn the fade net-positive where tp1r
stays negative, the lever is confirmed and we have a candidate that is
structurally *opposite* to the live trend-follower (fades the breakouts
the trend chases) — i.e. a genuine diversifier, not a correlated
variant. The emitted per-trade JSONL feeds
``scripts/ops/portfolio_combine.py`` for the correlation-vs-trend check.

Entry  : Donchian(N) channel from the prior N bars (no lookahead).
         Failed upside breakout -> SHORT: high pierces above dc_hi
         (>= pierce_min x ATR beyond) but close falls back below dc_hi.
         Failed downside breakout -> LONG: mirror on dc_lo.
Stop   : beyond the rejection wick + buffer x ATR — WIDE + fee-efficient
         (the fade is wrong if price makes a new extreme past the grab).
Exit   : per --exit-style above. SL-first intrabar (conservative).
         Timeout backstop. Optional --adx-max gate (fade only in chop).

Net-of-fee, long/short split, by-outcome, per-calendar-year, and
month-over-month consistency — same readout shape as every other
backtest in the program. Research only (Tier-1), not wired into live.
Reads an OHLCV CSV or Parquet (optionally --resample to a higher TF;
optionally --start/--end for walk-forward windows).
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
_EXIT_STYLES = ("tp1r", "mid", "far", "trail")


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


def _adx(df: pd.DataFrame, period: int) -> pd.Series:
    """Wilder's ADX — regime filter. Low ADX = chop (where a mean-reverting
    fade has its edge); high ADX = trending (where the breakout it fades
    tends to follow through and the fade bleeds)."""
    h, low, c = df["high"], df["low"], df["close"]
    up = h.diff()
    down = -low.diff()
    plus_dm = ((up > down) & (up > 0)) * up.clip(lower=0)
    minus_dm = ((down > up) & (down > 0)) * down.clip(lower=0)
    pc = c.shift(1)
    tr = pd.concat([(h - low), (h - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    alpha = 1.0 / period
    atr = tr.ewm(alpha=alpha, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=alpha, adjust=False).mean() / atr.replace(0, pd.NA)
    minus_di = 100 * minus_dm.ewm(alpha=alpha, adjust=False).mean() / atr.replace(0, pd.NA)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, pd.NA)
    return dx.ewm(alpha=alpha, adjust=False).mean()


def run_backtest(df: pd.DataFrame, *, donchian: int, atr_period: int,
                 atr_stop_buffer: float, pierce_min: float, exit_style: str,
                 tp_r: float, trail_mult: float, timeout_bars: int,
                 cooldown_bars: int, timeframe: str, symbol: str,
                 adx_max: Optional[float] = None, adx_period: int = 14,
                 emit_path: Optional[str] = None) -> Dict[str, Any]:
    if exit_style not in _EXIT_STYLES:
        raise ValueError(f"exit_style must be one of {_EXIT_STYLES}")
    df = df.reset_index(drop=True)
    df["atr"] = _atr(df, atr_period)
    # Channel from the PRIOR N bars only (shift(1)) — no lookahead. Same
    # construction as the trend-follower so the two are exact mirrors.
    df["dc_hi"] = df["high"].rolling(donchian).max().shift(1)
    df["dc_lo"] = df["low"].rolling(donchian).min().shift(1)
    df["adx"] = _adx(df, adx_period).shift(1) if adx_max is not None else None
    trades: List[Trade] = []
    n = len(df)
    i = donchian + atr_period + 1
    next_idx = i
    while i < n - 1:
        if i < next_idx:
            i += 1
            continue
        atr = float(df["atr"].iloc[i])
        hi = df["dc_hi"].iloc[i]
        lo = df["dc_lo"].iloc[i]
        if atr <= 0 or pd.isna(hi) or pd.isna(lo):
            i += 1
            continue
        if adx_max is not None:
            adx_i = df["adx"].iloc[i]
            if pd.isna(adx_i) or float(adx_i) >= adx_max:
                i += 1
                continue
        hi, lo = float(hi), float(lo)
        bar_hi = float(df["high"].iloc[i])
        bar_lo = float(df["low"].iloc[i])
        c = float(df["close"].iloc[i])
        # Failed breakout detection: pierce beyond the band by >= pierce_min
        # x ATR, then close back inside. Upside-failed -> short; else
        # downside-failed -> long.
        direction: Optional[str] = None
        if bar_hi >= hi + pierce_min * atr and c < hi:
            direction = "short"
        elif bar_lo <= lo - pierce_min * atr and c > lo:
            direction = "long"
        if direction is None:
            i += 1
            continue
        entry = c
        if direction == "short":
            sl = bar_hi + atr_stop_buffer * atr
        else:
            sl = bar_lo - atr_stop_buffer * atr
        risk = abs(entry - sl)
        if risk <= 0:
            i += 1
            continue
        # Fixed price target for the non-trailing styles (None => trailing).
        target: Optional[float] = None
        if exit_style == "tp1r":
            target = entry - tp_r * risk if direction == "short" else entry + tp_r * risk
        elif exit_style == "mid":
            target = (hi + lo) / 2.0
        elif exit_style == "far":
            target = lo if direction == "short" else hi
        # A fixed target must sit on the profit side of entry, else the
        # setup is degenerate (entry already past it) — skip.
        if target is not None:
            if direction == "short" and target >= entry:
                i += 1
                continue
            if direction == "long" and target <= entry:
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
                if exit_style == "trail":
                    if bh >= trail:
                        exit_price, exit_idx = trail, j
                        exit_reason = "trail_stop" if trail < sl else "stop"
                        break
                    ext = min(ext, bl)
                    trail = min(trail, ext + trail_mult * atr)
                else:
                    if bh >= sl:                       # SL-first (conservative)
                        exit_price, exit_idx = sl, j
                        exit_reason = "stop"
                        break
                    if target is not None and bl <= target:
                        exit_price, exit_idx = target, j
                        exit_reason = "target"
                        break
                    ext = min(ext, bl)
                mfe = max(mfe, (entry - ext) / risk)
            else:
                if exit_style == "trail":
                    if bl <= trail:
                        exit_price, exit_idx = trail, j
                        exit_reason = "trail_stop" if trail > sl else "stop"
                        break
                    ext = max(ext, bh)
                    trail = max(trail, ext - trail_mult * atr)
                else:
                    if bl <= sl:
                        exit_price, exit_idx = sl, j
                        exit_reason = "stop"
                        break
                    if target is not None and bh >= target:
                        exit_price, exit_idx = target, j
                        exit_reason = "target"
                        break
                    ext = max(ext, bh)
                mfe = max(mfe, (ext - entry) / risk)
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
                    "strategy": "fade_breakout", "entry_time": str(t.entry_time),
                    "direction": t.direction, "gross_r": t.r_multiple,
                    "net_r": round(t.r_multiple - fr, 4)}, default=str) + "\n")
    return _summarize(trades, df, timeframe=timeframe, symbol=symbol,
                      params={"donchian": donchian, "atr_stop_buffer": atr_stop_buffer,
                              "pierce_min": pierce_min, "exit_style": exit_style,
                              "tp_r": tp_r, "trail_mult": trail_mult,
                              "adx_max": adx_max})


def _fee_r(t: Trade) -> float:
    if not t.exit_price or t.risk <= 0:
        return 0.0
    return (FEE_BPS_ROUNDTRIP / 10_000.0) * ((t.entry + t.exit_price) / 2.0) / t.risk


def _summarize(trades: List[Trade], df: pd.DataFrame, *, timeframe: str,
               symbol: str, params: Dict[str, Any]) -> Dict[str, Any]:
    n = len(trades)
    base: Dict[str, Any] = {
        "strategy": "fade_breakout", "symbol": symbol, "timeframe": timeframe,
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
        "total_r_long": round(sum(t.r_multiple for t in longs), 4),
        "total_r_short": round(sum(t.r_multiple for t in shorts), 4),
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
    lines = [f"fade_breakout — {s['symbol']} {s['timeframe']} {s.get('params')}",
             f"  data {s.get('data_start')} -> {s.get('data_end')}  trades={s['total_trades']}"]
    if s["total_trades"]:
        lines += [
            f"  win_rate={s['win_rate_pct']}%  gross_r={s['total_r']} "
            f"(L {s.get('total_r_long')}/S {s.get('total_r_short')})",
            f"  net_r={s['net_total_r']} (net_exp {s['net_expectancy_r']}, "
            f"fee_r {s['total_fee_r']}, net L/S {s.get('net_total_r_long')}/{s.get('net_total_r_short')})",
            f"  avg_win_r={s.get('avg_win_r')} max_mfe_r={s.get('max_mfe_r')} "
            f"maxdd_r={s['max_drawdown_r']} by={s['by_outcome']}",
            f"  by_year={s.get('by_year')}"]
        c = s.get("consistency") or {}
        if c:
            lines.append(
                f"  consistency: months={c.get('months')} "
                f"pos={c.get('pct_months_positive')}% "
                f"ratio={c.get('consistency_ratio')} "
                f"(mean {c.get('monthly_mean_r')}/std {c.get('monthly_std_r')}) "
                f"worst={c.get('worst_month_r')} "
                f"max_neg_streak={c.get('max_consecutive_negative_months')} "
                f"top_month_share={c.get('top_month_share')}"
            )
    return "\n".join(lines)


def main(argv: List[str]) -> int:
    global FEE_BPS_ROUNDTRIP
    p = argparse.ArgumentParser(description="Failed-breakout fade backtest (net-of-fee).")
    p.add_argument("--data", default=os.environ.get("BACKTEST_DATA_PATH", "data/backtest_candles.csv"))
    p.add_argument("--timeframe", default="2h")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--resample", default=None, help="Resample to this rule first (e.g. 2h, 4h).")
    p.add_argument("--start", default=None, help="Walk-forward window start (ISO date, inclusive).")
    p.add_argument("--end", default=None, help="Walk-forward window end (ISO date, inclusive).")
    p.add_argument("--donchian", type=int, default=20)
    p.add_argument("--atr-period", type=int, default=14)
    p.add_argument("--atr-stop-buffer", type=float, default=0.5,
                   help="Stop placed buffer x ATR beyond the rejection wick.")
    p.add_argument("--pierce-min", type=float, default=0.0,
                   help="Min pierce beyond the band, in ATR, to qualify as a breakout attempt.")
    p.add_argument("--exit-style", choices=_EXIT_STYLES, default="far",
                   help="tp1r=fixed 1R | mid=channel midpoint | far=far band | trail=Chandelier.")
    p.add_argument("--tp-r", type=float, default=1.0, help="R target for --exit-style tp1r.")
    p.add_argument("--trail-mult", type=float, default=3.0, help="ATR mult for --exit-style trail.")
    p.add_argument("--adx-max", type=float, default=None,
                   help="Regime gate: only fade when ADX < this (chop). Off when unset.")
    p.add_argument("--adx-period", type=int, default=14)
    p.add_argument("--timeout-bars", type=int, default=48)
    p.add_argument("--cooldown-bars", type=int, default=1)
    p.add_argument("--fee-bps-roundtrip", type=float, default=FEE_BPS_ROUNDTRIP)
    p.add_argument("--json", dest="json_out", default=None)
    p.add_argument("--emit-trades", default=None, metavar="PATH",
                   help="Write per-trade {entry_time, net_r} JSONL for portfolio_combine.")
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
    s = run_backtest(df, donchian=args.donchian, atr_period=args.atr_period,
                     atr_stop_buffer=args.atr_stop_buffer, pierce_min=args.pierce_min,
                     exit_style=args.exit_style, tp_r=args.tp_r,
                     trail_mult=args.trail_mult, timeout_bars=args.timeout_bars,
                     cooldown_bars=args.cooldown_bars, timeframe=args.timeframe,
                     symbol=args.symbol, adx_max=args.adx_max,
                     adx_period=args.adx_period, emit_path=args.emit_trades)
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
