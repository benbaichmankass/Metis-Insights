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
    "confirm_bars": 0,
}


# Bybit (and most exchanges) reject TP further than ~10% from the
# reference base price (ErrCode 10001 — hit every trend_donchian short
# at BTC ~$75k on 2026-05-27). PR #2141 clamped the 50R sentinel to
# `entry*0.01` to satisfy the in-process `tp>0` pre-flight, but that
# value sits ~99% below entry and the exchange refuses it. Cap to ~9.9%
# from entry so the sentinel is exchange-valid AND still far enough that
# the monitor's Chandelier trail remains the real profit-exit.
_TP_SENTINEL_CAP_PCT = 0.099


def _resolve_params(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Return strategy params with cfg overrides on top of _DEFAULTS."""
    return {key: cfg.get(key, default) for key, default in _DEFAULTS.items()}


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

    Mirrors ``scripts/backtest_trend.py``'s pending-entry semantics exactly:
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
                 "trail_decay_tight_mult"):
        if cfg.get(_key) is not None:
            package["meta"][_key] = cfg[_key]
    if confirm_bars > 0:
        # Auditability: record that this entry was confirmation-gated
        # (M21 E-2). Entry-side only — the monitor never reads it.
        package["meta"]["confirm_bars"] = confirm_bars
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
        if not (score < tau and open_r < below_r):
            return None
        return {"action": "close", "reason": "exit_head",
                "exit_price": current_price}
    except Exception:  # noqa: BLE001 — fail-closed, never a spurious close
        return None


def _stale_stop_verdict(
    meta: Dict[str, Any],
    cfg_dict: Dict[str, Any],
    open_pkg: Dict[str, Any],
    candles_df: pd.DataFrame,
    current_price: float,
    direction: str,
) -> Optional[Dict[str, Any]]:
    """M20 conditional stale-stop — close a position that is ≥ N native bars
    old and still below the declared open-R threshold at bar close.

    Declared (``stale_exit_bars`` in meta/cfg) ⇒ may return a real
    ``{"action": "close", "reason": "stale_stop"}`` verdict. Undeclared ⇒
    evaluates the reference cell (8 bars, < 0R) and writes one observe-only
    annotate row when it would fire, returning ``None`` (behaviour unchanged).
    Fail-safe: any missing input (entry_time, frozen risk, entry) skips both
    paths — never a spurious close. **Never raises.**
    """
    try:
        declared_bars = _coerce_int(
            meta.get("stale_exit_bars") if meta.get("stale_exit_bars") is not None
            else cfg_dict.get("stale_exit_bars")
        )
        below_r_raw = (
            meta.get("stale_exit_below_r")
            if meta.get("stale_exit_below_r") is not None
            else cfg_dict.get("stale_exit_below_r")
        )
        below_r = _coerce_float(below_r_raw)
        n_bars = declared_bars if declared_bars is not None else _STALE_REF_BARS
        threshold = below_r if (declared_bars is not None and below_r is not None) \
            else (_STALE_REF_BELOW_R if declared_bars is None else 0.0)

        entry = _coerce_float(open_pkg.get("entry"))
        risk = _coerce_float(meta.get("risk_per_unit"))
        if entry is None or risk is None or risk <= 0:
            return None
        if not meta.get("entry_time"):
            return None  # age unknowable — fail-safe skip
        window = _since_entry(candles_df, open_pkg)
        # _since_entry falls back to the FULL frame when the entry time can't
        # be matched; that would fake a huge age, so require a real restriction
        # (or a genuinely long-lived trade spanning the whole fetch window).
        if len(window) >= len(candles_df) and len(candles_df) > 0:
            # Ambiguous: either fallback or a trade older than the fetch
            # window (limit≈200 bars ≫ any sane stale_exit_bars). Treat a
            # full-window match as "at least window-length old" ONLY when the
            # first window bar is at/after the entry time; _since_entry
            # guarantees that when it actually filtered, so equality here
            # means fallback — skip (fail-safe).
            return None
        age_bars = max(0, len(window) - 1)  # bars strictly after the entry bar
        if age_bars < n_bars:
            return None
        open_r = ((current_price - entry) if direction == "long"
                  else (entry - current_price)) / risk
        if open_r >= threshold:
            return None
        if declared_bars is not None:
            return {"action": "close", "reason": "stale_stop",
                    "exit_price": current_price}
        # Annotate-only path (undeclared): observe, never act.
        try:
            from src.runtime.exit_lever_soak import record_exit_lever_annotation

            record_exit_lever_annotation(
                lever="stale_stop",
                strategy=str(meta.get("strategy_label")
                             or open_pkg.get("strategy_name") or "trend_donchian"),
                symbol=str(open_pkg.get("symbol") or ""),
                direction=direction,
                order_package_id=open_pkg.get("order_package_id"),
                params={"stale_exit_bars": n_bars,
                        "stale_exit_below_r": threshold},
                state={"age_bars": age_bars, "open_r": round(open_r, 4),
                       "price": current_price, "entry": entry},
            )
        except Exception:  # noqa: BLE001 — annotate must never affect the path
            pass
        return None
    except Exception:  # noqa: BLE001 — monitor must never crash on this lever
        return None


# M20 giveback-stop reference params — the fleet-sweep-validated cell
# (exit at close once the trade has SEEN >= 1R of open profit and given
# back >= 1R from that peak; "grab the PnL" instead of riding the full
# retrace to the chandelier trail). Used ONLY for the observe-only
# annotate soak when a strategy has not declared its own params; a
# declared strategy uses exactly what its YAML says.
_GIVEBACK_REF_MIN_MFE_R = 1.0
_GIVEBACK_REF_GIVEBACK_R = 1.0


def _giveback_verdict(
    meta: Dict[str, Any],
    cfg_dict: Dict[str, Any],
    open_pkg: Dict[str, Any],
    candles_df: pd.DataFrame,
    current_price: float,
    direction: str,
) -> Optional[Dict[str, Any]]:
    """M20 giveback-stop — close a position that has seen at least
    ``giveback_min_mfe_r`` R of open profit (peak basis, since entry) and
    has given back at least ``giveback_r`` R from that peak at bar close.
    An R-based profit lock, distinct from the price/ATR chandelier trail —
    the harness reference is ``scripts/research/backtest_trend.py``'s
    ``gb`` lever (identical peak_r/r_close math).

    Declared (BOTH ``giveback_min_mfe_r`` AND ``giveback_r`` positive in
    meta/cfg) ⇒ may return a real ``{"action": "close", "reason":
    "giveback_stop"}`` verdict. Undeclared ⇒ evaluates the reference cell
    (1R giveback after 1R MFE) and writes one observe-only annotate row
    when it would fire, returning ``None`` (behaviour unchanged).
    Fail-safe: any missing input (entry, frozen risk, entry_time, an
    unrestrictable candle window whose pre-entry bars would fake the
    peak) skips both paths — never a spurious close. **Never raises.**
    """
    try:
        declared_min_mfe = _coerce_float(
            meta.get("giveback_min_mfe_r")
            if meta.get("giveback_min_mfe_r") is not None
            else cfg_dict.get("giveback_min_mfe_r")
        )
        declared_gb = _coerce_float(
            meta.get("giveback_r") if meta.get("giveback_r") is not None
            else cfg_dict.get("giveback_r")
        )
        declared = (declared_min_mfe is not None and declared_min_mfe > 0
                    and declared_gb is not None and declared_gb > 0)
        min_mfe_r = declared_min_mfe if declared else _GIVEBACK_REF_MIN_MFE_R
        giveback_r = declared_gb if declared else _GIVEBACK_REF_GIVEBACK_R

        entry = _coerce_float(open_pkg.get("entry"))
        risk = _coerce_float(meta.get("risk_per_unit"))
        if entry is None or risk is None or risk <= 0:
            return None
        if not meta.get("entry_time"):
            return None  # peak window unanchorable — fail-safe skip
        window = _since_entry(candles_df, open_pkg)
        # Same ambiguity guard as _stale_stop_verdict: a full-frame
        # "restriction" means _since_entry fell back, and a pre-entry
        # extreme would fake a peak the trade never actually saw.
        if len(window) >= len(candles_df) and len(candles_df) > 0:
            return None
        if direction == "long":
            peak = _coerce_float(window["high"].max())
            if peak is None:
                return None
            peak_r = (peak - entry) / risk
            r_close = (current_price - entry) / risk
        else:
            peak = _coerce_float(window["low"].min())
            if peak is None:
                return None
            peak_r = (entry - peak) / risk
            r_close = (entry - current_price) / risk
        if not (peak_r >= min_mfe_r and (peak_r - r_close) >= giveback_r):
            return None
        if declared:
            return {"action": "close", "reason": "giveback_stop",
                    "exit_price": current_price}
        # Annotate-only path (undeclared): observe, never act.
        try:
            from src.runtime.exit_lever_soak import record_exit_lever_annotation

            record_exit_lever_annotation(
                lever="giveback_stop",
                strategy=str(meta.get("strategy_label")
                             or open_pkg.get("strategy_name") or "trend_donchian"),
                symbol=str(open_pkg.get("symbol") or ""),
                direction=direction,
                order_package_id=open_pkg.get("order_package_id"),
                params={"giveback_min_mfe_r": min_mfe_r,
                        "giveback_r": giveback_r},
                state={"peak_r": round(peak_r, 4), "open_r": round(r_close, 4),
                       "price": current_price, "entry": entry},
            )
        except Exception:  # noqa: BLE001 — annotate must never affect the path
            pass
        return None
    except Exception:  # noqa: BLE001 — monitor must never crash on this lever
        return None


def _since_entry(candles_df: pd.DataFrame, open_pkg: Dict[str, Any]) -> pd.DataFrame:
    """Restrict the candle window to bars at/after the package entry time.

    The Chandelier trail tracks the extreme SINCE ENTRY; the fetched
    window (limit=200) can include pre-entry bars whose extreme would
    move the trail too far. Falls back to the full frame when the entry
    time or a timestamp column is unavailable — the caller's
    correct-side-of-price guard still prevents an instant stop-out in
    that case.
    """
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
