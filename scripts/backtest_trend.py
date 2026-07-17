#!/usr/bin/env python3
"""Donchian trend-follower backtest (confidence-sweep harness).

The literal INVERSE of ``scripts/backtest_fade.py``: where the fade SHORTS
a *failed* Donchian breakout (a pierce that snaps back inside), this BUYS a
*confirmed* one — a bar that closes BEYOND the prior-N-bar channel — and
rides it with a Chandelier ATR trail. It mirrors the live strategy
``src/units/strategies/trend_donchian.py`` so a backtest can sweep the same
``min_confidence`` entry gate the live builder would apply.

Entry  : Donchian(N) channel from the prior N bars (shift(1), no lookahead).
         close > dc_hi -> LONG ; close < dc_lo -> SHORT (confirmed break).
Stop   : entry ∓ atr_stop_mult × ATR (symmetric, wide + fee-efficient).
Exit   : Chandelier ATR trail (trail_mult × ATR off the since-entry extreme),
         SL-first intrabar (conservative). Timeout backstop. The live tp is a
         far ~50R sentinel — the trail is the sole profit-exit, so the
         backtest never targets it.
Confidence: breakout depth past the channel / ATR, clamped [0,1] — the EXACT
         live formula (trend_donchian.order_package). ``--min-confidence``
         skips entries below the floor; ``--confidence-sweep`` tabulates the
         net metrics per threshold so a PnL-optimal floor reads off directly.

Net-of-fee, long/short split, by-outcome, per-year. Research only (Tier-1),
not wired into live. Reads an OHLCV CSV or Parquet (optionally --resample
to a higher TF; optionally --start/--end for walk-forward windows).
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
    # Live-parity confidence (trend_donchian.order_package): breakout depth
    # past the channel / ATR, clamped [0,1].
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


def _directional_indicators(df: pd.DataFrame, period: int) -> tuple:
    """Wilder's +DI / -DI series (the DIRECTION half of the ADX family).

    Shared source of truth for both the ADX magnitude (``_adx``) and the
    direction-aware regime filter (``--direction-filter di``). ``+DI > -DI``
    means recent directional pressure is UP, ``-DI > +DI`` DOWN — the sign ADX
    throws away (see docs/research/M-regime-direction-filter-DESIGN.md).
    ``min_periods`` leaves the warm-up bars NaN so a filter never reads an
    undefined direction.
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
    return plus_di, minus_di


def _adx(df: pd.DataFrame, period: int) -> pd.Series:
    """Wilder's Average Directional Index (regime-strength filter, shared lever).

    Standard construction: +DI/-DI (via ``_directional_indicators``), DX, then
    ADX as the Wilder-smoothed DX. ``min_periods`` leaves the warm-up bars NaN so
    an ADX band cannot admit an undefined-regime bar. Recombination-pool axis
    (SRQ-20260618-001/-002): the highest-value entry-regime lever.
    """
    plus_di, minus_di = _directional_indicators(df, period)
    di_sum = (plus_di + minus_di).replace(0.0, float("nan"))
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    alpha = 1.0 / period
    return dx.ewm(alpha=alpha, adjust=False, min_periods=period).mean()


