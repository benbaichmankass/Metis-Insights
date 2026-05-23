#!/usr/bin/env python3
"""Standalone backtest CLI for the turtle_soup strategy
(S-STRAT-IMPROVE-S5).

Mirror of ``scripts/backtest_ict_scalp.py`` for the second production
strategy, which had no harness. Reads an OHLCV CSV at the setup
timeframe (15m default), walks it bar-by-bar from a warm-up offset,
invokes the units-layer ``turtle_soup.order_package()`` on a rolling
window, and simulates fills on the package's entry/SL/TP1 using the
subsequent bars. Reports gross AND net-of-fee R (long/short split).

Scope note (inherent-edge audit): this simulates the **single TP1**
exit only — it does NOT model turtle_soup's TP2 / partial-close /
ATR-trail / break-even refinements (those live in ``monitor()``). That
is deliberate: the question this harness answers is "does the SETUP
have edge?", which is separable from exit sophistication (the same
lesson S4-B taught for vwap). A faithful multi-stage version is a later
follow-up.

Exit codes: 0 success, 1 runtime error, 2 CLI usage error.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.units.strategies.turtle_soup import order_package  # noqa: E402
from src.units.strategies import load_strategy_config  # noqa: E402

# Round-trip taker fee (bps) for net-of-fee R — matches the vwap +
# ict_scalp backtests (S-STRAT-IMPROVE-S4). fee_r = (FEE_BPS/1e4) ×
# (entry+exit)/2 / risk. Settable via --fee-bps-roundtrip; 0 = gross.
FEE_BPS_ROUNDTRIP = 7.5


@dataclass
class Trade:
    entry_index: int
    entry_time: Any
    direction: str
    entry: float
    sl: float
    tp: float
    risk: float
    exit_index: Optional[int] = None
    exit_time: Any = None
    exit_price: Optional[float] = None
    outcome: str = "open"
    r_multiple: float = 0.0
    meta: Dict[str, Any] = field(default_factory=dict)


def _load_candles(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    cols = {c.lower(): c for c in df.columns}
    need = ["timestamp", "open", "high", "low", "close"]
    missing = [c for c in need if c not in cols]
    if missing:
        raise ValueError(f"CSV missing columns: {missing} (have {list(df.columns)})")
    df = df.rename(columns={cols[c]: c for c in need if cols[c] != c})
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df = df.dropna(subset=["timestamp"]).reset_index(drop=True)
    return df


def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Resample an OHLCV frame to a coarser bar (e.g. 5m CSV -> 15m)."""
    tmp = df.set_index("timestamp")
    out = tmp.resample(rule, label="right", closed="right").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    ).dropna().reset_index()
    return out


def _load_yaml_params() -> Dict[str, Any]:
    try:
        cfg = dict(load_strategy_config().get("turtle_soup", {}) or {})
    except Exception:  # noqa: BLE001
        cfg = {}
    for k in ("enabled", "model", "signal_prefixes", "symbols", "risk_pct",
              "shadow_model_ids", "service"):
        cfg.pop(k, None)
    return cfg


def _simulate_exit(
    df: pd.DataFrame, *, start_idx: int, direction: str, sl: float,
    tp: float, timeout_bars: int,
) -> Dict[str, Any]:
    """Walk forward checking SL/TP against bar extremes (SL-first on a
    same-bar tie). Timeout closes at the last bar's close."""
    last = min(len(df) - 1, start_idx + timeout_bars)
    for j in range(start_idx, last + 1):
        bar_low = float(df["low"].iloc[j])
        bar_high = float(df["high"].iloc[j])
        if direction == "long":
            if bar_low <= sl:
                return {"outcome": "sl_hit", "exit_index": j, "exit_price": sl}
            if bar_high >= tp:
                return {"outcome": "tp_hit", "exit_index": j, "exit_price": tp}
        else:
            if bar_high >= sl:
                return {"outcome": "sl_hit", "exit_index": j, "exit_price": sl}
            if bar_low <= tp:
                return {"outcome": "tp_hit", "exit_index": j, "exit_price": tp}
    return {"outcome": "timeout", "exit_index": last,
            "exit_price": float(df["close"].iloc[last])}


