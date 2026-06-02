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
    """Velotrade DXtrade prop firm API — phase-2 infrastructure.

    Phase-2 (this PR) turns the phase-1 stub into the real
    integration shape. Live placement is dispatched to an injected
    :class:`src.units.accounts.dxtrade_client.DXtradeClient` so the
    code path has the same routing skeleton as the bybit branch.
    The DXtrade *SDK calls themselves* (HTTP / auth / response
    parsing) live in :class:`DXtradeClient` and currently raise
    ``NotImplementedError`` until the operator drops the API
    contract — the rest of the pipeline (account loader, coordinator
    routing, executor branch, diagnostic ping) is already wired.

    Live placement preferred path is ``execute_pkg`` from
    :mod:`src.units.accounts.execute`, which receives the
    ``DXtradeClient`` from
    :func:`src.units.accounts.clients.velotrade_client_for`. The bare
    :meth:`place` here is kept for parity with :class:`BybitAPI` and
    for legacy callers that still reach into the integrator directly.
    """

    def __init__(
        self,
        api_key_env: str,
        *,
        client: object = None,
    ) -> None:
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
            trade_id = f"dry-velotrade-{uuid.uuid4().hex[:10]}"
            logger.info("VelotradeAPI DRY-RUN %s → %s", order.symbol, trade_id)
            return trade_id
        # Live path — the injected DXtradeClient owns the SDK call.
        # The executor in src.units.accounts.execute._submit_order
        # is the canonical caller and already mirrors bybit's
        # retCode-style error handling. The bare class still raises
        # if it's invoked without a client (legacy callers).
        from src.units.accounts.dxtrade_client import (
            DXtradeClient,
            MissingCredentialsError,
        )
        cli = client or self._client
        if cli is None:
            raise MissingCredentialsError(
                f"VelotradeAPI: live placement requires a DXtradeClient "
                f"(api_key_env={self.api_key_env!r}); call execute_pkg "
                f"with exchange_client=velotrade_client_for(account_cfg)."
            )
        if not isinstance(cli, DXtradeClient):
            raise TypeError(
                f"VelotradeAPI.place: expected DXtradeClient, got "
                f"{type(cli).__name__}"
            )
        side = "Buy" if order.direction == "long" else "Sell"
        resp = cli.place({
            "symbol": order.symbol,
            "side": side,
            "direction": order.direction,
            "entry": order.entry,
            "sl": order.sl,
            "tp": order.tp,
            "strategy": order.strategy,
        }) or {}
        ret_code = resp.get("retCode")
        if ret_code in (0, "0", None):
            order_id = (resp.get("result") or {}).get("orderId")
            return str(order_id or uuid.uuid4().hex)
        reason = str(resp.get("retMsg") or f"retCode={ret_code}")
        raise RuntimeError(f"DXtrade rejected order: {reason}")


class TradovateAPI:
    """Tradovate futures broker — phase-1 wiring.

    Phase-1 (this PR) wires the integrator routing for ``exchange:
    tradovate`` so the coordinator can dispatch order packages to a
    Tradovate account exactly like it does for bybit / velotrade /
    interactive_brokers. The actual HTTP / WS work lives in
    :class:`src.units.accounts.tradovate.adapter.TradovateAdapter`,
    which is built by
    :func:`src.units.accounts.clients.tradovate_client_for` and
    injected here as ``client``.

    Live-fire safety:
        - The account ships at ``mode: dry_run`` in
          ``config/accounts.yaml`` with ``strategies: []``, so this
          branch never fires until the operator explicitly promotes it
          via the ``set-account-mode`` action AND assigns a strategy.
        - The injected adapter additionally honors its own
          ``TradovateConfig.dry_run`` flag (env
          ``TRADOVATE_DRY_RUN``); when set, ``OrderService.place``
          short-circuits with a synthetic negative-id ``Order``. This
          double-gate matches the live-VM rule that no money moves
          without explicit, declared opt-in.

    Live placement preferred path is ``execute_pkg`` from
    :mod:`src.units.accounts.execute`, which receives the
    ``TradovateAdapter`` from
    :func:`src.units.accounts.clients.tradovate_client_for`. The bare
    :meth:`place` here is kept for parity with :class:`VelotradeAPI`
    and for legacy callers.
    """

    def __init__(self, api_key_env: str, *, client: object = None) -> None:
        # Kept for shape-parity with the other API classes even though
        # Tradovate auth doesn't use the single api_key_env pattern —
        # creds are 7 env vars read by TradovateConfig.load() inside
        # the adapter.
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
            trade_id = f"dry-tradovate-{uuid.uuid4().hex[:10]}"
            logger.info("TradovateAPI DRY-RUN %s → %s", order.symbol, trade_id)
            return trade_id
        from src.units.accounts.tradovate.adapter import TradovateAdapter
        from src.units.accounts.tradovate.exceptions import (
            TradovateAPIError,
            TradovateConfigError,
            TradovateRiskRejection,
        )
        from src.units.accounts.tradovate.models import (
            OrderRequest,
            OrderSide,
            OrderType,
        )

        cli = client or self._client
        if cli is None:
            raise TradovateConfigError(
                f"TradovateAPI: live placement requires a TradovateAdapter "
                f"(api_key_env={self.api_key_env!r}); call execute_pkg "
                f"with exchange_client=tradovate_client_for(account_cfg)."
            )
        if not isinstance(cli, TradovateAdapter):
            raise TypeError(
                f"TradovateAPI.place: expected TradovateAdapter, got "
                f"{type(cli).__name__}"
            )
        side = OrderSide.BUY if (order.direction or "").lower() == "long" else OrderSide.SELL
        # OrderPackage doesn't carry a Tradovate accountId — it's resolved
        # from the per-account config by the executor branch in
        # src/units/accounts/execute.py, not here. The bare class is a
        # legacy entry point: callers that hit it must supply qty=1 and
        # the adapter's already-configured default account.
        req = OrderRequest(
            account_id=getattr(order, "account_id", 0) or 0,
            symbol=order.symbol,
            side=side,
            qty=int(getattr(order, "qty", 1) or 1),
            order_type=OrderType.MARKET,
        )
        try:
            placed = cli.place_order(req)
        except (TradovateRiskRejection, TradovateAPIError) as exc:
            raise RuntimeError(f"Tradovate rejected order: {exc}") from exc
        return str(placed.id)


EXCHANGE_MAP: dict[str, type] = {
    "bybit": BybitAPI,
    "breakout": BreakoutAPI,
    "velotrade": VelotradeAPI,
    "tradovate": TradovateAPI,
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
