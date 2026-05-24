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
}


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

    donchian = int(params["donchian"])
    atr_period = int(params["atr_period"])
    atr_stop_mult = float(params["atr_stop_mult"])
    trail_mult = float(params["trail_mult"])
    tp_r = float(params["tp_r"])
    timeframe = str(cfg.get("timeframe") or params["timeframe"])

    needed = donchian + atr_period + 2
    if len(candles_df) < needed:
        raise ValueError(
            f"Strategy 'trend_donchian': need at least {needed} candles for "
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
            "Strategy 'trend_donchian': ATR non-positive or Donchian channel "
            "undefined on the latest bar (non-actionable)."
        )

    hi = float(hi)
    lo = float(lo)
    if close > hi:
        direction = "long"
    elif close < lo:
        direction = "short"
    else:
        raise ValueError(
            f"Strategy 'trend_donchian': no breakout on the latest bar "
            f"(close={close} within channel [{lo}, {hi}]) — non-actionable."
        )

    entry = close
    if direction == "long":
        sl = entry - atr_stop_mult * atr
        risk = entry - sl
        tp = entry + tp_r * risk
        breakout_depth = (close - hi) / atr
    else:
        sl = entry + atr_stop_mult * atr
        risk = sl - entry
        tp = entry - tp_r * risk
        breakout_depth = (lo - close) / atr

    if risk <= 0:
        raise ValueError(
            "Strategy 'trend_donchian': non-positive risk after stop "
            "computation; skipping signal."
        )

    # Confidence: breakout depth past the channel, normalised to ATR and
    # clamped to [0, 1]. A clean break well past the channel scores
    # higher; a marginal poke scores near 0.
    confidence = round(min(max(breakout_depth, 0.0), 1.0), 4)

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
    return package


# ---------------------------------------------------------------------------
# monitor() — live Chandelier ATR trailing stop
# ---------------------------------------------------------------------------


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
