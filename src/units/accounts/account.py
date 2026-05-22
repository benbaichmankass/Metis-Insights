"""TradingAccount — independent per-account subunit (S-010 PR #1 / S-011 PR #1).

Each account owns its own RiskManager instance and routes orders through
the Integrator.  Accounts are isolated: a risk breach on one account
does not affect others.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.core.coordinator import OrderPackage
from src.units.accounts.risk import RiskManager


class RiskBreach(Exception):
    """Raised when an order fails the account's RiskManager.approve() check."""


class TradingAccount:
    """Independent per-account subunit.

    Parameters
    ----------
    name : str
        Unique account identifier (matches accounts.yaml key).
    exchange : str
        Exchange name: ``"bybit"`` | ``"breakout"``.
    api_key_env : str
        Name of the environment variable holding the API key (never the key
        itself — keeps secrets out of config files).
    risk_manager : RiskManager
        Per-account risk gate instance.
    account_type : str
        ``"regular"`` or ``"prop"`` — informational; prop accounts use stricter
        risk defaults.
    dry_run : bool
        When True (default), orders are simulated — no live exchange calls.
        Toggle via ``/accounts dry|live <name>`` Telegram command or
        ``Coordinator.set_account_dry_run()``.
    """

    def __init__(
        self,
        name: str,
        exchange: str,
        api_key_env: str,
        risk_manager: RiskManager,
        account_type: str = "regular",
        dry_run: bool = True,
        strategies: Optional[List[str]] = None,
        configured: bool = True,
        configured_reason: Optional[str] = None,
        market_type: str = "spot",
        demo: bool = False,
        ib_host: Optional[str] = None,
        ib_port: Optional[int] = None,
        ib_account: Optional[str] = None,
        ib_client_id: Optional[int] = None,
        symbols: Optional[List[str]] = None,
    ) -> None:
        self.name = name
        self.exchange = exchange
        self.api_key_env = api_key_env     # env var name, not the actual key
        self.risk_manager = risk_manager
        self.account_type = account_type
        self.dry_run = dry_run             # default safe; toggle via Telegram
        self.positions: List[Dict[str, Any]] = []
        # BUG-033: per-account strategy assignment, surfaced in
        # /accounts_status so the operator can verify each wallet is
        # routed to the right model. accounts.yaml is the source of
        # truth; this is just the rendering hook.
        # Preserve the None / [] distinction from accounts.yaml so the
        # coordinator's per-account strategy filter can tell "legacy
        # account, no mapping declared" (None → fall through to allow)
        # from "explicitly assigned no strategies" ([] → block all).
        # Pre-fix this collapsed both forms to ``[]`` and ``[]`` was
        # treated as "legacy fallthrough", which silently routed live
        # signals to scaffolded accounts like ``prop_velotrade_1``.
        self.strategies: Optional[List[str]] = (
            None if strategies is None else list(strategies)
        )
        # Velotrade phase-2: ``configured`` reflects whether the
        # account's env-var credentials are populated. False accounts
        # still load (so /accounts_status can list them) but every
        # action that needs creds refuses + emits a diagnostic ping.
        # ``configured_reason`` carries a human-readable explanation
        # for the operator (e.g. "VELOTRADE_API_KEY_1 not set").
        self.configured: bool = bool(configured)
        self.configured_reason: Optional[str] = configured_reason
        # Bybit V5 category — ``spot`` for cash market, ``linear`` for
        # USDT-margined perps. Source of truth is ``config/accounts.yaml``
        # ``market_type`` field; default is ``spot`` per the operator
        # directive 2026-05-06 (the perp-instead-of-spot fix).
        self.market_type: str = str(market_type or "spot").strip().lower()
        # True when this account routes to Bybit's demo trading endpoint
        # (https://api-demo.bybit.com). Populated from accounts.yaml `demo: true`.
        # Used by: coordinator (demo Telegram prefix), execute.py (is_demo DB flag),
        # clients.py (demo=True to pybit HTTP).
        self.demo: bool = bool(demo)
        # Interactive Brokers connection identity (no API keys — auth is
        # the IB Gateway login session). Populated from accounts.yaml
        # ``ib_host`` / ``ib_port`` / ``ib_account`` / ``ib_client_id`` for
        # ``exchange: interactive_brokers`` accounts; None otherwise. The
        # coordinator forwards these into the account_cfg dict so
        # ``ib_client_for`` can build the socket. See src/units/accounts/
        # ib_client.py for the connection model.
        self.ib_host: Optional[str] = ib_host
        self.ib_port: Optional[int] = ib_port
        self.ib_account: Optional[str] = ib_account
        self.ib_client_id: Optional[int] = ib_client_id
        # Instrument symbol(s) this account trades. accounts.yaml is the
        # single source of truth for "what does this account trade", which
        # the multi-symbol tick loop unions to decide which symbols to run
        # each tick (src/main.py::_resolve_tick_symbols). None / empty ⇒
        # fall back to the per-exchange default (bybit→BTCUSDT,
        # interactive_brokers→MES) so an account that omits the field still
        # trades its natural instrument rather than nothing.
        self.symbols: Optional[List[str]] = (
            None if symbols is None else [str(s).strip() for s in symbols if str(s).strip()]
        )

    def place_order(self, order: OrderPackage, *, dry_run: Optional[bool] = None) -> str:
        """Risk-check and route *order* to the exchange.

        Parameters
        ----------
        order : OrderPackage
            The typed order from the Coordinator.
        dry_run : bool, optional
            Override the account-level ``self.dry_run`` flag.  When None
            (default) the account's own ``dry_run`` attribute is used.

        Returns
        -------
        str
            trade_id string (``"dry-..."`` in dry-run, exchange orderId live).

        Raises
        ------
        RiskBreach
            When the order fails the account's risk checks.
        """
        effective_dry_run = self.dry_run if dry_run is None else dry_run

        if not self.risk_manager.approve(order):
            reason = (
                "daily loss limit exceeded"
                if self.risk_manager.daily_pnl < -self.risk_manager.max_daily_loss_usd
                else "position size limit exceeded"
            )
            raise RiskBreach(
                f"Account '{self.name}' rejected order for {order.symbol}: {reason}"
            )

        from src.units.accounts.integrator import route_order
        return route_order(self, order, dry_run=effective_dry_run)

    def status(self) -> Dict[str, Any]:
        """Return a summary dict suitable for Telegram display."""
        risk_report = self.risk_manager.report()
        return {
            "name": self.name,
            "exchange": self.exchange,
            "account_type": self.account_type,
            "dry_run": self.dry_run,
            "configured": self.configured,
            "configured_reason": self.configured_reason,
            "open_positions": len(self.positions),
            "strategies": list(self.strategies or []),
            **risk_report,
        }
