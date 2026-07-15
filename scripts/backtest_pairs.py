#!/usr/bin/env python3
"""Pairs / spread mean-reversion backtest (market-neutral stat-arb) — net-of-fee.

A NEW market-neutral edge type for the book: instead of betting on either leg's
direction, trade the **spread** between two co-moving instruments back to its
mean. Dollar-neutral (long one leg, short the other), so near-zero beta to the
underlying — the same uncorrelated-sleeve goal as funding carry, but driven by
relative value rather than a cash flow.

Spread : ``log(close_A) - hedge_beta * log(close_B)`` (default beta=1 — a log
         RATIO, the standard same-asset-class spread, e.g. ETH/BTC). A rolling
         OLS hedge ratio is available via ``--hedge-beta rolling``.
Signal : rolling z-score of the spread over ``--lookback`` bars, computed on the
         trailing window **shifted by 1 bar** (the #1 leakage guard — bar i's z
         uses only the mean/std through i-1).
Entry  : ``z >= +entry_z`` -> SHORT the spread (short A, long B);
         ``z <= -entry_z`` -> LONG the spread (long A, short B).
Exit   : ``|z| <= exit_z`` (reverted), OR an adverse ``stop_z``-sigma move from
         entry (the R-normalizer / divergence stop), OR ``--max-hold-bars``.
R      : risk = ``stop_z * sigma_at_entry`` (spread units); R = spread P&L / risk.
Fees   : TWO legs, each round-trip -> ``2 * FEE_BPS_ROUNDTRIP`` charged in return
         units (log-spread ≈ sum of log-returns, so the units line up).

Emits the standard ``{entry_time, net_r}`` JSONL so it flows through
``portfolio_robustness.py`` / the k-fold gate with no changes. Research only
(Tier-1), not wired into live. SHORT is intrinsic (half of all entries).

Self-test: ``--self-test`` builds a synthetic Ornstein-Uhlenbeck (mean-reverting)
spread and asserts the harness extracts positive net R from it — a correctness
guard before trusting any real-data result.
"""
from __future__ import annotations

import argparse
import json
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

FEE_BPS_ROUNDTRIP = 7.5   # per leg, round-trip; two legs are charged


@dataclass
class Trade:
    entry_time: Any
    direction: str          # "long_spread" | "short_spread"
    entry_spread: float
    exit_time: Any
    exit_spread: float
    risk: float
    outcome: str            # "revert" | "stop" | "timeout"
    gross_r: float
    z_at_entry: float


def _load_candles(path: str) -> pd.DataFrame:
    df = pd.read_parquet(path) if path.endswith(".parquet") else pd.read_csv(path)
    cols = {c.lower(): c for c in df.columns}
    need = ["timestamp", "open", "high", "low", "close"]
    df = df.rename(columns={cols[c]: c for c in need if c in cols and cols[c] != c})
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    return df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)


def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    return (df.set_index("timestamp")
            .resample(rule, label="right", closed="right")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
            .dropna().reset_index())


