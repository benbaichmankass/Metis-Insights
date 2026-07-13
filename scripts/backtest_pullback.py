#!/usr/bin/env python3
"""HTF trend-pullback continuation backtest harness (research, Tier-1).

Mirrors the live unit ``src/units/strategies/htf_pullback_trend_2h.py``:
in an established Donchian-midline trend, enter on a short-term pullback into
the lower (long) / upper (short) ``pullback_frac`` of the recent
``pullback_lookback`` range, on a confirmation bar; exit via the shared
Chandelier ATR trail (the same trail trend/fade/squeeze use), SL-first
intrabar. No fixed profit target — the trail is the sole profit exit; the
``tp_r`` (~50R) sentinel is parked far from price.

Driven by the EXACT live params from ``config/strategies.yaml``
(``trend_lookback=40, pullback_lookback=10, pullback_frac=0.5,
atr_period=14, atr_stop_mult=2.5, trail_mult=5.0``).

Writes the shared per-trade JSONL schema (``{strategy, entry_time, direction,
gross_r, net_r, confidence}``) via ``--emit-trades`` so
``scripts/research/regime_tag_emitted.py`` can drop the row into the regime
matrix without engine-specific glue.

PERF-20260601-004 (regime-roster coverage gap).
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


def _adx(df: pd.DataFrame, period: int) -> pd.Series:
    """Wilder's Average Directional Index (regime-strength filter, shared lever).

    Standard construction: +DM/-DM from the directional moves, true range,
    Wilder-smoothed (EWM with alpha=1/period) +DI/-DI, DX, then ADX as the
    Wilder-smoothed DX. ``min_periods`` leaves the warm-up bars NaN so an
    ADX band cannot admit an undefined-regime bar. Recombination-pool axis
    (SRQ-20260618-001/-002): the highest-value entry-regime lever.
    """
    h, low, c = df["high"], df["low"], df["close"]
    up = h.diff()
    down = -low.diff()
    plus_dm = ((up > down) & (up > 0)).astype(float) * up.clip(lower=0)
    minus_dm = ((down > up) & (down > 0)).astype(float) * down.clip(lower=0)
    pc = c.shift(1)
    tr = pd.concat([(h - low), (h - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    alpha = 1.0 / period
    atr_w = tr.ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    plus_di = 100.0 * plus_dm.ewm(alpha=alpha, adjust=False, min_periods=period).mean() / atr_w
    minus_di = 100.0 * minus_dm.ewm(alpha=alpha, adjust=False, min_periods=period).mean() / atr_w
    di_sum = (plus_di + minus_di).replace(0.0, float("nan"))
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    return dx.ewm(alpha=alpha, adjust=False, min_periods=period).mean()


def run_backtest(df: pd.DataFrame, *, trend_lookback: int, pullback_lookback: int,
                 pullback_frac: float, atr_period: int, atr_stop_mult: float,
                 trail_mult: float, timeout_bars: int, cooldown_bars: int,
                 timeframe: str, symbol: str,
                 emit_path: Optional[str] = None,
                 min_confidence: float = 0.0,
                 adx_min: Optional[float] = None,
                 adx_max: Optional[float] = None,
                 adx_period: int = 14,
                 stale_exit_bars: Optional[int] = None,
                 stale_exit_below_r: float = 0.0,
                 flip_exit_bars: Optional[int] = None,
                 bank_frac: float = 0.0,
                 bank_at_r: float = 1.0,
                 giveback_min_mfe_r: float = 0.0,
                 giveback_r: float = 1.0,
                 trail_decay_arm_r: float = 0.0,
                 trail_decay_stall_bars: int = 0,
                 trail_decay_tight_mult: float = 0.0,
                 confirm_bars: int = 0) -> Dict[str, Any]:
    df = df.reset_index(drop=True)
    df["atr"] = _atr(df, atr_period)
    # Trend filter: Donchian midline of the prior trend_lb bars (shift(1) — no
    # lookahead). Matches htf_pullback_trend_2h.order_package exactly.
    dc_hi = df["high"].rolling(trend_lookback).max().shift(1)
    dc_lo = df["low"].rolling(trend_lookback).min().shift(1)
    df["mid"] = (dc_hi + dc_lo) / 2.0
    # Recent range for the pullback test (prior pull_lb bars, shift(1)).
    df["pr_hi"] = df["high"].rolling(pullback_lookback).max().shift(1)
    df["pr_lo"] = df["low"].rolling(pullback_lookback).min().shift(1)
    # ADX regime filter (recombination lever): only computed/consulted when a
    # band is set, so the default (None/None) run is byte-identical to before.
    adx_active = adx_min is not None or adx_max is not None
    if adx_active:
        df["adx"] = _adx(df, adx_period)

    trades: List[Trade] = []
    n = len(df)
    # Warm-up start: ensure the trend/pullback/ATR indicators AND (when a band
    # is set) the ADX are defined. ADX needs ~2×period bars to converge.
    i = max(trend_lookback, pullback_lookback) + atr_period + 1
    if adx_active:
        i = max(i, adx_period + 1)
    next_idx = i
    while i < n - 1:
        if i < next_idx:
            i += 1
            continue
        atr = float(df["atr"].iloc[i])
        mid = df["mid"].iloc[i]
        rhi, rlo = df["pr_hi"].iloc[i], df["pr_lo"].iloc[i]
        if atr <= 0 or pd.isna(mid) or pd.isna(rhi) or pd.isna(rlo):
            i += 1
            continue
        mid, rhi, rlo = float(mid), float(rhi), float(rlo)
        rng = rhi - rlo
        if rng <= 0:
            i += 1
            continue
        c = float(df["close"].iloc[i])
        prev_c = float(df["close"].iloc[i - 1])
        pos = (c - rlo) / rng
        uptrend = c > mid
        downtrend = c < mid
        direction: Optional[str] = None
        depth = 0.0
        if uptrend and pos <= pullback_frac and c > prev_c:
            direction = "long"
            depth = (c - mid) / atr
        elif downtrend and pos >= (1 - pullback_frac) and c < prev_c:
            direction = "short"
            depth = (mid - c) / atr
        if direction is None:
            i += 1
            continue
        # Regime filter (recombination lever): admit the bar only if its ADX sits
        # inside the [adx_min, adx_max] band. A NaN (warm-up) ADX is never
        # admitted when any band is set. No-op when both bands are None.
        if adx_active:
            adx_val = float(df["adx"].iloc[i])
            if pd.isna(adx_val):
                i += 1
                continue
            if adx_min is not None and adx_val < adx_min:
                i += 1
                continue
            if adx_max is not None and adx_val > adx_max:
                i += 1
                continue
        confidence = round(min(max(depth, 0.0), 1.0), 4)
        if confidence < min_confidence:
            i += 1
            continue
        # M21 E-2 confirmation-bar lever (0 = off, byte-identical): the
        # trigger bar does not enter — the next ``confirm_bars`` closes must
        # each HOLD beyond the trigger close (continued resumption: above it
        # for longs, below for shorts); any failing close cancels the setup
        # (that bar is re-evaluated as a fresh trigger). Entry fires at the
        # Nth confirming close with THAT bar's ATR; the depth/confidence
        # gate stays at the trigger bar — same contract as the donchian
        # harness lever (scripts/research/backtest_trend.py --confirm-bars).
        if confirm_bars > 0:
            lvl = c
            cancel_at: Optional[int] = None
            mature = i + confirm_bars
            if mature > n - 1:
                break
            for k in range(i + 1, mature + 1):
                ck = float(df["close"].iloc[k])
                held = ck > lvl if direction == "long" else ck < lvl
                if not held:
                    cancel_at = k
                    break
            if cancel_at is not None:
                i = cancel_at
                continue
            i = mature
            atr = float(df["atr"].iloc[i])
            if atr <= 0:
                i += 1
                continue
            c = float(df["close"].iloc[i])
        entry = c
        sl = entry - atr_stop_mult * atr if direction == "long" else entry + atr_stop_mult * atr
        risk = abs(entry - sl)
        if risk <= 0:
            i += 1
            continue
        ext = entry
        ext_j = i
        trail = sl
        exit_price: Optional[float] = None
        exit_reason = "timeout"
        exit_idx = min(i + timeout_bars, n - 1)
        mfe = 0.0
        flip_streak = 0
        banked = False

        def _eff_tm(peak_px: float, peak_j: int, j: int) -> float:
            # M20 P4.1 trail-decay lever (tight_mult 0 = off, byte-identical):
            # tighten the trail mult once the move shows exhaustion — R-armed
            # (peak R >= arm_r) and/or stall-armed (>= stall_bars since the
            # last new favourable extreme; re-loosens the MULT on a new peak,
            # never the price-ratcheted stop). Design:
            # docs/research/M20-momentum-exhaustion-DESIGN.md § P4.1.
            if trail_decay_tight_mult <= 0.0:
                return trail_mult
            pr = ((peak_px - entry) if direction == "long"
                  else (entry - peak_px)) / risk
            if ((trail_decay_arm_r > 0.0 and pr >= trail_decay_arm_r)
                    or (trail_decay_stall_bars > 0
                        and (j - peak_j) >= trail_decay_stall_bars)):
                return trail_decay_tight_mult
            return trail_mult
        for j in range(i + 1, min(i + timeout_bars + 1, n)):
            bh, bl = float(df["high"].iloc[j]), float(df["low"].iloc[j])
            # M20 partial-TP bank lever (0=off, byte-identical): bank
            # `bank_frac` at entry ± bank_at_r × risk; remainder keeps the
            # trail. Rung credited only when its price actually printed.
            if bank_frac > 0.0 and not banked:
                if direction == "long" and bh >= entry + bank_at_r * risk:
                    banked = True
                elif direction == "short" and bl <= entry - bank_at_r * risk:
                    banked = True
            # M20 exit levers — both default-off (None) ⇒ byte-identical run.
            # Checked on bar close, AFTER the intrabar stop check below cannot
            # be pre-empted (stop-first stays conservative because the levers
            # only ever fire at the close of a bar the stop did NOT hit).
            bc = float(df["close"].iloc[j])
            open_r = ((bc - entry) / risk if direction == "long"
                      else (entry - bc) / risk)
            if flip_exit_bars is not None:
                bar_mid = df["mid"].iloc[j]
                if not pd.isna(bar_mid):
                    against = (bc < float(bar_mid)) if direction == "long" \
                        else (bc > float(bar_mid))
                    flip_streak = flip_streak + 1 if against else 0
            if direction == "long":
                if bl <= trail:                       # SL-first (conservative)
                    exit_price, exit_idx = trail, j
                    exit_reason = "trail_stop" if trail > sl else "stop"
                    break
                if bh > ext:
                    ext, ext_j = bh, j
                trail = max(trail, ext - _eff_tm(ext, ext_j, j) * atr)
                mfe = max(mfe, (ext - entry) / risk)
            else:
                if bh >= trail:
                    exit_price, exit_idx = trail, j
                    exit_reason = "trail_stop" if trail < sl else "stop"
                    break
                if bl < ext:
                    ext, ext_j = bl, j
                trail = min(trail, ext + _eff_tm(ext, ext_j, j) * atr)
                mfe = max(mfe, (entry - ext) / risk)
            # Lever exits fire at bar close, only when the stop did not hit
            # this bar (a stop hit breaks above) — stop-first stays intact.
            if giveback_min_mfe_r > 0.0:
                # M20 giveback-stop: once peak open profit >= min_mfe R, exit
                # when >= giveback_r R has been surrendered from the peak.
                if mfe >= giveback_min_mfe_r and (mfe - open_r) >= giveback_r:
                    exit_price, exit_idx = bc, j
                    exit_reason = "giveback_stop"
                    break
            if flip_exit_bars is not None and flip_streak >= flip_exit_bars:
                exit_price, exit_idx = bc, j
                exit_reason = "trend_flip"
                break
            if (stale_exit_bars is not None and (j - i) >= stale_exit_bars
                    and open_r < stale_exit_below_r):
                exit_price, exit_idx = bc, j
                exit_reason = "stale_stop"
                break
        if exit_price is None:
            exit_price = float(df["close"].iloc[exit_idx])
        r = ((exit_price - entry) / risk if direction == "long"
             else (entry - exit_price) / risk)
        if banked:
            r = bank_frac * bank_at_r + (1.0 - bank_frac) * r
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
                    "strategy": "htf_pullback_trend_2h", "symbol": symbol,
                    "entry_time": str(t.entry_time),
                    "direction": t.direction, "gross_r": t.r_multiple,
                    "net_r": round(t.r_multiple - fr, 4),
                    "confidence": t.confidence,
                    # M20 E0 exit-head dataset fields (additive — existing
                    # consumers .get() what they need): trade geometry so the
                    # builder can reconstruct the per-bar in-trade path.
                    "entry": t.entry, "sl": t.sl,
                    "exit_time": str(t.exit_time),
                    "mfe_r": t.mfe_r,
                    "exit_reason": t.outcome}, default=str) + "\n")
    params: Dict[str, Any] = {"trend_lookback": trend_lookback,
                              "pullback_lookback": pullback_lookback,
                              "pullback_frac": pullback_frac,
                              "atr_stop_mult": atr_stop_mult,
                              "trail_mult": trail_mult,
                              "min_confidence": min_confidence}
    if confirm_bars > 0:
        params["confirm_bars"] = confirm_bars
    if stale_exit_bars is not None:
        params["stale_exit_bars"] = stale_exit_bars
        params["stale_exit_below_r"] = stale_exit_below_r
    if flip_exit_bars is not None:
        params["flip_exit_bars"] = flip_exit_bars
    if bank_frac > 0.0:
        params["bank_frac"] = bank_frac
        params["bank_at_r"] = bank_at_r
    if adx_min is not None:
        params["adx_min"] = adx_min
    if adx_max is not None:
        params["adx_max"] = adx_max
    if adx_active:
        params["adx_period"] = adx_period
    return _summarize(trades, df, timeframe=timeframe, symbol=symbol, params=params)


def _fee_r(t: Trade) -> float:
    if not t.exit_price or t.risk <= 0:
        return 0.0
    return (FEE_BPS_ROUNDTRIP / 10_000.0) * ((t.entry + t.exit_price) / 2.0) / t.risk


def _summarize(trades: List[Trade], df: pd.DataFrame, *, timeframe: str,
               symbol: str, params: Dict[str, Any]) -> Dict[str, Any]:
    n = len(trades)
    base: Dict[str, Any] = {
        "strategy": "htf_pullback_trend_2h", "symbol": symbol, "timeframe": timeframe,
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
    for r in net:
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
    base.update({
        "win_rate_pct": round(100 * len(wins) / n, 2),
        "total_r": round(sum(rs), 4),
        "net_total_r": round(sum(net), 4),
        "net_total_r_long": round(sum(t.r_multiple - _fee_r(t) for t in longs), 4),
        "net_total_r_short": round(sum(t.r_multiple - _fee_r(t) for t in shorts), 4),
        "net_expectancy_r": round(sum(net) / n, 4),
        "trades_long": len(longs), "trades_short": len(shorts),
        "avg_win_r": round(sum(wins) / len(wins), 4) if wins else 0.0,
        "max_mfe_r": round(max(t.mfe_r for t in trades), 3),
        "max_drawdown_r": round(mdd, 4), "by_outcome": by, "by_year": by_year})
    return base


def _fmt(s: Dict[str, Any]) -> str:
    lines = [f"htf_pullback_trend_2h — {s['symbol']} {s['timeframe']} {s.get('params')}",
             f"  data {s.get('data_start')} -> {s.get('data_end')}  trades={s['total_trades']}"]
    if s["total_trades"]:
        lines += [
            f"  win_rate={s['win_rate_pct']}%  net_r={s['net_total_r']} "
            f"(exp {s['net_expectancy_r']}, L/S {s['trades_long']}/{s['trades_short']}, "
            f"netL/S {s.get('net_total_r_long')}/{s.get('net_total_r_short')})",
            f"  avg_win_r={s.get('avg_win_r')} max_mfe_r={s.get('max_mfe_r')} "
            f"maxdd_r={s['max_drawdown_r']} by={s['by_outcome']}",
            f"  by_year={s.get('by_year')}"]
    return "\n".join(lines)


def main(argv: List[str]) -> int:
    global FEE_BPS_ROUNDTRIP
    p = argparse.ArgumentParser(description="HTF pullback trend-continuation backtest (net-of-fee).")
    p.add_argument("--data", default=os.environ.get("BACKTEST_DATA_PATH", "data/backtest_candles.csv"))
    p.add_argument("--timeframe", default="2h")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--resample", default=None, help="Resample to this rule first (e.g. 2h, 4h).")
    p.add_argument("--start", default=None, help="Walk-forward window start (ISO date, inclusive).")
    p.add_argument("--end", default=None, help="Walk-forward window end (ISO date, inclusive).")
    p.add_argument("--trend-lookback", type=int, default=40,
                   help="Donchian window whose midline defines the trend (live default 40).")
    p.add_argument("--pullback-lookback", type=int, default=10,
                   help="Recent-range window for the pullback test (live default 10).")
    p.add_argument("--pullback-frac", type=float, default=0.5,
                   help="Close must sit in the lower/upper this fraction of the recent range (live default 0.5).")
    p.add_argument("--atr-period", type=int, default=14)
    p.add_argument("--atr-stop-mult", type=float, default=2.5,
                   help="Initial stop entry ∓ this × ATR (live default 2.5).")
    p.add_argument("--trail-mult", type=float, default=5.0,
                   help="Chandelier trail distance in ATR (live default 5.0).")
    p.add_argument("--timeout-bars", type=int, default=200)
    p.add_argument("--cooldown-bars", type=int, default=1)
    p.add_argument("--fee-bps-roundtrip", type=float, default=FEE_BPS_ROUNDTRIP)
    p.add_argument("--min-confidence", type=float, default=0.0,
                   help="Skip entries whose live-parity confidence (trend-depth/ATR) is below this.")
    p.add_argument("--adx-min", type=float, default=None,
                   help="Regime filter: skip entries whose Wilder ADX is below this (None=off).")
    p.add_argument("--adx-max", type=float, default=None,
                   help="Regime filter: skip entries whose Wilder ADX is above this (None=off).")
    p.add_argument("--adx-period", type=int, default=14,
                   help="Wilder ADX period for the regime filter (default 14).")
    p.add_argument("--stale-exit-bars", type=int, default=None,
                   help="M20 exit lever: close at bar N after entry when the open "
                        "R is below --stale-exit-below-r (None=off, legacy behaviour).")
    p.add_argument("--stale-exit-below-r", type=float, default=0.0,
                   help="Threshold R for --stale-exit-bars (default 0.0 = only cut "
                        "trades that are flat-or-losing at the check bar).")
    p.add_argument("--flip-exit-bars", type=int, default=None,
                   help="M20 exit lever: close when the close crosses the Donchian "
                        "trend midline AGAINST the position for this many consecutive "
                        "bars (None=off). The trend-invalidation exit.")
    p.add_argument("--bank-frac", type=float, default=0.0,
                   help="M20 partial-TP ladder lever: fraction of the position "
                        "banked at +bank_at_r R (0=off, legacy behaviour).")
    p.add_argument("--bank-at-r", type=float, default=1.0,
                   help="R-multiple of the bank rung for --bank-frac (default 1.0).")
    p.add_argument("--giveback-min-mfe-r", type=float, default=0.0,
                   help="M20 giveback-stop lever: arm once peak open profit reaches "
                        "this many R (0=off, legacy behaviour).")
    p.add_argument("--giveback-r", type=float, default=1.0,
                   help="R surrendered from the peak that triggers the exit (default 1.0).")
    p.add_argument("--trail-decay-arm-r", type=float, default=0.0,
                   help="M20 P4.1 trail-decay: tighten the trail once peak open profit "
                        "reaches this many R (0=off).")
    p.add_argument("--trail-decay-stall-bars", type=int, default=0,
                   help="M20 P4.1: tighten the trail after this many bars without a new "
                        "favourable extreme (0=off; mult re-loosens on a new peak).")
    p.add_argument("--trail-decay-tight-mult", type=float, default=0.0,
                   help="The tightened trail mult once armed (0 disables the lever, "
                        "byte-identical).")
    p.add_argument("--confirm-bars", type=int, default=0,
                   help="M21 E-2 entry lever (0=off): the next N closes must "
                        "each hold beyond the trigger close before entering.")
    p.add_argument("--json", dest="json_out", default=None)
    p.add_argument("--emit-trades", default=None, metavar="PATH",
                   help="Write per-trade {entry_time, net_r, confidence} JSONL for regime tagging.")
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
    out = run_backtest(df,
                       trend_lookback=args.trend_lookback,
                       pullback_lookback=args.pullback_lookback,
                       pullback_frac=args.pullback_frac,
                       atr_period=args.atr_period,
                       atr_stop_mult=args.atr_stop_mult,
                       trail_mult=args.trail_mult,
                       timeout_bars=args.timeout_bars,
                       cooldown_bars=args.cooldown_bars,
                       timeframe=args.timeframe,
                       symbol=args.symbol,
                       emit_path=args.emit_trades,
                       min_confidence=args.min_confidence,
                       adx_min=args.adx_min,
                       adx_max=args.adx_max,
                       adx_period=args.adx_period,
                       stale_exit_bars=args.stale_exit_bars,
                       stale_exit_below_r=args.stale_exit_below_r,
                       flip_exit_bars=args.flip_exit_bars,
                       bank_frac=args.bank_frac,
                       bank_at_r=args.bank_at_r,
                       giveback_min_mfe_r=args.giveback_min_mfe_r,
                       giveback_r=args.giveback_r,
                       trail_decay_arm_r=args.trail_decay_arm_r,
                       trail_decay_stall_bars=args.trail_decay_stall_bars,
                       trail_decay_tight_mult=args.trail_decay_tight_mult,
                       confirm_bars=args.confirm_bars)
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
