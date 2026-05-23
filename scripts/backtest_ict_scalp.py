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

# Round-trip taker fee (bps) for net-of-fee R, matching the vwap backtest
# (S-STRAT-IMPROVE-S4). Each trade's fee in R = (FEE_BPS_ROUNDTRIP/1e4) ×
# (entry+exit)/2 / risk. Settable via --fee-bps-roundtrip; 0 = gross only.
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
    outcome: str = "open"           # tp_hit | sl_hit | timeout | open
    r_multiple: float = 0.0
    meta: Dict[str, Any] = field(default_factory=dict)


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
            )
        )
        next_eligible_idx = int(result["exit_index"]) + 1 + cooldown_bars

    return _summarize(trades, df, timeframe=timeframe, symbol=symbol)


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
            "fee_bps_roundtrip": FEE_BPS_ROUNDTRIP,
            "total_fee_r": 0.0,
            "net_total_r": 0.0,
            "net_expectancy_r": 0.0,
            "net_win_rate_pct": 0.0,
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
    # Net-of-fee R per trade (S-STRAT-IMPROVE-S4): subtract the round-trip
    # taker fee expressed in R. Tight stops make this a large fraction of R.
    def _fee_r(t: Trade) -> float:
        if not t.exit_price or t.risk <= 0:
            return 0.0
        return (FEE_BPS_ROUNDTRIP / 10_000.0) * ((t.entry + t.exit_price) / 2.0) / t.risk
    net_rs = [t.r_multiple - _fee_r(t) for t in trades]
    net_wins = [r for r in net_rs if r > 0]
    total_fee_r = sum(_fee_r(t) for t in trades)
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
        # Net-of-fee (S-STRAT-IMPROVE-S4) — the inherent-edge metric.
        "fee_bps_roundtrip": FEE_BPS_ROUNDTRIP,
        "total_fee_r": round(total_fee_r, 4),
        "net_total_r": round(sum(net_rs), 4),
        "net_expectancy_r": round(sum(net_rs) / n, 4),
        "net_win_rate_pct": round(100.0 * len(net_wins) / n, 2),
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
            f"  net_total_r    : {summary.get('net_total_r')}  "
            f"(net_exp_r {summary.get('net_expectancy_r')}, "
            f"net_wr {summary.get('net_win_rate_pct')}%, "
            f"fee_r {summary.get('total_fee_r')} @ "
            f"{summary.get('fee_bps_roundtrip')}bps rt)",
            f"  max_drawdown_r : {summary['max_drawdown_r']}",
            f"  sharpe_r       : {summary['sharpe_r']}",
            f"  avg_win_r      : {summary['avg_win_r']}",
            f"  avg_loss_r     : {summary['avg_loss_r']}",
            f"  by_outcome     : {summary['by_outcome']}",
        ])
    return "\n".join(lines)


# ----------------------------------------------------------------------
# Variation sweep (S6): ONE expensive entry pass -> MANY cheap exit
# variations. Tests whether exit/target logic seriously moves the edge
# (operator directive 2026-05-23: test many versions per run, not one
# config over years). Entries use fixed timeout-bar spacing for
# non-overlap so every variation compares on the SAME entry set — a
# deliberate approximation (true exit-dependent cooldown would differ
# per variation and confound the comparison).

def _collect_entries_for_grid(df, cfg, *, warmup_bars, timeout_bars,
                              htf_rule, htf_ema_period):
    htf_df = _build_htf_series(df, htf_rule=htf_rule, ema_period=htf_ema_period)
    window_size = max(int(cfg.get("swing_lookback_bars", 20)),
                      int(cfg.get("sweep_lookback_bars", 12)),
                      int(cfg.get("atr_period", 14))) + 10
    window_size = max(window_size, warmup_bars)
    entries: List[Dict[str, Any]] = []
    next_idx = warmup_bars
    for i in range(warmup_bars, len(df) - 1):
        if i < next_idx:
            continue
        window = df.iloc[max(0, i + 1 - window_size): i + 1]
        per_bar = dict(cfg)
        if htf_df is not None and "timestamp" in df.columns:
            hc, he = _htf_values_for_bar(htf_df, bar_ts=df["timestamp"].iloc[i])
            if hc is not None and he is not None:
                per_bar["htf_close"] = hc
                per_bar["htf_ema"] = he
        try:
            pkg = order_package(per_bar, candles_df=window)
        except ValueError:
            continue
        direction = pkg["direction"]
        entry = float(pkg["entry"])
        sl = float(pkg["sl"])
        risk = abs(entry - sl)
        if risk <= 0:
            continue
        entries.append({"i": i, "direction": direction, "entry": entry,
                        "sl": sl, "risk": risk})
        next_idx = i + 1 + timeout_bars
    return entries


