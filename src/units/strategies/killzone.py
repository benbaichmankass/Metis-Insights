"""Killzone strategy — units layer adapter (S-008 PR #121).

The killzone strategy wraps the existing ``killzone_signal_builder`` result
when candles are pre-fetched, or accepts a pre-built signal dict directly
via ``cfg["_signal"]`` (for test injection and coordinator use).

In production the runtime pipeline calls the full ``killzone_signal_builder``
(which builds its own exchange connection and fetches live data).  The units
layer is designed to accept the **already-built** signal dict so that the
Coordinator can route it without creating a second exchange connection.

Entry / SL / TP derivation
--------------------------
The killzone signal meta may carry ``stop_loss`` / ``take_profit`` from the
underlying ``KillZoneScalperBot``.  We use those when present, otherwise
fall back to ±2 % / 4 % percentages.

Confidence is 0.8 when the signal fires, 0.0 when side="none".
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from src.units.strategies._base import (
    derive_sl_tp,
    last_close,
    require_candles,
    side_to_direction,
)


def order_package(cfg: dict, candles_df: Optional[pd.DataFrame] = None) -> dict:
    """Build a killzone OrderPackage dict.

    Accepts a pre-built signal via ``cfg["_signal"]`` (injected by the
    Coordinator or tests) to avoid a second live exchange call.  Falls back
    to using ``candles_df`` to build a simplified directional signal based
    on the most recent candle when no pre-built signal is available.

    Parameters
    ----------
    cfg : dict
        Strategy config from units.yaml.  May contain ``_signal`` key with a
        pre-built signal dict ``{symbol, side, qty, meta}``.
    candles_df : pd.DataFrame, optional
        OHLCV frame.  Required when ``cfg["_signal"]`` is not present.

    Returns
    -------
    dict
        Keys: symbol, direction, entry, sl, tp, confidence, meta.

    Raises
    ------
    ValueError
        When no signal is available or signal is non-actionable.
    """
    symbol = cfg.get("symbol") or cfg.get("SYMBOL") or "BTCUSDT"

    # Accept pre-built signal (from live pipeline or test injection).
    signal = cfg.get("_signal")
    if signal is None:
        candles_df = require_candles(candles_df, "killzone")
        # Build a minimal directional signal from candles: use close vs open
        # as a naive direction proxy.  In production the full killzone bot
        # does sophisticated session/kill-zone analysis.
        last = candles_df.iloc[-1]
        if float(last["close"]) > float(last["open"]):
            side = "buy"
        elif float(last["close"]) < float(last["open"]):
            side = "sell"
        else:
            raise ValueError(f"Killzone: candle is doji (no direction) for {symbol}.")
        signal = {
            "symbol": symbol,
            "side": side,
            "qty": float(cfg.get("max_qty") or cfg.get("MAX_QTY") or 1.0),
            "meta": {"strategy_name": "killzone", "source": "candle_proxy"},
        }

    side = signal.get("side", "none")
    direction = side_to_direction(side)  # raises ValueError when side=="none"

    meta = signal.get("meta") or {}

    # Use stop_loss / take_profit from meta when the live bot provided them.
    raw_sl = meta.get("stop_loss")
    raw_tp = meta.get("take_profit")

    if candles_df is not None and not candles_df.empty:
        entry = last_close(candles_df)
    else:
        entry = float(meta.get("entry_price") or 0.0)
        if entry <= 0:
            raise ValueError(
                f"Killzone: cannot determine entry price for {symbol}; "
                "provide candles_df or a signal with entry_price in meta."
            )

    try:
        sl = float(raw_sl) if raw_sl is not None else None
        tp = float(raw_tp) if raw_tp is not None else None
    except (TypeError, ValueError):
        sl, tp = None, None

    if sl is None or tp is None or sl <= 0 or tp <= 0:
        sl, tp = derive_sl_tp(entry, direction)

    return {
        "symbol": symbol,
        "direction": direction,
        "entry": round(entry, 8),
        "sl": round(sl, 8),
        "tp": round(tp, 8),
        "confidence": 0.8,
        "meta": {**meta, "signal": signal},
    }
