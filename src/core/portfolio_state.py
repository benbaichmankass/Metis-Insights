"""PortfolioState — typed portfolio snapshot passed to the allocator (S8).

Replaces the raw ``portfolio_state: dict`` formerly accepted by
``AllocatorInterface.allocate()``.  The concrete allocator implementations
(``PassthroughAllocator`` and future adaptive versions) unpack balance,
per-strategy risk budgets, and current net positions from this typed object
instead of relying on dict key conventions.

Backward compatibility: ``from_dict()`` is the migration path for callers
that still build a ``{"balance": ..., "risk_pct_by_strategy": ...}`` dict;
``PassthroughAllocator`` also accepts a plain dict directly so that existing
test fixtures and pipeline callers do not need to be updated in lockstep.
"""
from __future__ import annotations

import dataclasses


@dataclasses.dataclass
class PortfolioState:
    """Snapshot of portfolio state at the moment the allocator is called.

    Attributes
    ----------
    balance : float
        Available cash balance for sizing (USDT or equivalent).
    risk_pct_by_strategy : dict[str, float]
        Per-strategy risk fraction of balance.  Missing keys fall back to
        ``PassthroughAllocator``'s default (0.005 — matches risk.py).
    net_positions : dict[str, float]
        Current signed net qty per symbol aggregated across all live accounts.
        Positive = net long; negative = net short; 0.0 = flat.
        Populated from ``src.runtime.positions.net_positions_by_symbol()``
        in ``Coordinator.build_order_packages()``.
    """

    balance: float
    risk_pct_by_strategy: dict[str, float] = dataclasses.field(default_factory=dict)
    net_positions: dict[str, float] = dataclasses.field(default_factory=dict)

    @classmethod
    def from_balance(cls, balance: float) -> "PortfolioState":
        """Minimal factory: balance only, empty risk map, flat positions."""
        return cls(balance=balance)

    @classmethod
    def from_dict(cls, d: dict) -> "PortfolioState":
        """Migrate a legacy portfolio_state dict to a typed instance."""
        return cls(
            balance=float(d.get("balance", 0.0)),
            risk_pct_by_strategy=dict(d.get("risk_pct_by_strategy", {})),
            net_positions=dict(d.get("net_positions", {})),
        )

    def net_for(self, symbol: str) -> float:
        """Signed net qty for *symbol*; 0.0 when no open position."""
        return self.net_positions.get(symbol, 0.0)