def _simulate_exit_be(df, *, start_idx, direction, entry, sl, tp,
                      be_trigger_r, timeout_bars):
    """SL/TP exit with an optional break-even move: once price reaches
    be_trigger_r x risk in favor, slide SL to entry. be_trigger_r=None
    disables BE. SL-first on same-bar ties (pessimistic)."""
    risk = abs(entry - sl)
    cur_sl = sl
    moved = False
    last = min(len(df) - 1, start_idx + timeout_bars)
    for j in range(start_idx, last + 1):
        hi = float(df["high"].iloc[j])
        lo = float(df["low"].iloc[j])
        if direction == "long":
            if be_trigger_r is not None and not moved and hi >= entry + be_trigger_r * risk:
                cur_sl = entry
                moved = True
            if lo <= cur_sl:
                return {"outcome": "be_stop" if moved else "sl_hit", "exit_index": j, "exit_price": cur_sl}
            if hi >= tp:
                return {"outcome": "tp_hit", "exit_index": j, "exit_price": tp}
        else:
            if be_trigger_r is not None and not moved and lo <= entry - be_trigger_r * risk:
                cur_sl = entry
                moved = True
            if hi >= cur_sl:
                return {"outcome": "be_stop" if moved else "sl_hit", "exit_index": j, "exit_price": cur_sl}
            if lo <= tp:
                return {"outcome": "tp_hit", "exit_index": j, "exit_price": tp}
    return {"outcome": "timeout", "exit_index": last, "exit_price": float(df["close"].iloc[last])}


def run_exit_grid(df, *, cfg_overrides, timeframe, symbol, warmup_bars,
                  timeout_bars, htf_rule, htf_ema_period, tp_grid, be_grid):
    cfg = {"symbol": symbol, "timeframe": timeframe, **cfg_overrides}
    entries = _collect_entries_for_grid(
        df, cfg, warmup_bars=warmup_bars, timeout_bars=timeout_bars,
        htf_rule=htf_rule, htf_ema_period=htf_ema_period)
    grid: List[Dict[str, Any]] = []
    for tp_r in tp_grid:
        for be in be_grid:
            trades: List[Trade] = []
            for e in entries:
                d, entry, risk, sl = e["direction"], e["entry"], e["risk"], e["sl"]
                tp = entry + tp_r * risk if d == "long" else entry - tp_r * risk
                res = _simulate_exit_be(df, start_idx=e["i"] + 1, direction=d,
                                        entry=entry, sl=sl, tp=tp,
                                        be_trigger_r=be, timeout_bars=timeout_bars)
                xp = float(res["exit_price"])
                r = (xp - entry) / risk if d == "long" else (entry - xp) / risk
                trades.append(Trade(
                    entry_index=e["i"], entry_time=df["timestamp"].iloc[e["i"]],
                    direction=d, entry=entry, sl=sl, tp=tp, risk=risk,
                    exit_index=int(res["exit_index"]),
                    exit_time=df["timestamp"].iloc[res["exit_index"]],
                    exit_price=xp, outcome=str(res["outcome"]),
                    r_multiple=round(r, 4)))
            s = _summarize(trades, df, timeframe=timeframe, symbol=symbol)
            grid.append({
                "tp_at_r": tp_r, "be_trigger_r": be,
                "total_trades": s["total_trades"], "win_rate_pct": s["win_rate_pct"],
                "total_r": s["total_r"], "net_total_r": s["net_total_r"],
                "net_expectancy_r": s["net_expectancy_r"],
                "total_fee_r": s["total_fee_r"],
                "max_drawdown_r": s["max_drawdown_r"], "by_outcome": s["by_outcome"]})
    grid_sorted = sorted(grid, key=lambda g: g["net_total_r"], reverse=True)
    return {
        "strategy": "ict_scalp_5m", "symbol": symbol, "timeframe": timeframe,
        "entries_pool": len(entries), "fee_bps_roundtrip": FEE_BPS_ROUNDTRIP,
        "data_start": str(df["timestamp"].iloc[0]) if len(df) else None,
        "data_end": str(df["timestamp"].iloc[-1]) if len(df) else None,
        "grid": grid, "grid_ranked_by_net": grid_sorted, "run_date": str(date.today())}


