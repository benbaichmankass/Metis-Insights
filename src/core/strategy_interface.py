"""Abstract base class for ICT trading strategies.

All concrete strategy modules (vwap, turtle_soup, ict_scalp) MAY implement
this interface. The interface is NOT enforced on existing live strategies
until S3 wires signal builders — it is purely additive scaffolding.

NOTE: src/strategy_registry.py (existing) is an ML model registry, NOT a
strategy execution registry. A future StrategyRegistry type will be a
separate concept introduced in S3.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.core.order_contract import OrderPackage
    from src.core.signal_contract import SignalPackage


class StrategyInterface(ABC):
    """Contract every concrete strategy module must satisfy."""

    # Must be set as a class attribute on concrete implementations
    strategy_id: str

    @abstractmethod
    def build_signal(
        self,
        bars: Any,
        cfg: dict,
        **kwargs: Any,
    ) -> "SignalPackage":
        """Analyse market data and return a normalized SignalPackage.

        Returns a SignalPackage with side="none" when there is no actionable
        signal. Must never raise — catch all internal exceptions and return
        a flat signal instead.
        """

    @abstractmethod
    def build_order_package(
        self,
        signal: "SignalPackage",
        cfg: dict,
        **kwargs: Any,
    ) -> "OrderPackage":
        """Size and package a signal into a normalized OrderPackage.

        The allocator calls this after approving a signal. The concrete
        implementation may delegate sizing to risk.py or use the allocator's
        qty directly.
        """

    @property
    def category(self) -> str:
        """Strategy category string. Override in concrete subclasses.

        Expected values (mirrors docs/architecture target):
          'mean_reversion_dislocation'   — vwap
          'trend_pullback_continuation'  — turtle_soup
          'breakout_expansion'           — ict_scalp_5m
        """
        return "unknown"

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(strategy_id={self.strategy_id!r})"
