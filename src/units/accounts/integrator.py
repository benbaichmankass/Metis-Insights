"""Exchange integrator — routes orders to the correct API (S-010 PR #1).

EXCHANGE_MAP maps exchange name → API stub class.
Live exchange clients are injected at runtime; tests use dry-run mode.

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
    ``EXCHANGE_MAP`` registry marker (route_order was removed 2026-06-28).
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


# ---------------------------------------------------------------------------
# Router — REMOVED 2026-06-28 (full-system audit Workstream B, operator-approved)
# ---------------------------------------------------------------------------
#
# ``route_order(account, order)`` + ``TradingAccount.place_order`` were the
# legacy per-account dispatch path, superseded by ``execute_pkg`` (the live
# path; per-exchange branches in ``src/units/accounts/execute.py``). They had
# **zero production callers** — the only references were in the unit tests that
# exercised them (see the audit finding) and the VWAP "0 fills" incident
# (``coordinator.py`` documents that ``account.place_order`` was removed from
# the live path after it raised NotImplementedError every tick). ``execute_pkg``
# is the single canonical entry point; ``Coordinator.multi_account_execute``
# calls it directly.
#
# ``EXCHANGE_MAP`` + the four stub ``*API`` classes ABOVE are retained — they are
# the integration registry consumed by the ``test_ltmgmt_p5_contract_ci`` CI
# guard (every ``EXCHANGE_MAP`` key must declare ``EXCHANGE_MANAGEMENT_CAPS``) and
# the ``new-broker`` skill, and ``BybitAPI.place`` is the patch target for the
# ``test_s028_vwap_execute_routing`` regression guard (which asserts the live
# path routes through ``execute_pkg``, NOT this map). Do not remove them.