def _format_exit_grid(out: Dict[str, Any]) -> str:
    lines = [
        f"ict_scalp exit-grid — {out['symbol']} {out['timeframe']}  "
        f"({out['data_start']} → {out['data_end']})",
        f"  entries pool: {out['entries_pool']}  fee {out['fee_bps_roundtrip']}bps rt",
        f"  {'tp_at_r':>7} {'be':>5} {'trades':>7} {'wr%':>6} {'gross_r':>9} {'net_r':>9} {'net_exp':>8} {'maxdd':>7}",
    ]
    for g in out["grid_ranked_by_net"]:
        be = "off" if g["be_trigger_r"] is None else str(g["be_trigger_r"])
        lines.append(
            f"  {g['tp_at_r']:>7} {be:>5} {g['total_trades']:>7} {g['win_rate_pct']:>6} "
            f"{g['total_r']:>9} {g['net_total_r']:>9} {g['net_expectancy_r']:>8} {g['max_drawdown_r']:>7}")
    return "\n".join(lines)


def run_entry_grid(df, *, cfg_overrides, timeframe, symbol, warmup_bars,
                   timeout_bars, cooldown_bars, htf_rule, htf_ema_period,
                   disp_grid):
    """Entry-selectivity sweep: vary displacement_atr_mult (the core
    entry-quality knob) — each value is a full entry+exit pass at the
    strategy's live exit config. Tests whether fewer/higher-quality
    entries lift net-of-fee (the 2024 over-trading hypothesis)."""
    rows: List[Dict[str, Any]] = []
    for disp in disp_grid:
        cfg = dict(cfg_overrides)
        cfg["displacement_atr_mult"] = disp
        s = run_backtest(df, cfg_overrides=cfg, timeframe=timeframe,
                         symbol=symbol, warmup_bars=warmup_bars,
                         timeout_bars=timeout_bars, cooldown_bars=cooldown_bars,
                         htf_rule=htf_rule, htf_ema_period=htf_ema_period)
        rows.append({
            "displacement_atr_mult": disp,
            "total_trades": s["total_trades"], "win_rate_pct": s["win_rate_pct"],
            "total_r": s["total_r"], "net_total_r": s["net_total_r"],
            "net_expectancy_r": s["net_expectancy_r"],
            "total_fee_r": s["total_fee_r"], "max_drawdown_r": s["max_drawdown_r"]})
    return {
        "strategy": "ict_scalp_5m", "symbol": symbol, "timeframe": timeframe,
        "fee_bps_roundtrip": FEE_BPS_ROUNDTRIP,
        "data_start": str(df["timestamp"].iloc[0]) if len(df) else None,
        "data_end": str(df["timestamp"].iloc[-1]) if len(df) else None,
        "entry_grid": rows, "run_date": str(date.today())}


def _format_entry_grid(out: Dict[str, Any]) -> str:
    lines = [
        f"ict_scalp entry-selectivity grid — {out['symbol']} {out['timeframe']}  "
        f"({out['data_start']} → {out['data_end']})  fee {out['fee_bps_roundtrip']}bps",
        f"  {'disp_atr':>8} {'trades':>7} {'wr%':>6} {'gross_r':>9} {'net_r':>9} {'net_exp':>8} {'maxdd':>7}",
    ]
    for g in out["entry_grid"]:
        lines.append(
            f"  {g['displacement_atr_mult']:>8} {g['total_trades']:>7} {g['win_rate_pct']:>6} "
            f"{g['total_r']:>9} {g['net_total_r']:>9} {g['net_expectancy_r']:>8} {g['max_drawdown_r']:>7}")
    return "\n".join(lines)


