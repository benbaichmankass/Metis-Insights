"""Normalized order contract between the allocator and the execution layer.

OrderPackage carries the final sized, attributed order that the execution
layer (src/units/accounts/execute.py) will submit to the exchange.
It preserves full strategy attribution so audit logs can trace every
filled order back to the strategy and signal that generated it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from src.core.signal_contract import SignalPackage

OrderType = Literal["limit", "market", "stop_limit", "stop_market"]


@dataclass
class OrderPackage:
    """Sized, attributed order ready for execution layer submission."""

    strategy_id: str
    symbol: str
    account_id: str
    side: str
    qty: float
    entry_price: float | None
    stop_loss: float | None
    take_profit: float | None
    order_type: OrderType
    timestamp_utc: str
    # Preserves strategy_id, signal side, raw signal dict for audit trail
    attribution: dict = field(default_factory=dict)
    # Future: cross-strategy net position context populated by allocator (S4)
    net_position_context: dict = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_signal(
        cls,
        signal: "SignalPackage",
        qty: float,
        order_type: OrderType = "limit",
    ) -> "OrderPackage":
        """Build an OrderPackage from a SignalPackage with calculated qty."""
        return cls(
            strategy_id=signal.strategy_id,
            symbol=signal.symbol,
            account_id=signal.account_id,
            side=signal.side,
            qty=qty,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            order_type=order_type,
            timestamp_utc=signal.timestamp_utc,
            attribution={
                "strategy_id": signal.strategy_id,
                "signal_side": signal.side,
                "signal_entry": signal.entry_price,
                "signal_sl": signal.stop_loss,
                "signal_tp": signal.take_profit,
                "raw": signal.raw,
                "source_context": signal.source_context,
            },
        )

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    @property
    def is_flat(self) -> bool:
        return self.qty == 0.0 or self.side == "none"

    def __repr__(self) -> str:
        return (
            f"OrderPackage({self.strategy_id!r}, {self.symbol}, "
            f"{self.side}, qty={self.qty}, {self.order_type})"
        )
