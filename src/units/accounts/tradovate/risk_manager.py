"""Pre-trade guardrails.

The risk manager is the *only* place that decides whether an order is
allowed to leave the process. ``OrderService`` calls ``check()`` before
every placement; a violation raises ``TradovateRiskRejection`` with a
machine-readable reason that the caller (or logs) can branch on.

Guardrails implemented:

- allowed-symbols whitelist (empty = anything goes)
- max position per symbol (computed from current net + order qty)
- max in-flight open orders
- max notional (best-effort, requires a quote)
- duplicate in-flight client_order_id rejection
- paper-mode-only safety flag — refuses live orders when set
- dry-run mode (handled by ``OrderService``, not here; this just flags)
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

from .config import TradovateConfig
from .exceptions import TradovateRiskRejection
from .models import OrderRequest, OrderSide, RiskLimits


@dataclass
class RiskState:
    open_orders: int = 0
    in_flight_client_ids: set[str] | None = None

    def __post_init__(self) -> None:
        if self.in_flight_client_ids is None:
            self.in_flight_client_ids = set()


class RiskManager:
    def __init__(
        self,
        config: TradovateConfig,
        limits: RiskLimits | None = None,
        paper_only: bool = False,
    ):
        self._cfg = config
        self._limits = limits or RiskLimits(
            allowed_symbols=config.allowed_symbols,
            max_position_per_symbol=config.max_position_per_symbol,
            max_open_orders=config.max_open_orders,
        )
        self._paper_only = paper_only
        self._state = RiskState()
        self._lock = threading.Lock()

    @property
    def limits(self) -> RiskLimits:
        return self._limits

    def check(
        self,
        req: OrderRequest,
        *,
        current_net_qty: int = 0,
        latest_price: float | None = None,
    ) -> None:
        with self._lock:
            if self._paper_only and not self._cfg.is_demo:
                raise TradovateRiskRejection("paper_only_violation",
                                             "paper_only=True but TRADOVATE_ENV=live")

            if self._limits.allowed_symbols and req.symbol.upper() not in self._limits.allowed_symbols:
                raise TradovateRiskRejection("symbol_not_whitelisted", req.symbol)

            if req.qty <= 0:
                raise TradovateRiskRejection("non_positive_qty", str(req.qty))

            projected = current_net_qty + (req.qty if req.side is OrderSide.BUY else -req.qty)
            if abs(projected) > self._limits.max_position_per_symbol:
                raise TradovateRiskRejection(
                    "max_position_per_symbol",
                    f"projected={projected} cap={self._limits.max_position_per_symbol}",
                )

            if self._state.open_orders >= self._limits.max_open_orders:
                raise TradovateRiskRejection(
                    "max_open_orders",
                    f"open={self._state.open_orders} cap={self._limits.max_open_orders}",
                )

            if (
                self._limits.max_notional is not None
                and latest_price is not None
                and abs(req.qty * latest_price) > self._limits.max_notional
            ):
                raise TradovateRiskRejection(
                    "max_notional",
                    f"req={req.qty * latest_price:.2f} cap={self._limits.max_notional:.2f}",
                )

            if req.client_order_id and req.client_order_id in self._state.in_flight_client_ids:
                raise TradovateRiskRejection("duplicate_client_order_id", req.client_order_id)

    def register_submitted(self, req: OrderRequest) -> None:
        with self._lock:
            self._state.open_orders += 1
            if req.client_order_id:
                self._state.in_flight_client_ids.add(req.client_order_id)

    def register_terminal(self, client_order_id: str | None) -> None:
        with self._lock:
            self._state.open_orders = max(0, self._state.open_orders - 1)
            if client_order_id:
                self._state.in_flight_client_ids.discard(client_order_id)