def main(argv: List[str]) -> int:
    global FEE_BPS_ROUNDTRIP  # set from --fee-bps-roundtrip after parse
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
    p.add_argument("--fee-bps-roundtrip", type=float, default=FEE_BPS_ROUNDTRIP,
                   help="Round-trip taker fee bps for net-of-fee R (default 7.5; 0=gross).")
    p.add_argument("--ignore-yaml", action="store_true",
                   help="Ignore config/strategies.yaml; use unit defaults only.")
    p.add_argument("--exit-grid", action="store_true",
                   help="Variation sweep: one entry pass x many exit variations "
                        "(tp_at_r x break-even), net-of-fee, ranked by net R.")
    p.add_argument("--entry-grid", action="store_true",
                   help="Entry-selectivity sweep: vary displacement_atr_mult "
                        "(full pass each), net-of-fee — tests over-trading.")
    p.add_argument("--displacement", type=float, default=None,
                   help="Override displacement_atr_mult for a single run "
                        "(e.g. 1.6, the S6 robust setting).")
    args = p.parse_args(argv[1:])
    FEE_BPS_ROUNDTRIP = args.fee_bps_roundtrip

    try:
        df = _load_candles(args.data)
    except Exception as exc:
        print(f"ERROR: failed to load candles from {args.data}: {exc}", file=sys.stderr)
        return 1

    cfg_overrides = {} if args.ignore_yaml else _load_yaml_params()
    if args.displacement is not None:
        cfg_overrides["displacement_atr_mult"] = args.displacement

    if args.exit_grid:
        try:
            out = run_exit_grid(
                df, cfg_overrides=cfg_overrides, timeframe=args.timeframe,
                symbol=args.symbol, warmup_bars=int(args.warmup_bars),
                timeout_bars=int(args.timeout_bars), htf_rule=str(args.htf_rule),
                htf_ema_period=int(args.htf_ema_period),
                tp_grid=[1.0, 1.5, 2.0, 2.5, 3.0], be_grid=[None, 0.5, 1.0])
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR: exit-grid failed: {exc}", file=sys.stderr)
            return 1
        print(_format_exit_grid(out))
        if args.json_out:
            payload = json.dumps(out, indent=2, default=str)
            if args.json_out == "-":
                print(payload)
            else:
                Path(args.json_out).write_text(payload)
                print(f"\nJSON written to {args.json_out}", file=sys.stderr)
        return 0

    if args.entry_grid:
        try:
            out = run_entry_grid(
                df, cfg_overrides=cfg_overrides, timeframe=args.timeframe,
                symbol=args.symbol, warmup_bars=int(args.warmup_bars),
                timeout_bars=int(args.timeout_bars), cooldown_bars=int(args.cooldown_bars),
                htf_rule=str(args.htf_rule), htf_ema_period=int(args.htf_ema_period),
                disp_grid=[1.3, 1.6, 2.0, 2.5, 3.0])
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR: entry-grid failed: {exc}", file=sys.stderr)
            return 1
        print(_format_entry_grid(out))
        if args.json_out:
            payload = json.dumps(out, indent=2, default=str)
            if args.json_out == "-":
                print(payload)
            else:
                Path(args.json_out).write_text(payload)
                print(f"\nJSON written to {args.json_out}", file=sys.stderr)
        return 0

    try:
        summary = run_backtest(
            df,
            cfg_overrides=cfg_overrides,
            timeframe=args.timeframe,
            symbol=args.symbol,
            warmup_bars=int(args.warmup_bars),
            timeout_bars=int(args.timeout_bars),
            cooldown_bars=int(args.cooldown_bars),
            htf_rule=str(args.htf_rule),
            htf_ema_period=int(args.htf_ema_period),
        )
    except Exception as exc:
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
