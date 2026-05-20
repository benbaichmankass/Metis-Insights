"""Allocator interface and default passthrough implementation.

The allocator sits between signal builders and the execution layer.
It receives a batch of SignalPackages and returns a list of sized OrderPackages.

PassthroughAllocator is the default (S1-S4) — it replicates the current
per-strategy risk.py sizing formula exactly so that when it is wired in S4
it produces identical results with CENTRALIZED_ALLOCATOR=true.

Feature flag: CENTRALIZED_ALLOCATOR (default false).
The allocator is NOT wired into the live pipeline until S4 after Tier-2 review.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from src.core.order_contract import OrderPackage
from src.core.signal_contract import SignalPackage


class AllocatorInterface(ABC):
    """Contract for all allocator implementations."""

    @abstractmethod
    def allocate(
        self,
        signals: Sequence[SignalPackage],
        portfolio_state: dict,
    ) -> list[OrderPackage]:
        """Convert a batch of signals into sized OrderPackages.

        Args:
            signals: All signals generated this bar across all strategies.
            portfolio_state: Snapshot of current portfolio — must include at
                minimum: 'balance' (float) and 'risk_pct_by_strategy'
                (dict[str, float]).

        Returns:
            List of OrderPackages ready for execution. Empty list when no
            signal is actionable.
        """


class PassthroughAllocator(AllocatorInterface):
    """Identity allocator that replicates current per-strategy risk.py sizing.

    Formula (mirrors src/units/accounts/risk.py):
        risk_usd  = balance * risk_pct
        qty       = risk_usd / abs(entry - stop_loss)

    No cross-strategy netting, no portfolio-level exposure caps.
    Those are introduced in S4 (net position accounting) and S5 (adaptive
    sizing). Until then, this allocator is a safe drop-in replacement.
    """

    def allocate(
        self,
        signals: Sequence[SignalPackage],
        portfolio_state: dict,
    ) -> list[OrderPackage]:
        packages: list[OrderPackage] = []

        balance: float = portfolio_state.get("balance", 0.0)
        risk_pct_map: dict[str, float] = portfolio_state.get(
            "risk_pct_by_strategy", {}
        )

        for signal in signals:
            if not signal.is_actionable:
                continue

            risk_pct = risk_pct_map.get(signal.strategy_id, 0.005)
            risk_usd = balance * risk_pct

            sl_distance = signal.sl_distance
            if sl_distance is None or sl_distance <= 0:
                # Cannot size without a valid stop-loss — skip
                continue

            qty = risk_usd / sl_distance
            if qty <= 0:
                continue

            packages.append(
                OrderPackage.from_signal(signal, qty=qty, order_type="limit")
            )

        return packages