def run_backtest(df: pd.DataFrame, *, donchian: int, atr_period: int,
                 atr_stop_mult: float, trail_mult: float, timeout_bars: int,
                 cooldown_bars: int, timeframe: str, symbol: str,
                 emit_path: Optional[str] = None,
                 min_confidence: float = 0.0,
                 long_only: bool = False,
                 adx_min: Optional[float] = None,
                 adx_max: Optional[float] = None,
                 adx_period: int = 14,
                 direction_filter: str = "off") -> Dict[str, Any]:
    df = df.reset_index(drop=True)
    df["atr"] = _atr(df, atr_period)
    # Channel from the PRIOR N bars only (shift(1)) — no lookahead. Same
    # construction as the live strategy and the fade mirror.
    df["dc_hi"] = df["high"].rolling(donchian).max().shift(1)
    df["dc_lo"] = df["low"].rolling(donchian).min().shift(1)
    # ADX regime filter (recombination lever): only computed/consulted when a
    # band is set, so the default (None/None) run is byte-identical to before.
    adx_active = adx_min is not None or adx_max is not None
    if adx_active:
        df["adx"] = _adx(df, adx_period)
    # Direction-aware regime filter (Phase 2, BL-20260717-REGIME-COVERAGE-DEBT):
    # ADX is direction-blind, so skip a long in a DOWN regime / a short in an UP
    # regime. `off` = byte-identical (no series computed). `di` = Wilder +DI/-DI
    # sign; `slope` = sign of the Donchian channel-midline slope. NaN (warm-up)
    # read never skips (fail-permissive). See the design doc referenced above.
    direction_filter = str(direction_filter or "off").lower()
    dir_di_plus = dir_di_minus = dir_slope = None
    if direction_filter == "di":
        dir_di_plus, dir_di_minus = _directional_indicators(df, adx_period)
    elif direction_filter == "slope":
        dir_slope = ((df["dc_hi"] + df["dc_lo"]) / 2.0).diff()
    trades: List[Trade] = []
    n = len(df)
    # Warm-up start: ensure both the channel/ATR indicators AND (when a band is
    # set) the ADX are defined. ADX needs ~2×period bars to converge from NaN.
    i = donchian + atr_period + 1
    if adx_active:
        i = max(i, adx_period + 1)
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
        hi, lo = float(hi), float(lo)
        c = float(df["close"].iloc[i])
        # Confirmed breakout: close beyond the prior-N channel.
        direction: Optional[str] = None
        breakout_depth = 0.0
        if c > hi:
            direction = "long"
            breakout_depth = (c - hi) / atr
        elif c < lo:
            direction = "short"
            breakout_depth = (lo - c) / atr
        if direction is None:
            i += 1
            continue
        # Live-parity direction gate: trend_donchian is LONG-ONLY on the live
        # config (2026-06-01, Tier-3), so a long-only sweep must skip shorts or
        # the optimum reflects trades the strategy never takes.
        if long_only and direction == "short":
            i += 1
            continue
        # Direction-aware regime gate (Phase 2): skip a long whose direction read
        # is DOWN and a short whose read is UP. NaN (warm-up) → never skip.
        if dir_di_plus is not None:
            pdi, mdi = dir_di_plus.iloc[i], dir_di_minus.iloc[i]
            if not (pd.isna(pdi) or pd.isna(mdi)):
                down_regime = float(mdi) > float(pdi)
                if (direction == "long" and down_regime) or \
                        (direction == "short" and not down_regime):
                    i += 1
                    continue
        elif dir_slope is not None:
            sl_val = dir_slope.iloc[i]
            if not pd.isna(sl_val):
                down_regime = float(sl_val) < 0.0
                if (direction == "long" and down_regime) or \
                        (direction == "short" and not down_regime):
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
        # Live-parity confidence + entry gate (mirrors a live min_confidence
        # floor: skipping a low-confidence break lets the next qualifying bar
        # fire, exactly as the live builder would).
        confidence = round(min(max(breakout_depth, 0.0), 1.0), 4)
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
                if bl <= trail:                       # SL-first (conservative)
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
                    "strategy": "trend_donchian", "entry_time": str(t.entry_time),
                    "direction": t.direction, "gross_r": t.r_multiple,
                    "net_r": round(t.r_multiple - fr, 4),
                    "confidence": t.confidence}, default=str) + "\n")
    params: Dict[str, Any] = {"donchian": donchian, "atr_stop_mult": atr_stop_mult,
                              "trail_mult": trail_mult, "min_confidence": min_confidence}
    if adx_min is not None:
        params["adx_min"] = adx_min
    if adx_max is not None:
        params["adx_max"] = adx_max
    if adx_active:
        params["adx_period"] = adx_period
    if direction_filter != "off":
        params["direction_filter"] = direction_filter
    return _summarize(trades, df, timeframe=timeframe, symbol=symbol, params=params)


def _fee_r(t: Trade) -> float:
    if not t.exit_price or t.risk <= 0:
        return 0.0
    return (FEE_BPS_ROUNDTRIP / 10_000.0) * ((t.entry + t.exit_price) / 2.0) / t.risk


