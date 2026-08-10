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
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
# Repo root must PRECEDE scripts/ — see the same block in backtest_ict_scalp.py.
# `scripts/ml/` is a regular package that shadows the repo's top-level `ml/`.
# This harness does not currently import anything that reaches `ml.*`, so the
# ordering is latent here rather than fatal; it is fixed in both files because
# the next import added to either one decides which.
if str(_REPO_ROOT) in sys.path:
    sys.path.remove(str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT))

from src.runtime import execution_costs  # noqa: E402  (the ONE shared cost model)
import capital_efficiency  # noqa: E402  (the ONE capital-efficiency definition)

# Execution-realism cost knobs (P1, FAITHFUL-BACKTEST-PLATFORM-DESIGN § 3.B).
# MANDATORY venue-aware cost is applied by main() (the CLI / production path): unset
# --slippage/--funding flags resolve to the venue-aware defaults (funding is perp-only
# → 0 for MES/GLD/EURUSD), so a real backtest is net-of-real-cost by default; every run
# also emits the fee-only arm for the with/without comparison. These MODULE GLOBALS
# default to 0.0 only so a DIRECT run_backtest() caller (the lever unit tests) stays
# byte-identical to the fee-only engine — main() overwrites them with the venue-aware
# policy. (Operator directive 2026-08-04: there is no reason to run a faithful backtest
# fee-only; pullback-first, the other harnesses roll onto this next.)
FEE_BPS_ROUNDTRIP = execution_costs.DEFAULT_FEE_BPS_ROUNDTRIP
SLIPPAGE_BPS_ROUNDTRIP = 0.0
FUNDING_BPS_PER_WINDOW = 0.0
FUNDING_WINDOW_HOURS = execution_costs.FUNDING_WINDOW_HOURS


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
    means the recent directional pressure is UP, ``-DI > +DI`` DOWN — the sign
    ADX itself throws away (a strong down-move has the same high ADX as a strong
    up-move; see docs/research/M-regime-direction-filter-DESIGN.md). ``min_periods``
    leaves the warm-up bars NaN so a filter never reads an undefined direction.
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


