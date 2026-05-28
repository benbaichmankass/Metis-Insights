#!/usr/bin/env python3
"""Standalone backtest CLI for the ict_scalp_5m strategy.

Reads an OHLCV CSV (timestamp, open, high, low, close, volume), walks
the frame bar-by-bar from a warm-up offset, invokes the units-layer
``order_package()`` on a rolling window, and simulates fills on the
strategy's own SL/TP using the subsequent bars. Prints a summary and
optionally writes it to JSON.

This script is the pre-live gate referenced by
``.github/workflows/ict-scalp-backtest.yml`` and
``docs/strategies/ict_scalp_5m.md``. The runtime signal builder
(``src/runtime/strategy_signal_builders.py::ict_scalp_signal_builder``)
honours the YAML ``enabled`` flag separately — running this script
does not place orders or change live behaviour.

Exit codes
----------
0  success
1  runtime error (bad data, exception during walk)
2  CLI usage error
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

# Ensure src/ is importable when invoked as a script.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.units.strategies.ict_scalp import order_package  # noqa: E402
from src.units.strategies import load_strategy_config  # noqa: E402


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
    outcome: str = "open"           # tp_hit | sl_hit | timeout | open
    r_multiple: float = 0.0
    meta: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0         # live order_package() confidence (blend)


def _load_candles(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Candle CSV not found: {path}")
    df = pd.read_csv(path)
    needed = {"open", "high", "low", "close"}
    missing = needed - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns in {path}: {sorted(missing)}")
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
        df = df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    return df


def _load_yaml_params() -> Dict[str, Any]:
    try:
        cfg = load_strategy_config().get("ict_scalp_5m", {}) or {}
    except Exception:
        cfg = {}
    # Strip fields the unit doesn't consume.
    for k in ("enabled", "model", "signal_prefixes", "symbols", "risk_pct", "shadow_model_ids"):
        cfg.pop(k, None)
    return cfg


def _simulate_exit(
    df: pd.DataFrame,
    *,
    start_idx: int,
    direction: str,
    sl: float,
    tp: float,
    timeout_bars: int,
) -> Dict[str, Any]:
    """Walk forward from start_idx checking SL/TP hits against bar
    extremes. Assumes intra-bar SL/TP fills are at the level (no slippage).
    Returns dict with outcome, exit_index, exit_price.
    """
    last = min(len(df) - 1, start_idx + timeout_bars)
    for j in range(start_idx, last + 1):
        bar_low = float(df["low"].iloc[j])
        bar_high = float(df["high"].iloc[j])
        if direction == "long":
            # Pessimistic ordering: if both touched in one bar, count SL first.
            if bar_low <= sl:
                return {"outcome": "sl_hit", "exit_index": j, "exit_price": sl}
            if bar_high >= tp:
                return {"outcome": "tp_hit", "exit_index": j, "exit_price": tp}
        else:
            if bar_high >= sl:
                return {"outcome": "sl_hit", "exit_index": j, "exit_price": sl}
            if bar_low <= tp:
                return {"outcome": "tp_hit", "exit_index": j, "exit_price": tp}
    # Timeout: close at the last bar's close.
    return {
        "outcome": "timeout",
        "exit_index": last,
        "exit_price": float(df["close"].iloc[last]),
    }


def _build_htf_series(
    df: pd.DataFrame,
    *,
    htf_rule: str,
    ema_period: int,
) -> Optional[pd.DataFrame]:
    """Resample the 5m OHLCV feed to ``htf_rule`` and return a per-row
    DataFrame containing (timestamp, htf_close, htf_ema) aligned to the
    HTF bar. Caller forward-fills onto the 5m index.

    v2 backtest CLI: lets the strategy's HTF bias filter run without a
    second data feed. Returns None when the frame doesn't have a
    ``timestamp`` column or pandas resample fails.
    """
    if "timestamp" not in df.columns:
        return None
    try:
        ts = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        if ts.isna().any():
            return None
        tmp = df.copy()
        tmp["timestamp"] = ts
        tmp = tmp.set_index("timestamp")
        agg = tmp.resample(htf_rule).agg({"close": "last"}).dropna()
        if len(agg) < ema_period + 1:
            return None
        agg["htf_ema"] = agg["close"].ewm(span=ema_period, adjust=False).mean()
        agg = agg.rename(columns={"close": "htf_close"}).reset_index()
        return agg
    except Exception:
        return None


def _htf_values_for_bar(
    htf_df: Optional[pd.DataFrame], *, bar_ts: Any
) -> tuple[Optional[float], Optional[float]]:
    """Return the most recent (htf_close, htf_ema) at or before bar_ts."""
    if htf_df is None or len(htf_df) == 0:
        return None, None
    try:
        bar_ts = pd.Timestamp(bar_ts)
        if bar_ts.tzinfo is None:
            bar_ts = bar_ts.tz_localize("UTC")
        mask = htf_df["timestamp"] <= bar_ts
        if not mask.any():
            return None, None
        row = htf_df.loc[mask].iloc[-1]
        return float(row["htf_close"]), float(row["htf_ema"])
    except Exception:
        return None, None


def run_backtest(
    df: pd.DataFrame,
    *,
    cfg_overrides: Dict[str, Any],
    timeframe: str,
    symbol: str,
    warmup_bars: int,
    timeout_bars: int,
    cooldown_bars: int,
    htf_rule: str = "1h",
    htf_ema_period: int = 20,
    min_confidence: float = 0.0,
    _collect_trades: bool = False,
) -> Dict[str, Any]:
    cfg = {"symbol": symbol, "timeframe": timeframe, **cfg_overrides}
    htf_df = _build_htf_series(df, htf_rule=htf_rule, ema_period=htf_ema_period)
    trades: List[Trade] = []
    n = len(df)
    if n < warmup_bars + 5:
        raise ValueError(
            f"Not enough candles: have {n}, need at least {warmup_bars + 5}"
        )

    # Slide a fixed-size window instead of growing prefix. The strategy
    # only needs max(swing_lookback, sweep_lookback, atr_period) bars
    # of history, so a 80-bar window is sufficient at defaults. This
    # makes the backtest O(n * window) instead of O(n^2), which is the
    # difference between minutes and hours on a 26k-bar feed.
    window_size = max(
        int(cfg.get("swing_lookback_bars", 20)),
        int(cfg.get("sweep_lookback_bars", 12)),
        int(cfg.get("atr_period", 14)),
    ) + 10
    window_size = max(window_size, warmup_bars)

    next_eligible_idx = warmup_bars
    for i in range(warmup_bars, n - 1):
        if i < next_eligible_idx:
            continue
        lo = max(0, i + 1 - window_size)
        window = df.iloc[lo : i + 1]
        # v2 HTF bias: look up htf_close + htf_ema as of this bar's
        # timestamp from the resampled series. The strategy reads
        # them from cfg, so we copy + augment per iteration.
        per_bar_cfg = dict(cfg)
        if htf_df is not None and "timestamp" in df.columns:
            bar_ts = df["timestamp"].iloc[i]
            htf_close, htf_ema = _htf_values_for_bar(htf_df, bar_ts=bar_ts)
            if htf_close is not None and htf_ema is not None:
                per_bar_cfg["htf_close"] = htf_close
                per_bar_cfg["htf_ema"] = htf_ema
        try:
            pkg = order_package(per_bar_cfg, candles_df=window)
        except ValueError:
            continue
        direction = pkg["direction"]
        # Confidence is computed by the live order_package() itself, so this
        # gate is an exact mirror of a live min_confidence floor.
        confidence = float(pkg.get("confidence") or 0.0)
        if confidence < min_confidence:
            continue
        entry = float(pkg["entry"])
        sl = float(pkg["sl"])
        tp = float(pkg["tp"])
        risk = abs(entry - sl)
        if risk <= 0:
            continue
        # Fill simulation starts on the next bar.
        result = _simulate_exit(
            df,
            start_idx=i + 1,
            direction=direction,
            sl=sl,
            tp=tp,
            timeout_bars=timeout_bars,
        )
        exit_price = float(result["exit_price"])
        if direction == "long":
            r = (exit_price - entry) / risk
        else:
            r = (entry - exit_price) / risk
        ts = df["timestamp"].iloc[i] if "timestamp" in df.columns else i
        exit_ts = (
            df["timestamp"].iloc[result["exit_index"]] if "timestamp" in df.columns else result["exit_index"]
        )
        trades.append(
            Trade(
                entry_index=i,
                entry_time=ts,
                direction=direction,
                entry=entry,
                sl=sl,
                tp=tp,
                risk=risk,
                exit_index=int(result["exit_index"]),
                exit_time=exit_ts,
                exit_price=exit_price,
                outcome=str(result["outcome"]),
                r_multiple=round(float(r), 4),
                meta=pkg.get("meta") or {},
                confidence=round(confidence, 4),
            )
        )
        next_eligible_idx = int(result["exit_index"]) + 1 + cooldown_bars

    summary = _summarize(trades, df, timeframe=timeframe, symbol=symbol)
    if _collect_trades:
        # (confidence, r_multiple) per trade — lets the sweep filter by
        # threshold without re-walking the (expensive) 5m frame N times.
        summary["_trades"] = [(t.confidence, t.r_multiple) for t in trades]
    return summary


def _summarize(
    trades: List[Trade],
    df: pd.DataFrame,
    *,
    timeframe: str,
    symbol: str,
) -> Dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {
            "strategy": "ict_scalp_5m",
            "symbol": symbol,
            "timeframe": timeframe,
            "total_trades": 0,
            "win_rate_pct": 0.0,
            "expectancy_r": 0.0,
            "total_r": 0.0,
            "max_drawdown_r": 0.0,
            "sharpe_r": 0.0,
            "by_outcome": {},
            "data_start": str(df["timestamp"].iloc[0]) if "timestamp" in df.columns and len(df) else None,
            "data_end": str(df["timestamp"].iloc[-1]) if "timestamp" in df.columns and len(df) else None,
            "bars": int(len(df)),
            "run_date": str(date.today()),
        }
    rs = [t.r_multiple for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    by_outcome: Dict[str, int] = {}
    for t in trades:
        by_outcome[t.outcome] = by_outcome.get(t.outcome, 0) + 1
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in rs:
        cum += r
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
    mean = sum(rs) / n
    stdev = statistics.pstdev(rs) if n > 1 else 0.0
    sharpe = (mean / stdev) if stdev > 0 else 0.0
    return {
        "strategy": "ict_scalp_5m",
        "symbol": symbol,
        "timeframe": timeframe,
        "total_trades": int(n),
        "winning_trades": int(len(wins)),
        "losing_trades": int(len(losses)),
        "win_rate_pct": round(100.0 * len(wins) / n, 2),
        "expectancy_r": round(mean, 4),
        "total_r": round(sum(rs), 4),
        "max_drawdown_r": round(max_dd, 4),
        "sharpe_r": round(sharpe, 4),
        "avg_win_r": round(sum(wins) / len(wins), 4) if wins else 0.0,
        "avg_loss_r": round(sum(losses) / len(losses), 4) if losses else 0.0,
        "by_outcome": by_outcome,
        "data_start": str(df["timestamp"].iloc[0]) if "timestamp" in df.columns else None,
        "data_end": str(df["timestamp"].iloc[-1]) if "timestamp" in df.columns else None,
        "bars": int(len(df)),
        "run_date": str(date.today()),
    }


def _format_text(summary: Dict[str, Any]) -> str:
    lines = [
        f"ict_scalp_5m backtest — {summary['symbol']} {summary['timeframe']}",
        f"  data: {summary.get('data_start')} → {summary.get('data_end')} ({summary['bars']} bars)",
        f"  total_trades   : {summary['total_trades']}",
    ]
    if summary["total_trades"]:
        lines.extend([
            f"  win_rate_pct   : {summary['win_rate_pct']}%",
            f"  expectancy_r   : {summary['expectancy_r']}",
            f"  total_r        : {summary['total_r']}",
            f"  max_drawdown_r : {summary['max_drawdown_r']}",
            f"  sharpe_r       : {summary['sharpe_r']}",
            f"  avg_win_r      : {summary['avg_win_r']}",
            f"  avg_loss_r     : {summary['avg_loss_r']}",
            f"  by_outcome     : {summary['by_outcome']}",
        ])
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


def _metrics_for(thr: float, pairs: List[tuple]) -> Dict[str, Any]:
    """Compute headline metrics for the subset of (confidence, r) trades at
    or above the threshold."""
    sub = [r for c, r in pairs if c >= thr]
    n = len(sub)
    if n == 0:
        return {"min_confidence": thr, "trades": 0, "win_rate_pct": 0.0,
                "total_r": 0.0, "expectancy_r": 0.0, "max_drawdown_r": 0.0}
    wins = sum(1 for r in sub if r > 0)
    cum = peak = mdd = 0.0
    for r in sub:
        cum += r
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    return {"min_confidence": thr, "trades": n,
            "win_rate_pct": round(100.0 * wins / n, 2),
            "total_r": round(sum(sub), 4),
            "expectancy_r": round(sum(sub) / n, 4),
            "max_drawdown_r": round(mdd, 4)}


def _confidence_sweep(df: pd.DataFrame, grid: List[float], kwargs: Dict[str, Any]) -> Dict[str, Any]:
    # Walk the frame ONCE at min_confidence=0 (the 5m walk is the expensive
    # part — order_package() per bar), then filter the resulting trades by
    # each threshold. NOTE: this is a post-hoc filter, so it does not model
    # the cooldown re-entries that gating at entry would free up — a small,
    # conservative approximation adequate for threshold selection.
    full = run_backtest(df, min_confidence=0.0, _collect_trades=True, **kwargs)
    pairs = full.get("_trades", [])
    rows = [_metrics_for(thr, pairs) for thr in grid]
    best = max(rows, key=lambda r: r["total_r"]) if rows else None
    best_exp = max((r for r in rows if r["trades"] >= 20),
                   key=lambda r: r["expectancy_r"], default=None)
    return {"strategy": "ict_scalp_5m", "symbol": kwargs.get("symbol"),
            "timeframe": kwargs.get("timeframe"),
            "data_start": str(df["timestamp"].iloc[0]) if "timestamp" in df.columns and len(df) else None,
            "data_end": str(df["timestamp"].iloc[-1]) if "timestamp" in df.columns and len(df) else None,
            "baseline_trades": full.get("total_trades", 0),
            "grid": rows, "best_by_total_r": best,
            "best_by_expectancy_r_min20": best_exp,
            "note": "post-hoc confidence filter on a single walk; ignores cooldown re-entries"}


def _fmt_sweep(sw: Dict[str, Any]) -> str:
    lines = [f"ict_scalp_5m confidence sweep — {sw['symbol']} {sw['timeframe']} "
             f"({sw['data_start']} -> {sw['data_end']})",
             f"  {'min_conf':>8} {'trades':>7} {'WR%':>6} {'total_R':>9} {'exp_R':>8} {'maxDD_R':>8}"]
    for r in sw["grid"]:
        lines.append(f"  {r['min_confidence']:>8.3f} {r['trades']:>7d} {r['win_rate_pct']:>6.1f} "
                     f"{r['total_r']:>9.2f} {r['expectancy_r']:>8.3f} {r['max_drawdown_r']:>8.2f}")
    b = sw.get("best_by_total_r")
    if b:
        lines.append(f"  -> best total_R @ min_confidence={b['min_confidence']} "
                     f"(total_R={b['total_r']}, trades={b['trades']})")
    be = sw.get("best_by_expectancy_r_min20")
    if be:
        lines.append(f"  -> best expectancy_R (>=20 trades) @ min_confidence={be['min_confidence']} "
                     f"(exp={be['expectancy_r']}, trades={be['trades']})")
    return "\n".join(lines)


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(description="Backtest ict_scalp_5m.")
    p.add_argument("--data", default=os.environ.get("BACKTEST_DATA_PATH", "data/backtest_candles.csv"),
                   help="OHLCV CSV path (default: $BACKTEST_DATA_PATH or data/backtest_candles.csv).")
    p.add_argument("--timeframe", default="5m", help="Strategy timeframe label (default: 5m).")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--warmup-bars", type=int, default=50,
                   help="Skip the first N bars to give lookback windows room (default: 50).")
    p.add_argument("--timeout-bars", type=int, default=24,
                   help="Force-close a trade after N bars if neither SL nor TP hits (default: 24 → 2h on 5m).")
    p.add_argument("--cooldown-bars", type=int, default=3,
                   help="Skip N bars after each exit before re-evaluating (default: 3).")
    p.add_argument("--htf-rule", default="1h",
                   help="HTF resample rule for the bias filter (default: 1h).")
    p.add_argument("--htf-ema-period", type=int, default=20,
                   help="HTF EMA period for the bias filter (default: 20).")
    p.add_argument("--json", dest="json_out", default=None,
                   help="Write summary to this JSON file. '-' means stdout.")
    p.add_argument("--ignore-yaml", action="store_true",
                   help="Ignore config/strategies.yaml; use unit defaults only.")
    p.add_argument("--min-confidence", type=float, default=0.0,
                   help="Skip entries whose live order_package() confidence is below this.")
    p.add_argument("--confidence-sweep", default=None, metavar="GRID",
                   help="Sweep min_confidence over GRID ('0:0.6:0.05' or '0,0.1,0.2') and tabulate.")
    args = p.parse_args(argv[1:])

    try:
        df = _load_candles(args.data)
    except Exception as exc:
        print(f"ERROR: failed to load candles from {args.data}: {exc}", file=sys.stderr)
        return 1

    cfg_overrides = {} if args.ignore_yaml else _load_yaml_params()
    bt_kwargs = dict(
        cfg_overrides=cfg_overrides,
        timeframe=args.timeframe,
        symbol=args.symbol,
        warmup_bars=int(args.warmup_bars),
        timeout_bars=int(args.timeout_bars),
        cooldown_bars=int(args.cooldown_bars),
        htf_rule=str(args.htf_rule),
        htf_ema_period=int(args.htf_ema_period),
    )

    try:
        if args.confidence_sweep:
            summary = _confidence_sweep(df, _parse_grid(args.confidence_sweep), bt_kwargs)
            print(_fmt_sweep(summary))
        else:
            summary = run_backtest(df, min_confidence=float(args.min_confidence), **bt_kwargs)
            print(_format_text(summary))
    except Exception as exc:
        print(f"ERROR: backtest failed: {exc}", file=sys.stderr)
        return 1

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