def run_backtest(
    df: pd.DataFrame, *, cfg_overrides: Dict[str, Any], timeframe: str,
    symbol: str, warmup_bars: int, timeout_bars: int, cooldown_bars: int,
) -> Dict[str, Any]:
    cfg = {"symbol": symbol, "timeframe": timeframe, **cfg_overrides}
    trades: List[Trade] = []
    n = len(df)
    if n < warmup_bars + 5:
        raise ValueError(f"Not enough candles: have {n}, need >= {warmup_bars + 5}")
    window_size = max(
        int(cfg.get("sweep_lookback_15m", 60)),
        int(cfg.get("atr_period", 14)),
    ) + int(cfg.get("setup_lookback_bars", 4)) + 10
    window_size = max(window_size, warmup_bars)

    next_eligible_idx = warmup_bars
    for i in range(warmup_bars, n - 1):
        if i < next_eligible_idx:
            continue
        lo = max(0, i + 1 - window_size)
        window = df.iloc[lo : i + 1]
        try:
            pkg = order_package(dict(cfg), candles_df=window)
        except ValueError:
            continue  # no setup on the most-recent bars
        direction = pkg["direction"]
        entry = float(pkg["entry"])
        sl = float(pkg["sl"])
        tp = float(pkg["tp"])
        risk = abs(entry - sl)
        if risk <= 0:
            continue
        result = _simulate_exit(df, start_idx=i + 1, direction=direction,
                                sl=sl, tp=tp, timeout_bars=timeout_bars)
        exit_price = float(result["exit_price"])
        r = (exit_price - entry) / risk if direction == "long" else (entry - exit_price) / risk
        ts = df["timestamp"].iloc[i]
        trades.append(Trade(
            entry_index=i, entry_time=ts, direction=direction, entry=entry,
            sl=sl, tp=tp, risk=risk, exit_index=int(result["exit_index"]),
            exit_time=df["timestamp"].iloc[result["exit_index"]],
            exit_price=exit_price, outcome=str(result["outcome"]),
            r_multiple=round(float(r), 4), meta=pkg.get("meta") or {},
        ))
        next_eligible_idx = int(result["exit_index"]) + 1 + cooldown_bars

    return _summarize(trades, df, timeframe=timeframe, symbol=symbol)


def _summarize(trades: List[Trade], df: pd.DataFrame, *, timeframe: str,
               symbol: str) -> Dict[str, Any]:
    def _fee_r(t: Trade) -> float:
        if not t.exit_price or t.risk <= 0:
            return 0.0
        return (FEE_BPS_ROUNDTRIP / 10_000.0) * ((t.entry + t.exit_price) / 2.0) / t.risk

    n = len(trades)
    base = {
        "strategy": "turtle_soup", "symbol": symbol, "timeframe": timeframe,
        "total_trades": n, "fee_bps_roundtrip": FEE_BPS_ROUNDTRIP,
        "data_start": str(df["timestamp"].iloc[0]) if n is not None and len(df) else None,
        "data_end": str(df["timestamp"].iloc[-1]) if len(df) else None,
        "bars": int(len(df)), "run_date": str(date.today()),
    }
    if n == 0:
        base.update({"win_rate_pct": 0.0, "expectancy_r": 0.0, "total_r": 0.0,
                     "net_total_r": 0.0, "net_expectancy_r": 0.0,
                     "net_win_rate_pct": 0.0, "total_fee_r": 0.0,
                     "max_drawdown_r": 0.0, "sharpe_r": 0.0, "by_outcome": {}})
        return base

    longs = [t for t in trades if t.direction == "long"]
    shorts = [t for t in trades if t.direction == "short"]
    rs = [t.r_multiple for t in trades]
    net_rs = [t.r_multiple - _fee_r(t) for t in trades]
    wins = [r for r in rs if r > 0]
    net_wins = [r for r in net_rs if r > 0]
    cum = peak = max_dd = 0.0
    for r in rs:
        cum += r
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    mean = sum(rs) / n
    stdev = statistics.pstdev(rs) if n > 1 else 0.0
    by_outcome: Dict[str, int] = {}
    for t in trades:
        by_outcome[t.outcome] = by_outcome.get(t.outcome, 0) + 1
    base.update({
        "winning_trades": len(wins), "losing_trades": n - len(wins),
        "trades_long": len(longs), "trades_short": len(shorts),
        "win_rate_pct": round(100.0 * len(wins) / n, 2),
        "expectancy_r": round(mean, 4), "total_r": round(sum(rs), 4),
        "total_r_long": round(sum(t.r_multiple for t in longs), 4),
        "total_r_short": round(sum(t.r_multiple for t in shorts), 4),
        "total_fee_r": round(sum(_fee_r(t) for t in trades), 4),
        "net_total_r": round(sum(net_rs), 4),
        "net_total_r_long": round(sum(t.r_multiple - _fee_r(t) for t in longs), 4),
        "net_total_r_short": round(sum(t.r_multiple - _fee_r(t) for t in shorts), 4),
        "net_expectancy_r": round(sum(net_rs) / n, 4),
        "net_win_rate_pct": round(100.0 * len(net_wins) / n, 2),
        "max_drawdown_r": round(max_dd, 4),
        "sharpe_r": round((mean / stdev) if stdev > 0 else 0.0, 4),
        "avg_win_r": round(sum(wins) / len(wins), 4) if wins else 0.0,
        "by_outcome": by_outcome,
    })
    return base


