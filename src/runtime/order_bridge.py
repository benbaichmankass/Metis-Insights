"""Signal-to-OrderPackage bridge — extracted from pipeline.py (PR-9 / D1).

Converts a pipeline-shape signal dict (``{symbol, side, price/entry_price,
stop_loss, take_profit, meta}``) into the ``OrderPackage`` the Coordinator
expects (``direction``, ``entry``, ``sl``, ``tp``). Kept as a thin module so
pipeline.py and tests can import it without dragging in the full coordinator.
"""
from __future__ import annotations

from typing import Any, Dict


def _signal_to_order_package(signal: Dict[str, Any], settings: dict):
    """Build an ``OrderPackage`` from a pipeline signal dict.

    The signal shape is what every builder in this module produces:
    ``{symbol, side, price/entry_price, stop_loss, take_profit,
    meta: {strategy_name, ...}}`` — S-026 G1: no qty (sizing is the
    per-account RiskManager's job in G2). The Coordinator's
    per-account dispatch path consumes ``OrderPackage``, which has a
    slightly different shape (``direction`` instead of ``side``,
    ``entry`` / ``sl`` / ``tp``). This helper bridges the two so we
    can fan a pipeline-generated signal out to every account in
    ``config/accounts.yaml`` without changing the strategy builders.
    """
    from src.core.coordinator import OrderPackage

    meta = dict(signal.get("meta") or {})
    side = str(signal.get("side", "")).strip().lower()
    if side not in ("buy", "sell"):
        raise ValueError(
            f"_signal_to_order_package: side must be buy/sell, got {side!r}"
        )
    direction = "long" if side == "buy" else "short"

    entry = signal.get("entry_price") or signal.get("price") or meta.get("price")
    sl = signal.get("stop_loss") or meta.get("stop_loss") or meta.get("sl")
    tp = signal.get("take_profit") or meta.get("take_profit") or meta.get("tp")
    if entry is None or sl is None or tp is None:
        raise ValueError(
            "_signal_to_order_package: signal missing entry/sl/tp "
            f"(entry={entry!r}, sl={sl!r}, tp={tp!r}); strategy must "
            "populate price+stop_loss+take_profit before fan-out."
        )

    strategy = (
        meta.get("strategy_name")
        or signal.get("strategy")
        or settings.get("STRATEGY")
        or "unknown"
    )
    return OrderPackage(
        strategy=str(strategy),
        symbol=str(signal.get("symbol") or settings.get("SYMBOL") or "BTCUSDT"),
        direction=direction,
        entry=float(entry),
        sl=float(sl),
        tp=float(tp),
        confidence=float(meta.get("confidence") or 0.0),
        meta=meta,
    )
