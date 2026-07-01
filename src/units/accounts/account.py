"""TradingAccount — independent per-account subunit (S-010 PR #1 / S-011 PR #1).

Each account owns its own RiskManager instance and routes orders through
the Integrator.  Accounts are isolated: a risk breach on one account
does not affect others.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.units.accounts.risk import RiskManager

logger = logging.getLogger(__name__)

# The valid funding categories. account_class is the single source of truth for
# the paper/real-money reporting axis; anything else coerces to "real_money"
# (the safe, no-strand default) with a logged warning.
# `prop` (2026-06-17) is a THIRD category: a prop-firm evaluation/funded account
# (e.g. Breakout). NOT our custody + a sim venue, so it's tracked SEPARATELY —
# excluded from BOTH the real-money and paper aggregates (routers' predicates).
_VALID_ACCOUNT_CLASSES = frozenset({"paper", "real_money", "prop"})


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
        Resolved from ``config/accounts.yaml::mode`` at load time; the only
        sanctioned writer is the ``set-account-mode`` system-action
        (``scripts/ops/set_account_mode.sh``).
    account_class : str
        The paper-vs-real-money FUNDING category — ``"paper"`` or
        ``"real_money"`` (default ``"real_money"``). Single source of truth
        for the paper/real reporting axis, ORTHOGONAL to ``dry_run`` /
        ``mode:`` (the technical execution gate). Resolved from
        ``config/accounts.yaml::account_class``. An invalid value is coerced
        to ``"real_money"`` with a logged WARNING — never raises, so a typo
        in config can never break the trader boot (Prime Directive). Stamped
        onto every trade row (``trades.account_class``) by the executor and
        surfaced on the API as ``accountClass``.
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
        account_class: str = "real_money",
        ib_host: Optional[str] = None,
        ib_port: Optional[int] = None,
        ib_account: Optional[str] = None,
        ib_client_id: Optional[int] = None,
        alpaca_env: Optional[str] = None,
        base_url: Optional[str] = None,
        oanda_env: Optional[str] = None,
        symbols: Optional[List[str]] = None,
        api_secret_env: Optional[str] = None,
    ) -> None:
        self.name = name
        self.exchange = exchange
        self.api_key_env = api_key_env     # env var name, not the actual key
        # Companion secret env-var NAME (not the value). Load-bearing for
        # accounts that name their OWN key pair — e.g. alpaca_live points at
        # ALPACA_API_KEY_ID_LIVE / ALPACA_API_SECRET_KEY_LIVE so it can run
        # concurrently with the paper account. MUST be forwarded into the
        # execution/management account_cfg so alpaca_client_for pairs the live
        # KEY with the live SECRET; without it the client falls back to the
        # shared paper secret (ALPACA_API_SECRET_KEY) → live-key+paper-secret
        # mismatch → Alpaca 401 "unauthorized" on every order while READS
        # (built from the raw YAML, which has both) still succeed
        # (BL-20260701-ALPACA-LIVE-SECRET-ENV).
        self.api_secret_env: Optional[str] = api_secret_env
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
        # signals to scaffolded accounts that declared no strategies.
        self.strategies: Optional[List[str]] = (
            None if strategies is None else list(strategies)
        )
        # ``configured`` reflects whether the account's env-var
        # credentials are populated. False accounts still load (so
        # /accounts_status can list them) but every action that needs
        # creds refuses + emits a diagnostic ping. ``configured_reason``
        # carries a human-readable explanation for the operator
        # (e.g. "OANDA_API_TOKEN not set").
        self.configured: bool = bool(configured)
        self.configured_reason: Optional[str] = configured_reason
        # Bybit V5 category — ``spot`` for cash market, ``linear`` for
        # USDT-margined perps. Source of truth is ``config/accounts.yaml``
        # ``market_type`` field; default is ``spot`` per the operator
        # directive 2026-05-06 (the perp-instead-of-spot fix).
        self.market_type: str = str(market_type or "spot").strip().lower()
        # True when this account routes to Bybit's demo trading endpoint
        # (https://api-demo.bybit.com). Populated from accounts.yaml `demo: true`.
        # BYBIT-TRANSPORT ONLY — selects the demo endpoint; it is NOT the
        # paper/real category (that's account_class below). Used by:
        # clients.py (demo=True to pybit HTTP) and the Bybit-demo PnL
        # fallback in execute.py / clients.py.
        self.demo: bool = bool(demo)
        # Paper-vs-real-money FUNDING category — the single source of truth
        # for the paper/real reporting axis, orthogonal to dry_run / mode.
        # Populated from accounts.yaml `account_class:`. Normalised here;
        # an invalid value coerces to "real_money" (the safe no-strand
        # default) with a logged WARNING — never raises, so a config typo
        # can't break the trader boot (Prime Directive). The executor
        # stamps it onto every trade row (trades.account_class) and the API
        # surfaces it as `accountClass`.
        normalized_class = str(account_class or "real_money").strip().lower()
        if normalized_class not in _VALID_ACCOUNT_CLASSES:
            logger.warning(
                "TradingAccount %r: invalid account_class %r — coercing to "
                "'real_money'. Valid values: %s.",
                name, account_class, sorted(_VALID_ACCOUNT_CLASSES),
            )
            normalized_class = "real_money"
        self.account_class: str = normalized_class
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
        # Alpaca / OANDA host selector — paper vs live. Populated from
        # accounts.yaml ``alpaca_env`` / ``oanda_env`` (+ optional
        # ``base_url`` override). The coordinator + read path forward these
        # into the account_cfg dict so ``alpaca_client_for`` /
        # ``oanda_client_for`` dial the correct host. WITHOUT them the
        # factories fall back to ``os.environ`` ALPACA_ENV/OANDA_ENV
        # (default "paper"/"practice"), so a LIVE account's live key is sent
        # to the PAPER host → "request is not authorized"
        # (BL-20260628-ALPACA-LIVE-HOST: alpaca_live was inert since the
        # 2026-06-26 live flip because this field was dropped on load).
        # None for non-Alpaca/OANDA accounts.
        self.alpaca_env: Optional[str] = alpaca_env
        self.base_url: Optional[str] = base_url
        self.oanda_env: Optional[str] = oanda_env
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

    # ``place_order`` REMOVED 2026-06-28 (full-system audit Workstream B,
    # operator-approved). It was the legacy per-account dispatch entry point
    # (risk-check → ``integrator.route_order`` → ``<Exchange>API.place``),
    # superseded by ``execute_pkg`` and never called in production — only by
    # the unit tests that exercised it. ``RiskManager.approve`` (the risk gate
    # it wrapped) is now reached on the live path via ``RiskManager.evaluate``
    # inside ``Coordinator.multi_account_execute``, which raises/catches
    # ``RiskBreach`` (still defined above — load-bearing on the live path).

    def status(self) -> Dict[str, Any]:
        """Return a summary dict suitable for Telegram display."""
        risk_report = self.risk_manager.report()
        return {
            "name": self.name,
            "exchange": self.exchange,
            "account_type": self.account_type,
            "account_class": self.account_class,
            "dry_run": self.dry_run,
            "configured": self.configured,
            "configured_reason": self.configured_reason,
            "open_positions": len(self.positions),
            "strategies": list(self.strategies or []),
            **risk_report,
        }
