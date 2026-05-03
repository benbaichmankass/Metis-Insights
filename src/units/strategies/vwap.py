"""VWAP strategy — units layer (S-008 PR #121, S-012 PR C5 self-contained).

Pure VWAP mean-reversion signal builder + ``order_package`` adapter. Before
S-012 PR C5 the helpers (``compute_vwap``, ``build_vwap_signal``,
``ENTRY_STD_THRESHOLD``) lived in ``strategies/vwap_signal_builder.py``.
That module has been removed; everything now lives here so the
production strategy directory is exactly one path.

Public surface
--------------
- ``ENTRY_STD_THRESHOLD`` — module constant; std-dev threshold for entry.
- ``compute_vwap(df)`` — pure VWAP scalar from an OHLCV frame.
- ``build_vwap_signal(df, symbol)`` — pipeline-shape signal dict
  (``{symbol, side, entry_price, stop_loss, take_profit, meta}``);
  never raises on bad data — returns side="none" with a logged reason.
  Used by the runtime pipeline.
- ``order_package(cfg, candles_df)`` — units-layer adapter conforming to
  the contract in ``src/units/strategies/_base.py``. Used by the
  Coordinator dispatch path.

Strategies are pure signal generators (see ``_base.py`` docstring): no
``dry_run`` flag, no execution awareness, **no qty** — quantity is an
account-side decision (S-026 G1) computed by the per-account
RiskManager from balance + risk rules, not a strategy output.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import pandas as pd

from src.units.strategies._base import (
    derive_sl_tp,
    last_close,
    require_candles,
    side_to_direction,
)

logger = logging.getLogger(__name__)

# Minimum candles needed for a meaningful VWAP reading.
MIN_CANDLES = 2

# Minimum standard-deviation bands required to call a reversion signal.
# Price must deviate at least this many std-devs from VWAP to be actionable.
#
# 2026-05-03: raised from 1.0σ to 2.0σ following the
# `2026-05-03-vwap-improvement` training run (PR #350 — RECOMMENDATIONS,
# merged d52a816). The 1.0σ default produced an unprofitable backtest
# (Sharpe -0.12, expectancy -0.002 R, max DD -21 R over 365 d BTCUSDT 5m,
# 946 trades). The 2.0σ threshold flipped the same window to Sharpe 1.71,
# expectancy +0.044 R, max DD -5 R, 336 trades — see
# `experiments/2026-05-03-vwap-improvement/RECOMMENDATIONS.md`. The
# threshold sweep (1.0 / 1.5 / 2.0 / 2.5) showed monotonic improvement
# up to 2.0σ with a small regression at 2.5σ, so 2.0σ is a clean local
# maximum and not overfit to the sample.
ENTRY_STD_THRESHOLD = 2.0

# Internal alias retained for backwards-compatible imports.
_ENTRY_STD_THRESHOLD = ENTRY_STD_THRESHOLD

# G5 (CP-2026-05-02-12, operator picked option (a)): VWAP must populate
# entry / stop_loss / take_profit on every actionable signal so the
# multi-account dispatch fast-path can fan it out. Operator directive:
# "the trade package should always include entry/sl/tp levels".
#
# Mean-reversion logic:
#   * entry      = current candle close (the price at which the signal fires)
#   * take_profit = VWAP (the mean-reversion target — by definition price
#                  deviates from VWAP and we expect it to revert)
#   * stop_loss   = entry ± SL_STD_MULT × std_dev
#                   - BUY  (entry < vwap): SL = entry - SL_STD_MULT * std_dev (further below)
#                   - SELL (entry > vwap): SL = entry + SL_STD_MULT * std_dev (further above)
#
# Risk/reward at entry: R = (vwap - entry) for BUY, (entry - vwap) for SELL.
# That's |deviation_std| × std_dev. With SL_STD_MULT = 1.0, the trade
# carries R/R = |deviation_std| : 1, which is favourable when |deviation_std|
# >= ENTRY_STD_THRESHOLD = 2.0 (the entry threshold; 2:1 R/R at the boundary).
# Operator can tune
# via the ``sl_std_mult`` arg to ``build_vwap_signal`` or the matching
# entry in ``config/strategies.yaml`` (consumed by the pipeline-side
# vwap_signal_builder when it wires it through).
SL_STD_MULT_DEFAULT = 1.0


def compute_vwap(candles_df: pd.DataFrame) -> float:
    """Return VWAP for the supplied candle window.

    Raises ValueError with a clear, non-secret message if the data is
    insufficient or degenerate (zero volume).
    """
    if not isinstance(candles_df, pd.DataFrame) or candles_df.empty:
        raise ValueError(
            "VWAP computation requires a non-empty DataFrame. "
            "Check that market data was fetched correctly."
        )

    required = {"high", "low", "close", "volume"}
    missing = required - set(candles_df.columns)
    if missing:
        raise ValueError(
            f"VWAP computation: candle DataFrame is missing columns: {sorted(missing)}. "
            "Expected columns: high, low, close, volume."
        )

    if len(candles_df) < MIN_CANDLES:
        raise ValueError(
            f"VWAP requires at least {MIN_CANDLES} candles, got {len(candles_df)}. "
            "Ensure the timeframe and lookback window are configured correctly."
        )

    total_volume = candles_df["volume"].sum()
    if total_volume <= 0:
        raise ValueError(
            "VWAP cannot be computed: total volume across all candles is zero or negative. "
            "Check that the candle data contains valid volume information."
        )

    typical_price = (
        candles_df["high"] + candles_df["low"] + candles_df["close"]
    ) / 3.0
    return float((typical_price * candles_df["volume"]).sum() / total_volume)


def _no_trade(symbol: str, reason: str) -> Dict[str, Any]:
    """Standardised no-trade signal for invalid or insufficient candle data."""
    logger.warning("VWAP no-trade for %s: %s", symbol, reason)
    return {
        "symbol": symbol,
        "side": "none",
        "meta": {
            "strategy_name": "vwap",
            "reason": reason,
        },
    }


def build_vwap_signal(
    candles_df: pd.DataFrame,
    symbol: str,
    sl_std_mult: float = SL_STD_MULT_DEFAULT,
) -> Dict[str, Any]:
    """Compute a VWAP mean-reversion signal from OHLCV candle data.

    Returns a signal dict with keys:
        symbol, side,
        entry_price, stop_loss, take_profit  (only when side != "none"),
        meta.

    S-026 G1: this strategy package is the trade *idea*, not the order.
    No ``qty`` field is produced — quantity is decided per-account by
    the RiskManager from balance + risk rules.

    * side='buy'  when price is at least ENTRY_STD_THRESHOLD std-devs *below*
                  VWAP (mean-reversion long).
    * side='sell' when price is at least ENTRY_STD_THRESHOLD std-devs *above*
                  VWAP (mean-reversion short).
    * side='none' when price is near VWAP or data is insufficient.

    Invalid candle data (empty, missing volume column, zero/negative total
    volume) returns a no-trade signal instead of raising — the tick completes
    safely.

    G5 (CP-2026-05-02-12, operator option (a)): when the signal IS
    actionable, ``entry_price`` / ``stop_loss`` / ``take_profit`` are
    populated at the top level so the pipeline's multi-account dispatch
    fast-path (``_signal_carries_full_sltp``) accepts the signal and
    fans it out per account. Per-account ``RiskManager.dry_run``
    (operator directive 2026-05-03) is the only dry/live toggle that
    runs against the dispatched signal.

    SL/TP rules:
        entry = current candle close
        TP    = VWAP (mean-reversion target)
        SL    = entry - sl_std_mult * std_dev   (BUY — further below entry)
                entry + sl_std_mult * std_dev   (SELL — further above entry)

    With ``sl_std_mult = SL_STD_MULT_DEFAULT = 1.0``, R/R at entry equals
    ``|deviation_std| : 1``, which is favourable when the entry threshold
    (``|deviation_std| >= ENTRY_STD_THRESHOLD = 2.0``) is met (2:1 R/R at
    the boundary).
    """
    if not isinstance(candles_df, pd.DataFrame) or candles_df.empty:
        return _no_trade(symbol, "VWAP skipped: candle data is empty or invalid")

    if "volume" not in candles_df.columns:
        return _no_trade(symbol, "VWAP skipped: candle data is empty or invalid")

    if candles_df["volume"].sum() <= 0:
        return _no_trade(symbol, "VWAP skipped: total candle volume is zero or negative")

    vwap = compute_vwap(candles_df)
    current_price = float(candles_df["close"].iloc[-1])

    typical_price = (
        candles_df["high"] + candles_df["low"] + candles_df["close"]
    ) / 3.0
    std_dev = float(typical_price.std())

    deviation = (current_price - vwap) / std_dev if std_dev > 0 else 0.0

    if deviation <= -ENTRY_STD_THRESHOLD:
        side = "buy"
        reason = f"price {current_price:.4f} is {abs(deviation):.2f} std-devs below VWAP {vwap:.4f}"
    elif deviation >= ENTRY_STD_THRESHOLD:
        side = "sell"
        reason = f"price {current_price:.4f} is {deviation:.2f} std-devs above VWAP {vwap:.4f}"
    else:
        side = "none"
        reason = f"price {current_price:.4f} within {ENTRY_STD_THRESHOLD} std-dev of VWAP {vwap:.4f} — no signal"

    logger.info(
        "VWAP signal: symbol=%s vwap=%.4f price=%.4f std=%.4f deviation=%.2f side=%s",
        symbol, vwap, current_price, std_dev, deviation, side,
    )

    # BUG-043: confidence must be threaded through to the order package
    # so the journal records a real conviction value (not 0.0). Same
    # formula as ``order_package()`` below — magnitude of the std-dev
    # deviation, normalised to ENTRY_STD_THRESHOLD, capped at 1.0.
    confidence = round(min(abs(deviation) / ENTRY_STD_THRESHOLD, 1.0), 4)

    base_meta = {
        "strategy_name": "vwap",
        "vwap": vwap,
        "current_price": current_price,
        "std_dev": std_dev,
        "deviation_std": deviation,
        "confidence": confidence,
        "reason": reason,
    }

    if side == "none":
        return {
            "symbol": symbol,
            "side": "none",
            "confidence": confidence,
            "meta": base_meta,
        }

    # G5 — populate entry/sl/tp so multi-account dispatch can fan this out.
    entry_price = current_price
    take_profit = vwap
    if side == "buy":
        stop_loss = entry_price - sl_std_mult * std_dev
    else:  # "sell"
        stop_loss = entry_price + sl_std_mult * std_dev

    return {
        "symbol": symbol,
        "side": side,
        "entry_price": float(entry_price),
        "stop_loss": float(stop_loss),
        "take_profit": float(take_profit),
        "confidence": confidence,
        "meta": {
            **base_meta,
            "sl_std_mult": sl_std_mult,
            "entry_price": float(entry_price),
            "stop_loss": float(stop_loss),
            "take_profit": float(take_profit),
        },
    }


def order_package(cfg: dict, candles_df: Optional[pd.DataFrame] = None) -> dict:
    """Build a VWAP OrderPackage dict.

    Parameters
    ----------
    cfg : dict
        Strategy config from units.yaml.
    candles_df : pd.DataFrame
        OHLCV frame.  Required — raises ValueError when absent.

    Returns
    -------
    dict
        Keys: symbol, direction, entry, sl, tp, confidence, meta.

    Raises
    ------
    ValueError
        When candles_df is absent or signal is non-actionable (side="none").
    """
    candles_df = require_candles(candles_df, "vwap")

    symbol = cfg.get("symbol") or cfg.get("SYMBOL") or "BTCUSDT"

    signal = build_vwap_signal(candles_df, symbol=symbol)

    side = signal.get("side", "none")
    direction = side_to_direction(side)  # raises ValueError when side=="none"

    entry = last_close(candles_df)

    # Attempt VWAP-to-price TP; fall back to percentage-based.
    try:
        vwap = compute_vwap(candles_df)
        typical_price = (candles_df["high"] + candles_df["low"] + candles_df["close"]) / 3.0
        std_dev = float(typical_price.std())

        if direction == "long":
            tp = vwap
            risk = entry - tp
            sl = entry + risk if risk > 0 else entry * 0.98
        else:
            tp = vwap
            risk = tp - entry
            sl = entry - risk if risk > 0 else entry * 1.02

        # Confidence: deviation in std-dev units capped at 1.0
        if std_dev > 0:
            deviation = abs(entry - vwap) / std_dev
            confidence = min(deviation / ENTRY_STD_THRESHOLD, 1.0)
        else:
            confidence = 0.5

    except Exception:
        sl, tp = derive_sl_tp(entry, direction)
        confidence = 0.5

    meta = signal.get("meta") or {}
    return {
        "symbol": symbol,
        "direction": direction,
        "entry": round(entry, 8),
        "sl": round(float(sl), 8),
        "tp": round(float(tp), 8),
        "confidence": round(confidence, 4),
        "meta": {**meta, "signal": signal},
    }


# ---------------------------------------------------------------------------
# monitor() — S-030 PR2 (architecture-audit-2026-05-02 P1-4)
# ---------------------------------------------------------------------------


def monitor(cfg, candles_df, open_pkg):
    """Re-evaluate an open vwap order package against fresh candles.

    Per CLAUDE.md § Architecture rules § 2 the strategy unit *monitors*
    open packages — generates updates while a trade is live. The
    monitor loop (S-030 PR3) calls this hook on every heartbeat tick
    for each open package; the strategy decides whether to do nothing,
    tighten the stop, move the target, or close.

    v1 logic — break-even SL after 1R. The mean-reversion thesis is
    "price returns to vwap"; once the trade has captured 1R of risk
    the original invalidation no longer holds and the SL should not
    risk the realised profit. Future versions can add: vwap-cross
    close, volume-spike close, time-decay close.

    Parameters
    ----------
    cfg : dict
        Strategy config (passed through; not currently consumed in
        v1 but the signature mirrors ``order_package`` so the monitor
        loop can reuse the same cfg dict).
    candles_df : pandas.DataFrame
        Fresh OHLCV at the strategy's timeframe.
    open_pkg : dict
        Current order package row from the DB unit's order_packages
        table — keys: ``entry``, ``sl``, ``tp``, ``direction``,
        ``symbol``, ``meta`` (str-or-dict).

    Returns
    -------
    None | dict
        See ``_base.monitor_breakeven_sl`` for the exact contract.
    """
    from src.units.strategies._base import monitor_breakeven_sl
    return monitor_breakeven_sl(open_pkg, candles_df)