def run_backtest(df: pd.DataFrame, *, trend_lookback: int, pullback_lookback: int,
                 pullback_frac: float, atr_period: int, atr_stop_mult: float,
                 trail_mult: float, timeout_bars: int, cooldown_bars: int,
                 timeframe: str, symbol: str,
                 emit_path: Optional[str] = None,
                 min_confidence: float = 0.0,
                 adx_min: Optional[float] = None,
                 adx_max: Optional[float] = None,
                 adx_period: int = 14,
                 direction_filter: str = "off",
                 stale_exit_bars: Optional[int] = None,
                 stale_exit_below_r: float = 0.0,
                 flip_exit_bars: Optional[int] = None,
                 bank_frac: float = 0.0,
                 bank_at_r: float = 1.0,
                 tp_cap_pct: float = 0.0,
                 tp_r: float = 50.0,
                 giveback_min_mfe_r: float = 0.0,
                 giveback_r: float = 1.0,
                 trail_decay_arm_r: float = 0.0,
                 trail_decay_stall_bars: int = 0,
                 trail_decay_tight_mult: float = 0.0,
                 confirm_bars: int = 0,
                 skip_hours: str = "",
                 vol_skip_above_pctl: float = 0.0,
                 vol_skip_below_pctl: float = 0.0,
                 vol_pctl_window: int = 200,
                 trail_vol_above_pctl: float = 0.0,
                 trail_vol_below_pctl: float = 0.0,
                 trail_vol_tight_mult: float = 0.0,
                 side_filter: str = "both",
                 subbar_df: Optional[pd.DataFrame] = None,
                 exit_grain: str = "leg") -> Dict[str, Any]:
    # M21 E-2 time-of-day entry lever (empty = off, byte-identical): skip any
    # NEW entry whose TRIGGER bar's UTC hour is in the CSV set. Exits are
    # never touched — an open trade rides through skipped hours unchanged.
    skip_hour_set = {int(h) for h in str(skip_hours).split(",") if str(h).strip() != ""}
    df = df.reset_index(drop=True)
    # ── Intrabar exit-evaluation grain (three arms; `leg` = byte-identical) ──
    # docs/live-exit-monitor-cadence-DESIGN.md § 4.1. Live evaluates bot-side
    # exit levers ~21x per 1h bar; the harness evaluates them ONCE, at the
    # bar's close. Neither models the other, so "does more frequent evaluation
    # help?" has never been asked of the data. Three arms, because a finer
    # grain moves TWO variables at once and a single-arm comparison could not
    # separate them after the fact:
    #   leg    (A) everything on the leg-bar grain — what ships today.
    #   levers (B) levers on the sub-bar grain, SL/TP still resolved at
    #              leg-bar grain with the SL-first convention. Isolates the
    #              CADENCE question, which is the operator's actual ask.
    #              A LOWER BOUND: a lever may not pre-empt a stop that hit
    #              anywhere in the same leg bar, so a trade the lever would
    #              have saved earlier in the bar still scores as a stop.
    #   full   (C) SL/TP resolved per sub-bar too. Measures how much of the
    #              baseline is the SL-FIRST ARTIFACT — a bar trading through
    #              both stop and target scores as a stop today, and at a finer
    #              grain some of those become target-first. C will look better
    #              than A partly for a reason unrelated to exit logic, which is
    #              why reporting C without B would credit the lever for the
    #              convention.
    # THE LOOKAHEAD TRAP this avoids: `ext`/`mfe`/`trail` advance on the
    # SUB-BAR clock, in lockstep with the sub-bar closes the levers read. The
    # obvious implementation extends them to the WHOLE leg bar's extreme first,
    # which would let a giveback rule checked at 14:05 compare against a peak
    # set at 14:47 — an exit at the top with uncanny timing, and an artifact.
    # Bar-COUNTED params (`stale_exit_bars`, `trail_decay_stall_bars`, `peak_j`)
    # stay on the LEG-BAR clock: converting them to sub-bars would silently
    # redefine every threshold and the cell would no longer be config-exact.
    exit_grain = str(exit_grain or "leg").lower()
    if exit_grain not in ("leg", "levers", "full"):
        raise ValueError(f"unknown --exit-grain {exit_grain!r} "
                         "(expected leg|levers|full)")
    _sub_slices: Optional[List[tuple]] = None
    _sub_rows: Optional[List[tuple]] = None
    _sub_coverage: Optional[float] = None
    _sub_reason: Optional[str] = None
    if exit_grain != "leg":
        if subbar_df is None:
            raise ValueError("--exit-grain levers|full requires --subbar-data")
        import subbar_align  # noqa: E402 — sibling script, scripts/ on sys.path
        _al = subbar_align.align(list(df["timestamp"]),
                                 list(subbar_df["timestamp"]))
        _sub_reason = _al.get("reason")
        if _sub_reason:
            raise ValueError(f"sub-bar alignment refused: {_sub_reason}")
        _sub_slices = _al["slices"]
        _sub_coverage = _al["coverage"]
        sd = subbar_df.reset_index(drop=True)
        _sub_rows = list(zip((float(x) for x in sd["high"]),
                             (float(x) for x in sd["low"]),
                             (float(x) for x in sd["close"])))
    df["atr"] = _atr(df, atr_period)
    # M21 E-2 vol-at-entry lever (both 0 = off, byte-identical): skip any NEW
    # entry whose TRIGGER bar's ATR sits at an extreme TRAILING percentile
    # (rank within the previous `vol_pctl_window` bars — causal; NaN until
    # the window fills → never skip, fail-permissive). Exits untouched.
    # M20-X vol-conditional trail lever shares the same percentile series.
    vol_trail_on = (trail_vol_tight_mult > 0.0
                    and (trail_vol_above_pctl > 0.0 or trail_vol_below_pctl > 0.0))
    atr_pctl = None
    if (vol_skip_above_pctl > 0.0 or vol_skip_below_pctl > 0.0
            or vol_trail_on):
        atr_pctl = df["atr"].rolling(vol_pctl_window,
                                     min_periods=vol_pctl_window).rank(pct=True)
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
    # Direction-aware regime filter (Phase 2, MB-20260717 / BL-20260717-REGIME-COVERAGE-DEBT):
    # ADX measures trend STRENGTH not DIRECTION, so a long-only pullback buyer
    # fires into a strong DOWN-trend (the 2026-07-16 falling-knife losses). This
    # lever computes a per-bar direction read and skips an entry whose direction
    # opposes it. `off` = byte-identical (no series computed). `di` = Wilder
    # +DI/-DI sign. `slope` = sign of the Donchian trend-midline slope. A NaN
    # (warm-up) read never skips (fail-permissive — same posture as the ADX band).
    direction_filter = str(direction_filter or "off").lower()
    dir_di_plus = dir_di_minus = dir_slope = None
    if direction_filter == "di":
        dir_di_plus, dir_di_minus = _directional_indicators(df, adx_period)
    elif direction_filter == "slope":
        # Sign of the Donchian trend-midline slope (df["mid"], computed above):
        # mid rising = UP regime, falling = DOWN. shift(1)-based (no lookahead).
        dir_slope = df["mid"].diff()

    trades: List[Trade] = []
    subbar_missing_bars = 0

    # Per-entry live-TP distance in R -- answers whether the 9.9% clamp

    # actually BINDS on this leg, from the leg's own frame. Empty when off.

    _tp_r_effective: List[float] = []
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
        # Live-parity directional gate (side_filter). The live pullback builder
        # honours a per-strategy ``side_filter: long|short|both``; a config-exact
        # sweep must apply the same gate. ``both`` (default) = byte-identical no
        # gate. Added 2026-07-30 for the sol_pullback_2h short-only fine-tune
        # (docs/research/crypto-finetune-proposals-2026-07-30.md).
        if (side_filter == "long" and direction == "short") or \
                (side_filter == "short" and direction == "long"):
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
        if skip_hour_set:
            try:
                if pd.Timestamp(df["timestamp"].iloc[i]).hour in skip_hour_set:
                    i += 1
                    continue
            except (TypeError, ValueError):
                pass  # unparseable ts: never skip (fail-permissive)
        if atr_pctl is not None:
            vp = atr_pctl.iloc[i]
            if not pd.isna(vp):
                if vol_skip_above_pctl > 0.0 and float(vp) > vol_skip_above_pctl:
                    i += 1
                    continue
                if vol_skip_below_pctl > 0.0 and float(vp) < vol_skip_below_pctl:
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
        # LIVE-PARITY TAKE-PROFIT. Tracking id on its own line, never wrapped:
        # BL-20260810-BACKTEST-DOES-NOT-MODEL-THE-LIVE-CAPPED-TP
        # htf_pullback_trend_2h.py places tp = min(entry*(1+0.099), entry +
        # tp_r*risk) -- the 50R sentinel clamped to 9.9% because Bybit rejects a
        # TP beyond ~10%. This harness had NO take-profit exit path, and its own
        # line 10 stated the premise: "the tp_r (~50R) sentinel is parked far
        # from price". DEFAULT OFF so prior verdicts stay reproducible and
        # capped-vs-uncapped is an explicit A/B. Mirrors backtest_trend.py.
        tp_price: Optional[float] = None
        if tp_cap_pct > 0.0:
            if direction == "long":
                tp_price = min(entry * (1.0 + tp_cap_pct), entry + tp_r * risk)
            else:
                tp_price = max(entry * (1.0 - tp_cap_pct), entry - tp_r * risk)
            _tp_r_effective.append(abs(tp_price - entry) / risk)
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

        def _vol_tm(base_tm: float, j: int) -> float:
            # M20-X vol-conditional trail lever (tight_mult 0 = off,
            # byte-identical): tighten the mult on any managed bar whose
            # trailing ATR percentile sits in the gated tail — conditional,
            # not a ratchet (the price-ratcheted stop never loosens).
            # Undefined percentile ⇒ inert (fail-permissive). Tightest wins.
            # Design: docs/research/M20X-vol-conditional-trail-DESIGN.md.
            if not vol_trail_on:
                return base_tm
            vp = atr_pctl.iloc[j]
            if pd.isna(vp):
                return base_tm
            fired = ((trail_vol_above_pctl > 0.0
                      and float(vp) > trail_vol_above_pctl)
                     or (trail_vol_below_pctl > 0.0
                         and float(vp) < trail_vol_below_pctl))
            return min(base_tm, trail_vol_tight_mult) if fired else base_tm
        # The three grain arms share ONE definition of the stop/target
        # test, the ratchet and the levers — written once as closures so an
        # arm cannot drift from the baseline by an editing accident. Each
        # returns an (price, reason) exit or None.
        def _stop_or_target(h: float, lo: float):
            if direction == "long":
                if lo <= trail:                   # SL-first (conservative)
                    return trail, ("trail_stop" if trail > sl else "stop")
                if tp_price is not None and h >= tp_price:
                    return tp_price, "take_profit"
            else:
                if h >= trail:
                    return trail, ("trail_stop" if trail < sl else "stop")
                if tp_price is not None and lo <= tp_price:
                    return tp_price, "take_profit"
            return None

        def _ratchet(h: float, lo: float) -> None:
            # Advances ext/mfe/trail over ONE window — a leg bar in arm A,
            # a sub-bar in B/C. `ext_j` stays a LEG-bar index so the
            # stall-armed decay keeps counting in the strategy's own bars.
            nonlocal ext, ext_j, trail, mfe
            if direction == "long":
                if h > ext:
                    ext, ext_j = h, j
                trail = max(trail, ext - _vol_tm(_eff_tm(ext, ext_j, j), j) * atr)
                mfe = max(mfe, (ext - entry) / risk)
            else:
                if lo < ext:
                    ext, ext_j = lo, j
                trail = min(trail, ext + _vol_tm(_eff_tm(ext, ext_j, j), j) * atr)
                mfe = max(mfe, (entry - ext) / risk)

        def _levers(px: float):
            # Lever exits read a CLOSE — the leg bar's in arm A, the
            # sub-bar's in B/C — and fire only when the stop did not hit
            # first, so stop-first stays intact at whichever grain the arm
            # resolves the stop.
            o_r = ((px - entry) / risk if direction == "long"
                   else (entry - px) / risk)
            if giveback_min_mfe_r > 0.0:
                # M20 giveback-stop: once peak open profit >= min_mfe R,
                # exit when >= giveback_r R has been surrendered.
                if mfe >= giveback_min_mfe_r and (mfe - o_r) >= giveback_r:
                    return px, "giveback_stop"
            if flip_exit_bars is not None and flip_streak >= flip_exit_bars:
                return px, "trend_flip"
            if (stale_exit_bars is not None and (j - i) >= stale_exit_bars
                    and o_r < stale_exit_below_r):
                return px, "stale_stop"
            return None

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
            if flip_exit_bars is not None:
                bar_mid = df["mid"].iloc[j]
                if not pd.isna(bar_mid):
                    against = (bc < float(bar_mid)) if direction == "long" \
                        else (bc > float(bar_mid))
                    flip_streak = flip_streak + 1 if against else 0
            sub = None
            if _sub_slices is not None:
                s0, s1 = _sub_slices[j]
                if s1 > s0:
                    sub = _sub_rows[s0:s1]
                else:
                    subbar_missing_bars += 1
            hit = None
            if exit_grain == "full" and sub:
                # Arm C — stop/target AND levers resolved per sub-bar.
                for sh, sl_px, sc in sub:
                    hit = _stop_or_target(sh, sl_px)
                    if hit:
                        break
                    _ratchet(sh, sl_px)
                    hit = _levers(sc)
                    if hit:
                        break
            else:
                # Arms A and B share the leg-bar stop/target test. In B that
                # is deliberate: it is what makes B a measure of CADENCE
                # rather than of the SL-first convention.
                hit = _stop_or_target(bh, bl)
                if hit is None:
                    if exit_grain == "levers" and sub:
                        for sh, sl_px, sc in sub:
                            _ratchet(sh, sl_px)
                            hit = _levers(sc)
                            if hit:
                                break
                    else:
                        _ratchet(bh, bl)
                        hit = _levers(bc)
            if hit:
                exit_price, exit_reason = hit[0], hit[1]
                exit_idx = j
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
                cb = _cost_breakdown(t)
                fr = cb["total_cost_r"]
                fh.write(json.dumps({
                    "strategy": "htf_pullback_trend_2h", "symbol": symbol,
                    "entry_time": str(t.entry_time),
                    "direction": t.direction, "gross_r": t.r_multiple,
                    "net_r": round(t.r_multiple - fr, 4),
                    # BOTH arms always emitted (operator directive 2026-08-04): net_r
                    # is net-of-full-cost (fee+slippage+funding, § 3.B); net_r_fee_only
                    # is the fees-only number for the with/without comparison on every
                    # run. The breakdown lets the calibrator attribute the gap.
                    "net_r_fee_only": round(t.r_multiple - cb["fee_r"], 4),
                    "cost_fee_r": round(cb["fee_r"], 5),
                    "cost_slippage_r": round(cb["slippage_r"], 5),
                    "cost_funding_r": round(cb["funding_r"], 5),
                    "funding_windows": round(cb["funding_windows"], 3),
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
    if skip_hour_set:
        params["skip_hours"] = ",".join(str(h) for h in sorted(skip_hour_set))
    if vol_skip_above_pctl > 0.0 or vol_skip_below_pctl > 0.0:
        params["vol_skip_above_pctl"] = vol_skip_above_pctl
        params["vol_skip_below_pctl"] = vol_skip_below_pctl
        params["vol_pctl_window"] = vol_pctl_window
    if vol_trail_on:
        params["trail_vol_above_pctl"] = trail_vol_above_pctl
        params["trail_vol_below_pctl"] = trail_vol_below_pctl
        params["trail_vol_tight_mult"] = trail_vol_tight_mult
        params["vol_pctl_window"] = vol_pctl_window
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
    if direction_filter != "off":
        params["direction_filter"] = direction_filter
    summary = _summarize(trades, df, timeframe=timeframe, symbol=symbol, params=params,
                      tp_r_effective=_tp_r_effective)
    # The exit grain the numbers above were produced at, plus how much of the
    # leg-bar population the finer frame ACTUALLY covered. A verdict over an
    # unstated denominator is the failure this reports around: an arm that
    # silently fell back to bar-close evaluation on the leg bars the finer
    # frame does not describe would dilute the A/B by an unstated amount and
    # still print as "the intrabar result". `subbar_missing_bars` is None (not
    # 0) on arm A — nothing was looked for, which is not the same claim as
    # nothing was missing.
    summary["exit_grain"] = exit_grain
    summary["subbar_coverage"] = _sub_coverage
    summary["subbar_missing_bars"] = (subbar_missing_bars
                                      if exit_grain != "leg" else None)
    return summary


def _cost_breakdown(t: Trade) -> Dict[str, float]:
    """Per-trade round-trip cost in R via the ONE shared execution-realism model
    (fee + slippage + funding). Funding counts the 8h perp windows the hold crossed
    (t.entry_time → t.exit_time). Slippage/funding are 0.0 by default → fee-only,
    byte-identical to the legacy term."""
    if not t.exit_price or t.risk <= 0:
        return {"fee_r": 0.0, "slippage_r": 0.0, "funding_r": 0.0,
                "total_cost_r": 0.0, "funding_windows": 0.0}
    return execution_costs.roundtrip_cost_r(
        entry=t.entry, exit_price=t.exit_price, risk=t.risk,
        entry_time=t.entry_time, exit_time=t.exit_time,
        fee_bps_roundtrip=FEE_BPS_ROUNDTRIP,
        slippage_bps_roundtrip=SLIPPAGE_BPS_ROUNDTRIP,
        funding_bps_per_window=FUNDING_BPS_PER_WINDOW,
        funding_window_hours=FUNDING_WINDOW_HOURS,
    )


def _fee_r(t: Trade) -> float:
    """Total round-trip cost in R (fee + slippage + funding). Name kept for the
    existing call sites; with slippage/funding at 0.0 it equals the legacy fee."""
    return _cost_breakdown(t)["total_cost_r"]


def _summarize(trades: List[Trade], df: pd.DataFrame, *, timeframe: str,
               tp_r_effective: Optional[List[float]] = None,
               symbol: str, params: Dict[str, Any]) -> Dict[str, Any]:
    n = len(trades)
    base: Dict[str, Any] = {
        "strategy": "htf_pullback_trend_2h", "symbol": symbol, "timeframe": timeframe,
        "params": params, "total_trades": n, "fee_bps_roundtrip": FEE_BPS_ROUNDTRIP,
        # Execution-realism cost config in effect for this run (P1 § 3.B).
        "slippage_bps_roundtrip": SLIPPAGE_BPS_ROUNDTRIP,
        "funding_bps_per_window": FUNDING_BPS_PER_WINDOW,
        "data_start": str(df["timestamp"].iloc[0]) if len(df) else None,
        "data_end": str(df["timestamp"].iloc[-1]) if len(df) else None,
        "run_date": str(date.today())}
    # Live-TP reach: MEASURED per entry, so "does the 9.9% clamp bind on this
    # leg" comes from the leg's own frame, not an assumed ATR%. None (never 0)
    # when the lever is off -- unmeasured and zero-distance are opposite.
    _tpe = sorted(tp_r_effective or [])
    base["tp_r_effective_n"] = len(_tpe)
    base["tp_r_effective_median"] = (round(_tpe[len(_tpe) // 2], 3) if _tpe else None)
    base["tp_r_effective_min"] = (round(_tpe[0], 3) if _tpe else None)
    base["tp_r_effective_max"] = (round(_tpe[-1], 3) if _tpe else None)
    if n == 0:
        base.update({"win_rate_pct": 0.0, "net_total_r": 0.0, "net_expectancy_r": 0.0,
                     "trades_long": 0, "trades_short": 0, "max_drawdown_r": 0.0,
                     "by_outcome": {}, "by_year": {},
                     **capital_efficiency.empty()})
        return base
    rs = [t.r_multiple for t in trades]
    costs = [_cost_breakdown(t) for t in trades]
    net = [t.r_multiple - c["total_cost_r"] for t, c in zip(trades, costs)]
    # Fee-only arm — always computed so every run carries the with/without comparison.
    net_fee_only = [t.r_multiple - c["fee_r"] for t, c in zip(trades, costs)]
    mean_cost_r = {k: round(sum(c[k] for c in costs) / n, 5)
                   for k in ("fee_r", "slippage_r", "funding_r",
                             "total_cost_r", "funding_windows")}
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
        # Fee-only comparison arm (always present): the number BEFORE slippage+funding,
        # so a reader sees the execution-realism delta on every run without a re-run.
        "net_total_r_fee_only": round(sum(net_fee_only), 4),
        "net_expectancy_r_fee_only": round(sum(net_fee_only) / n, 4),
        "trades_long": len(longs), "trades_short": len(shorts),
        "avg_win_r": round(sum(wins) / len(wins), 4) if wins else 0.0,
        "max_mfe_r": round(max(t.mfe_r for t in trades), 3),
        "mean_cost_r": mean_cost_r,
        "max_drawdown_r": round(mdd, 4), "by_outcome": by, "by_year": by_year})
    # Capital efficiency — the exit-refinement gate's declared-but-never-built
    # tiebreak (operator directive 2026-08-10: a trade that reaches TP after 149
    # bars is not the same object as one that got there in 10). DEFINITION is
    # single-homed in scripts/capital_efficiency.py; this harness owns only the
    # extraction. Its Trade has no meta dict, so hold comes from the indices.
    #
    # capital_bars == position_bars here, HONESTLY: the levers this harness is
    # swept for (stale_exit_bars / giveback_r / timeout_bars) close the WHOLE
    # position, so no capital is released early and the two coincide by
    # definition. Its --bank-frac lever DOES release early, but this harness
    # records only the `banked` boolean and not the rung BAR, so a
    # capital-weighted hold is not derivable — reporting one would be a
    # fabricated number. Banking cells read here are therefore UNDER-credited
    # on capital efficiency; wire banked_index (as backtest_ict_scalp.py does)
    # before reading this column for a ladder cell.
    _pos_bars = float(sum(max(0, int(t.exit_index) - int(t.entry_index))
                          for t in trades))
    base.update(capital_efficiency.summarize(
        bar_minutes=capital_efficiency.bar_minutes_from_frame(df),
        position_bars=_pos_bars, capital_bars=_pos_bars,
        net_total_r=base.get("net_total_r"), n_trades=n))

    return base


def _fmt(s: Dict[str, Any]) -> str:
    lines = [f"htf_pullback_trend_2h — {s['symbol']} {s['timeframe']} {s.get('params')}",
             f"  data {s.get('data_start')} -> {s.get('data_end')}  trades={s['total_trades']}"]
    if s["total_trades"]:
        lines += [
            f"  win_rate={s['win_rate_pct']}%  net_r={s['net_total_r']} "
            f"(exp {s['net_expectancy_r']}, L/S {s['trades_long']}/{s['trades_short']}, "
            f"netL/S {s.get('net_total_r_long')}/{s.get('net_total_r_short')})",
            f"  cost: slip={s.get('slippage_bps_roundtrip')}bps fund="
            f"{s.get('funding_bps_per_window')}bps/8h → net-of-full-cost above vs "
            f"FEE-ONLY net_r={s.get('net_total_r_fee_only')} "
            f"(exp {s.get('net_expectancy_r_fee_only')}); mean_cost_r={s.get('mean_cost_r')}",
            f"  avg_win_r={s.get('avg_win_r')} max_mfe_r={s.get('max_mfe_r')} "
            f"maxdd_r={s['max_drawdown_r']} by={s['by_outcome']}",
            f"  by_year={s.get('by_year')}"]
    return "\n".join(lines)


def main(argv: List[str]) -> int:
    global FEE_BPS_ROUNDTRIP, SLIPPAGE_BPS_ROUNDTRIP, FUNDING_BPS_PER_WINDOW, FUNDING_WINDOW_HOURS
    p = argparse.ArgumentParser(description="HTF pullback trend-continuation backtest (net-of-cost: fee+slippage+funding).")
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
    p.add_argument("--slippage-bps-roundtrip", type=float, default=None,
                   help="Execution-realism (P1 § 3.B): round-trip slippage in bps of "
                        "notional (half-spread + impact, summed over both sides). "
                        "DEFAULT (unset) = the venue-aware default "
                        "(execution_costs.slippage_bps_roundtrip_for, ~5 bps). Pass "
                        "0 for the fee-only comparison arm.")
    p.add_argument("--funding-bps-per-window", type=float, default=None,
                   help="Execution-realism (P1 § 3.B): perp funding magnitude in bps "
                        "of notional per 8h window; the hold is charged for every "
                        "window it crosses (a multi-bar 2h-strategy hold spans "
                        "several). DEFAULT (unset) = the VENUE-AWARE default "
                        "(execution_costs.funding_bps_per_window_for): ~1 bps/8h for a "
                        "crypto PERP, 0 for futures/equity/fx (they pay no perp "
                        "funding). Pass 0 for the fee-only arm. Directionless drag at "
                        "P1 (a signed funding feed is the P2 upgrade).")
    p.add_argument("--funding-window-hours", type=float, default=FUNDING_WINDOW_HOURS,
                   help="Perp funding window length in hours (default 8.0).")
    p.add_argument("--min-confidence", type=float, default=0.0,
                   help="Skip entries whose live-parity confidence (trend-depth/ATR) is below this.")
    p.add_argument("--adx-min", type=float, default=None,
                   help="Regime filter: skip entries whose Wilder ADX is below this (None=off).")
    p.add_argument("--adx-max", type=float, default=None,
                   help="Regime filter: skip entries whose Wilder ADX is above this (None=off).")
    p.add_argument("--adx-period", type=int, default=14,
                   help="Wilder ADX period for the regime filter (default 14).")
    p.add_argument("--direction-filter", choices=["off", "di", "slope"], default="off",
                   help="Phase-2 direction-aware regime gate (default off, byte-identical): "
                        "skip a long in a DOWN regime / a short in an UP regime. "
                        "'di' = Wilder +DI/-DI sign; 'slope' = Donchian midline slope sign. "
                        "See docs/research/M-regime-direction-filter-DESIGN.md.")
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
    p.add_argument("--tp-cap-pct", type=float, default=0.0,
                   help="LIVE-PARITY take-profit: place tp at "
                        "min(entry*(1+pct), entry + tp_r*risk) and EXIT there. "
                        "Production uses 0.099 (the Bybit ~10%% TP-distance "
                        "clamp on the 50R sentinel). 0 = off, byte-identical to "
                        "every verdict measured before 2026-08-10.")
    p.add_argument("--tp-r", type=float, default=50.0,
                   help="The leg's declared tp_r sentinel (default 50R). Only "
                        "consulted when --tp-cap-pct > 0; the cap normally binds.")
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
    p.add_argument("--skip-hours", default="",
                   help="M21 E-2 time-of-day entry lever (empty=off): CSV of "
                        "UTC hours whose trigger bars never enter.")
    p.add_argument("--vol-skip-above-pctl", type=float, default=0.0,
                   help="M21 E-2 vol-at-entry lever (0=off): skip entries whose "
                        "trigger-bar ATR trailing percentile exceeds this (hot tail).")
    p.add_argument("--vol-skip-below-pctl", type=float, default=0.0,
                   help="M21 E-2 vol-at-entry lever (0=off): skip entries whose "
                        "trigger-bar ATR trailing percentile is below this (dead tail).")
    p.add_argument("--vol-pctl-window", type=int, default=200,
                   help="Trailing window (bars) for the ATR percentile rank.")
    p.add_argument("--trail-vol-above-pctl", type=float, default=0.0,
                   help="M20-X vol-conditional trail lever (0=off): tighten the "
                        "trail mult on bars whose ATR trailing percentile exceeds "
                        "this (hot tail).")
    p.add_argument("--trail-vol-below-pctl", type=float, default=0.0,
                   help="M20-X vol-conditional trail lever (0=off): tighten the "
                        "trail mult on bars whose ATR trailing percentile is below "
                        "this (dead tail).")
    p.add_argument("--trail-vol-tight-mult", type=float, default=0.0,
                   help="The tightened trail mult while the vol condition fires "
                        "(0 disables the lever).")
    p.add_argument("--subbar-data",
                   help="Finer OHLCV frame for the intrabar exit-evaluation arms "
                        "(docs/live-exit-monitor-cadence-DESIGN.md § 4). Entries stay "
                        "on the leg timeframe; only exits are re-graded.")
    p.add_argument("--exit-grain", choices=["leg", "levers", "full"], default="leg",
                   help="leg (A, DEFAULT, byte-identical): everything on the leg-bar "
                        "grain. levers (B): exit levers evaluated at every sub-bar "
                        "close, SL/TP still resolved at leg-bar grain SL-first — "
                        "isolates the CADENCE question and is a LOWER BOUND. "
                        "full (C): SL/TP resolved per sub-bar too, which also "
                        "removes the SL-first artifact — report C only beside B, "
                        "or the convention gets credited to the lever.")
    p.add_argument("--side-filter", choices=["long", "short", "both"], default="both",
                   help="Config-exact directional gate matching the live builder's "
                        "side_filter: skip shorts (long) / skip longs (short) / no gate "
                        "(both, default). Used for the crypto short-only fine-tunes.")
    p.add_argument("--json", dest="json_out", default=None)
    p.add_argument("--emit-trades", default=None, metavar="PATH",
                   help="Write per-trade {entry_time, net_r, confidence} JSONL for regime tagging.")
    args = p.parse_args(argv[1:])
    FEE_BPS_ROUNDTRIP = args.fee_bps_roundtrip
    # Mandatory venue-aware cost policy (operator directive 2026-08-04): a faithful
    # backtest is net-of-real-cost by default. Unset flags resolve to the venue-aware
    # defaults (funding is perp-only → 0 for MES/GLD/EURUSD, never a fabricated cost);
    # an explicit value (incl. 0 for the fee-only comparison arm) always wins.
    SLIPPAGE_BPS_ROUNDTRIP = (
        execution_costs.slippage_bps_roundtrip_for(args.symbol)
        if args.slippage_bps_roundtrip is None else args.slippage_bps_roundtrip)
    FUNDING_BPS_PER_WINDOW = (
        execution_costs.funding_bps_per_window_for(args.symbol)
        if args.funding_bps_per_window is None else args.funding_bps_per_window)
    FUNDING_WINDOW_HOURS = args.funding_window_hours
    try:
        df = _load_candles(args.data)
        if args.resample:
            df = _resample(df, args.resample)
        df = _date_filter(df, args.start, args.end)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: load failed: {exc}", file=sys.stderr)
        return 1
    subbar_df = None
    if args.subbar_data:
        try:
            subbar_df = _load_candles(args.subbar_data)
            # The sub-bar frame is windowed to the SAME span as the leg frame.
            # Skipping this would let the finer frame reach outside the window
            # the run is labelled with — the sub-bars would simply never be
            # indexed, but a coverage figure computed over a wider frame would
            # then describe a population the run never graded.
            subbar_df = _date_filter(subbar_df, args.start, args.end)
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR: sub-bar load failed: {exc}", file=sys.stderr)
            return 1
    if args.exit_grain != "leg" and subbar_df is None:
        # A missing frame must NOT degrade to the baseline wearing arm B's
        # label — that would report the leg-grain result as the intrabar one.
        print("ERROR: --exit-grain levers|full requires --subbar-data",
              file=sys.stderr)
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
                       direction_filter=args.direction_filter,
                       stale_exit_bars=args.stale_exit_bars,
                       stale_exit_below_r=args.stale_exit_below_r,
                       flip_exit_bars=args.flip_exit_bars,
                       bank_frac=args.bank_frac,
                       bank_at_r=args.bank_at_r,
                       tp_cap_pct=args.tp_cap_pct, tp_r=args.tp_r,
                       giveback_min_mfe_r=args.giveback_min_mfe_r,
                       giveback_r=args.giveback_r,
                       trail_decay_arm_r=args.trail_decay_arm_r,
                       trail_decay_stall_bars=args.trail_decay_stall_bars,
                       trail_decay_tight_mult=args.trail_decay_tight_mult,
                       confirm_bars=args.confirm_bars,
                       skip_hours=args.skip_hours,
                       vol_skip_above_pctl=args.vol_skip_above_pctl,
                       vol_skip_below_pctl=args.vol_skip_below_pctl,
                       vol_pctl_window=args.vol_pctl_window,
                       trail_vol_above_pctl=args.trail_vol_above_pctl,
                       trail_vol_below_pctl=args.trail_vol_below_pctl,
                       trail_vol_tight_mult=args.trail_vol_tight_mult,
                       side_filter=args.side_filter,
                       subbar_df=subbar_df,
                       exit_grain=args.exit_grain)
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
