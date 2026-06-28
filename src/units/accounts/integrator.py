"""Exchange registry — the canonical set of wired exchanges (S-010 PR #1).

``EXCHANGE_MAP`` maps exchange name → API stub class. It is the canonical
registry of wired exchanges, consumed by the live-trade management-caps CI
contract (``tests/test_ltmgmt_p5_contract_ci.py``: every ``EXCHANGE_MAP``
integration must declare its ``EXCHANGE_MANAGEMENT_CAPS``) and by the
``test_s028`` VWAP-regression guard (which asserts the live path never reaches
``BybitAPI.place``).

> **Note (2026-06-28 full-system audit):** the legacy ``route_order()``
> dispatcher and ``TradingAccount.place_order()`` were REMOVED — they were a
> vestigial router fully superseded by ``execute_pkg`` (the live per-exchange
> path in ``src/units/accounts/execute.py``); nothing in the live path called
> them (``coordinator.py`` documents ``account.place_order`` was dropped after
> it raised ``NotImplementedError`` — the VWAP "0 fills" bug). The stub classes
> + ``EXCHANGE_MAP`` are retained as the wired-exchange registry above.

Supported exchanges:
  bybit     — Bybit Unified Trading (live + dry-run)
  breakout  — Breakout prop firm (manual browser-bridge ticket emitter)
  oanda     — OANDA v20 FX/metals (M15 Phase 2; practice host by default)
  alpaca    — Alpaca US stocks/ETFs (M15 Phase 2b; paper host by default)
"""
from __future__ import annotations

import uuid
import logging

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
    """Breakout prop firm — manual browser-bridge ticket emitter.

    Breakout has no order API we use: a prop-routed strategy emits a
    paste-ready DXTrade ticket (Telegram/FCM ``prop_signal``) for a human /
    assistant to place under supervision (design:
    ``docs/integrations/breakout-poc-manual-bridge-DESIGN.md``). So "live"
    placement here is a **ticket emission**, not an exchange call, and returns a
    ``prop-manual-<uuid>`` marker (no live position created). The canonical
    caller is ``execute_pkg`` (breakout branch); this stub is retained as the
    ``EXCHANGE_MAP["breakout"]`` registry entry.
    """

    def __init__(self, api_key_env: str) -> None:
        self.api_key_env = api_key_env

    def place(self, order: OrderPackage, *, dry_run: bool = True) -> str:
        if dry_run:
            trade_id = f"dry-breakout-{uuid.uuid4().hex[:10]}"
            logger.info("BreakoutAPI DRY-RUN %s → %s", order.symbol, trade_id)
            return trade_id
        from src.prop.breakout_executor import emit_prop_ticket
        order_dict = {
            "symbol": order.symbol,
            "direction": order.direction,
            "side": "Buy" if order.direction == "long" else "Sell",
            "entry": order.entry,
            "sl": order.sl,
            "tp": order.tp,
            "strategy": getattr(order, "strategy", "prop"),
        }
        return emit_prop_ticket(order_dict, {"account_id": "breakout"},
                                timeframe=(getattr(order, "meta", None) or {}).get("timeframe"))


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

# NOTE (2026-06-28 full-system audit): the legacy ``route_order(account, order)``
# dispatcher was REMOVED here, and ``TradingAccount.place_order`` with it. Both
# were a vestigial router superseded by ``execute_pkg`` (the live per-exchange
# path in ``src/units/accounts/execute.py``); nothing in the live path called
# them. ``EXCHANGE_MAP`` + the stub classes above are retained as the
# wired-exchange registry the management-caps CI contract enumerates.