def _format_text(s: Dict[str, Any]) -> str:
    lines = [
        f"turtle_soup backtest — {s['symbol']} {s['timeframe']}",
        f"  data: {s.get('data_start')} → {s.get('data_end')} ({s['bars']} bars)",
        f"  total_trades   : {s['total_trades']}",
    ]
    if s["total_trades"]:
        lines.extend([
            f"  win_rate_pct   : {s['win_rate_pct']}%",
            f"  total_r        : {s['total_r']}  (long {s.get('total_r_long')}, short {s.get('total_r_short')})",
            f"  net_total_r    : {s['net_total_r']}  (net_exp_r {s['net_expectancy_r']}, "
            f"net_wr {s['net_win_rate_pct']}%, fee_r {s['total_fee_r']} @ {s['fee_bps_roundtrip']}bps rt)",
            f"  net long/short : {s.get('net_total_r_long')} / {s.get('net_total_r_short')}",
            f"  max_drawdown_r : {s['max_drawdown_r']}",
            f"  by_outcome     : {s['by_outcome']}",
        ])
    return "\n".join(lines)


def main(argv: List[str]) -> int:
    global FEE_BPS_ROUNDTRIP
    p = argparse.ArgumentParser(description="turtle_soup backtest (net-of-fee)")
    p.add_argument("--data", default=os.environ.get("BACKTEST_DATA_PATH", "data/backtest_candles.csv"))
    p.add_argument("--timeframe", default="15m", help="Setup timeframe label (default 15m).")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--resample", default=None, help="Resample the CSV to this rule first (e.g. 15min) — use when feeding a finer CSV.")
    p.add_argument("--warmup-bars", type=int, default=70)
    p.add_argument("--timeout-bars", type=int, default=48)
    p.add_argument("--cooldown-bars", type=int, default=2)
    p.add_argument("--fee-bps-roundtrip", type=float, default=FEE_BPS_ROUNDTRIP,
                   help="Round-trip taker fee bps for net-of-fee R (default 7.5; 0=gross).")
    p.add_argument("--ignore-yaml", action="store_true")
    p.add_argument("--json", dest="json_out", default=None)
    args = p.parse_args(argv[1:])
    FEE_BPS_ROUNDTRIP = args.fee_bps_roundtrip

    try:
        df = _load_candles(args.data)
        if args.resample:
            df = _resample(df, args.resample)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: failed to load candles from {args.data}: {exc}", file=sys.stderr)
        return 1

    cfg_overrides = {} if args.ignore_yaml else _load_yaml_params()
    try:
        summary = run_backtest(
            df, cfg_overrides=cfg_overrides, timeframe=args.timeframe,
            symbol=args.symbol, warmup_bars=int(args.warmup_bars),
            timeout_bars=int(args.timeout_bars), cooldown_bars=int(args.cooldown_bars),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: backtest failed: {exc}", file=sys.stderr)
        return 1

    print(_format_text(summary))
    if args.json_out:
        payload = json.dumps(summary, indent=2, default=str)
        if args.json_out == "-":
            print(payload)
        else:
            Path(args.json_out).write_text(payload)
            print(f"\nJSON written to {args.json_out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
