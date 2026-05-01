"""Smoke-test "strategy" — synthetic OrderPackage for live-plumbing verification.

This module is **not** a real strategy. Its sole purpose is to feed the
9-unit pipeline (strategies → Coordinator → accounts → exchange) a tagged
order package whose ``meta.is_test`` flag tells the risk manager to skip
its usual gating, the executor to use a fixed micro qty, and the
exchange to reject for being below the minimum lot size.

The rejection is the success signal: it proves the wire is hot end-to-end
(payload built, risk bypass honoured, exchange reachable, response routed
back to the journal) without actually moving any money.

Returned shape matches ``src/units/strategies/_base.py``:

    {
      "symbol": "BTCUSDT",
      "direction": "long",
      "entry":  <ref price>,
      "sl":     <entry * 0.98>,
      "tp":     <entry * 1.02>,
      "confidence": 0.0,
      "meta": {
        "is_test":  True,        # canonical flag
        "is_smoke": True,        # mirrors scripts/smoke_test_trade.py
        "test_qty": 0.0001,      # below Bybit linear min-lot (0.001 BTC)
        "smoke_id": "<8-char hex>",
      },
    }

``order_package()`` does **not** require a live ``candles_df`` — the
ref price is taken from ``cfg["ref_price"]`` (default 70_000) so the
adapter is unit-testable with no exchange access.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

# A qty deliberately below Bybit linear perp min-lot (0.001 BTC). Bybit
# returns retCode=10001 ("qty invalid") on submission, which the
# accounts/execute layer turns into a structured "rejected_too_small"
# result. Adjust here if the exchange ever lowers its min-lot.
DEFAULT_TEST_QTY = 0.0001

# Static fallback ref price; overridden by cfg["ref_price"] when present.
# The exact entry/sl/tp values are irrelevant — Bybit will reject before
# the prices matter — but we keep them well-formed so downstream
# validators (safe_place_order, journal schema) accept the payload.
DEFAULT_REF_PRICE = 70_000.0


def order_package(cfg: dict, candles_df: Optional[Any] = None) -> Dict[str, Any]:
    """Build a smoke-test OrderPackage dict.

    Parameters
    ----------
    cfg : dict
        Optional overrides:
          ``symbol``     — default "BTCUSDT".
          ``direction``  — "long" | "short", default "long".
          ``ref_price``  — float, default 70_000.
          ``test_qty``   — float, default 0.0001 (below Bybit min-lot).
          ``note``       — free-form note recorded in meta.note.
    candles_df : ignored
        Present for interface symmetry with real strategies.

    Returns
    -------
    dict
        Keys: symbol, direction, entry, sl, tp, confidence, meta.
        ``meta.is_test`` is always True; ``meta.test_qty`` carries the
        micro qty the executor should use instead of risk-sized qty.
    """
    cfg = cfg or {}
    symbol = cfg.get("symbol") or "BTCUSDT"
    direction = (cfg.get("direction") or "long").lower()
    if direction not in {"long", "short"}:
        raise ValueError(f"smoke_test: direction must be long|short, got {direction!r}")

    ref_price = float(cfg.get("ref_price") or DEFAULT_REF_PRICE)
    test_qty = float(cfg.get("test_qty") or DEFAULT_TEST_QTY)
    if test_qty <= 0:
        raise ValueError(f"smoke_test: test_qty must be > 0, got {test_qty}")

    if direction == "long":
        sl = round(ref_price * 0.98, 2)
        tp = round(ref_price * 1.02, 2)
    else:
        sl = round(ref_price * 1.02, 2)
        tp = round(ref_price * 0.98, 2)

    smoke_id = uuid.uuid4().hex[:8]
    note = cfg.get("note") or "live-plumbing smoke test"

    return {
        "symbol": symbol,
        "direction": direction,
        "entry": round(ref_price, 2),
        "sl": sl,
        "tp": tp,
        "confidence": 0.0,
        "meta": {
            "is_test": True,
            "is_smoke": True,
            "test_qty": test_qty,
            "smoke_id": smoke_id,
            "strategy_name": "smoke_test",
            "note": note,
        },
    }
