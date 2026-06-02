"""Order placement / modify / cancel.

Order placement passes through ``RiskManager.check`` *before* the wire
call and registers itself with the manager on submission so the
in-flight count is accurate. ``client_order_id`` is the idempotency
key — generated automatically if the caller doesn't pass one. The same
``client_order_id`` will be rejected by the risk layer if still
in-flight, which protects against duplicate sends on a retry.

Dry-run mode short-circuits the actual HTTP call and returns a synthetic
``Order`` whose ``id`` is negative so the rest of the system can tell
real from simulated. This is how the demo workflow stays useful even
before live API access is purchased.
"""
from __future__ import annotations

import uuid

from .endpoints import REST
from .exceptions import TradovateAPIError
from .logging_utils import get_logger
from .models import Order, OrderRequest
from .position_service import PositionService
from .rest_client import TradovateRestClient
from .risk_manager import RiskManager

_log = get_logger(__name__)


class OrderService:
    def __init__(
        self,
        rest: TradovateRestClient,
        risk: RiskManager,
        positions: PositionService | None = None,
        dry_run: bool = True,
        contract_resolver=None,
    ):
        self._rest = rest
        self._risk = risk
        self._positions = positions
        self._dry_run = dry_run
        # contract_resolver(symbol) -> contract_id, supplied by MarketDataService
        # so this module doesn't need to know about contract lookup details.
        self._contract_resolver = contract_resolver

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    def place(self, req: OrderRequest) -> Order:
        if req.client_order_id is None:
            req = _with_client_id(req, f"tv-{uuid.uuid4().hex[:12]}")

        contract_id = self._resolve_contract(req.symbol)
        net = (
            self._positions.net_qty(req.account_id, contract_id)
            if (self._positions and contract_id)
            else 0
        )

        self._risk.check(req, current_net_qty=net)
        self._risk.register_submitted(req)

        if self._dry_run:
            _log.info("dry-run order accepted",
                      extra={"symbol": req.symbol, "side": req.side.value,
                             "qty": req.qty, "client_order_id": req.client_order_id})
            return Order(
                id=-(abs(hash(req.client_order_id)) % (10 ** 9)) - 1,
                account_id=req.account_id,
                status="DryRunAccepted",
                symbol=req.symbol,
                side=req.side,
                qty=req.qty,
                filled_qty=0,
                avg_price=None,
                client_order_id=req.client_order_id,
            )

        try:
            raw = self._rest.post(REST.order_place, body=req.to_api(contract_id=contract_id or 0))
        except TradovateAPIError:
            self._risk.register_terminal(req.client_order_id)
            raise

        if not isinstance(raw, dict) or "orderId" not in raw and "id" not in raw:
            self._risk.register_terminal(req.client_order_id)
            raise TradovateAPIError(200, raw, "placeorder returned unexpected payload")

        order = Order.from_api({**raw, "id": raw.get("orderId", raw.get("id"))})
        _log.info("order placed",
                  extra={"order_id": order.id, "client_order_id": order.client_order_id,
                         "symbol": req.symbol, "side": req.side.value, "qty": req.qty})
        return order

    def cancel(self, order_id: int) -> dict:
        return self._rest.post(REST.order_cancel, body={"orderId": order_id})

    def cancel_all(self, account_id: int) -> list[dict]:
        results: list[dict] = []
        for o in self.list_working(account_id):
            try:
                results.append(self.cancel(o.id))
            except TradovateAPIError as e:
                _log.warning("cancel failed", extra={"order_id": o.id, "err": str(e)})
        return results

    def modify(self, order_id: int, *, qty: int | None = None,
               limit_price: float | None = None, stop_price: float | None = None) -> dict:
        body: dict = {"orderId": order_id}
        if qty is not None:
            body["orderQty"] = qty
        if limit_price is not None:
            body["price"] = limit_price
        if stop_price is not None:
            body["stopPrice"] = stop_price
        return self._rest.post(REST.order_modify, body=body)

    def list_working(self, account_id: int | None = None) -> list[Order]:
        raw = self._rest.get(REST.order_list)
        if not isinstance(raw, list):
            return []
        orders = [Order.from_api(o) for o in raw if isinstance(o, dict)]
        if account_id is None:
            return orders
        return [o for o in orders if o.account_id == account_id]

    def _resolve_contract(self, symbol: str) -> int | None:
        if self._contract_resolver is None:
            return None
        try:
            return int(self._contract_resolver(symbol))
        except Exception:
            return None


def _with_client_id(req: OrderRequest, cid: str) -> OrderRequest:
    return OrderRequest(
        account_id=req.account_id, symbol=req.symbol, side=req.side, qty=req.qty,
        order_type=req.order_type, limit_price=req.limit_price, stop_price=req.stop_price,
        time_in_force=req.time_in_force, client_order_id=cid, is_automated=req.is_automated,
    )