def _align(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    """Inner-join two candle frames on timestamp -> close_a / close_b."""
    m = pd.merge(a[["timestamp", "close"]].rename(columns={"close": "close_a"}),
                 b[["timestamp", "close"]].rename(columns={"close": "close_b"}),
                 on="timestamp", how="inner")
    m = m[(m["close_a"] > 0) & (m["close_b"] > 0)].reset_index(drop=True)
    return m


def _rolling_beta(la: pd.Series, lb: pd.Series, window: int) -> pd.Series:
    """Rolling OLS slope of la on lb (cov/var), shifted 1 bar (leakage-safe)."""
    cov = la.rolling(window).cov(lb)
    var = lb.rolling(window).var()
    beta = (cov / var).replace([np.inf, -np.inf], np.nan)
    return beta.shift(1).fillna(1.0)


def run_backtest(m: pd.DataFrame, *, lookback: int, entry_z: float, exit_z: float,
                 stop_z: float, max_hold_bars: int, cooldown_bars: int,
                 hedge_beta: str, timeframe: str, pair: str,
                 emit_path: Optional[str] = None) -> Dict[str, Any]:
    m = m.reset_index(drop=True)
    la, lb = np.log(m["close_a"]), np.log(m["close_b"])
    if hedge_beta == "rolling":
        beta = _rolling_beta(la, lb, lookback)
    else:
        beta = pd.Series(1.0, index=m.index)
    spread = la - beta * lb
    mean = spread.rolling(lookback).mean().shift(1)
    std = spread.rolling(lookback).std(ddof=0).shift(1)
    z = (spread - mean) / std
    z = z.replace([np.inf, -np.inf], np.nan)

    sp = spread.to_numpy()
    zz = z.to_numpy()
    sd = std.to_numpy()
    n = len(m)
    trades: List[Trade] = []
    i = lookback + 1
    next_idx = i
    while i < n - 1:
        if i < next_idx:
            i += 1
            continue
        zi, si = zz[i], sd[i]
        if zi is None or np.isnan(zi) or si is None or np.isnan(si) or si <= 0:
            i += 1
            continue
        if abs(zi) < entry_z:
            i += 1
            continue
        # z high -> spread above mean -> SHORT spread (expect fall). z low -> LONG.
        direction = "short_spread" if zi > 0 else "long_spread"
        dir_sign = 1.0 if direction == "long_spread" else -1.0
        entry_spread = float(sp[i])
        risk = float(stop_z * si)
        if risk <= 0:
            i += 1
            continue
        # adverse stop level in spread units (further divergence from the mean)
        stop_spread = entry_spread - risk if direction == "long_spread" else entry_spread + risk
        exit_spread: Optional[float] = None
        exit_idx = min(i + max_hold_bars, n - 1)
        outcome = "timeout"
        for j in range(i + 1, min(i + max_hold_bars + 1, n)):
            sj, zj = float(sp[j]), zz[j]
            # divergence stop (conservative: spread-level breach)
            if direction == "long_spread" and sj <= stop_spread:
                exit_spread, exit_idx, outcome = stop_spread, j, "stop"
                break
            if direction == "short_spread" and sj >= stop_spread:
                exit_spread, exit_idx, outcome = stop_spread, j, "stop"
                break
            # reversion exit
            if zj is not None and not np.isnan(zj) and abs(float(zj)) <= exit_z:
                exit_spread, exit_idx, outcome = sj, j, "revert"
                break
        if exit_spread is None:
            exit_spread = float(sp[exit_idx])
        gross_r = (exit_spread - entry_spread) * dir_sign / risk
        trades.append(Trade(
            entry_time=m["timestamp"].iloc[i], direction=direction,
            entry_spread=round(entry_spread, 6), exit_time=m["timestamp"].iloc[exit_idx],
            exit_spread=round(exit_spread, 6), risk=round(risk, 6), outcome=outcome,
            gross_r=round(gross_r, 4), z_at_entry=round(float(zi), 3)))
        next_idx = exit_idx + 1 + cooldown_bars
        i = next_idx

    if emit_path:
        Path(emit_path).parent.mkdir(parents=True, exist_ok=True)
        with open(emit_path, "w", encoding="utf-8") as fh:
            for t in trades:
                fh.write(json.dumps({
                    "strategy": "pairs", "pair": pair, "entry_time": str(t.entry_time),
                    "direction": t.direction, "gross_r": t.gross_r,
                    "net_r": round(t.gross_r - _fee_r(t), 4),
                    "z_at_entry": t.z_at_entry, "outcome": t.outcome}, default=str) + "\n")

    params = {"lookback": lookback, "entry_z": entry_z, "exit_z": exit_z,
              "stop_z": stop_z, "max_hold_bars": max_hold_bars, "hedge_beta": hedge_beta}
    return _summarize(trades, m, timeframe=timeframe, pair=pair, params=params)


def _fee_r(t: Trade) -> float:
    if t.risk <= 0:
        return 0.0
    # two legs, each round-trip; charged in return units (log-spread ≈ Σ returns)
    return 2.0 * (FEE_BPS_ROUNDTRIP / 10_000.0) / t.risk


def _summarize(trades: List[Trade], m: pd.DataFrame, *, timeframe: str, pair: str,
               params: Dict[str, Any]) -> Dict[str, Any]:
    n = len(trades)
    base: Dict[str, Any] = {
        "strategy": "pairs", "pair": pair, "timeframe": timeframe, "params": params,
        "total_trades": n, "fee_bps_roundtrip_per_leg": FEE_BPS_ROUNDTRIP,
        "data_start": str(m["timestamp"].iloc[0]) if len(m) else None,
        "data_end": str(m["timestamp"].iloc[-1]) if len(m) else None,
        "run_date": str(date.today())}
    if n == 0:
        base.update({"win_rate_pct": 0.0, "net_total_r": 0.0, "net_expectancy_r": 0.0,
                     "trades_long": 0, "trades_short": 0, "max_drawdown_r": 0.0,
                     "mean_hold_hours": None, "total_position_days": 0.0,
                     "net_r_per_pos_day": None, "by_outcome": {}, "by_year": {}})
        return base
    net = [t.gross_r - _fee_r(t) for t in trades]
    wins = [r for r in net if r > 0]
    # Capital efficiency: net_R per position-day (the operator's original
    # "PnL per unit time-in-market" metric). Time-in-market = Σ (exit-entry);
    # a market-neutral pair only ties up margin while the spread position is open.
    hold_secs = []
    for t in trades:
        try:
            dt = (pd.Timestamp(t.exit_time) - pd.Timestamp(t.entry_time)).total_seconds()
            hold_secs.append(max(dt, 0.0))
        except Exception:  # noqa: BLE001
            pass
    total_pos_days = sum(hold_secs) / 86400.0 if hold_secs else 0.0
    mean_hold_hours = (sum(hold_secs) / len(hold_secs) / 3600.0) if hold_secs else None
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
        "trades_long": sum(1 for t in trades if t.direction == "long_spread"),
        "trades_short": sum(1 for t in trades if t.direction == "short_spread"),
        "max_drawdown_r": round(mdd, 4),
        "mean_hold_hours": round(mean_hold_hours, 2) if mean_hold_hours is not None else None,
        "total_position_days": round(total_pos_days, 2),
        "net_r_per_pos_day": round(sum(net) / total_pos_days, 4) if total_pos_days > 0 else None,
        "by_outcome": by, "by_year": by_year})
    return base


