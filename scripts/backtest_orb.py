#!/usr/bin/env python3
"""Opening-Range Breakout (ORB) backtest — index-futures, 5-min RTH bars.

A different ENTRY TRIGGER than the price-channel / squeeze strategies: the
classic opening-range breakout. Each RTH session (CME equity-index pit hours
09:30-16:00 America/New_York) forms an Opening Range from its first N bars
(OR_high / OR_low). After the OR is formed, the FIRST bar to CLOSE beyond the
range triggers an entry in the breakout direction; one trade per day, flat by
the session close (no overnight holds, ever).

Thesis: the opening auction sets a balance area; a decisive break of that
range early in RTH tends to continue intraday. A too-wide OR (range >
1.5*ATR) is a no-trade day — there's no clean range to break.

Entry  : first bar whose close > OR_high => LONG @ close; close < OR_low =>
         SHORT @ close. Whichever fires first wins. No entry on the last bar.
Stop   : opposite_or (long=OR_low / short=OR_high) or atr (entry ∓ mult*ATR).
Exit   : (a) SL-first intrabar; else (b) time-stop flat at the session's last
         RTH bar close ("rth_close"). risk in POINTS.
Fees   : modelled in POINTS round-trip (futures), not bps.

Net-of-fee, long/short split, by-year, month-over-month consistency. Ships a
P5 validation gate (k-fold + holdout: PF / daily-Sharpe / multi-year) and a
walk-forward N selector. Research only (Tier-1) — reads OHLCV CSV/Parquet,
writes JSON/text/JSONL; never touches the order path or live config.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass
from datetime import date, time as dtime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

NY_TZ = ZoneInfo("America/New_York")
FEE_POINTS_ROUNDTRIP = 0.5
TRADING_DAYS_PER_YEAR = 252
# ATR smoothing: Wilder (EMA alpha=1/period) by default; set False (--atr-simple)
# for a simple rolling mean of TR. Module-global so the pure collectors don't
# need an extra param threaded through every signature; set once in main().
_ATR_WILDER = True


@dataclass
class Trade:
    entry_index: int
    entry_time: Any
    direction: str
    entry: float
    sl: float
    risk: float  # in POINTS
    exit_index: int
    exit_time: Any
    exit_price: float
    outcome: str
    r_multiple: float
    mfe_r: float
    session_date: Any = None  # NY calendar date of the session
    or_bars: int = 0          # OR width used for this trade (walk-forward bookkeeping)


# --------------------------------------------------------------------------- #
# Data loading
# --------------------------------------------------------------------------- #
def _load_candles(path: str) -> pd.DataFrame:
    if path.endswith(".parquet"):
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path)
    cols = {c.lower(): c for c in df.columns}
    need = ["timestamp", "open", "high", "low", "close", "volume"]
    df = df.rename(columns={cols[c]: c for c in need if c in cols and cols[c] != c})
    # tz-aware UTC; a naive timestamp is assumed UTC (utc=True localizes it).
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    if "volume" not in df.columns:
        df["volume"] = 0.0
    return df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)


def _date_filter(df: pd.DataFrame, start: Optional[str], end: Optional[str]) -> pd.DataFrame:
    if start:
        df = df[df["timestamp"] >= pd.Timestamp(start, tz="UTC")]
    if end:
        df = df[df["timestamp"] <= pd.Timestamp(end, tz="UTC")]
    return df.reset_index(drop=True)


def _parse_hhmm(s: str) -> dtime:
    s = s.strip()
    parts = s.split(":")
    return dtime(int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)


def _to_rth(df: pd.DataFrame, session_start: dtime, session_end: dtime) -> pd.DataFrame:
    """Convert to NY time, filter to [session_start, session_end), add ny_time
    + session_date (NY calendar date) columns. zoneinfo handles DST."""
    out = df.copy()
    ny = out["timestamp"].dt.tz_convert(NY_TZ)
    out["ny_dt"] = ny
    out["ny_time"] = ny.dt.time
    out["session_date"] = ny.dt.date
    mask = (out["ny_time"] >= session_start) & (out["ny_time"] < session_end)
    return out[mask].reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Indicators
# --------------------------------------------------------------------------- #
def _atr(df: pd.DataFrame, period: int, wilder: Optional[bool] = None) -> pd.Series:
    """ATR on the 5-min bars. Wilder's smoothing by default (EMA alpha=1/period);
    pass wilder=False (or --atr-simple, which sets the module default) for a
    simple rolling mean of TR. Computed over the full (RTH-filtered) frame in
    time order; the per-day stop reads the value AS OF the OR-formation bar."""
    use_wilder = _ATR_WILDER if wilder is None else wilder
    h, low, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([(h - low), (h - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    if use_wilder:
        return tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    return tr.rolling(period, min_periods=period).mean()


# --------------------------------------------------------------------------- #
# Core: one session -> at most one trade
# --------------------------------------------------------------------------- #
def _simulate_session(g: pd.DataFrame, *, or_bars: int, atr_period: int,
                      max_or_width_atr: float, stop_mode: str,
                      atr_stop_mult: float) -> Optional[Trade]:
    """Simulate a single RTH session (rows in time order, already RTH-filtered).
    Returns the Trade or None (skipped: too few bars / OR too wide / no break).
    Pure: never raises on a short/empty session — returns None."""
    n = len(g)
    if n <= or_bars:  # need OR bars + at least one bar to break out
        return None

    or_slice = g.iloc[:or_bars]
    or_high = float(or_slice["high"].max())
    or_low = float(or_slice["low"].min())
    if not (math.isfinite(or_high) and math.isfinite(or_low)) or or_high <= or_low:
        return None

    # ATR as of the OR-formation bar (the close of the Nth bar = index or_bars-1).
    atr = g["atr"].iloc[or_bars - 1]
    if atr is None or pd.isna(atr) or float(atr) <= 0:
        return None
    atr = float(atr)

    # Skip-day: OR too wide relative to ATR.
    if (or_high - or_low) > max_or_width_atr * atr:
        return None

    # Last RTH bar index of this session (time-stop target).
    last_idx = n - 1

    # Scan bars AFTER the OR for the first breakout close. No entry on the last
    # bar (nothing left to exit into / would be a zero-bar hold).
    entry_local = None
    direction = None
    for k in range(or_bars, last_idx):  # exclude the final bar as an entry bar
        close_k = float(g["close"].iloc[k])
        if close_k > or_high:
            entry_local, direction = k, "long"
            break
        if close_k < or_low:
            entry_local, direction = k, "short"
            break
    if entry_local is None:
        return None

    entry = float(g["close"].iloc[entry_local])
    if stop_mode == "atr":
        sl = entry - atr_stop_mult * atr if direction == "long" else entry + atr_stop_mult * atr
    else:  # opposite_or
        sl = or_low if direction == "long" else or_high
    risk = abs(entry - sl)
    if risk <= 0:
        return None

    # Exit scan: SL-first intrabar; else flat at the last RTH bar close.
    exit_price: Optional[float] = None
    exit_local = last_idx
    outcome = "rth_close"
    mfe = 0.0
    for j in range(entry_local + 1, last_idx + 1):
        bh, bl = float(g["high"].iloc[j]), float(g["low"].iloc[j])
        if direction == "long":
            if bl <= sl:
                exit_price, exit_local, outcome = sl, j, "stop"
                break
            mfe = max(mfe, (bh - entry) / risk)
        else:
            if bh >= sl:
                exit_price, exit_local, outcome = sl, j, "stop"
                break
            mfe = max(mfe, (entry - bl) / risk)
    if exit_price is None:  # time-stop at session close
        exit_price = float(g["close"].iloc[last_idx])
        exit_local = last_idx
        outcome = "rth_close"

    r = ((exit_price - entry) / risk if direction == "long"
         else (entry - exit_price) / risk)

    return Trade(
        entry_index=int(g.index[entry_local]),
        entry_time=g["timestamp"].iloc[entry_local],
        direction=direction, entry=entry, sl=sl, risk=risk,
        exit_index=int(g.index[exit_local]),
        exit_time=g["timestamp"].iloc[exit_local], exit_price=exit_price,
        outcome=outcome, r_multiple=round(r, 4), mfe_r=round(mfe, 3),
        session_date=g["session_date"].iloc[0], or_bars=or_bars)


def run_backtest(df_rth: pd.DataFrame, *, or_bars: int, atr_period: int,
                 max_or_width_atr: float, stop_mode: str, atr_stop_mult: float,
                 fee_points_roundtrip: float, timeframe: str, symbol: str,
                 emit_path: Optional[str] = None,
                 fee_multiplier: float = 1.0) -> Dict[str, Any]:
    """Run ORB over the RTH-filtered frame (one trade per NY session)."""
    trades = _collect_trades(
        df_rth, or_bars=or_bars, atr_period=atr_period,
        max_or_width_atr=max_or_width_atr, stop_mode=stop_mode,
        atr_stop_mult=atr_stop_mult)
    if emit_path:
        Path(emit_path).parent.mkdir(parents=True, exist_ok=True)
        with open(emit_path, "w", encoding="utf-8") as fh:
            for t in trades:
                fr = _fee_r(t, fee_points_roundtrip, fee_multiplier)
                fh.write(json.dumps({
                    "strategy": "orb", "entry_time": str(t.entry_time),
                    "direction": t.direction, "gross_r": t.r_multiple,
                    "net_r": round(t.r_multiple - fr, 4),
                    "or_bars": t.or_bars}, default=str) + "\n")
    return _summarize(trades, df_rth, timeframe=timeframe, symbol=symbol,
                      fee_points_roundtrip=fee_points_roundtrip,
                      fee_multiplier=fee_multiplier,
                      params={"or_bars": or_bars, "atr_period": atr_period,
                              "max_or_width_atr": max_or_width_atr,
                              "stop_mode": stop_mode, "atr_stop_mult": atr_stop_mult})


def _collect_trades(df_rth: pd.DataFrame, *, or_bars: int, atr_period: int,
                    max_or_width_atr: float, stop_mode: str,
                    atr_stop_mult: float) -> List[Trade]:
    """RTH frame -> list[Trade]. ATR is computed once over the full RTH series
    (preserves the index so per-session slices read the right value)."""
    if df_rth.empty:
        return []
    work = df_rth.copy()
    work["atr"] = _atr(work, atr_period)
    trades: List[Trade] = []
    for _sd, g in work.groupby("session_date", sort=True):
        g = g.sort_values("timestamp")
        # keep the global index intact, but use positional .iloc inside the sim
        t = _simulate_session(
            g.reset_index(drop=False).rename(columns={"index": "_gidx"}),
            or_bars=or_bars, atr_period=atr_period,
            max_or_width_atr=max_or_width_atr, stop_mode=stop_mode,
            atr_stop_mult=atr_stop_mult)
        if t is not None:
            trades.append(t)
    trades.sort(key=lambda x: pd.Timestamp(x.entry_time))
    return trades


def _fee_r(t: Trade, fee_points_roundtrip: float, fee_multiplier: float = 1.0) -> float:
    """Round-trip fee expressed in R: fee_points / risk_points."""
    if t.risk <= 0:
        return 0.0
    return (fee_points_roundtrip * fee_multiplier) / t.risk


# --------------------------------------------------------------------------- #
# Summary / reporting
# --------------------------------------------------------------------------- #
def _summarize(trades: List[Trade], df_rth: pd.DataFrame, *, timeframe, symbol,
               fee_points_roundtrip, fee_multiplier, params) -> Dict[str, Any]:
    n = len(trades)
    base: Dict[str, Any] = {
        "strategy": "orb", "symbol": symbol, "timeframe": timeframe,
        "params": params, "total_trades": n,
        "fee_points_roundtrip": fee_points_roundtrip,
        "fee_multiplier": fee_multiplier,
        "data_start": str(df_rth["timestamp"].iloc[0]) if len(df_rth) else None,
        "data_end": str(df_rth["timestamp"].iloc[-1]) if len(df_rth) else None,
        "run_date": str(date.today())}
    if n == 0:
        base.update({"win_rate_pct": 0.0, "net_total_r": 0.0,
                     "net_total_r_long": 0.0, "net_total_r_short": 0.0,
                     "net_expectancy_r": 0.0, "trades_long": 0, "trades_short": 0,
                     "max_drawdown_r": 0.0, "by_outcome": {}, "by_year": {},
                     "consistency": None})
        return base
    net = [t.r_multiple - _fee_r(t, fee_points_roundtrip, fee_multiplier) for t in trades]
    wins = [r for r in net if r > 0]
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
    for t, nr in zip(trades, net):
        yr = str(pd.Timestamp(t.entry_time).year)
        slot = by_year.setdefault(yr, {"trades": 0, "net_r": 0.0})
        slot["trades"] += 1
        slot["net_r"] = round(slot["net_r"] + nr, 4)
    try:
        from scripts.ops.consistency import monthly_consistency
        consistency = monthly_consistency(
            (t.entry_time, nr) for t, nr in zip(trades, net))
    except ImportError:
        consistency = None
    base.update({
        "win_rate_pct": round(100 * len(wins) / n, 2),
        "net_total_r": round(sum(net), 4),
        "net_total_r_long": round(sum(
            t.r_multiple - _fee_r(t, fee_points_roundtrip, fee_multiplier)
            for t in longs), 4),
        "net_total_r_short": round(sum(
            t.r_multiple - _fee_r(t, fee_points_roundtrip, fee_multiplier)
            for t in shorts), 4),
        "net_expectancy_r": round(sum(net) / n, 4),
        "trades_long": len(longs), "trades_short": len(shorts),
        "max_drawdown_r": round(mdd, 4), "by_outcome": by, "by_year": by_year,
        "consistency": consistency})
    return base


def _fmt(s: Dict[str, Any]) -> str:
    lines = [f"orb — {s['symbol']} {s['timeframe']} {s.get('params')}",
             f"  data {s.get('data_start')} -> {s.get('data_end')}  "
             f"trades={s['total_trades']}  fee_pts_rt={s.get('fee_points_roundtrip')}"
             f"x{s.get('fee_multiplier')}"]
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


# --------------------------------------------------------------------------- #
# P5 validation gate
# --------------------------------------------------------------------------- #
def _daily_net_returns(trades: List[Trade], fee_points_roundtrip: float,
                       fee_multiplier: float) -> List[Tuple[Any, float]]:
    """Group net-R by NY session date -> [(date, net_r_for_day), ...] sorted."""
    by_day: Dict[Any, float] = {}
    for t in trades:
        sd = t.session_date
        nr = t.r_multiple - _fee_r(t, fee_points_roundtrip, fee_multiplier)
        by_day[sd] = by_day.get(sd, 0.0) + nr
    return sorted(by_day.items(), key=lambda kv: kv[0])


def _profit_factor(net: List[float]) -> Optional[float]:
    gross_win = sum(r for r in net if r > 0)
    gross_loss = -sum(r for r in net if r < 0)
    if gross_loss <= 0:
        return float("inf") if gross_win > 0 else None
    return gross_win / gross_loss


def _annualized_daily_sharpe(daily: List[float]) -> Optional[float]:
    if len(daily) < 2:
        return None
    mean = sum(daily) / len(daily)
    var = sum((x - mean) ** 2 for x in daily) / (len(daily) - 1)
    sd = math.sqrt(var)
    if sd <= 0:
        return None
    return (mean / sd) * math.sqrt(TRADING_DAYS_PER_YEAR)


def _fold_metrics(trades: List[Trade], fee_points_roundtrip: float,
                  fee_multiplier: float) -> Dict[str, Any]:
    net = [t.r_multiple - _fee_r(t, fee_points_roundtrip, fee_multiplier) for t in trades]
    daily = [v for _d, v in _daily_net_returns(trades, fee_points_roundtrip, fee_multiplier)]
    years = {}
    for t, nr in zip(trades, net):
        y = pd.Timestamp(t.entry_time).year
        years[y] = years.get(y, 0.0) + nr
    pos_years = sorted(y for y, v in years.items() if v > 0)
    pf = _profit_factor(net)
    return {
        "trades": len(trades),
        "net_total_r": round(sum(net), 4),
        "profit_factor": (round(pf, 4) if pf not in (None, float("inf")) else
                          ("inf" if pf == float("inf") else None)),
        "daily_sharpe": (round(_annualized_daily_sharpe(daily), 4)
                         if _annualized_daily_sharpe(daily) is not None else None),
        "positive_years": pos_years,
        "n_positive_years": len(pos_years)}


def run_gate(trades: List[Trade], *, kfolds: int, holdout_frac: float,
             fee_points_roundtrip: float, fee_multiplier: float) -> Dict[str, Any]:
    """P5 gate: split by SESSION DATE, time-ordered. Holdout = most-recent
    holdout_frac of sessions; k-fold over the remaining in-sample by time order.

    PASS iff: PF>1.3 AND daily-Sharpe>0.7 in EVERY in-sample fold, AND holdout
    daily-Sharpe>=0.5, AND >=2 distinct fold-years contribute positive net_r
    (profit not concentrated in <2 years)."""
    reasons: List[str] = []
    # Sort trades by session date (stable, deterministic split).
    trades = sorted(trades, key=lambda t: (t.session_date, pd.Timestamp(t.entry_time)))
    sessions = sorted({t.session_date for t in trades})
    n_sessions = len(sessions)
    if n_sessions < max(kfolds + 1, 3):
        return {"passed": False, "reasons": ["insufficient sessions for gate"],
                "per_fold": [], "holdout": {}, "fee_multiplier_applied": fee_multiplier,
                "n_sessions": n_sessions}

    n_hold = max(1, int(round(holdout_frac * n_sessions)))
    insample_sessions = set(sessions[:n_sessions - n_hold])
    holdout_sessions = set(sessions[n_sessions - n_hold:])
    in_trades = [t for t in trades if t.session_date in insample_sessions]
    hold_trades = [t for t in trades if t.session_date in holdout_sessions]

    in_session_list = sorted(insample_sessions)
    # k-fold over in-sample sessions, contiguous time-ordered chunks.
    per_fold: List[Dict[str, Any]] = []
    fold_pass = True
    fold_years_positive = set()
    if len(in_session_list) < kfolds:
        kfolds = max(1, len(in_session_list))
    chunk = max(1, len(in_session_list) // kfolds)
    for f in range(kfolds):
        lo = f * chunk
        hi = (f + 1) * chunk if f < kfolds - 1 else len(in_session_list)
        fold_sess = set(in_session_list[lo:hi])
        ft = [t for t in in_trades if t.session_date in fold_sess]
        m = _fold_metrics(ft, fee_points_roundtrip, fee_multiplier)
        m["fold"] = f
        pf = m["profit_factor"]
        pf_val = float("inf") if pf == "inf" else (pf if pf is not None else 0.0)
        sh = m["daily_sharpe"] if m["daily_sharpe"] is not None else -999.0
        ok = (pf_val > 1.3) and (sh > 0.7)
        m["passed"] = ok
        if not ok:
            fold_pass = False
        fold_years_positive.update(m["positive_years"])
        per_fold.append(m)

    hold_m = _fold_metrics(hold_trades, fee_points_roundtrip, fee_multiplier)
    hold_sh = hold_m["daily_sharpe"] if hold_m["daily_sharpe"] is not None else -999.0

    if not fold_pass:
        reasons.append("a k-fold failed PF>1.3 AND daily-Sharpe>0.7")
    if hold_sh < 0.5:
        reasons.append(f"holdout daily-Sharpe {hold_m['daily_sharpe']} < 0.5")
    if len(fold_years_positive) < 2:
        reasons.append(
            f"profit concentrates in <2 years (positive years: "
            f"{sorted(fold_years_positive)})")

    passed = (fold_pass and hold_sh >= 0.5 and len(fold_years_positive) >= 2)
    if passed:
        reasons.append("all in-sample folds PF>1.3 & Sharpe>0.7; holdout "
                       "Sharpe>=0.5; >=2 positive years")
    return {
        "passed": passed, "reasons": reasons, "per_fold": per_fold,
        "holdout": hold_m, "fee_multiplier_applied": fee_multiplier,
        "n_sessions": n_sessions, "n_insample_sessions": len(in_session_list),
        "n_holdout_sessions": len(holdout_sessions),
        "distinct_positive_fold_years": sorted(fold_years_positive)}


def _fmt_gate(g: Dict[str, Any]) -> str:
    lines = [f"P5 gate (fee x{g.get('fee_multiplier_applied')}): "
             f"{'PASS' if g.get('passed') else 'FAIL'}",
             f"  sessions={g.get('n_sessions')} "
             f"insample={g.get('n_insample_sessions')} "
             f"holdout={g.get('n_holdout_sessions')}"]
    for m in g.get("per_fold", []):
        lines.append(
            f"  fold {m.get('fold')}: trades={m.get('trades')} "
            f"net_r={m.get('net_total_r')} PF={m.get('profit_factor')} "
            f"sharpe={m.get('daily_sharpe')} pos_years={m.get('positive_years')} "
            f"{'ok' if m.get('passed') else 'X'}")
    h = g.get("holdout", {})
    lines.append(f"  holdout: trades={h.get('trades')} net_r={h.get('net_total_r')} "
                 f"PF={h.get('profit_factor')} sharpe={h.get('daily_sharpe')}")
    for r in g.get("reasons", []):
        lines.append(f"  - {r}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Walk-forward N (or-bars) selection
# --------------------------------------------------------------------------- #
def run_walk_forward(df_rth: pd.DataFrame, *, or_bars_grid: List[int],
                     atr_period: int, max_or_width_atr: float, stop_mode: str,
                     atr_stop_mult: float, fee_points_roundtrip: float,
                     fee_multiplier: float, kfolds: int, holdout_frac: float,
                     n_segments: int, timeframe: str, symbol: str) -> Dict[str, Any]:
    """Expanding-window walk-forward over SESSIONS. Split the session timeline
    into n_segments contiguous folds. For each fold i>=1, train on sessions
    [0..start_of_fold_i): pick the or-bars N that maximized in-sample
    net_total_r; test on fold i with that N; concatenate the OOS trades.
    Report chosen-N-per-segment + stitched OOS metrics + gate over the OOS."""
    # Precompute per-N trades once (each is the full-history run for that N).
    trades_by_n: Dict[int, List[Trade]] = {}
    for n in or_bars_grid:
        trades_by_n[n] = _collect_trades(
            df_rth, or_bars=n, atr_period=atr_period,
            max_or_width_atr=max_or_width_atr, stop_mode=stop_mode,
            atr_stop_mult=atr_stop_mult)

    all_sessions = sorted({t.session_date for n in or_bars_grid for t in trades_by_n[n]})
    if not all_sessions:
        return {"strategy": "orb", "symbol": symbol, "timeframe": timeframe,
                "walk_forward": True, "or_bars_grid": or_bars_grid,
                "segments": [], "oos": _summarize(
                    [], df_rth, timeframe=timeframe, symbol=symbol,
                    fee_points_roundtrip=fee_points_roundtrip,
                    fee_multiplier=fee_multiplier, params={"walk_forward": True}),
                "oos_gate": {"passed": False, "reasons": ["no trades"]}}

    n_segments = max(2, min(n_segments, len(all_sessions)))
    chunk = max(1, len(all_sessions) // n_segments)
    seg_bounds: List[Tuple[int, int]] = []
    for f in range(n_segments):
        lo = f * chunk
        hi = (f + 1) * chunk if f < n_segments - 1 else len(all_sessions)
        seg_bounds.append((lo, hi))

    def _net_total(trs: List[Trade]) -> float:
        return sum(t.r_multiple - _fee_r(t, fee_points_roundtrip, fee_multiplier)
                   for t in trs)

    segments: List[Dict[str, Any]] = []
    oos_trades: List[Trade] = []
    for i in range(1, n_segments):  # fold 0 is pure train seed (no OOS)
        train_hi = seg_bounds[i][0]
        train_sessions = set(all_sessions[:train_hi])
        test_lo, test_hi = seg_bounds[i]
        test_sessions = set(all_sessions[test_lo:test_hi])

        # pick N maximizing in-sample net_total_r on the training sessions
        best_n, best_net = or_bars_grid[0], -float("inf")
        is_scores = {}
        for n in or_bars_grid:
            tr_in = [t for t in trades_by_n[n] if t.session_date in train_sessions]
            sc = _net_total(tr_in)
            is_scores[n] = round(sc, 4)
            if sc > best_net:
                best_net, best_n = sc, n

        # apply chosen N out-of-sample
        tr_oos = [t for t in trades_by_n[best_n] if t.session_date in test_sessions]
        oos_trades.extend(tr_oos)
        segments.append({
            "segment": i, "chosen_or_bars": best_n,
            "in_sample_net_total_r_by_n": is_scores,
            "train_sessions": len(train_sessions),
            "test_sessions": len(test_sessions),
            "oos_trades": len(tr_oos),
            "oos_net_total_r": round(_net_total(tr_oos), 4),
            "test_window": [str(all_sessions[test_lo]),
                            str(all_sessions[test_hi - 1])]})

    oos_trades.sort(key=lambda t: (t.session_date, pd.Timestamp(t.entry_time)))
    oos_summary = _summarize(
        oos_trades, df_rth, timeframe=timeframe, symbol=symbol,
        fee_points_roundtrip=fee_points_roundtrip, fee_multiplier=fee_multiplier,
        params={"walk_forward": True, "or_bars_grid": or_bars_grid})
    oos_gate = run_gate(
        oos_trades, kfolds=kfolds, holdout_frac=holdout_frac,
        fee_points_roundtrip=fee_points_roundtrip, fee_multiplier=fee_multiplier)
    return {"strategy": "orb", "symbol": symbol, "timeframe": timeframe,
            "walk_forward": True, "or_bars_grid": or_bars_grid,
            "n_segments": n_segments, "segments": segments,
            "chosen_or_bars_per_segment": [s["chosen_or_bars"] for s in segments],
            "oos": oos_summary, "oos_gate": oos_gate}


def _fmt_wf(w: Dict[str, Any]) -> str:
    lines = [f"orb walk-forward — {w['symbol']} {w['timeframe']} "
             f"grid={w['or_bars_grid']} segments={w.get('n_segments')}"]
    for s in w.get("segments", []):
        lines.append(
            f"  seg {s['segment']}: chose N={s['chosen_or_bars']} "
            f"(IS net_r by N={s['in_sample_net_total_r_by_n']}) "
            f"-> OOS trades={s['oos_trades']} net_r={s['oos_net_total_r']} "
            f"win={s['test_window']}")
    lines.append(f"  chosen-N-per-segment: {w.get('chosen_or_bars_per_segment')}")
    o = w.get("oos", {})
    lines.append(f"  stitched OOS: trades={o.get('total_trades')} "
                 f"win_rate={o.get('win_rate_pct')}% net_r={o.get('net_total_r')} "
                 f"exp={o.get('net_expectancy_r')} maxdd_r={o.get('max_drawdown_r')}")
    lines.append("  OOS " + _fmt_gate(w.get("oos_gate", {})).replace("\n", "\n  "))
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(description="Opening-Range Breakout (ORB) backtest.")
    p.add_argument("--data", default=os.environ.get("BACKTEST_DATA_PATH",
                                                     "data/backtest_candles.csv"))
    p.add_argument("--timeframe", default="5m")
    p.add_argument("--symbol", default="MES")
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--session-start", default="09:30", help="RTH start (NY).")
    p.add_argument("--session-end", default="16:00", help="RTH end (NY), exclusive.")
    p.add_argument("--or-bars", type=int, default=3,
                   help="Opening-range size (# of first session bars).")
    p.add_argument("--atr-period", type=int, default=14)
    p.add_argument("--atr-simple", action="store_true",
                   help="Use simple-mean TR instead of Wilder ATR.")
    p.add_argument("--max-or-width-atr", type=float, default=1.5,
                   help="Skip-day: OR width > this * ATR => no trade.")
    p.add_argument("--stop-mode", choices=["opposite_or", "atr"], default="opposite_or")
    p.add_argument("--atr-stop-mult", type=float, default=0.5,
                   help="ATR stop multiple (stop_mode=atr).")
    p.add_argument("--fee-points-roundtrip", type=float, default=FEE_POINTS_ROUNDTRIP,
                   help="Round-trip fee+slippage in POINTS (MES tick=0.25pt).")
    p.add_argument("--fee-multiplier", type=float, default=1.0,
                   help="Scale the fee (gate runs at 2.0 per spec).")
    # gate
    p.add_argument("--gate", action="store_true", help="Run the P5 validation gate.")
    p.add_argument("--kfolds", type=int, default=5)
    p.add_argument("--holdout-frac", type=float, default=0.2)
    # walk-forward
    p.add_argument("--walk-forward-or-bars", default=None, metavar="CSV",
                   help="Walk-forward N selection over a CSV grid, e.g. '1,3,6'.")
    p.add_argument("--wf-segments", type=int, default=5,
                   help="# of contiguous walk-forward segments.")
    p.add_argument("--json", dest="json_out", default=None)
    p.add_argument("--emit-trades", default=None)
    a = p.parse_args(argv[1:])

    wilder = not a.atr_simple
    try:
        df = _load_candles(a.data)
        df = _date_filter(df, a.start, a.end)
        df_rth = _to_rth(df, _parse_hhmm(a.session_start), _parse_hhmm(a.session_end))
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: load failed: {exc}", file=sys.stderr)
        return 1

    global _ATR_WILDER
    _ATR_WILDER = wilder  # threaded into _atr (read at compute time)

    if a.walk_forward_or_bars:
        grid = [int(x) for x in a.walk_forward_or_bars.split(",") if x.strip()]
        out = run_walk_forward(
            df_rth, or_bars_grid=grid, atr_period=a.atr_period,
            max_or_width_atr=a.max_or_width_atr, stop_mode=a.stop_mode,
            atr_stop_mult=a.atr_stop_mult, fee_points_roundtrip=a.fee_points_roundtrip,
            fee_multiplier=a.fee_multiplier, kfolds=a.kfolds,
            holdout_frac=a.holdout_frac, n_segments=a.wf_segments,
            timeframe=a.timeframe, symbol=a.symbol)
        print(_fmt_wf(out))
    else:
        # When --gate is set, evaluate at the given fee-multiplier internally.
        out = run_backtest(
            df_rth, or_bars=a.or_bars, atr_period=a.atr_period,
            max_or_width_atr=a.max_or_width_atr, stop_mode=a.stop_mode,
            atr_stop_mult=a.atr_stop_mult, fee_points_roundtrip=a.fee_points_roundtrip,
            fee_multiplier=a.fee_multiplier, timeframe=a.timeframe, symbol=a.symbol,
            emit_path=a.emit_trades)
        print(_fmt(out))
        if a.gate:
            trades = _collect_trades(
                df_rth, or_bars=a.or_bars, atr_period=a.atr_period,
                max_or_width_atr=a.max_or_width_atr, stop_mode=a.stop_mode,
                atr_stop_mult=a.atr_stop_mult)
            gate = run_gate(
                trades, kfolds=a.kfolds, holdout_frac=a.holdout_frac,
                fee_points_roundtrip=a.fee_points_roundtrip,
                fee_multiplier=a.fee_multiplier)
            out["gate"] = gate
            print(_fmt_gate(gate))

    if a.json_out:
        payload = json.dumps(out, indent=2, default=str)
        if a.json_out == "-":
            print(payload)
        else:
            Path(a.json_out).write_text(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