def _summarize(trades: List[Trade], df: pd.DataFrame, *, timeframe: str,
               symbol: str, params: Dict[str, Any]) -> Dict[str, Any]:
    n = len(trades)
    base: Dict[str, Any] = {
        "strategy": "trend_donchian", "symbol": symbol, "timeframe": timeframe,
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
    lines = [f"trend_donchian — {s['symbol']} {s['timeframe']} {s.get('params')}",
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


def _confidence_sweep(df: pd.DataFrame, grid: List[float], kwargs: Dict[str, Any]) -> Dict[str, Any]:
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
    return {"strategy": "trend_donchian", "symbol": kwargs.get("symbol"),
            "timeframe": kwargs.get("timeframe"),
            "data_start": str(df["timestamp"].iloc[0]) if len(df) else None,
            "data_end": str(df["timestamp"].iloc[-1]) if len(df) else None,
            "grid": rows, "best_by_net_total_r": best,
            "best_by_net_expectancy_r_min20": best_exp}


def _fmt_sweep(sw: Dict[str, Any]) -> str:
    lines = [f"trend_donchian confidence sweep — {sw['symbol']} {sw['timeframe']} "
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


def main(argv: List[str]) -> int:
    global FEE_BPS_ROUNDTRIP
    p = argparse.ArgumentParser(description="Donchian trend-follower backtest (net-of-fee).")
    p.add_argument("--data", default=os.environ.get("BACKTEST_DATA_PATH", "data/backtest_candles.csv"))
    p.add_argument("--timeframe", default="1h")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--resample", default=None, help="Resample to this rule first (e.g. 1h, 2h).")
    p.add_argument("--start", default=None, help="Walk-forward window start (ISO date, inclusive).")
    p.add_argument("--end", default=None, help="Walk-forward window end (ISO date, inclusive).")
    p.add_argument("--donchian", type=int, default=20)
    p.add_argument("--atr-period", type=int, default=14)
    p.add_argument("--atr-stop-mult", type=float, default=2.5,
                   help="Initial stop entry ∓ this × ATR (live default 2.5).")
    p.add_argument("--trail-mult", type=float, default=3.0,
                   help="Chandelier trail distance in ATR (live default 3.0; must exceed atr-stop-mult).")
    p.add_argument("--timeout-bars", type=int, default=200)
    p.add_argument("--cooldown-bars", type=int, default=1)
    p.add_argument("--fee-bps-roundtrip", type=float, default=FEE_BPS_ROUNDTRIP)
    p.add_argument("--min-confidence", type=float, default=0.0,
                   help="Skip entries whose live-parity confidence (breakout/ATR) is below this.")
    p.add_argument("--long-only", action="store_true",
                   help="Skip short entries (matches the live LONG-ONLY config since 2026-06-01).")
    p.add_argument("--adx-min", type=float, default=None,
                   help="Regime filter: skip entries whose Wilder ADX is below this (None=off).")
    p.add_argument("--adx-max", type=float, default=None,
                   help="Regime filter: skip entries whose Wilder ADX is above this (None=off).")
    p.add_argument("--adx-period", type=int, default=14,
                   help="Wilder ADX period for the regime filter (default 14).")
    p.add_argument("--direction-filter", choices=["off", "di", "slope"], default="off",
                   help="Phase-2 direction-aware regime gate (default off, byte-identical): "
                        "skip a long in a DOWN regime / a short in an UP regime. "
                        "'di' = Wilder +DI/-DI sign; 'slope' = Donchian channel-midline slope sign. "
                        "See docs/research/M-regime-direction-filter-DESIGN.md.")
    p.add_argument("--confidence-sweep", default=None, metavar="GRID",
                   help="Sweep min_confidence over GRID ('0:0.5:0.05' or '0,0.1,0.2') and tabulate.")
    p.add_argument("--json", dest="json_out", default=None)
    p.add_argument("--emit-trades", default=None, metavar="PATH",
                   help="Write per-trade {entry_time, net_r, confidence} JSONL for portfolio_combine.")
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
    bt_kwargs = dict(donchian=args.donchian, atr_period=args.atr_period,
                     atr_stop_mult=args.atr_stop_mult, trail_mult=args.trail_mult,
                     timeout_bars=args.timeout_bars, cooldown_bars=args.cooldown_bars,
                     timeframe=args.timeframe, symbol=args.symbol,
                     long_only=args.long_only,
                     adx_min=args.adx_min, adx_max=args.adx_max,
                     adx_period=args.adx_period,
                     direction_filter=args.direction_filter)
    if args.confidence_sweep:
        out = _confidence_sweep(df, _parse_grid(args.confidence_sweep), bt_kwargs)
        print(_fmt_sweep(out))
    else:
        out = run_backtest(df, emit_path=args.emit_trades,
                           min_confidence=args.min_confidence, **bt_kwargs)
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
