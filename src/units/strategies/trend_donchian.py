"""Donchian-breakout trend-follower — units-layer adapter
(S-STRAT-IMPROVE-S8, go-live plan docs/sprint-plans/TREND-GOLIVE-PLAN-2026-05-23.md).

Strategy summary
----------------
Donchian-channel breakout entry + ATR initial stop + Chandelier (ATR)
trailing exit, on BTCUSDT 1h. The first net-positive strategy found in
the strategy-improvement program (net +22.5R / 3yr, robust parameter
plateau — see docs/audits/complementary-trend-strategy-2026-05-23.md).
Low win rate, occasional big winners, WIDE fee-efficient stops — the
opposite trade profile to the chop-sensitive 5m scalps, so it covers
the directional regimes (2023/2024) where ict_scalp loses.

This adapter ports the validated entry/exit logic from
``scripts/backtest_trend.py`` into the live
``order_package(cfg, candles_df) -> dict`` + ``monitor(cfg, candles_df,
open_pkg)`` contract (see ``src/units/strategies/_base.py``).

Entry
-----
On the most recent closed bar: a LONG when ``close`` prints above the
prior ``donchian``-bar high channel, a SHORT when it prints below the
prior-bar low channel. Initial stop is ``atr_stop_mult × ATR`` away from
entry. There is no fixed profit target — the trail is the sole
profit-exit, so ``tp`` is placed ``tp_r × risk`` away (a deliberately
far sentinel; matches the backtest, which has no TP).

Live trailing stop (the real-money-critical piece)
--------------------------------------------------
``monitor()`` re-implements the backtest's Chandelier exit as a live,
ratcheting stop-loss update. Each tick it recomputes the highest-high
(longs) / lowest-low (shorts) **since entry** and proposes a new stop at
``extreme ∓ trail_mult × ATR``. The proposed stop is only ever moved in
the favourable direction (a ratchet — it never loosens) and is never
placed on the wrong side of the current price (so a stale candle can't
cause an instant stop-out). The entry-time ATR is frozen in ``meta`` so
the trail distance matches the backtest's fixed-ATR semantics exactly.

Two design constraints from the runtime drove this shape:
  * ``run_monitor_tick`` calls ``monitor()`` with ``cfg={}`` in
    production, so every parameter the trail needs is carried in the
    package ``meta`` (read from the DB row each tick), not in cfg.
  * The monitor verdict can only return ``{"sl": ...}`` / ``{"tp":
    ...}`` / ``{"action": "close", ...}`` — there is no channel to
    persist extra state back to the package — so the trail is derived
    each tick from (a) the persisted SL (= the current ratchet level)
    and (b) the candle window, rather than from a stored running high.

Strategies are pure signal generators (no dry/live awareness); the
dry/live decision lives in the Accounts layer per ``mode:`` in
``config/accounts.yaml``.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

import pandas as pd

from src.runtime.tp_venue_cap import (  # the ONE owner of the clamp
    TP_VENUE_CAP_PCT as _TP_SENTINEL_CAP_PCT)
from src.units.strategies._base import require_candles


# Defaults mirror the validated config from scripts/backtest_trend.py +
# the robust-plateau centre (docs/audits/complementary-trend-strategy-
# 2026-05-23.md): donchian 20 / atr_stop 2.5 / trail 3.0. ``trail`` MUST
# stay LOOSER than the entry stop (trail_mult > atr_stop_mult) — the
# robustness sweep showed trail ≤ stop cuts winners early and turns the
# edge negative. Any caller may override via cfg.get(<name>); the runtime
# builder merges config/strategies.yaml::trend_donchian params into cfg.
_DEFAULTS: Dict[str, Any] = {
    "donchian": 20,
    "atr_period": 14,
    "atr_stop_mult": 2.5,
    "trail_mult": 3.0,
    # No fixed profit target — the Chandelier trail is the sole
    # profit-exit (matches the backtest). ``tp`` is set this many R away
    # so it acts as a far, effectively-unreachable sentinel that still
    # satisfies the pipeline's "signal carries full SL/TP" gate. Max
    # single-trade excursion in 3yr of backtest was ~10R, so 50R is
    # safely beyond anything the strategy realistically reaches.
    "tp_r": 50.0,
    "timeframe": "1h",
    # Minimum signal confidence (breakout depth / ATR, [0,1]) required to
    # emit an order. 0.0 = no gate. A 6yr BTC 2h sweep found 0.30 optimal
    # (net +25%, expectancy +44%, maxDD -35% vs ungated); the live value is
    # set in config/strategies.yaml.
    "min_confidence": 0.0,
    # M21 E-2 confirmation-bar entry lever (0 = off, byte-identical): a raw
    # breakout is actionable only after the close has HELD beyond the signal
    # bar's channel edge for this many further closed bars (entry then fires
    # at the latest close — worse price, fewer false breakouts). Mirrors
    # scripts/backtest_trend.py --confirm-bars exactly; declared per leg in
    # config/strategies.yaml (Tier-3).
    # NOTE (2026-08-09): this pointed at scripts/research/backtest_trend.py as
    # the lever's only home. True when written on 2026-08-08; false since
    # PR #8633 ported all 15 research-only levers into scripts/backtest_trend.py
    # (verified: it declares --confirm-bars), and the research copy is a retired
    # hard-fail shim as of 2026-08-09. Comment corrected, behaviour untouched
    # (field beats comment). There is now exactly ONE trend engine, enforced by
    # the trend-engine-convergence-guard.
    "confirm_bars": 0,
    # M21 E-2 time-of-day entry lever (empty = off, byte-identical): skip any
    # NEW entry whose TRIGGER bar's UTC hour is in this CSV set (e.g. "0").
    # With confirm_bars > 0 the trigger is the breakout bar confirm_bars back,
    # matching the harness (the skip gates the SIGNAL bar, not the entry bar).
    # Exits are never touched — an open trade rides through skipped hours
    # unchanged. Mirrors scripts/research/backtest_trend.py --skip-hours
    # exactly; declared per leg in config/strategies.yaml (Tier-3).
    "skip_hours": "",
    # M21 E-2 vol-at-entry lever (both 0.0 = off, byte-identical): skip any
    # NEW entry whose TRIGGER bar's ATR sits at an extreme TRAILING
    # percentile — the rank of the trigger bar's ATR within the previous
    # `vol_pctl_window` bars (causal; includes the bar itself). above>0
    # skips the hot tail (pctl > above); below>0 the dead tail (pctl <
    # below). Undefined percentile (fewer than `vol_pctl_window` bars in
    # the df — the live fetch is 200, matching the default window exactly)
    # NEVER skips (fail-permissive). Exits are never touched. Mirrors
    # scripts/research/backtest_trend.py --vol-skip-*-pctl exactly;
    # declared per leg in config/strategies.yaml (Tier-3).
    "vol_skip_above_pctl": 0.0,
    "vol_skip_below_pctl": 0.0,
    "vol_pctl_window": 200,
}


# Bybit (and most exchanges) reject TP further than ~10% from the
# reference base price (ErrCode 10001 — hit every trend_donchian short
# at BTC ~$75k on 2026-05-27). PR #2141 clamped the 50R sentinel to
# `entry*0.01` to satisfy the in-process `tp>0` pre-flight, but that
# value sits ~99% below entry and the exchange refuses it. Cap to ~9.9%
# from entry so the sentinel is exchange-valid AND still far enough that
# the monitor's Chandelier trail remains the real profit-exit.
# The venue TP clamp. ONE owner: src/runtime/tp_venue_cap.py, imported at
# the top of this file as the same local name, so the clamp expressions
# below are unchanged. The rationale (Bybit ErrCode 10001), why NO `tp_r`
# reproduces it, and the open question about non-Bybit legs live there.


def _resolve_params(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Return strategy params with cfg overrides on top of _DEFAULTS."""
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
    """ATR as the simple moving average of True Range.

    Identical formula to ``scripts/backtest_trend.py::_atr`` so the live
    stop distance matches what was validated. (Not Wilder's smoothing —
    deliberately the same SMA-of-TR the backtest used.)
    """
    h, low, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([(h - low), (h - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=1).mean()


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def _confirmed_breakout(df: pd.DataFrame, dc_hi: pd.Series, dc_lo: pd.Series,
                        atr_series: pd.Series, n: int, long_only: bool,
                        label: str) -> tuple:
    """Return (direction, signal_bar_depth) for a matured N-bar confirmation.

    Mirrors ``scripts/backtest_trend.py``'s pending-entry semantics exactly
    (that engine declares ``--confirm-bars``; the research copy that once held
    the lever was retired 2026-08-09):
    the raw breakout fired at the bar ``n`` bars back (the signal bar); every
    close since must have HELD beyond THAT bar's channel edge, with no
    opposite raw breakout in between (a suppressed side on a long_only leg
    never cancels, matching the harness's zeroed signal). The depth gate is
    evaluated at the signal bar (its own channel edge + ATR), as the harness
    does before creating the pending. Raises ValueError (the standard
    non-actionable path) when no matured confirmation exists.
    """
    t = len(df) - 1
    s = t - n
    hi_s, lo_s = dc_hi.iloc[s], dc_lo.iloc[s]
    atr_s = float(atr_series.iloc[s]) if pd.notna(atr_series.iloc[s]) else 0.0
    if pd.isna(hi_s) or pd.isna(lo_s) or atr_s <= 0:
        raise ValueError(
            f"Strategy '{label}': channel/ATR undefined at the confirmation "
            "signal bar (non-actionable)."
        )
    hi_s, lo_s = float(hi_s), float(lo_s)
    close_s = float(df["close"].iloc[s])
    if close_s > hi_s:
        direction, level, depth = "long", hi_s, (close_s - hi_s) / atr_s
    elif close_s < lo_s and not long_only:
        direction, level, depth = "short", lo_s, (lo_s - close_s) / atr_s
    else:
        raise ValueError(
            f"Strategy '{label}': no breakout {n} bar(s) back to confirm "
            "(non-actionable)."
        )
    for i in range(s + 1, t + 1):
        ci = float(df["close"].iloc[i])
        held = ci > level if direction == "long" else ci < level
        if direction == "long":
            lo_i = dc_lo.iloc[i]
            opp = (not long_only) and pd.notna(lo_i) and ci < float(lo_i)
        else:
            hi_i = dc_hi.iloc[i]
            opp = pd.notna(hi_i) and ci > float(hi_i)
        if not held or opp:
            raise ValueError(
                f"Strategy '{label}': breakout confirmation failed at bar "
                f"{i - s}/{n} (close back inside / opposite break) — "
                "non-actionable."
            )
    return direction, depth


def order_package(cfg: dict, candles_df: Optional[pd.DataFrame] = None) -> dict:
    """Build a trend_donchian OrderPackage dict from the latest candles.

    Parameters
    ----------
    cfg : dict
        Strategy config (config/strategies.yaml::trend_donchian merged
        with the resolved symbol by the runtime builder).
    candles_df : pd.DataFrame
        OHLCV frame at the configured timeframe (1h). Required.

    Returns
    -------
    dict
        Keys: symbol, direction, entry, sl, tp, confidence, meta.

    Raises
    ------
    ValueError
        When candles are absent, there are too few rows for the
        Donchian / ATR windows, or the latest bar is not a breakout
        (non-actionable — the runtime builder treats this as side="none").
    """
    candles_df = require_candles(candles_df, "trend_donchian")
    params = _resolve_params(cfg)
    symbol = cfg.get("symbol") or cfg.get("SYMBOL") or "BTCUSDT"
    # Clone-template strategies (mes/mgc/xauusd/… variants) reuse this unit; the
    # caller threads its OWN name via cfg["strategy_label"] so the non-actionable
    # reason strings name the emitting strategy, not the parent template
    # (BL-20260611-003). Defaults to the canonical name for the flagship caller.
    label = str(cfg.get("strategy_label") or "trend_donchian")

    donchian = int(params["donchian"])
    atr_period = int(params["atr_period"])
    atr_stop_mult = float(params["atr_stop_mult"])
    trail_mult = float(params["trail_mult"])
    tp_r = float(params["tp_r"])
    timeframe = str(cfg.get("timeframe") or params["timeframe"])

    confirm_bars = int(params["confirm_bars"] or 0)
    needed = donchian + atr_period + 2 + max(confirm_bars, 0)
    if len(candles_df) < needed:
        raise ValueError(
            f"Strategy '{label}': need at least {needed} candles for "
            f"the donchian({donchian}) / atr({atr_period}) windows; got "
            f"{len(candles_df)}."
        )

    df = candles_df.reset_index(drop=True)
    atr_series = _atr(df, atr_period)
    dc_hi = df["high"].rolling(donchian).max().shift(1)
    dc_lo = df["low"].rolling(donchian).min().shift(1)

    atr = float(atr_series.iloc[-1]) if pd.notna(atr_series.iloc[-1]) else 0.0
    close = float(df["close"].iloc[-1])
    hi = dc_hi.iloc[-1]
    lo = dc_lo.iloc[-1]

    if atr <= 0 or pd.isna(hi) or pd.isna(lo):
        raise ValueError(
            f"Strategy '{label}': ATR non-positive or Donchian channel "
            "undefined on the latest bar (non-actionable)."
        )

    hi = float(hi)
    lo = float(lo)
    if confirm_bars > 0:
        # M21 E-2 confirmation-bar lever: the breakout fired confirm_bars
        # bars back and every close since held beyond that bar's channel
        # edge; entry fires at the LATEST close (below), the depth gate at
        # the signal bar. Raises the standard non-actionable ValueError
        # when no matured confirmation exists.
        direction, breakout_depth = _confirmed_breakout(
            df, dc_hi, dc_lo, atr_series, confirm_bars,
            bool(cfg.get("long_only", False)), label)
    elif close > hi:
        direction = "long"
    elif close < lo:
        direction = "short"
    else:
        raise ValueError(
            f"Strategy '{label}': no breakout on the latest bar "
            f"(close={close} within channel [{lo}, {hi}]) — non-actionable."
        )

    # M21 E-2 time-of-day gate — placed after direction resolution, before
    # the depth/confidence gate, mirroring scripts/research/backtest_trend.py
    # bar-for-bar: the gate reads the TRIGGER bar (the breakout/signal bar —
    # confirm_bars back when the confirmation lever is on, else the latest
    # bar). Fail-permissive: an unparseable timestamp never skips.
    skip_hour_set = _parse_skip_hours(params.get("skip_hours"))
    if skip_hour_set:
        trigger_idx = -1 - confirm_bars if confirm_bars > 0 else -1
        trigger_hour = _bar_hour_utc(df, trigger_idx)
        if trigger_hour is not None and trigger_hour in skip_hour_set:
            raise ValueError(
                f"Strategy '{label}': trigger bar hour {trigger_hour} in "
                f"skip_hours {sorted(skip_hour_set)} — time-of-day gate, "
                "non-actionable."
            )

    # M21 E-2 vol-at-entry gate — same trigger-bar anchor as skip_hours,
    # mirroring scripts/research/backtest_trend.py bar-for-bar. An undefined
    # percentile (window unfilled / any error) never skips (fail-permissive).
    vol_above = _coerce_float(params.get("vol_skip_above_pctl")) or 0.0
    vol_below = _coerce_float(params.get("vol_skip_below_pctl")) or 0.0
    vol_pctl: Optional[float] = None
    if vol_above > 0.0 or vol_below > 0.0:
        vol_window = int(_coerce_float(params.get("vol_pctl_window")) or 200)
        trigger_idx = -1 - confirm_bars if confirm_bars > 0 else -1
        vol_pctl = _trailing_atr_pctl(atr_series, trigger_idx, vol_window)
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

    entry = close
    if direction == "long":
        sl = entry - atr_stop_mult * atr
        risk = entry - sl
        # See `_TP_SENTINEL_CAP_PCT` — cap the 50R sentinel within the
        # exchange's TP-distance tolerance.
        tp = min(entry * (1 + _TP_SENTINEL_CAP_PCT), entry + tp_r * risk)
        if confirm_bars == 0:
            breakout_depth = (close - hi) / atr
    else:
        sl = entry + atr_stop_mult * atr
        risk = sl - entry
        tp = max(entry * (1 - _TP_SENTINEL_CAP_PCT), entry - tp_r * risk)
        if confirm_bars == 0:
            breakout_depth = (lo - close) / atr

    if risk <= 0:
        raise ValueError(
            f"Strategy '{label}': non-positive risk after stop "
            "computation; skipping signal."
        )

    # Confidence: breakout depth past the channel, normalised to ATR and
    # clamped to [0, 1]. A clean break well past the channel scores
    # higher; a marginal poke scores near 0.
    confidence = round(min(max(breakout_depth, 0.0), 1.0), 4)

    # Minimum-confidence entry gate. Below the floor the break is too
    # shallow to be worth the fee-and-stop risk (a 6yr 2h sweep showed
    # low-confidence breaks are where the strategy bleeds); skip via the
    # same non-actionable path as "no breakout".
    min_confidence = float(params["min_confidence"])
    if confidence < min_confidence:
        raise ValueError(
            f"Strategy '{label}': confidence {confidence} below "
            f"min_confidence {min_confidence} — non-actionable."
        )

    # entry_time anchors the monitor's since-entry highest-high window so
    # a long-running trade's trail tracks the extreme from entry forward,
    # not from an arbitrary pre-entry bar in the fetched window.
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
            "donchian_hi": hi,
            "donchian_lo": lo,
            "donchian": donchian,
            # Entry-time ATR is FROZEN here and used by the monitor for
            # the trail distance, matching the backtest's fixed-ATR trail
            # (scripts/backtest_trend.py uses the entry bar's ATR for the
            # whole trade). Without this the live trail would drift with a
            # rolling ATR and diverge from what was validated.
            "atr": atr,
            "atr_period": atr_period,
            "atr_stop_mult": atr_stop_mult,
            "trail_mult": trail_mult,
            "tp_r": tp_r,
            "risk_per_unit": float(risk),
            "entry_time": entry_time,
            # Canonical key the order_monitor's ohlcv_fetcher reads to
            # pull fresh candles for monitor(). Without it the fetcher
            # short-circuits to None and the trail never updates.
            "timeframe": timeframe,
        },
    }
    # M20 stale-stop (Tier-3, YAML-declared): thread the declared params into
    # the package meta because run_monitor_tick passes cfg={} in production —
    # meta is the only channel monitor() reliably sees. Absent = the lever is
    # annotate-only (see _stale_stop_verdict); declared = a real close path.
    for _key in ("stale_exit_bars", "stale_exit_below_r",
                 "giveback_min_mfe_r", "giveback_r",
                 "trail_decay_arm_r", "trail_decay_stall_bars",
                 "trail_decay_tight_mult",
                 # M20-X vol-conditional trail (paper test, 2026-07-15):
                 "trail_vol_below_pctl", "trail_vol_above_pctl",
                 "trail_vol_tight_mult", "vol_pctl_window"):
        if cfg.get(_key) is not None:
            package["meta"][_key] = cfg[_key]
    if confirm_bars > 0:
        # Auditability: record that this entry was confirmation-gated
        # (M21 E-2). Entry-side only — the monitor never reads it.
        package["meta"]["confirm_bars"] = confirm_bars
    if skip_hour_set:
        # Auditability: this entry passed a declared time-of-day gate
        # (M21 E-2). Entry-side only — the monitor never reads it.
        package["meta"]["skip_hours"] = ",".join(str(h) for h in sorted(skip_hour_set))
    if (vol_above > 0.0 or vol_below > 0.0) and vol_pctl is not None:
        # Auditability: this entry passed a declared vol-at-entry gate
        # (M21 E-2) — record the trigger bar's ATR percentile it passed at.
        package["meta"]["vol_at_entry_pctl"] = round(vol_pctl, 4)
    # M18 Phase A (observe-only): annotate the signal with the P_win entry
    # head's score so the allocator soak sees it next to the confidence
    # proxy (rides Intent.meta -> SignalPackage.raw). Never gates or sizes.
    try:
        from src.runtime.entry_head_pwin import maybe_score_entry_pwin

        _pw = maybe_score_entry_pwin(
            family="donchian", symbol=symbol, timeframe=timeframe,
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
# monitor() — live Chandelier ATR trailing stop
# ---------------------------------------------------------------------------


# M20 stale-stop reference params — the harness-validated cell (8 native
# bars, still below 0R). Used ONLY for the observe-only annotate soak when a
# strategy has not declared its own params; a declared strategy uses exactly
# what its YAML says.
_STALE_REF_BARS = 8
_STALE_REF_BELOW_R = 0.0


def _coerce_int(value: Any) -> Optional[int]:
    try:
        i = int(value)
        return i if i > 0 else None
    except (TypeError, ValueError):
        return None


def _exit_head_verdict(
    rec: Optional[Dict[str, Any]],
    meta: Dict[str, Any],
    cfg_dict: Dict[str, Any],
    current_price: float,
) -> Optional[Dict[str, Any]]:
    """M20 E3 apply path — full close when the ADVISORY-stage exit head
    fires AND the strategy YAML declares it. Fail-closed on anything
    missing or malformed (returns ``None``); **never raises**.

    Gates, all required:
    - ``rec`` — a fresh score from ``maybe_score_exit_head`` (None on any
      scoring skip, incl. the once-per-closed-bar dedup — so the decision
      is evaluated once per bar, matching the trained policy's cadence).
    - ``exit_head_action: close`` declared in meta (new packages) or cfg
      (live YAML — covers already-open packages via the monitor's
      live-cfg default).
    - artifact ``stage == "advisory"`` — the operator promotion gate; a
      shadow-stage artifact NEVER closes anything.
    - optional ``exit_head_model`` pin must match the artifact's model_id.
    - the conditional policy fires: score < τ AND open_r < below_r, where
      τ is ``exit_head_threshold`` (meta/cfg override) or the artifact's
      own shape default.
    """
    try:
        if not rec:
            return None
        action = str(meta.get("exit_head_action")
                     or cfg_dict.get("exit_head_action") or "").lower()
        if action != "close":
            return None
        if str(rec.get("stage") or "") != "advisory":
            return None
        pin = meta.get("exit_head_model") or cfg_dict.get("exit_head_model")
        if pin and str(pin) != str(rec.get("model_id")):
            return None
        tau = _coerce_float(meta.get("exit_head_threshold")
                            or cfg_dict.get("exit_head_threshold"))
        if tau is None:
            tau = _coerce_float(rec.get("tau"))
        below_r = _coerce_float(rec.get("below_r"))
        score = _coerce_float(rec.get("score"))
        open_r = _coerce_float((rec.get("feature_row") or {}).get("open_r"))
        if None in (tau, below_r, score) or open_r is None:
            return None
        # The firing rule follows the artifact's declared SHAPE (mirrors
        # exit_head_shadow.py's would_exit): the below_half_r head fires LOW
        # scores on losers (score < tau AND open_r < below_r); the peak_*
        # heads fire HIGH scores when the peak is in (score > tau [AND
        # open_r >= below_r for peak_winner]). Hardcoding the below_half_r
        # rule would fire a peak head on exactly the wrong condition
        # (MB-20260716 / M20 P4.2 graduation). `exit_head_threshold` still
        # overrides tau on either branch.
        policy = str(rec.get("policy") or "below_half_r")
        if policy.startswith("peak"):
            fires = score > tau and (
                policy != "peak_winner" or open_r >= below_r)
        else:
            fires = score < tau and open_r < below_r
        if not fires:
            return None
        return {"action": "close", "reason": "exit_head",
                "exit_price": current_price}
    except Exception:  # noqa: BLE001 — fail-closed, never a spurious close
        return None


def _stale_stop_verdict(meta, cfg_dict, open_pkg, candles_df,
                        current_price, direction):
    """Moved to `src/runtime/exit_levers.py` — see that module for the contract.

    Kept as a thin delegation rather than deleted: the body is now SHARED with
    the pullback family, and a shim makes the move reviewable as a move (this
    module's behaviour is unchanged) instead of as a rewrite.
    """
    from src.runtime.exit_levers import stale_stop_verdict

    return stale_stop_verdict(meta, cfg_dict, open_pkg, candles_df,
                              current_price, direction,
                              default_label="trend_donchian")

# M20 giveback-stop reference params — the fleet-sweep-validated cell
# (exit at close once the trade has SEEN >= 1R of open profit and given
# back >= 1R from that peak; "grab the PnL" instead of riding the full
# retrace to the chandelier trail). Used ONLY for the observe-only
# annotate soak when a strategy has not declared its own params; a
# declared strategy uses exactly what its YAML says.
_GIVEBACK_REF_MIN_MFE_R = 1.0
_GIVEBACK_REF_GIVEBACK_R = 1.0


def _giveback_verdict(meta, cfg_dict, open_pkg, candles_df,
                      current_price, direction):
    """Moved to `src/runtime/exit_levers.py` — see that module for the contract."""
    from src.runtime.exit_levers import giveback_verdict

    return giveback_verdict(meta, cfg_dict, open_pkg, candles_df,
                            current_price, direction,
                            default_label="trend_donchian")

def _since_entry(candles_df: pd.DataFrame, open_pkg: Dict[str, Any]) -> pd.DataFrame:
    """Moved to `src/runtime/exit_levers.py::since_entry` — ONE definition.

    This module and `htf_pullback_trend_2h` each carried a byte-identical copy
    (docstring aside). Every R measurement in the system depends on this window,
    so two copies of it is the defect class `_regime_score_semantics.py` exists
    to prevent.
    """
    from src.runtime.exit_levers import since_entry

    return since_entry(candles_df, open_pkg)

def _donchian_thesis_intact(meta, candles_df, current_price, direction):
    """Is the Donchian breakout thesis still intact? -> (bool|None, detail).

    The strategy's OWN entry condition, re-evaluated on fresh candles: a long
    entered because price broke the prior `donchian`-bar high, so the thesis
    holds while price still sits at or above that rolling high. Returns
    ``None`` when the channel cannot be recomputed — *we could not look*, which
    `evaluate_extension` maps to `thesis_unknown` and never extends.

    Excludes the forming bar from the channel (``[-n-1:-1]``) exactly as the
    entry side does, so the level compared against is a CLOSED-bar extreme and
    price is not being compared to a channel it is itself setting.
    """
    try:
        n = int(_coerce_float(meta.get("donchian")) or 0)
        if n <= 0 or candles_df is None or len(candles_df) < n + 1:
            return None, {"predicate": "donchian_rebreak", "reason": "insufficient_bars"}
        window = candles_df.iloc[-n - 1:-1]
        hi = float(window["high"].max())
        lo = float(window["low"].min())
        if direction == "long":
            intact = current_price >= hi
            level = hi
        else:
            intact = current_price <= lo
            level = lo
        return bool(intact), {
            "predicate": "donchian_rebreak", "donchian": n,
            "level": level, "price": current_price,
        }
    except Exception:  # noqa: BLE001
        return None, {"predicate": "donchian_rebreak", "reason": "compute_failed"}


def monitor(cfg, candles_df, open_pkg):
    """Re-evaluate an open trend_donchian package against fresh candles.

    Close-path priority (first match wins), then the trailing ratchet:

    1. **SL-cross** — price has hit the package's ``sl`` (long: close ≤
       sl; short: close ≥ sl). Full close. (Belt-and-braces: the
       exchange-side SL normally fires first on linear perps; this
       catches the case where it didn't.)
    2. **TP-cross** — price crossed the far sentinel ``tp`` (long: close
       ≥ tp; short: close ≤ tp). Full close. Practically never fires
       given ``tp_r`` defaults to 50R; present for completeness.
    3. **Chandelier trail ratchet** — propose a new stop at
       ``extreme ∓ trail_mult × ATR`` using the since-entry extreme and
       the frozen entry-time ATR. Returned as ``{"sl": new_sl}`` ONLY
       when it tightens the stop (ratchet) AND sits on the correct side
       of the current price (never an instant stop-out).
    4. Otherwise ``None`` — no change.

    See ``_base.monitor_breakeven_sl`` for the verdict return contract.
    Reads all trail parameters from ``open_pkg["meta"]`` because
    ``run_monitor_tick`` passes ``cfg={}`` in production.
    """
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

    # 1. SL-cross close.
    if direction == "long" and current_price <= sl:
        return {"action": "close", "reason": "sl_cross", "exit_price": current_price}
    if direction == "short" and current_price >= sl:
        return {"action": "close", "reason": "sl_cross", "exit_price": current_price}

    # 2. TP-cross close (far sentinel; rarely fires).
    tp = _coerce_float(open_pkg.get("tp"))
    if tp is not None:
        if direction == "long" and current_price >= tp:
            return {"action": "close", "reason": "tp_cross", "exit_price": current_price}
        if direction == "short" and current_price <= tp:
            return {"action": "close", "reason": "tp_cross", "exit_price": current_price}

    # 2.45 Target-extension ANNOTATE soak (exit-geometry rebuild, 2026-08-23).
    # OBSERVE-ONLY: it returns nothing and moves no order — it records what a
    # target-extension decision WOULD do, so the Tier-3 flip has an evidence
    # trail. Placed AFTER the two close checks (a trade that should close is
    # not a trade whose target we are extending) and BEFORE the levers, so a
    # row is written even on ticks a lever then acts on.
    #
    # THE THESIS IS THE STRATEGY'S OWN ENTRY CONDITION, RE-EVALUATED — per
    # exit-mechanism-construction-PROCESS.md § 4, a revision rule that reads
    # only the trade's own path is the eleven-endogenous-feature substrate
    # already identified as the root cause. For a Donchian breakout the thesis
    # is "the channel is still being pushed", so `_donchian_thesis_intact`
    # recomputes the entry-time channel and asks whether price still sits at
    # its extreme in the trade's direction. An unreadable channel yields None
    # → `thesis_unknown`, which never extends.
    try:
        _thesis_ok, _thesis_detail = _donchian_thesis_intact(
            meta, candles_df, current_price, direction
        )
        from src.runtime.target_extension_soak import annotate_from_monitor
        annotate_from_monitor(
            strategy=str(open_pkg.get("strategy_name") or "trend_donchian"),
            open_pkg=open_pkg, meta=meta, price=current_price,
            thesis_intact=_thesis_ok, thesis=_thesis_detail,
        )
    except Exception:  # noqa: BLE001 — observe-only; never break the monitor
        pass

    # 2.5 M20 conditional stale-stop (evidence: docs/research/
    # M20-exit-refinement-2026-07-12.md § 4-5). Behaviour is YAML-declared:
    # a strategy whose config (threaded into meta by order_package) sets
    # `stale_exit_bars` gets a REAL close; every other donchian-family
    # package is evaluated at the proposed reference params and, when the
    # lever WOULD fire, logs one observe-only annotate row instead
    # (runtime_logs/exit_lever_soak.jsonl) — the pre-declare soak.
    stale_verdict = _stale_stop_verdict(
        meta, cfg_dict, open_pkg, candles_df, current_price, direction
    )
    if stale_verdict is not None:
        return stale_verdict

    # 2.55 M20 giveback-stop (fleet-sweep evidence: runtime_logs/m20_fleet/
    # 2026-07-12 — USO-1h gb1R@MFE1R walk-forward PASS). Same YAML-declared
    # contract as the stale-stop above: `giveback_min_mfe_r` + `giveback_r`
    # declared (threaded into meta by order_package; live cfg covers
    # already-open packages) ⇒ a REAL close; undeclared ⇒ reference-cell
    # annotate row only. Checked AFTER stale-stop, matching the harness's
    # exit precedence.
    giveback_verdict = _giveback_verdict(
        meta, cfg_dict, open_pkg, candles_df, current_price, direction
    )
    if giveback_verdict is not None:
        return giveback_verdict

    # 2.6 M20 exit head — E2 shadow scoring + E3 apply (memo § 9; program
    # doc § E3). Scoring logs once per closed bar and never raises; a
    # missing artifact (mirror not published / dev sandbox) is a cheap
    # no-op. The APPLY below requires ALL of: (a) the strategy's YAML
    # declares `exit_head_action: close` (threaded into meta for new
    # packages; live cfg covers already-open ones), (b) the mirrored
    # artifact is at stage "advisory" — the operator promotion gate — and
    # (c) the conditional policy fires (P(pays) < τ AND open_r < below_r;
    # proven trades above +0.5R are never touched — the trail owns them).
    # Rollback = delete the YAML lines and/or demote the artifact stage.
    try:
        from src.runtime.exit_head_shadow import maybe_score_exit_head

        _eh_rec = maybe_score_exit_head(meta, open_pkg, candles_df, direction)
    except Exception:  # noqa: BLE001 — scoring must never affect the monitor
        _eh_rec = None
    eh_verdict = _exit_head_verdict(_eh_rec, meta, cfg_dict, current_price)
    if eh_verdict is not None:
        return eh_verdict

    # 3. Chandelier trail ratchet.
    atr = _coerce_float(meta.get("atr"))
    if atr is None or atr <= 0:
        # Legacy / missing meta — recompute a rolling ATR from candles.
        period = int(
            meta.get("atr_period")
            or cfg_dict.get("atr_period")
            or _DEFAULTS["atr_period"]
        )
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
    # M20 P4.1 trail-decay lever (docs/research/M20-momentum-exhaustion-
    # DESIGN.md § P4.1): the EFFECTIVE mult tightens once the move is R-armed
    # or stalls. YAML-declared per leg (Tier-3); undeclared = base mult
    # unchanged + an observe-only annotate row when the reference cell would
    # arm. Fail-safe to base_mult on any missing input; never raises.
    try:
        from src.runtime.trail_decay import resolve_trail_mult

        trail_mult = resolve_trail_mult(meta, cfg_dict, open_pkg, window,
                                        trail_mult, direction)
    except Exception:  # noqa: BLE001 — the lever must never break the trail
        pass
    # M20-X vol-conditional trail lever (docs/research/M20X-vol-conditional-
    # trail-DESIGN.md): the EFFECTIVE mult tightens on any managed bar whose
    # trailing ATR percentile sits in the gated tail. YAML-declared per leg
    # (Tier-3); undeclared = base mult unchanged. Composes with trail-decay
    # via min() (tightest fired mult wins), matching the harness. Fail-safe to
    # base_mult on any missing input; never raises.
    try:
        from src.runtime.trail_vol import resolve_vol_trail_mult

        trail_mult = resolve_vol_trail_mult(meta, cfg_dict, candles_df,
                                            trail_mult, direction,
                                            open_pkg=open_pkg)
    except Exception:  # noqa: BLE001 — the lever must never break the trail
        pass
    # M31 P2 — position telemetry (observe-only). Hooked HERE because `window`
    # is already the since-entry frame and the peak this records is the same
    # one `resolve_trail_mult` just armed on: one definition, no extra fetch,
    # no second notion of MFE. Reads nothing back and cannot alter the trail.
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
            # Ratchet up only; never above the current price.
            if candidate > sl and candidate < current_price:
                return {"sl": round(candidate, 8)}
        else:
            ext = float(window["low"].min())
            candidate = ext + trail_mult * atr
            # Ratchet down only; never below the current price.
            if candidate < sl and candidate > current_price:
                return {"sl": round(candidate, 8)}
    except (KeyError, ValueError, TypeError):
        return None

    return None
