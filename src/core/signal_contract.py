"""Normalized signal contract between strategy signal builders and the allocator.

SignalPackage is the canonical output of any strategy's signal-building step.
It carries enough information for the allocator to size and route an order
without needing to know strategy internals.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Side = Literal["long", "short", "none"]


@dataclass
class SignalPackage:
    """Normalized output from a strategy's build_signal() step."""

    strategy_id: str
    symbol: str
    account_id: str
    side: Side
    entry_price: float | None
    stop_loss: float | None
    take_profit: float | None
    timestamp_utc: str
    # Original strategy-specific signal dict for audit / Streamlit transparency
    raw: dict = field(default_factory=dict)
    # Additional context (regime, ML advisory, etc.) for allocator decisions
    source_context: dict = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    @property
    def is_actionable(self) -> bool:
        """True when the signal carries a directional intent with an entry price."""
        return self.side != "none" and self.entry_price is not None

    @property
    def sl_distance(self) -> float | None:
        """Absolute distance between entry and stop-loss, or None if either is missing."""
        if self.entry_price is None or self.stop_loss is None:
            return None
        return abs(self.entry_price - self.stop_loss)

    def with_account(self, account_id: str) -> "SignalPackage":
        """Return a copy of this signal bound to a different account."""
        import dataclasses
        return dataclasses.replace(self, account_id=account_id)

    def __repr__(self) -> str:
        return (
            f"SignalPackage({self.strategy_id!r}, {self.symbol}, "
            f"{self.side}, entry={self.entry_price})"
        )
