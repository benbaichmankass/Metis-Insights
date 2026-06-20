#!/usr/bin/env python3
"""Funding-rate carry backtest (perp funding harvest) — net-of-fee.

A NEW edge type for the book (the first non-price-action strategy): instead of
betting on price direction, harvest the periodic **funding** cash flow a perp
pays. On a linear perp a positive funding rate means longs PAY shorts (and vice
versa), so holding the *receiving* side collects funding every settlement.

Two variants, one harness:
  * **directional** (``--hedge none``, default): take the receiving side as a
    directional position with an ATR stop. P&L = price move + funding collected.
    Collects funding AND is long/short the move — higher variance, the simplest
    gate-compatible cell.
  * **market-neutral** (``--hedge neutral``): the price leg is assumed hedged
    out (spot/inverse), so P&L ≈ funding collected − hedge cost − fees. This is
    the uncorrelated, dollar-neutral sleeve — near-zero beta to the underlying.

Signal : trailing-mean funding over ``--funding-window`` settlements, **lagged
         by ``--funding-lag-settlements``** (≥1, the #1 leakage guard — a
         settlement is not known before it settles). |feat| ≥ ``--funding-threshold``
         opens the receiving side; positive funding -> SHORT, negative -> LONG.
Risk   : entry ∓ ``--atr-stop-mult`` × ATR (the R-normalizer; in the neutral
         variant it is the nominal sizing unit, not a real price stop).
Exit   : funding decays below ``--funding-exit-threshold``, OR the ATR stop
         (directional only, SL-first intrabar), OR ``--max-hold-bars`` timeout.
Accrual: exact — sum the real funding rates settled in (entry, exit].

Emits the standard ``{entry_time, net_r}`` JSONL (+ price_r / funding_r split)
so it flows through ``research_sweep.py`` -> ``m15_ws_b_fold_report.py`` ->
``classify_strategy_tier.py`` with no gate changes. Research only (Tier-1), not
wired into live. SHORT is in scope (perp funding skews positive, so the short
receive-leg is in fact the primary trade).
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

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

FEE_BPS_ROUNDTRIP = 7.5


@dataclass
class Trade:
    entry_time: Any
    direction: str          # "long" | "short" (the receiving side)
    entry: float
    exit_time: Any
    exit_price: float
    risk: float
    outcome: str            # "stop" | "funding_decay" | "timeout"
    price_r: float          # price P&L in R (0 in the neutral variant)
    funding_r: float        # funding collected in R (can be negative if it flipped)
    gross_r: float          # price_r + funding_r
    confidence: float = 0.0


def _load_candles(path: str) -> pd.DataFrame:
    df = pd.read_parquet(path) if path.endswith(".parquet") else pd.read_csv(path)
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


def _load_funding(path: str) -> pd.DataFrame:
    """Funding history CSV: timestamp + funding_rate (per-8h fraction).

    Tolerant of column casing / the camelCase ``fundingRate`` Bybit returns.
    Produced by ``scripts/ops/fetch_bybit_funding.py`` (timestamp,funding_rate).
    """
    df = pd.read_parquet(path) if path.endswith(".parquet") else pd.read_csv(path)
    low = {c.lower(): c for c in df.columns}
    ts_col = low.get("timestamp") or low.get("ts") or low.get("time")
    rate_col = (low.get("funding_rate") or low.get("fundingrate")
                or low.get("rate") or low.get("funding"))
    if ts_col is None or rate_col is None:
        raise ValueError(f"funding file needs timestamp+funding_rate; saw {list(df.columns)}")
    out = df[[ts_col, rate_col]].rename(columns={ts_col: "ts", rate_col: "rate"})
    out["ts"] = pd.to_datetime(out["ts"], utc=True, errors="coerce")
    out["rate"] = pd.to_numeric(out["rate"], errors="coerce")
    return out.dropna(subset=["ts", "rate"]).sort_values("ts").reset_index(drop=True)


def _funding_feature(fund: pd.DataFrame, window: int, lag: int) -> pd.DataFrame:
    """Lagged trailing-mean funding — the leakage-safe entry signal.

    ``roll`` = mean of the last ``window`` settlements; ``feat`` = that rolling
    mean shifted by ``lag`` settlements so a bar can only ever see funding that
    had fully settled in the past. Returns fund with a ``feat`` column.
    """
    fund = fund.copy()
    fund["roll"] = fund["rate"].rolling(window, min_periods=window).mean()
    fund["feat"] = fund["roll"].shift(max(lag, 0))
    return fund


def run_backtest(df: pd.DataFrame, fund: pd.DataFrame, *, atr_period: int,
                 atr_stop_mult: float, funding_window: int, funding_lag: int,
                 funding_threshold: float, funding_exit_threshold: float,
                 max_hold_bars: int, cooldown_bars: int, hedge: str,
                 hedge_cost_bps: float, timeframe: str, symbol: str,
                 emit_path: Optional[str] = None) -> Dict[str, Any]:
    df = df.reset_index(drop=True)
    df["atr"] = _atr(df, atr_period)
    fund = _funding_feature(fund, funding_window, funding_lag)

    # As-of (backward) join: each bar sees the most recent lagged funding feature.
    bars = pd.merge_asof(df[["timestamp"]], fund[["ts", "feat"]],
                         left_on="timestamp", right_on="ts", direction="backward")
    feat = bars["feat"].to_numpy()

    # Exact funding accrual scaffold: cumulative rate over settlement events.
    f_ts = fund["ts"].to_numpy().astype("datetime64[ns]")
    f_rate = fund["rate"].to_numpy()
    f_cum = np.concatenate([[0.0], np.cumsum(f_rate)])  # f_cum[k] = sum of first k rates

    def accrued_rate(entry_ts, exit_ts) -> float:
        """Sum of funding rates settled in (entry_ts, exit_ts]."""
        lo = int(np.searchsorted(f_ts, np.datetime64(pd.Timestamp(entry_ts).to_datetime64()), side="right"))
        hi = int(np.searchsorted(f_ts, np.datetime64(pd.Timestamp(exit_ts).to_datetime64()), side="right"))
        return float(f_cum[hi] - f_cum[lo])

    neutral = hedge == "neutral"
    trades: List[Trade] = []
    n = len(df)
    i = max(atr_period + 1, funding_window + funding_lag + 1)
    next_idx = i
    while i < n - 1:
        if i < next_idx:
            i += 1
            continue
        atr = float(df["atr"].iloc[i])
        fv = feat[i]
        if atr <= 0 or fv is None or (isinstance(fv, float) and np.isnan(fv)):
            i += 1
            continue
        fv = float(fv)
        if abs(fv) < funding_threshold:
            i += 1
            continue
        # Receive side: positive funding -> longs pay shorts -> SHORT receives.
        direction = "short" if fv > 0 else "long"
        dir_sign = 1.0 if direction == "long" else -1.0
        entry = float(df["close"].iloc[i])
        sl = entry - atr_stop_mult * atr if direction == "long" else entry + atr_stop_mult * atr
        risk = abs(entry - sl)
        if risk <= 0:
            i += 1
            continue
        exit_price: Optional[float] = None
        exit_idx = min(i + max_hold_bars, n - 1)
        outcome = "timeout"
        for j in range(i + 1, min(i + max_hold_bars + 1, n)):
            # Directional stop (SL-first, conservative). Skipped when hedged —
            # the price leg is neutralized so there is no price stop to hit.
            if not neutral:
                bh, bl = float(df["high"].iloc[j]), float(df["low"].iloc[j])
                if direction == "long" and bl <= sl:
                    exit_price, exit_idx, outcome = sl, j, "stop"
                    break
                if direction == "short" and bh >= sl:
                    exit_price, exit_idx, outcome = sl, j, "stop"
                    break
            # Funding-decay exit: the carry reason is gone.
            fj = feat[j]
            if fj is not None and not (isinstance(fj, float) and np.isnan(fj)) and abs(float(fj)) < funding_exit_threshold:
                exit_price, exit_idx, outcome = float(df["close"].iloc[j]), j, "funding_decay"
                break
        if exit_price is None:
            exit_price = float(df["close"].iloc[exit_idx])
        entry_ts = df["timestamp"].iloc[i]
        exit_ts = df["timestamp"].iloc[exit_idx]
        # Funding collected (price terms, per unit notional≈entry): receiving the
        # side that funding pays. funding_pnl = -dir_sign * Σrate * entry.
        funding_pnl_price = -dir_sign * accrued_rate(entry_ts, exit_ts) * entry
        funding_r = funding_pnl_price / risk
        price_r = 0.0 if neutral else (exit_price - entry) * dir_sign / risk
        gross_r = price_r + funding_r
        trades.append(Trade(
            entry_time=entry_ts, direction=direction, entry=entry, exit_time=exit_ts,
            exit_price=exit_price, risk=risk, outcome=outcome,
            price_r=round(price_r, 4), funding_r=round(funding_r, 4),
            gross_r=round(gross_r, 4),
            confidence=round(min(abs(fv) / max(funding_threshold, 1e-9) / 2.0, 1.0), 4)))
        next_idx = exit_idx + 1 + cooldown_bars
        i = next_idx

    if emit_path:
        Path(emit_path).parent.mkdir(parents=True, exist_ok=True)
        with open(emit_path, "w", encoding="utf-8") as fh:
            for t in trades:
                fr = _fee_r(t, hedge_cost_bps if neutral else 0.0)
                fh.write(json.dumps({
                    "strategy": "funding_carry", "entry_time": str(t.entry_time),
                    "direction": t.direction, "gross_r": t.gross_r,
                    "price_r": t.price_r, "funding_r": t.funding_r,
                    "net_r": round(t.gross_r - fr, 4),
                    "confidence": t.confidence}, default=str) + "\n")

    params = {"hedge": hedge, "atr_stop_mult": atr_stop_mult,
              "funding_window": funding_window, "funding_lag": funding_lag,
              "funding_threshold": funding_threshold,
              "funding_exit_threshold": funding_exit_threshold,
              "max_hold_bars": max_hold_bars}
    if hedge == "neutral":
        params["hedge_cost_bps"] = hedge_cost_bps
    return _summarize(trades, df, timeframe=timeframe, symbol=symbol, params=params,
                      hedge_cost_bps=hedge_cost_bps if hedge == "neutral" else 0.0)


def _fee_r(t: Trade, extra_bps: float = 0.0) -> float:
    if not t.exit_price or t.risk <= 0:
        return 0.0
    bps = FEE_BPS_ROUNDTRIP + extra_bps   # extra = hedge-leg round-trip when neutral
    return (bps / 10_000.0) * ((t.entry + t.exit_price) / 2.0) / t.risk


def _summarize(trades: List[Trade], df: pd.DataFrame, *, timeframe: str, symbol: str,
               params: Dict[str, Any], hedge_cost_bps: float = 0.0) -> Dict[str, Any]:
    n = len(trades)
    base: Dict[str, Any] = {
        "strategy": "funding_carry", "symbol": symbol, "timeframe": timeframe,
        "params": params, "total_trades": n, "fee_bps_roundtrip": FEE_BPS_ROUNDTRIP,
        "data_start": str(df["timestamp"].iloc[0]) if len(df) else None,
        "data_end": str(df["timestamp"].iloc[-1]) if len(df) else None,
        "run_date": str(date.today())}
    if n == 0:
        base.update({"win_rate_pct": 0.0, "net_total_r": 0.0, "net_expectancy_r": 0.0,
                     "net_funding_r": 0.0, "net_price_r": 0.0,
                     "trades_long": 0, "trades_short": 0, "max_drawdown_r": 0.0,
                     "by_outcome": {}, "by_year": {}})
        return base
    net = [t.gross_r - _fee_r(t, hedge_cost_bps) for t in trades]
    wins = [r for r in net if r > 0]
    cum = peak = mdd = 0.0
    for r in net:
        cum += r
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    by: Dict[str, int] = {}
    for t in trades:
        by[t.outcome] = by.get(t.outcome, 0) + 1
    by_year: Dict[str, Dict[str, Any]] = {}
    for t, r in zip(trades, net):
        yr = str(pd.Timestamp(t.entry_time).year)
        slot = by_year.setdefault(yr, {"trades": 0, "net_r": 0.0})
        slot["trades"] += 1
        slot["net_r"] = round(slot["net_r"] + r, 4)
    base.update({
        "win_rate_pct": round(100 * len(wins) / n, 2),
        "net_total_r": round(sum(net), 4),
        "net_expectancy_r": round(sum(net) / n, 4),
        "net_funding_r": round(sum(t.funding_r for t in trades), 4),
        "net_price_r": round(sum(t.price_r for t in trades), 4),
        "trades_long": sum(1 for t in trades if t.direction == "long"),
        "trades_short": sum(1 for t in trades if t.direction == "short"),
        "max_drawdown_r": round(mdd, 4), "by_outcome": by, "by_year": by_year})
    return base


def _fmt(s: Dict[str, Any]) -> str:
    lines = [f"funding_carry — {s['symbol']} {s['timeframe']} {s.get('params')}",
             f"  data {s.get('data_start')} -> {s.get('data_end')}  trades={s['total_trades']}"]
    if s["total_trades"]:
        lines += [
            f"  win_rate={s['win_rate_pct']}%  net_r={s['net_total_r']} "
            f"(exp {s['net_expectancy_r']}, L/S {s['trades_long']}/{s['trades_short']})",
            f"  net_funding_r={s['net_funding_r']} net_price_r={s['net_price_r']} "
            f"maxdd_r={s['max_drawdown_r']} by={s['by_outcome']}",
            f"  by_year={s.get('by_year')}"]
    return "\n".join(lines)


def _parse_grid(spec: str) -> List[float]:
    spec = spec.strip()
    if ":" in spec:
        lo, hi, step = (float(x) for x in spec.split(":"))
        out, v = [], lo
        while v <= hi + 1e-12:
            out.append(round(v, 8))
            v += step
        return out
    return [float(x) for x in spec.split(",") if x.strip() != ""]


def main(argv: List[str]) -> int:
    global FEE_BPS_ROUNDTRIP
    p = argparse.ArgumentParser(description="Funding-rate carry backtest (net-of-fee).")
    p.add_argument("--data", default=os.environ.get("BACKTEST_DATA_PATH", "data/backtest_candles.csv"))
    p.add_argument("--funding-data", required=True,
                   help="Funding history CSV (timestamp,funding_rate) from fetch_bybit_funding.py.")
    p.add_argument("--timeframe", default="1h")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--resample", default=None)
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--atr-period", type=int, default=14)
    p.add_argument("--atr-stop-mult", type=float, default=2.5)
    p.add_argument("--funding-window", type=int, default=3,
                   help="Settlements in the trailing-mean funding signal (3 = ~1 day).")
    p.add_argument("--funding-lag-settlements", type=int, default=1,
                   help="Leakage guard: shift the signal back N settlements (>=1).")
    p.add_argument("--funding-threshold", type=float, default=0.0001,
                   help="Enter when |trailing funding| >= this per-settlement fraction.")
    p.add_argument("--funding-exit-threshold", type=float, default=0.00003,
                   help="Exit when |trailing funding| falls below this.")
    p.add_argument("--max-hold-bars", type=int, default=72)
    p.add_argument("--cooldown-bars", type=int, default=1)
    p.add_argument("--hedge", choices=["none", "neutral"], default="none",
                   help="none=directional carry; neutral=price leg hedged out (market-neutral).")
    p.add_argument("--hedge-cost-bps", type=float, default=2.0,
                   help="Extra round-trip cost of the hedge leg (neutral variant only).")
    p.add_argument("--fee-bps-roundtrip", type=float, default=FEE_BPS_ROUNDTRIP)
    p.add_argument("--funding-threshold-sweep", default=None, metavar="GRID")
    p.add_argument("--json", dest="json_out", default=None)
    p.add_argument("--emit-trades", default=None, metavar="PATH")
    args = p.parse_args(argv[1:])
    FEE_BPS_ROUNDTRIP = args.fee_bps_roundtrip
    try:
        df = _load_candles(args.data)
        if args.resample:
            df = _resample(df, args.resample)
        df = _date_filter(df, args.start, args.end)
        fund = _load_funding(args.funding_data)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: load failed: {exc}", file=sys.stderr)
        return 1

    bt = dict(atr_period=args.atr_period, atr_stop_mult=args.atr_stop_mult,
              funding_window=args.funding_window, funding_lag=args.funding_lag_settlements,
              funding_exit_threshold=args.funding_exit_threshold,
              max_hold_bars=args.max_hold_bars, cooldown_bars=args.cooldown_bars,
              hedge=args.hedge, hedge_cost_bps=args.hedge_cost_bps,
              timeframe=args.timeframe, symbol=args.symbol)

    if args.funding_threshold_sweep:
        rows = []
        for thr in _parse_grid(args.funding_threshold_sweep):
            s = run_backtest(df, fund, funding_threshold=thr, **bt)
            rows.append({"funding_threshold": thr, "trades": s["total_trades"],
                         "win_rate_pct": s.get("win_rate_pct", 0.0),
                         "net_total_r": s.get("net_total_r", 0.0),
                         "net_expectancy_r": s.get("net_expectancy_r", 0.0),
                         "max_drawdown_r": s.get("max_drawdown_r", 0.0)})
        best = max(rows, key=lambda r: r["net_total_r"]) if rows else None
        out = {"strategy": "funding_carry", "symbol": args.symbol, "timeframe": args.timeframe,
               "grid": rows, "best_by_net_total_r": best}
        print(json.dumps(out, indent=2, default=str))
    else:
        out = run_backtest(df, fund, funding_threshold=args.funding_threshold,
                           emit_path=args.emit_trades, **bt)
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
