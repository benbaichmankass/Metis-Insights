"""Volatility-squeeze breakout — units-layer adapter
(S-STRAT-IMPROVE-S9, complementary-strategy R&D, operator-approved
2026-05-24 for shadow data-collection).

Strategy summary
----------------
A TTM-style squeeze: when Bollinger Bands contract INSIDE the Keltner
Channels, volatility is compressed (a "squeeze"); when the BBs expand
back OUTSIDE the KC, the squeeze "fires" — enter in the direction of
price vs the basis MA, with the wide-ATR-stop + Chandelier trailing exit
(the let-winners-run lever shared with trend_donchian / fade_breakout_4h).
A different ENTRY TRIGGER (volatility compression) than the price-channel
strategies, so it fires on a different subset of bars.

Validation (`scripts/backtest_squeeze.py`, 6yr BTCUSDT, 4h, net-of-fee):
the BEST member-#3 candidate found in the program. +35.4R / 6yr, exp
0.325, max-DD only 6.0R, and the 4h nested walk-forward is net-positive
in BOTH train (2020-23) and OOS (2024-26) across the whole bb-std x
kc-mult plateau (robust, not a lucky corner). Diversifying: monthly_corr
0.30 vs the live 2h trend, -0.05 vs the fade. Full evidence:
docs/audits/squeeze-breakout-complement-2026-05-24.md.

CAVEAT (why this runs in SHADOW, not live): the OOS returns are
month-concentrated (top-month share 0.45-0.99 across the plateau) and the
most-diversifying corner (kc 1.0) is low-frequency. It is wired
`execution: shadow` so it logs order packages on real ticks (data
collection) but never sends a live order, pending live proof.

This adapter ports the validated logic into the live ``order_package(cfg,
candles_df) -> dict`` + ``monitor(cfg, candles_df, open_pkg)`` contract.
The ``monitor()`` Chandelier-trail logic is identical to trend_donchian /
fade_breakout_4h (same ratchet, same frozen-entry-ATR semantics) — see
those modules for the full rationale on meta-carried params and the
correct-side-of-price guard.

Strategies are pure signal generators (no dry/live awareness); the
dry/live decision lives in the Accounts layer per ``mode:`` /
``execution:`` in config.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

import pandas as pd

from src.units.strategies._base import require_candles


# Defaults mirror the validated config (docs/audits/squeeze-breakout-
# complement-2026-05-24.md): 4h / BB(20, 2.0) / KC(1.0 x ATR) / atr 14 /
# stop 2.5 / Chandelier trail 3.5. kc_mult 1.0 is the most-diversifying
# corner (corr 0.30 vs trend); the runtime builder merges
# config/strategies.yaml::squeeze_breakout_4h params over these.
_DEFAULTS: Dict[str, Any] = {
    "bb_period": 20,
    "bb_std": 2.0,
    "kc_mult": 1.0,
    "atr_period": 14,
    "atr_stop_mult": 2.5,
    # Chandelier trail distance (sole profit-exit, no fixed TP).
    "trail_mult": 3.5,
    # No fixed profit target — the trail is the sole profit-exit; ``tp`` is
    # a far ~50R sentinel that still satisfies the pipeline's SL/TP gate.
    "tp_r": 50.0,
    "timeframe": "4h",
}


def _resolve_params(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return {key: cfg.get(key, default) for key, default in _DEFAULTS.items()}


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    """ATR as the simple moving average of True Range — identical to
    ``scripts/backtest_squeeze.py::_atr`` so the live stop/KC distances
    match what was validated."""
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
    """Build a squeeze_breakout_4h OrderPackage dict from the latest candles.

    Returns a dict with keys: symbol, direction, entry, sl, tp,
    confidence, meta.

    Raises ``ValueError`` (non-actionable; the runtime builder treats it
    as side="none") when candles are absent, there are too few rows, or
    the squeeze did NOT fire on the latest bar (BB was inside KC on the
    prior bar and expanded outside on this one).
    """
    candles_df = require_candles(candles_df, "squeeze_breakout_4h")
    params = _resolve_params(cfg)
    symbol = cfg.get("symbol") or cfg.get("SYMBOL") or "BTCUSDT"

    bb_period = int(params["bb_period"])
    bb_std = float(params["bb_std"])
    kc_mult = float(params["kc_mult"])
    atr_period = int(params["atr_period"])
    atr_stop_mult = float(params["atr_stop_mult"])
    trail_mult = float(params["trail_mult"])
    tp_r = float(params["tp_r"])
    timeframe = str(cfg.get("timeframe") or params["timeframe"])

    needed = bb_period + atr_period + 2
    if len(candles_df) < needed:
        raise ValueError(
            f"Strategy 'squeeze_breakout_4h': need at least {needed} candles "
            f"for the BB({bb_period}) / ATR({atr_period}) windows; got "
            f"{len(candles_df)}."
        )

    df = candles_df.reset_index(drop=True)
    atr_series = _atr(df, atr_period)
    basis = df["close"].rolling(bb_period).mean()
    sd = df["close"].rolling(bb_period).std(ddof=0)
    bb_up = basis + bb_std * sd
    bb_lo = basis - bb_std * sd
    kc_up = basis + kc_mult * atr_series
    kc_lo = basis - kc_mult * atr_series
    sqz_on = (bb_up < kc_up) & (bb_lo > kc_lo)

    atr = float(atr_series.iloc[-1]) if pd.notna(atr_series.iloc[-1]) else 0.0
    close = float(df["close"].iloc[-1])
    basis_now = basis.iloc[-1]
    prev_on = sqz_on.iloc[-2] if len(sqz_on) >= 2 else None
    now_on = sqz_on.iloc[-1]

    if atr <= 0 or pd.isna(basis_now) or pd.isna(prev_on):
        raise ValueError(
            "Strategy 'squeeze_breakout_4h': ATR non-positive or BB/KC "
            "undefined on the latest bar (non-actionable)."
        )

    # Squeeze fires: compressed on the prior bar, expanded on this one.
    if not (bool(prev_on) and not bool(now_on)):
        raise ValueError(
            "Strategy 'squeeze_breakout_4h': no squeeze release on the latest "
            f"bar (prev_on={bool(prev_on)}, now_on={bool(now_on)}) "
            "— non-actionable."
        )

    basis_now = float(basis_now)
    direction = "long" if close > basis_now else "short"
    entry = close
    if direction == "long":
        sl = entry - atr_stop_mult * atr
        risk = entry - sl
        tp = entry + tp_r * risk
    else:
        sl = entry + atr_stop_mult * atr
        risk = sl - entry
        # Clamp to a tiny positive value so the pre-flight tp>0 guard
        # accepts the order; the Chandelier trail in monitor() is the
        # real exit. Same shape as trend_donchian/fade_breakout_4h.
        tp = max(entry * 0.01, entry - tp_r * risk)

    if risk <= 0:
        raise ValueError(
            "Strategy 'squeeze_breakout_4h': non-positive risk after stop "
            "computation; skipping signal."
        )

    # Confidence: how far price sits from the basis at the release,
    # normalised to ATR and clamped — a decisive expansion scores higher.
    confidence = round(min(max(abs(close - basis_now) / atr, 0.0), 1.0), 4)

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
            "basis": basis_now,
            "bb_period": bb_period,
            "bb_std": bb_std,
            "kc_mult": kc_mult,
            # Entry-time ATR FROZEN for the monitor's trail distance,
            # matching the backtest's fixed-ATR trail.
            "atr": atr,
            "atr_period": atr_period,
            "atr_stop_mult": atr_stop_mult,
            "trail_mult": trail_mult,
            "tp_r": tp_r,
            "risk_per_unit": float(risk),
            "entry_time": entry_time,
            # Canonical key the order_monitor's ohlcv_fetcher reads.
            "timeframe": timeframe,
        },
    }
    return package


# ---------------------------------------------------------------------------
# monitor() — live Chandelier ATR trailing stop
#
# Identical mechanics to trend_donchian / fade_breakout_4h: SL-cross close,
# far-TP sentinel close, then a since-entry Chandelier ratchet using the
# frozen entry-time ATR. Reads trail params from open_pkg["meta"] because
# run_monitor_tick passes cfg={} in production.
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
    """Re-evaluate an open squeeze_breakout_4h package against fresh candles.

    (1) SL-cross full close, (2) far-sentinel TP-cross full close,
    (3) Chandelier trail ratchet at ``extreme ∓ trail_mult × ATR`` using
    the since-entry extreme + frozen entry-time ATR, returned as
    ``{"sl": new_sl}`` only when it tightens AND sits on the correct side
    of price. Otherwise ``None``.
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
        period = int(
            meta.get("atr_period") or cfg_dict.get("atr_period")
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
