"""Exchange integrator — routes orders to the correct API (S-010 PR #1).

EXCHANGE_MAP maps exchange name → API stub class.
Live exchange clients are injected at runtime; tests use dry-run mode.

Supported exchanges:
  bybit     — Bybit Unified Trading (live + dry-run)
  breakout  — Breakout prop firm API (deprecated stub; replaced by velotrade)
  velotrade — Velotrade DXtrade prop firm API (stub; dry-run only)
"""
from __future__ import annotations

import uuid
import logging
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.units.accounts.account import TradingAccount
from src.core.coordinator import OrderPackage

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exchange API stubs
# ---------------------------------------------------------------------------

class BybitAPI:
    """Thin wrapper around the Bybit SDK.  Dry-run by default."""

    def __init__(self, api_key_env: str) -> None:
        self.api_key_env = api_key_env

    def place(self, order: OrderPackage, *, dry_run: bool = True) -> str:
        if dry_run:
            trade_id = f"dry-bybit-{uuid.uuid4().hex[:10]}"
            logger.info("BybitAPI DRY-RUN %s → %s", order.symbol, trade_id)
            return trade_id
        # Live path — inject real SDK client via exchange_client parameter
        raise NotImplementedError(
            "BybitAPI live placement requires an injected exchange_client; "
            "use execute_pkg() from src.units.accounts.execute for live trading."
        )


class BreakoutAPI:
    """Breakout prop firm API stub — DEPRECATED.

    The platform has been replaced by Velotrade (DXtrade) for the
    next prop onboarding. Kept as an alias to ``VelotradeAPI`` so any
    existing fixtures that still reference ``exchange: breakout``
    continue to load. New configs should use ``exchange: velotrade``.
    """

    def __init__(self, api_key_env: str) -> None:
        self.api_key_env = api_key_env

    def place(self, order: OrderPackage, *, dry_run: bool = True) -> str:
        if dry_run:
            trade_id = f"dry-breakout-{uuid.uuid4().hex[:10]}"
            logger.info("BreakoutAPI DRY-RUN %s → %s", order.symbol, trade_id)
            return trade_id
        raise NotImplementedError(
            "BreakoutAPI is deprecated; migrate to VelotradeAPI."
        )


class VelotradeAPI:
    """Velotrade DXtrade prop firm API stub — dry-run only.

    Replaces BreakoutAPI as the canonical prop-firm executor.
    Real DXtrade SDK integration ships in a follow-up sprint once
    credentials and the API contract are finalised. Until then the
    live path raises ``NotImplementedError`` so a misconfigured
    ``ALLOW_LIVE_TRADING=true`` cannot accidentally route a real
    order through an empty driver.
    """

    def __init__(self, api_key_env: str) -> None:
        self.api_key_env = api_key_env

    def place(self, order: OrderPackage, *, dry_run: bool = True) -> str:
        if dry_run:
            trade_id = f"dry-velotrade-{uuid.uuid4().hex[:10]}"
            logger.info("VelotradeAPI DRY-RUN %s → %s", order.symbol, trade_id)
            return trade_id
        raise NotImplementedError(
            "VelotradeAPI live integration not yet implemented; "
            "Velotrade accounts must run dry-run only until the "
            "DXtrade SDK is wired."
        )


EXCHANGE_MAP: dict[str, type] = {
    "bybit": BybitAPI,
    "breakout": BreakoutAPI,
    "velotrade": VelotradeAPI,
}


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

def route_order(
    account: "TradingAccount",
    order: OrderPackage,
    *,
    dry_run: bool = True,
) -> str:
    """Dispatch *order* to the correct exchange API for *account*.

    Parameters
    ----------
    account : TradingAccount
        Account whose exchange and api_key_env fields select the API class.
    order : OrderPackage
        Typed order from the Coordinator.
    dry_run : bool
        When True (default), simulate the order without live exchange calls.

    Returns
    -------
    str
        trade_id from the exchange API.

    Raises
    ------
    ValueError
        When *account.exchange* is not in EXCHANGE_MAP.
    """
    exchange = (account.exchange or "").lower()
    api_class = EXCHANGE_MAP.get(exchange)
    if api_class is None:
        raise ValueError(
            f"Unknown exchange '{exchange}' for account '{account.name}'. "
            f"Supported: {list(EXCHANGE_MAP)}"
        )
    api = api_class(account.api_key_env)
    return api.place(order, dry_run=dry_run)
