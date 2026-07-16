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
    dc_hi = df["high"].rolling(trend_lb).max().shift(1)
    dc_lo = df["low"].rolling(trend_lb).min().shift(1)
    midline = (dc_hi + dc_lo) / 2.0
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
    for _key in ("trail_decay_arm_r", "trail_decay_stall_bars",
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
    meta = open_pkg.get("meta") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta) if meta else {}
        except Exception:  # noqa: BLE001
            meta = {}
    entry_ts = (meta.get("entry_time") if isinstance(meta, dict) else None) or \
        open_pkg.get("created_at")
    if entry_ts is None or "timestamp" not in getattr(candles_df, "columns", []):
        return candles_df
    try:
        ts = pd.to_datetime(candles_df["timestamp"], utc=True, errors="coerce")
        cutoff = pd.to_datetime(entry_ts, utc=True, errors="coerce")
        if pd.isna(cutoff):
            return candles_df
        filtered = candles_df[ts >= cutoff]
        return filtered if len(filtered) > 0 else candles_df
    except Exception:  # noqa: BLE001
        return candles_df


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