def _fmt(s: Dict[str, Any]) -> str:
    lines = [f"pairs — {s['pair']} {s['timeframe']} {s.get('params')}",
             f"  data {s.get('data_start')} -> {s.get('data_end')}  trades={s['total_trades']}"]
    if s["total_trades"]:
        lines += [
            f"  win_rate={s['win_rate_pct']}%  net_r={s['net_total_r']} "
            f"(exp {s['net_expectancy_r']}, L/S {s['trades_long']}/{s['trades_short']})",
            f"  maxdd_r={s['max_drawdown_r']} by={s['by_outcome']}",
            f"  cap_eff: net_r/pos_day={s.get('net_r_per_pos_day')} "
            f"(mean_hold_hrs={s.get('mean_hold_hours')}, pos_days={s.get('total_position_days')})",
            f"  by_year={s.get('by_year')}"]
    return "\n".join(lines)


def _self_test() -> int:
    """Synthetic OU mean-reverting spread -> the harness must extract positive R."""
    rng = np.random.default_rng(7)
    n = 6000
    # B is a random walk; A = B + mean-reverting spread (OU). close = exp(log-price).
    lb = np.cumsum(rng.normal(0, 0.01, n)) + 5.0
    s = np.zeros(n)
    for k in range(1, n):
        s[k] = 0.92 * s[k - 1] + rng.normal(0, 0.02)   # OU, strong reversion
    la = lb + s
    ts = pd.date_range("2020-01-01", periods=n, freq="h", tz="UTC")
    m = pd.DataFrame({"timestamp": ts, "close_a": np.exp(la), "close_b": np.exp(lb)})
    out = run_backtest(m, lookback=24, entry_z=2.0, exit_z=0.3, stop_z=3.0,
                       max_hold_bars=48, cooldown_bars=0, hedge_beta="one",
                       timeframe="1h", pair="SYN_A/SYN_B")
    print(_fmt(out))
    ok = out["total_trades"] >= 30 and out["net_total_r"] > 0 and out["win_rate_pct"] >= 50
    print(f"SELF-TEST {'PASS' if ok else 'FAIL'} "
          f"(trades={out['total_trades']} net_r={out['net_total_r']} win={out['win_rate_pct']}%)")
    return 0 if ok else 1


