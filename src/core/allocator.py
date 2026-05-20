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
from typing import Sequence, Union

from src.core.order_contract import OrderPackage
from src.core.portfolio_state import PortfolioState
from src.core.signal_contract import SignalPackage

_PortfolioStateArg = Union[PortfolioState, dict]


class AllocatorInterface(ABC):
    """Contract for all allocator implementations."""

    @abstractmethod
    def allocate(
        self,
        signals: Sequence[SignalPackage],
        portfolio_state: _PortfolioStateArg,
    ) -> list[OrderPackage]:
        """Convert a batch of signals into sized OrderPackages.

        Args:
            signals: All signals generated this bar across all strategies.
            portfolio_state: Snapshot of current portfolio — either a typed
                ``PortfolioState`` (S8+) or a legacy dict with at minimum
                'balance' (float) and 'risk_pct_by_strategy' (dict[str, float]).

        Returns:
            List of OrderPackages ready for execution. Empty list when no
            signal is actionable.
        """


def _coerce_portfolio_state(ps: _PortfolioStateArg) -> PortfolioState:
    """Normalise a dict or PortfolioState to a typed PortfolioState."""
    if isinstance(ps, PortfolioState):
        return ps
    return PortfolioState.from_dict(ps)


class PassthroughAllocator(AllocatorInterface):
    """Identity allocator that replicates current per-strategy risk.py sizing.

    Formula (mirrors src/units/accounts/risk.py):
        risk_usd  = balance * risk_pct
        qty       = risk_usd / abs(entry - stop_loss)

    No cross-strategy netting, no portfolio-level exposure caps.
    Those are introduced in later sprints. Until then, this allocator is a
    safe drop-in replacement.

    Accepts both a typed ``PortfolioState`` (S8+) and a legacy ``dict``
    (backward-compatible with existing pipeline callers).
    """

    def allocate(
        self,
        signals: Sequence[SignalPackage],
        portfolio_state: _PortfolioStateArg,
    ) -> list[OrderPackage]:
        packages: list[OrderPackage] = []

        ps = _coerce_portfolio_state(portfolio_state)

        for signal in signals:
            if not signal.is_actionable:
                continue

            risk_pct = ps.risk_pct_by_strategy.get(signal.strategy_id, 0.005)
            risk_usd = ps.balance * risk_pct

            sl_distance = signal.sl_distance
            if sl_distance is None or sl_distance <= 0:
                continue

            qty = risk_usd / sl_distance
            if qty <= 0:
                continue

            pkg = OrderPackage.from_signal(signal, qty=qty, order_type="limit")
            net_qty = ps.net_for(signal.symbol)
            if net_qty != 0.0:
                pkg.net_position_context["net_qty"] = net_qty
            packages.append(pkg)

        return packages
