"""Exchange integrator — routes orders to the correct API (S-010 PR #1).

EXCHANGE_MAP maps exchange name → API stub class.
Live exchange clients are injected at runtime; tests use dry-run mode.

Supported exchanges:
  bybit     — Bybit Unified Trading (live + dry-run)
  breakout  — Breakout prop firm API (deprecated, inert stub)
  oanda     — OANDA v20 FX/metals (M15 Phase 2; practice host by default)
  alpaca    — Alpaca US stocks/ETFs (M15 Phase 2b; paper host by default)
"""
from __future__ import annotations

import uuid
import logging
from typing import TYPE_CHECKING

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
    """Breakout prop firm API stub — DEPRECATED and inert.

    Kept only so any legacy fixture that still references
    ``exchange: breakout`` continues to load. Dry-run returns a stub
    trade id; live placement is unsupported.
    """

    def __init__(self, api_key_env: str) -> None:
        self.api_key_env = api_key_env

    def place(self, order: OrderPackage, *, dry_run: bool = True) -> str:
        if dry_run:
            trade_id = f"dry-breakout-{uuid.uuid4().hex[:10]}"
            logger.info("BreakoutAPI DRY-RUN %s → %s", order.symbol, trade_id)
            return trade_id
        raise NotImplementedError(
            "BreakoutAPI is deprecated and unsupported for live placement."
        )


class AlpacaAPI:
    """Alpaca Trading API — M15 Phase 2b (second new-market wire).

    Live placement is dispatched to an injected
    :class:`src.units.accounts.alpaca_client.AlpacaClient`; the
    canonical caller is ``execute_pkg`` (executor branch mirrors the
    oanda retCode contract).
    """

    def __init__(self, api_key_env: str, *, client: object = None) -> None:
        self.api_key_env = api_key_env
        self._client = client

    def place(
        self,
        order: OrderPackage,
        *,
        dry_run: bool = True,
        client: object = None,
    ) -> str:
        if dry_run:
            trade_id = f"dry-alpaca-{uuid.uuid4().hex[:10]}"
            logger.info("AlpacaAPI DRY-RUN %s → %s", order.symbol, trade_id)
            return trade_id
        from src.units.accounts.alpaca_client import (
            AlpacaClient,
            MissingCredentialsError,
        )
        cli = client or self._client
        if cli is None:
            raise MissingCredentialsError(
                "AlpacaAPI: live placement requires an AlpacaClient "
                "(ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY); call "
                "execute_pkg with exchange_client=alpaca_client_for(account_cfg)."
            )
        if not isinstance(cli, AlpacaClient):
            raise TypeError(
                f"AlpacaAPI.place: expected AlpacaClient, got {type(cli).__name__}"
            )
        side = "Buy" if order.direction == "long" else "Sell"
        resp = cli.place({
            "symbol": order.symbol,
            "side": side,
            "qty": getattr(order, "qty", 1) or 1,
            "sl": order.sl,
            "tp": order.tp,
            "strategy": order.strategy,
        }) or {}
        ret_code = resp.get("retCode")
        if ret_code in (0, "0", None):
            order_id = (resp.get("result") or {}).get("orderId")
            return str(order_id or uuid.uuid4().hex)
        reason = str(resp.get("retMsg") or f"retCode={ret_code}")
        raise RuntimeError(f"Alpaca rejected order: {reason}")


class OandaAPI:
    """OANDA v20 — M15 Phase 2 (first new-market wire, XAU/USD verdict).

    Live placement is dispatched to an injected
    :class:`src.units.accounts.oanda_client.OandaClient`; the canonical
    caller is ``execute_pkg`` (executor branch mirrors the bybit
    retCode contract). The bare :meth:`place` is kept for parity with
    :class:`BybitAPI`.
    """

    def __init__(self, api_key_env: str, *, client: object = None) -> None:
        self.api_key_env = api_key_env
        self._client = client

    def place(
        self,
        order: OrderPackage,
        *,
        dry_run: bool = True,
        client: object = None,
    ) -> str:
        if dry_run:
            trade_id = f"dry-oanda-{uuid.uuid4().hex[:10]}"
            logger.info("OandaAPI DRY-RUN %s → %s", order.symbol, trade_id)
            return trade_id
        from src.units.accounts.oanda_client import (
            MissingCredentialsError,
            OandaClient,
        )
        cli = client or self._client
        if cli is None:
            raise MissingCredentialsError(
                "OandaAPI: live placement requires an OandaClient "
                "(OANDA_API_TOKEN / OANDA_ACCOUNT_ID); call execute_pkg "
                "with exchange_client=oanda_client_for(account_cfg)."
            )
        if not isinstance(cli, OandaClient):
            raise TypeError(
                f"OandaAPI.place: expected OandaClient, got {type(cli).__name__}"
            )
        side = "Buy" if order.direction == "long" else "Sell"
        resp = cli.place({
            "symbol": order.symbol,
            "side": side,
            "qty": getattr(order, "qty", 1) or 1,
            "sl": order.sl,
            "tp": order.tp,
            "strategy": order.strategy,
        }) or {}
        ret_code = resp.get("retCode")
        if ret_code in (0, "0", None):
            order_id = (resp.get("result") or {}).get("orderId")
            return str(order_id or uuid.uuid4().hex)
        reason = str(resp.get("retMsg") or f"retCode={ret_code}")
        raise RuntimeError(f"OANDA rejected order: {reason}")


# ---------------------------------------------------------------------------
# Exchange registry
# ---------------------------------------------------------------------------

EXCHANGE_MAP: dict[str, type] = {
    "bybit": BybitAPI,
    "breakout": BreakoutAPI,
    "oanda": OandaAPI,
    "alpaca": AlpacaAPI,
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