def main(argv: List[str]) -> int:
    global FEE_BPS_ROUNDTRIP
    p = argparse.ArgumentParser(description="Pairs / spread mean-reversion backtest (net-of-fee).")
    p.add_argument("--self-test", action="store_true", help="run the synthetic-correctness check and exit")
    p.add_argument("--data-a", help="leg A candle CSV/parquet (timestamp,open,high,low,close)")
    p.add_argument("--data-b", help="leg B candle CSV/parquet")
    p.add_argument("--symbol-a", default="A")
    p.add_argument("--symbol-b", default="B")
    p.add_argument("--timeframe", default="1h")
    p.add_argument("--resample", default=None, help="resample BOTH legs to this rule first (e.g. 1h, 1D)")
    p.add_argument("--lookback", type=int, default=20, help="z-score rolling window (bars)")
    p.add_argument("--entry-z", type=float, default=2.0)
    p.add_argument("--exit-z", type=float, default=0.5)
    p.add_argument("--stop-z", type=float, default=2.0, help="adverse sigma move from entry = the stop (R unit)")
    p.add_argument("--max-hold-bars", type=int, default=20)
    p.add_argument("--cooldown-bars", type=int, default=1)
    p.add_argument("--hedge-beta", choices=["one", "rolling"], default="one",
                   help="one=log-ratio (beta=1); rolling=rolling-OLS hedge ratio")
    p.add_argument("--fee-bps-roundtrip", type=float, default=FEE_BPS_ROUNDTRIP,
                   help="per-leg round-trip cost in bps (two legs are charged)")
    p.add_argument("--start", default=None, help="ISO date; drop aligned bars before it (walk-forward OOS split)")
    p.add_argument("--end", default=None, help="ISO date; drop aligned bars on/after it")
    p.add_argument("--json", dest="json_out", default=None)
    p.add_argument("--emit-trades", default=None, metavar="PATH")
    args = p.parse_args(argv[1:])

    if args.self_test:
        return _self_test()
    if not args.data_a or not args.data_b:
        p.error("--data-a and --data-b are required (or use --self-test)")
    FEE_BPS_ROUNDTRIP = args.fee_bps_roundtrip
    try:
        a = _load_candles(args.data_a)
        b = _load_candles(args.data_b)
        if args.resample:
            a, b = _resample(a, args.resample), _resample(b, args.resample)
        m = _align(a, b)
        if args.start:
            m = m[m["timestamp"] >= pd.to_datetime(args.start, utc=True)].reset_index(drop=True)
        if args.end:
            m = m[m["timestamp"] < pd.to_datetime(args.end, utc=True)].reset_index(drop=True)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: load failed: {exc}", file=sys.stderr)
        return 1
    if len(m) <= args.lookback + 2:
        print(f"ERROR: only {len(m)} aligned bars (need > lookback={args.lookback})", file=sys.stderr)
        return 1

    pair = f"{args.symbol_a}/{args.symbol_b}"
    out = run_backtest(m, lookback=args.lookback, entry_z=args.entry_z, exit_z=args.exit_z,
                       stop_z=args.stop_z, max_hold_bars=args.max_hold_bars,
                       cooldown_bars=args.cooldown_bars, hedge_beta=args.hedge_beta,
                       timeframe=args.timeframe, pair=pair, emit_path=args.emit_trades)
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
