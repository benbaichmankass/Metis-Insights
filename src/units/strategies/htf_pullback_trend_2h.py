"""HTF trend-pullback continuation — units-layer adapter (SCAFFOLD, not wired).

Rank-2 candidate from the new-strategy research pass
(docs/research/new-strategy-candidates-2026-05-31.md). **Not yet wired** into
``strategy_signal_builders.py``, ``intents.py``, or ``config/strategies.yaml``
(registration is explicit; no auto-discovery) — inert until the Tier-3
activation PR. Backtested via ``scripts/backtest_pullback.py`` first.

Strategy summary
----------------
Trend-continuation via a mean-reversion ENTRY. In an established trend,
pullbacks to a dynamic level (Donchian midline / EMA) overshoot on retail
panic and revert in the trend direction. You capture the trend payoff but
enter on weakness (better R:R) instead of chasing the breakout bar.

Why it complements ``trend_donchian`` — the flip-safety argument
----------------------------------------------------------------
This is the deliberate anti-correlation-by-construction play, and its key
property is STRUCTURAL, not a backtest hope:

  trend_donchian and squeeze enter on STRENGTH (breakout/expansion). This
  enters on WEAKNESS within the SAME trend. So when trend_donchian is mid-
  trade riding a runner up, this strategy wants to ADD on the retrace — the
  same side. Because both want the same side in a trend, their conflicts in
  the intent layer are SAME-SIDE (resolved to max-qty, no flip) rather than
  OPPOSITE-SIDE (flip-churn).

That directly honours the #1 system finding (flip-churn is the dominant cost;
``FLIP_POLICY=hold`` is now the live default). A pullback member cannot
re-introduce the churn that ``fade``/``turtle`` caused — it is flip-safe
against the live winner by construction. The risk it must clear is the
opposite: being so correlated with trend that it adds no diversification —
which is exactly what the backtest's corr-vs-trend gate checks.

Entry
-----
Require an HTF uptrend (close > Donchian-``trend_lookback`` midline, i.e. the
midline rising / price above it) AND a short-term pullback (close has pulled
back into the lower ``pullback_frac`` of the recent ``pullback_lookback``
range). Enter LONG on a reversal-confirmation bar (a bullish close off the
pullback low). Symmetric for downtrends/shorts. Anything else is
non-actionable (ValueError → side="none").

Exit
----
The VERBATIM shared Chandelier ATR trail (copied from trend_donchian) — let
the continuation run; NOT a tight target (the program's iron law: every
tight-target strategy died on BTC fees). Far ~50R ``tp`` sentinel +
``timeout_bars`` backstop. Frozen entry-time ATR in ``meta``.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

import pandas as pd

from src.units.strategies._base import require_candles

_DEFAULTS: Dict[str, Any] = {
    "trend_lookback": 50,        # Donchian window whose midline defines the trend
    "pullback_lookback": 10,     # recent-range window for the pullback test
    "pullback_frac": 0.33,       # close must be in the lower (long) third of it
    "atr_period": 14,
    "atr_stop_mult": 2.5,
    "trail_mult": 3.0,
    "tp_r": 50.0,
    "timeframe": "2h",
    "min_confidence": 0.0,
    # ADX regime filter (recombination lever, SRQ-20260618-001/-002). Default
    # None/None = OFF → behaviour-preserving (no gate), exactly as before. When
    # adx_min (and/or adx_max) is set in config/strategies.yaml, an actionable
    # setup is admitted only if its Wilder ADX(adx_period) on the closed signal
    # bar sits inside the band — VERBATIM the gate scripts/backtest_pullback.py
    # validated, so live == backtest. A NaN (warm-up) ADX is never admitted.
    "adx_min": None,
    "adx_max": None,
    "adx_period": 14,
    # M21 E-2 time-of-day entry lever (empty = off, byte-identical): skip any
    # NEW entry whose TRIGGER bar's UTC hour is in this CSV set (e.g. "19,20").
    # Exits are never touched — an open trade rides through skipped hours
    # unchanged. Mirrors scripts/backtest_pullback.py --skip-hours exactly;
    # declared per leg in config/strategies.yaml (Tier-3).
    "skip_hours": "",
    # M21 E-2 vol-at-entry lever (both 0.0 = off, byte-identical): skip any
    # NEW entry whose TRIGGER bar's ATR sits at an extreme TRAILING
    # percentile (rank within the previous `vol_pctl_window` bars — causal;
    # the live 200-bar fetch fills the default window exactly). above>0
    # skips the hot tail; below>0 the dead tail. An undefined percentile
    # NEVER skips (fail-permissive). Exits are never touched. Mirrors
    # scripts/backtest_pullback.py --vol-skip-*-pctl exactly; declared per
    # leg in config/strategies.yaml (Tier-3).
    "vol_skip_above_pctl": 0.0,
    "vol_skip_below_pctl": 0.0,
    "vol_pctl_window": 200,
}

_TP_SENTINEL_CAP_PCT = 0.099


def _trend_midline(df: "pd.DataFrame", trend_lb: int) -> "pd.Series":
    """Donchian-``trend_lb`` midline, ``shift(1)`` so there is no lookahead.

    ONE definition, because two consumers must not disagree about it: the ENTRY
    condition (`close` above it = uptrend) and `_pullback_thesis_intact`, which
    re-evaluates that same condition while the trade is open. A second copy
    would let the thesis grade a trade against a midline the entry never used --
    the same class as the two byte-identical `_since_entry` copies this module
    already had to collapse.
    """
    dc_hi = df["high"].rolling(trend_lb).max().shift(1)
    dc_lo = df["low"].rolling(trend_lb).min().shift(1)
    return (dc_hi + dc_lo) / 2.0


def _resolve_params(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return {key: cfg.get(key, default) for key, default in _DEFAULTS.items()}


def _parse_skip_hours(raw: Any) -> set:
    """CSV of UTC hours to skip (M21 E-2 time-of-day lever). ''/None = off.

    Fail-permissive: a malformed value resolves to the empty set (gate off)
    rather than raising — a YAML typo must never strand a live strategy.
    """
    try:
        return {int(h) for h in str(raw or "").split(",") if str(h).strip() != ""}
    except (TypeError, ValueError):
        return set()


def _bar_hour_utc(df: pd.DataFrame, idx: int) -> Optional[int]:
    """UTC hour of the bar at ``idx`` — None when unparseable (never skips)."""
    try:
        return int(pd.Timestamp(df["timestamp"].iloc[idx]).hour)
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _trailing_atr_pctl(atr_series: pd.Series, idx: int,
                       window: int) -> Optional[float]:
    """Trailing ATR percentile of the bar at ``idx`` (M21 vol-at-entry).

    Rank of ATR[idx] within the previous ``window`` values (causal, includes
    the bar itself) — the exact ``rolling(window, min_periods=window)
    .rank(pct=True)`` the research harness validated. None when the window
    has not filled or anything raises (fail-permissive: never skips).
    """
    try:
        pctl = atr_series.rolling(window, min_periods=window).rank(pct=True)
        val = pctl.iloc[idx]
        return None if pd.isna(val) else float(val)
    except Exception:  # noqa: BLE001 — any failure must never strand a leg
        return None


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    h, low, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([(h - low), (h - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=1).mean()


def _adx(df: pd.DataFrame, period: int) -> pd.Series:
    """Wilder's ADX — VERBATIM copy of scripts/backtest_pullback.py::_adx so the
    live regime gate matches the validated backtest bar-for-bar. +DM/-DM →
    Wilder-smoothed (EWM alpha=1/period) +DI/-DI → DX → ADX (Wilder-smoothed DX).
    min_periods=period leaves warm-up bars NaN (an undefined-regime bar is never
    admitted by a band)."""
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


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def order_package(cfg: dict, candles_df: Optional[pd.DataFrame] = None) -> dict:
    """Build an htf_pullback_trend_2h OrderPackage dict. Raises ValueError on
    any non-actionable tick (no trend, no pullback, no confirmation, etc.)."""
    candles_df = require_candles(candles_df, "htf_pullback_trend_2h")
    params = _resolve_params(cfg)
    symbol = cfg.get("symbol") or cfg.get("SYMBOL") or "BTCUSDT"
    # Clone-template strategies (mhg/mgc/tlt/… daily-pullback variants) reuse
    # this unit; the caller threads its OWN name via cfg["strategy_label"] so the
    # non-actionable reason strings name the emitting strategy, not the parent
    # template (BL-20260611-003). Defaults to the canonical name.
    label = str(cfg.get("strategy_label") or "htf_pullback_trend_2h")

    trend_lb = int(params["trend_lookback"])
    pull_lb = int(params["pullback_lookback"])
    pull_frac = float(params["pullback_frac"])
    atr_period = int(params["atr_period"])
    atr_stop_mult = float(params["atr_stop_mult"])
    timeframe = str(cfg.get("timeframe") or params["timeframe"])

    needed = trend_lb + atr_period + 2
    if params.get("adx_min") is not None or params.get("adx_max") is not None:
        # Wilder ADX needs ~2×period bars to converge off the NaN warm-up.
        needed = max(needed, int(params.get("adx_period") or 14) * 2 + 2)
    if len(candles_df) < needed:
        raise ValueError(
            f"Strategy '{label}': need at least {needed} candles "
            f"for the trend({trend_lb}) / atr({atr_period}) windows; got "
            f"{len(candles_df)}."
        )

    df = candles_df.reset_index(drop=True)
    atr_series = _atr(df, atr_period)
    # Trend filter: Donchian midline of the prior trend_lb bars (shift(1), no
    # lookahead). Price above a rising midline = uptrend; below = downtrend.
    midline = _trend_midline(df, trend_lb)
    # Recent range for the pullback test (prior pull_lb bars, shift(1)).
    pr_hi = df["high"].rolling(pull_lb).max().shift(1)
    pr_lo = df["low"].rolling(pull_lb).min().shift(1)

    atr = float(atr_series.iloc[-1]) if pd.notna(atr_series.iloc[-1]) else 0.0
    close = float(df["close"].iloc[-1])
    prev_close = float(df["close"].iloc[-2])
    mid = midline.iloc[-1]
    rhi, rlo = pr_hi.iloc[-1], pr_lo.iloc[-1]
    if atr <= 0 or pd.isna(mid) or pd.isna(rhi) or pd.isna(rlo):
        raise ValueError(f"Strategy '{label}': indicators undefined (non-actionable).")
    mid, rhi, rlo = float(mid), float(rhi), float(rlo)
    rng = rhi - rlo
    if rng <= 0:
        raise ValueError(f"Strategy '{label}': degenerate recent range (non-actionable).")

    # Position within the recent range, 0=at low .. 1=at high.
    pos_in_range = (close - rlo) / rng
    uptrend = close > mid
    downtrend = close < mid

    direction: Optional[str] = None
    if uptrend and pos_in_range <= pull_frac and close > prev_close:
        # Pullback into the lower third of the range + a bullish confirmation bar.
        direction = "long"
        # Trend-strength component (distance above the midline, in ATR units).
        depth = (close - mid) / atr
    elif downtrend and pos_in_range >= (1 - pull_frac) and close < prev_close:
        direction = "short"
        depth = (mid - close) / atr
    else:
        raise ValueError(
            f"Strategy '{label}': no trend-pullback-confirmation "
            "setup on the latest bar (non-actionable)."
        )

    # M21 E-2 time-of-day gate — placed after direction resolution, before the
    # ADX/confidence gates, mirroring scripts/backtest_pullback.py bar-for-bar.
    # Fail-permissive: an unparseable timestamp never skips.
    skip_hour_set = _parse_skip_hours(params.get("skip_hours"))
    if skip_hour_set:
        trigger_hour = _bar_hour_utc(df, -1)
        if trigger_hour is not None and trigger_hour in skip_hour_set:
            raise ValueError(
                f"Strategy '{label}': trigger bar hour {trigger_hour} in "
                f"skip_hours {sorted(skip_hour_set)} — time-of-day gate, "
                "non-actionable."
            )

    # M21 E-2 vol-at-entry gate — same trigger-bar anchor as skip_hours,
    # mirroring scripts/backtest_pullback.py bar-for-bar. An undefined
    # percentile (window unfilled / any error) never skips (fail-permissive).
    vol_above = _coerce_float(params.get("vol_skip_above_pctl")) or 0.0
    vol_below = _coerce_float(params.get("vol_skip_below_pctl")) or 0.0
    vol_pctl: Optional[float] = None
    if vol_above > 0.0 or vol_below > 0.0:
        vol_window = int(_coerce_float(params.get("vol_pctl_window")) or 200)
        vol_pctl = _trailing_atr_pctl(atr_series, -1, vol_window)
        if vol_pctl is not None:
            if vol_above > 0.0 and vol_pctl > vol_above:
                raise ValueError(
                    f"Strategy '{label}': trigger bar ATR percentile "
                    f"{vol_pctl:.3f} > vol_skip_above_pctl {vol_above} — "
                    "vol-at-entry gate (hot tail), non-actionable."
                )
            if vol_below > 0.0 and vol_pctl < vol_below:
                raise ValueError(
                    f"Strategy '{label}': trigger bar ATR percentile "
                    f"{vol_pctl:.3f} < vol_skip_below_pctl {vol_below} — "
                    "vol-at-entry gate (dead tail), non-actionable."
                )

    # ADX regime gate (recombination lever) — admit the confirmed setup only if
    # its Wilder ADX on the closed signal bar sits inside [adx_min, adx_max].
    # OFF by default (both None) → no-op. Matches scripts/backtest_pullback.py
    # bar-for-bar (ADX read on the entry bar; NaN warm-up rejected).
    adx_min_p = _coerce_float(params.get("adx_min"))
    adx_max_p = _coerce_float(params.get("adx_max"))
    adx_val: Optional[float] = None
    if adx_min_p is not None or adx_max_p is not None:
        adx_period_p = int(params.get("adx_period") or 14)
        adx_series = _adx(df, adx_period_p)
        adx_last = adx_series.iloc[-1] if len(adx_series) else float("nan")
        if pd.isna(adx_last):
            raise ValueError(
                f"Strategy '{label}': ADX undefined (warm-up) — "
                "regime filter active, non-actionable."
            )
        adx_val = float(adx_last)
        if adx_min_p is not None and adx_val < adx_min_p:
            raise ValueError(
                f"Strategy '{label}': ADX {adx_val:.2f} < adx_min "
                f"{adx_min_p} — regime filter, non-actionable."
            )
        if adx_max_p is not None and adx_val > adx_max_p:
            raise ValueError(
                f"Strategy '{label}': ADX {adx_val:.2f} > adx_max "
                f"{adx_max_p} — regime filter, non-actionable."
            )

    entry = close
    if direction == "long":
        sl = entry - atr_stop_mult * atr
        risk = entry - sl
        tp = min(entry * (1 + _TP_SENTINEL_CAP_PCT), entry + float(params["tp_r"]) * risk)
    else:
        sl = entry + atr_stop_mult * atr
        risk = sl - entry
        tp = max(entry * (1 - _TP_SENTINEL_CAP_PCT), entry - float(params["tp_r"]) * risk)
    if risk <= 0:
        raise ValueError(f"Strategy '{label}': non-positive risk; skipping.")

    # Confidence — a blended [0, 1] score (mirrors turtle_soup / fvg_range /
    # ict_scalp, which all combine two normalised components). The old
    # `min(depth, 1.0)` saturated at 1.0 for *every* signal, because on a
    # trend-pullback the close is almost always >= 1 ATR from the slow midline
    # (PERF-20260601-010: htf_pullback emitted confidence=1.0 on every package).
    # Blend now spreads across the range:
    #   * TREND strength — `depth` (ATR from midline), normalised over ~2 ATR.
    #   * PULLBACK quality — how deep into the actionable pullback zone the entry
    #     sits (a deeper retrace = better R:R): 1.0 at the range extreme, 0.0 at
    #     the `pull_frac` boundary.
    # Confidence is metadata only — it is NOT a sizing input and NOT part of the
    # intent-multiplexer selection key (target_qty/priority/timestamp/name); it
    # feeds the dashboard, confidence-weighting analysis, and ML features.
    trend_strength = min(max(depth, 0.0) / 2.0, 1.0)
    _pf = max(pull_frac, 1e-9)
    if direction == "long":
        pullback_quality = (pull_frac - pos_in_range) / _pf
    else:
        pullback_quality = (pos_in_range - (1.0 - pull_frac)) / _pf
    pullback_quality = min(max(pullback_quality, 0.0), 1.0)
    confidence = round(min(0.5 * trend_strength + 0.5 * pullback_quality, 1.0), 4)
    min_confidence = float(params["min_confidence"])
    if confidence < min_confidence:
        raise ValueError(
            f"Strategy '{label}': confidence {confidence} below "
            f"min_confidence {min_confidence} — non-actionable."
        )

    try:
        entry_time = str(df["timestamp"].iloc[-1])
    except (KeyError, IndexError):
        entry_time = None

    package = {
        "symbol": symbol,
        "direction": direction,
        "entry": round(entry, 8),
        "sl": round(float(sl), 8),
        "tp": round(float(tp), 8),
        "confidence": confidence,
        "meta": {
            "trend_midline": mid,
            "pullback_pos_in_range": round(pos_in_range, 4),
            "atr": atr,
            "atr_period": atr_period,
            "atr_stop_mult": atr_stop_mult,
            "trail_mult": float(params["trail_mult"]),
            "tp_r": float(params["tp_r"]),
            "risk_per_unit": float(risk),
            "entry_time": entry_time,
            "timeframe": timeframe,
            "adx": adx_val,
            "adx_min": adx_min_p,
            "adx_max": adx_max_p,
        },
    }
    # M20 P4.1 trail-decay + M20-X vol-conditional trail (Tier-3, YAML-declared):
    # thread the declared params into meta because run_monitor_tick can pass
    # cfg={} — meta is the channel monitor() reliably sees (same shape as
    # trend_donchian's lever threading). Absent = the lever is inert (base mult
    # unchanged). The trail_vol_* keys unlock resolve_vol_trail_mult for the
    # pullback family (qqq_pullback_1h shipped the first cell — #6510 sweep pass).
    for _key in (# M20 R-based exit levers, threaded 2026-08-18 alongside the
                 # shared-module wiring in monitor(). The verdicts fall back to
                 # cfg, so a declared key would reach the monitor either way —
                 # these are here for the reason donchian threads them: the
                 # package records the declaration that was in force AT ENTRY,
                 # so a later YAML edit cannot silently re-grade an open trade.
                 "stale_exit_bars", "stale_exit_below_r",
                 "giveback_min_mfe_r", "giveback_r",
                 "trail_decay_arm_r", "trail_decay_stall_bars",
                 "trail_decay_tight_mult",
                 "trail_vol_above_pctl", "trail_vol_below_pctl",
                 "trail_vol_tight_mult", "vol_pctl_window"):
        if cfg.get(_key) is not None:
            package["meta"][_key] = cfg[_key]
    if skip_hour_set:
        # Auditability: this entry passed a declared time-of-day gate
        # (M21 E-2). Entry-side only — the monitor never reads it.
        package["meta"]["skip_hours"] = ",".join(str(h) for h in sorted(skip_hour_set))
    if (vol_above > 0.0 or vol_below > 0.0) and vol_pctl is not None:
        # Auditability: this entry passed a declared vol-at-entry gate
        # (M21 E-2) — record the trigger bar's ATR percentile it passed at.
        package["meta"]["vol_at_entry_pctl"] = round(vol_pctl, 4)
    # M18 Phase A (observe-only): P_win entry-head annotation — same shape
    # as trend_donchian's. Never gates or sizes; allocator-soak consumer.
    try:
        from src.runtime.entry_head_pwin import maybe_score_entry_pwin

        _pw = maybe_score_entry_pwin(
            family="pullback", symbol=symbol, timeframe=timeframe,
            direction=direction, confidence=confidence, candles_df=df,
            strategy=label)
        if _pw is not None:
            package["meta"]["head_p_win"] = _pw["p_win"]
            package["meta"]["head_p_win_model"] = _pw["model_id"]
            package["meta"]["head_p_win_stage"] = _pw["stage"]
    except Exception:  # noqa: BLE001 — annotation must never block a signal
        pass
    return package


# ---------------------------------------------------------------------------
# monitor() — VERBATIM Chandelier ATR trail (copied from trend_donchian).
# ---------------------------------------------------------------------------
def _since_entry(candles_df: pd.DataFrame, open_pkg: Dict[str, Any]) -> pd.DataFrame:
    """Moved to `src/runtime/exit_levers.py::since_entry` — ONE definition.

    This module and `trend_donchian` each carried a byte-identical copy
    (docstring aside), and every R measurement depends on this window.
    """
    from src.runtime.exit_levers import since_entry

    return since_entry(candles_df, open_pkg)

def _pullback_thesis_intact(meta, candles_df, direction=None):
    """Is the trend-pullback thesis still intact? -> (bool|None, detail).

    TWO PREDICATES, IN PRIORITY ORDER, because this family declares its entry
    regime two different ways and a MINORITY of legs use the first:

    1. **ADX floor** -- when the leg declares ``adx_min``, the entry required
       ``ADX >= adx_min`` and the thesis holds while it still does. Unchanged.
    2. **Trend structure** -- when it does not. The entry ALSO required price on
       the correct side of the Donchian-``trend_lookback`` midline, and every
       leg in the family requires that, declared or not. The thesis holds while
       price is still on that side.

    ⚠️ **THE THESIS IS THE TREND, NOT THE PULLBACK, AND THAT IS THE WHOLE POINT.**
    The entry needed BOTH an uptrend AND a pullback into the lower
    ``pullback_frac`` of the recent range. Only the first is a *thesis*; the
    pullback is entry TIMING -- it says why we entered *now*, not why the trade
    should work. Re-evaluating it would invert the grade: a trade that is
    WORKING has moved AWAY from the pullback zone, so "is it still in the lower
    third?" reads a winner as thesis-broken. Pinned by test.

    ⚠️ **MOST LEGS DECLARE NO ``adx_min``** -- measured 2026-08-23 over the 19
    ENABLED legs the intent multiplexer routes to this module (16 ``live`` +
    3 ``shadow``): **6 declare a floor, 13 do not.** State the population: this
    is the enabled roster in ``config/strategies.yaml``, not every entry in the
    file, and it moves whenever a leg is added.

    ⚠️ **13 HERE vs 12 IN THE RESEARCH DOC IS A POPULATION DIFFERENCE, NOT A
    CONTRADICTION** -- state which one you mean. `bracket-expectation-
    construction-2026-08-23.md` § 7.5 counts the 12 SYMBOL-PINNED legs; this
    counts those plus the un-pinned base `htf_pullback_trend_2h` entry, which is
    also enabled and also declares no floor. 12 + 1 = 13, exactly.

    Before this those 13 returned ``None`` -> ``thesis_unknown`` -> never extend,
    and that was CORRECT reporting rather than a defect: those legs run **no ADX
    filter at entry at all**, so there was no declared regime condition to
    re-evaluate. Nor was there a value to port -- § 7.5 measured the family's own
    crypto floors (25/28/30) against **123 historical entries across 6 of the 12
    legs** (the other 6 were `insufficient_n` or `no_data`) and they would refuse
    a mean of 65%/74%/80%, range **53-86%**. The predicate was wrong for these
    legs, not the number. This gives them the predicate their entry actually uses.

    (That doc landed on `main` in PR #10183 on 2026-08-24, so the reference above
    resolves. This note replaced a caveat saying it did not exist yet -- true when
    written, stale the moment it merged.)

    ⚠️ **OBSERVE-ONLY, AND IT MUST STAY THAT WAY.** The sole consumer is
    ``target_extension_soak.annotate_from_monitor``, which returns nothing and
    moves no order, so widening this predicate changes what a soak row RECORDS,
    not what any trade DOES -- which is why it is Tier-1. A test asserts the
    call site stays annotate-only; if this ever reaches a close/verdict path it
    becomes Tier-3 and needs its own approval.

    Returns ``None`` -- *we could not look*, which never extends -- when neither
    predicate is computable. That is deliberately distinct from ``False``
    (*we looked; the thesis is broken*).
    """
    adx_detail = None
    try:
        adx_min_p = _coerce_float(meta.get("adx_min"))
        if adx_min_p is not None and adx_min_p > 0:
            period = int(_coerce_float(meta.get("adx_period")) or 14)
            if candles_df is None or len(candles_df) < period + 2:
                adx_detail = {"predicate": "adx_floor", "reason": "insufficient_bars"}
            else:
                value = float(_adx(candles_df, period).iloc[-1])
                if value != value:  # NaN
                    adx_detail = {"predicate": "adx_floor", "reason": "adx_nan"}
                else:
                    return bool(value >= adx_min_p), {
                        "predicate": "adx_floor", "adx": value,
                        "adx_min": adx_min_p, "adx_period": period,
                    }
        else:
            adx_detail = {"predicate": "adx_floor", "reason": "no_adx_min_declared"}
    except Exception:  # noqa: BLE001
        adx_detail = {"predicate": "adx_floor", "reason": "compute_failed"}

    # ---- 2. Trend structure: the entry condition every leg in the family uses.
    detail = {"predicate": "trend_structure",
              "adx_fallback_reason": (adx_detail or {}).get("reason")}
    try:
        side = str(direction or "").strip().lower()
        if side not in ("long", "short", "buy", "sell"):
            # Direction decides which side of the midline is INTACT, so an
            # unreadable one cannot be defaulted -- guessing would grade half
            # the book backwards.
            detail["reason"] = "direction_unreadable"
            return None, detail
        is_long = side in ("long", "buy")
        trend_lb = int(_coerce_float(meta.get("trend_lookback")) or 0)
        if trend_lb <= 0:
            detail["reason"] = "no_trend_lookback_declared"
            return None, detail
        if candles_df is None or len(candles_df) < trend_lb + 2:
            detail["reason"] = "insufficient_bars"
            return None, detail
        mid = float(_trend_midline(candles_df.reset_index(drop=True),
                                   trend_lb).iloc[-1])
        close = float(candles_df["close"].iloc[-1])
        if mid != mid or close != close:  # NaN
            detail["reason"] = "midline_nan"
            return None, detail
        intact = (close > mid) if is_long else (close < mid)
        detail.update({"trend_lookback": trend_lb, "midline": mid,
                       "close": close, "direction": "long" if is_long else "short"})
        return bool(intact), detail
    except Exception:  # noqa: BLE001
        detail["reason"] = "compute_failed"
        return None, detail


def monitor(cfg, candles_df, open_pkg):
    """Identical contract to ``trend_donchian.monitor`` — see that module."""
    if candles_df is None or len(candles_df) == 0:
        return None
    try:
        current_price = float(candles_df["close"].iloc[-1])
    except (KeyError, IndexError, ValueError, TypeError):
        return None
    try:
        sl = float(open_pkg["sl"])
        direction = str(open_pkg["direction"]).lower()
    except (KeyError, TypeError, ValueError):
        return None
    if direction not in ("long", "short"):
        return None

    meta = open_pkg.get("meta") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta) if meta else {}
        except Exception:  # noqa: BLE001
            meta = {}
    if not isinstance(meta, dict):
        meta = {}
    cfg_dict = cfg if isinstance(cfg, dict) else {}

    if direction == "long" and current_price <= sl:
        return {"action": "close", "reason": "sl_cross", "exit_price": current_price}
    if direction == "short" and current_price >= sl:
        return {"action": "close", "reason": "sl_cross", "exit_price": current_price}

    tp = _coerce_float(open_pkg.get("tp"))
    if tp is not None:
        if direction == "long" and current_price >= tp:
            return {"action": "close", "reason": "tp_cross", "exit_price": current_price}
        if direction == "short" and current_price <= tp:
            return {"action": "close", "reason": "tp_cross", "exit_price": current_price}

    # Target-extension ANNOTATE soak (exit-geometry rebuild, 2026-08-23).
    # OBSERVE-ONLY: returns nothing, moves no order. See
    # src/runtime/target_extension_soak.py. Placed after the two close checks
    # and before the levers, same position as on trend_donchian.
    #
    # THE THESIS IS THIS FAMILY'S OWN ENTRY CONDITION, and this family declares
    # it TWO ways -- so `_pullback_thesis_intact` tries both in priority order:
    # the declared ADX floor when there is one (6 of 19 enabled legs, measured
    # test every leg's entry uses (price on the correct side of the Donchian
    # 2026-08-23), else the trend-structure test every leg's entry uses (price
    # on the correct side of the Donchian `trend_lookback` midline; the other 13
    # declare no floor and would otherwise be
    # permanently `thesis_unknown`). Neither computable -> None ->
    # `thesis_unknown`, which never extends: *we did not look* is not *it holds*.
    # `direction` is passed because it decides WHICH side of the midline is
    # intact -- an unreadable one grades nothing rather than guessing.
    try:
        _thesis_ok, _thesis_detail = _pullback_thesis_intact(
            meta, candles_df, direction=open_pkg.get("direction"))
        from src.runtime.target_extension_soak import annotate_from_monitor
        annotate_from_monitor(
            strategy=str(open_pkg.get("strategy_name") or "htf_pullback_trend_2h"),
            open_pkg=open_pkg, meta=meta, price=current_price,
            thesis_intact=_thesis_ok, thesis=_thesis_detail,
        )
    except Exception:  # noqa: BLE001 — observe-only; never break the monitor
        pass

    # M20 R-based exit levers — the SHARED implementations in
    # src/runtime/exit_levers.py, the same bodies trend_donchian runs. Until
    # 2026-08-18 this family had NO close mechanism beyond sl_cross/tp_cross,
    # so a pullback leg could not run stale_stop or giveback_stop however its
    # YAML was written — while scripts/backtest_pullback.py has modelled BOTH
    # since M20. The harness simulated a book this module could not execute,
    # and 11 of the 22 open trades measured with no decision-driven exit path
    # on 2026-08-18 are this family.
    #
    # THE DECLARE IS THE GATE, exactly as on donchian: a leg whose YAML
    # declares the keys gets a REAL close; an undeclared leg evaluates the
    # reference cell and writes one observe-only row to
    # runtime_logs/exit_lever_soak.jsonl, returning None. So this block is
    # BYTE-IDENTICAL in effect for every leg live today — none declares either
    # key — and turning one on is the operator's Tier-3 decision.
    #
    # ORDER IS GIVEBACK THEN STALE, matching THIS family's own harness
    # (backtest_pullback._levers: giveback -> trend_flip -> stale). That is the
    # opposite of live trend_donchian.monitor, which runs stale first under a
    # comment claiming it matches the harness — it does not; both harnesses run
    # giveback first (backtest_trend.py, one per-bar loop, giveback at ~line
    # 566 and stale at ~582). Live/train parity for the family being wired
    # beats cross-family consistency with a module that is itself inverted.
    # The donchian inversion is LATENT — no live leg declares both keys, so the
    # order cannot bite — and is filed as
    # BL-20260818-LIVE-DONCHIAN-INVERTS-THE-HARNESS-LEVER-PRECEDENCE rather
    # than flipped here, because changing a live-money family's exit order is
    # Tier-3 and must not ride along inside a refactor.
    try:
        from src.runtime.exit_levers import giveback_verdict, stale_stop_verdict

        _gb = giveback_verdict(meta, cfg_dict, open_pkg, candles_df,
                               current_price, direction,
                               default_label="htf_pullback_trend_2h")
        if _gb is not None:
            return _gb
        _st = stale_stop_verdict(meta, cfg_dict, open_pkg, candles_df,
                                 current_price, direction,
                                 default_label="htf_pullback_trend_2h")
        if _st is not None:
            return _st
    except Exception:  # noqa: BLE001 — a lever must never break the monitor
        pass

    atr = _coerce_float(meta.get("atr"))
    if atr is None or atr <= 0:
        period = int(meta.get("atr_period") or cfg_dict.get("atr_period") or _DEFAULTS["atr_period"])
        try:
            atr = float(_atr(candles_df, period).iloc[-1])
        except Exception:  # noqa: BLE001
            return None
    if atr is None or atr <= 0:
        return None

    trail_mult = (
        _coerce_float(meta.get("trail_mult"))
        or _coerce_float(cfg_dict.get("trail_mult"))
        or float(_DEFAULTS["trail_mult"])
    )
    window = _since_entry(candles_df, open_pkg)
    # M20 P4.1 trail-decay lever — shared runtime helper; see
    # trend_donchian.monitor for the contract (YAML-declared per leg /
    # annotate-only undeclared; fail-safe to the base mult).
    try:
        from src.runtime.trail_decay import resolve_trail_mult

        trail_mult = resolve_trail_mult(meta, cfg_dict, open_pkg, window,
                                        trail_mult, direction)
    except Exception:  # noqa: BLE001 — the lever must never break the trail
        pass
    # M20-X vol-conditional trail lever (docs/research/M20X-vol-conditional-
    # trail-DESIGN.md): shared runtime helper — see trend_donchian.monitor for
    # the contract. YAML-declared per leg (Tier-3); undeclared = base mult
    # unchanged (byte-identical monitor). Composes with trail-decay via min()
    # (tightest fired mult wins), matching scripts/backtest_pullback.py's
    # _vol_tm — whose _atr (SMA-of-TR, min_periods=1) + trailing-ATR percentile
    # (rolling(win, min_periods=win).rank(pct=True)) are identical to the
    # trend_donchian helpers resolve_vol_trail_mult reuses, so live == train
    # for the pullback family too. Fail-safe to base_mult; never raises.
    try:
        from src.runtime.trail_vol import resolve_vol_trail_mult

        trail_mult = resolve_vol_trail_mult(meta, cfg_dict, candles_df,
                                            trail_mult, direction,
                                            open_pkg=open_pkg)
    except Exception:  # noqa: BLE001 — the lever must never break the trail
        pass
    # M31 P2 — position telemetry (observe-only); see trend_donchian.monitor
    # for the contract. Hooked here because `window` is already the since-entry
    # frame and the peak recorded is the same one the lever armed on.
    try:
        from src.runtime.position_telemetry import record_position_telemetry

        record_position_telemetry(
            open_pkg=open_pkg, meta=meta, window=window, direction=direction,
            current_price=current_price, stop=sl,
            target=_coerce_float(open_pkg.get("tp")),
            strategy=str(meta.get("strategy_label")
                         or open_pkg.get("strategy_name") or "") or None,
        )
    except Exception:  # noqa: BLE001 — telemetry must never break the trail
        pass
    try:
        if direction == "long":
            ext = float(window["high"].max())
            candidate = ext - trail_mult * atr
            if candidate > sl and candidate < current_price:
                return {"sl": round(candidate, 8)}
        else:
            ext = float(window["low"].min())
            candidate = ext + trail_mult * atr
            if candidate < sl and candidate > current_price:
                return {"sl": round(candidate, 8)}
    except (KeyError, ValueError, TypeError):
        return None
    return None
