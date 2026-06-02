"""Broker-agnostic adapter facade.

The rest of the bot should depend on this surface — not the underlying
REST/WS clients directly — so a future swap to a different broker
keeps the call sites untouched. Methods mirror the small interface the
existing ``ib_client`` and ``dxtrade_client`` modules expose so a
strategy can route to whichever account is configured.

Construction wires every component together using a single
``TradovateConfig``. Callers normally use ``TradovateAdapter.build()``
and pass the resulting object around — no service-by-service
instantiation in the host app.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .account_service import AccountService
from .auth import TradovateAuth
from .config import TradovateConfig
from .event_bus import EventBus
from .logging_utils import get_logger
from .market_data_service import MarketDataService
from .models import Account, Order, OrderRequest, Position
from .order_service import OrderService
from .position_service import PositionService
from .recorder import Recorder
from .rest_client import TradovateRestClient
from .risk_manager import RiskManager

_log = get_logger(__name__)


@dataclass
class HealthReport:
    env: str
    authed: bool
    ws_connected: bool
    last_quote_ts: Optional[str]
    last_order_event_ts: Optional[str]


class TradovateAdapter:
    def __init__(
        self,
        config: TradovateConfig,
        auth: TradovateAuth,
        rest: TradovateRestClient,
        accounts: AccountService,
        market_data: MarketDataService,
        orders: OrderService,
        positions: PositionService,
        risk: RiskManager,
        bus: EventBus,
        ws=None,
        recorder: Recorder | None = None,
    ):
        self.config = config
        self.auth = auth
        self.rest = rest
        self.accounts = accounts
        self.market_data = market_data
        self.orders = orders
        self.positions = positions
        self.risk = risk
        self.bus = bus
        self.ws = ws
        self.recorder = recorder
        self._last_quote_ts: str | None = None
        self._last_order_event_ts: str | None = None

        bus.subscribe("quote", lambda q: self._touch("quote", q))
        bus.subscribe("response", lambda r: self._touch("order_event", r))

    @classmethod
    def build(
        cls,
        config: TradovateConfig | None = None,
        *,
        recorder_path: str | None = None,
        attach_ws: bool = False,
    ) -> "TradovateAdapter":
        cfg = config or TradovateConfig.load()
        auth = TradovateAuth(cfg)
        rest = TradovateRestClient(cfg, auth)
        bus = EventBus()
        recorder = Recorder(recorder_path) if recorder_path else None
        risk = RiskManager(cfg)
        positions = PositionService(rest)
        market_data = MarketDataService(rest)
        orders = OrderService(
            rest, risk, positions=positions, dry_run=cfg.dry_run,
            contract_resolver=market_data.contract_id_for,
        )
        accounts = AccountService(rest)

        ws = None
        if attach_ws:
            from .websocket_client import TradovateWebSocket
            ws = TradovateWebSocket(cfg, auth, bus=bus, recorder=recorder)
            market_data._ws = ws  # noqa: SLF001 — wiring at build time
        _log.info("adapter built",
                  extra={"env": cfg.env.value, "dry_run": cfg.dry_run, "ws": bool(ws)})
        return cls(cfg, auth, rest, accounts, market_data, orders, positions, risk, bus,
                   ws=ws, recorder=recorder)

    # Broker-agnostic surface ------------------------------------

    def list_accounts(self) -> list[Account]:
        return self.accounts.list_accounts()

    def list_positions(self) -> list[Position]:
        return self.positions.list_positions()

    def place_order(self, req: OrderRequest) -> Order:
        return self.orders.place(req)

    def cancel_order(self, order_id: int) -> dict:
        return self.orders.cancel(order_id)

    def cancel_all(self, account_id: int) -> list[dict]:
        return self.orders.cancel_all(account_id)

    def health(self) -> HealthReport:
        return HealthReport(
            env=self.config.env.value,
            authed=self.auth.current() is not None,
            ws_connected=bool(self.ws and self.ws.connected),
            last_quote_ts=self._last_quote_ts,
            last_order_event_ts=self._last_order_event_ts,
        )

    def close(self) -> None:
        self.rest.close()
        self.auth.close()
        if self.recorder is not None:
            self.recorder.close()

    def _touch(self, kind: str, _payload) -> None:
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).isoformat()
        if kind == "quote":
            self._last_quote_ts = ts
        else:
            self._last_order_event_ts = ts
