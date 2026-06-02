"""Order service: dry-run + risk integration without hitting network."""
from __future__ import annotations

from src.units.accounts.tradovate.config import TradovateConfig
from src.units.accounts.tradovate.models import OrderRequest, OrderSide
from src.units.accounts.tradovate.order_service import OrderService
from src.units.accounts.tradovate.risk_manager import RiskManager


_CREDS = {
    "TRADOVATE_USERNAME": "u", "TRADOVATE_PASSWORD": "p",
    "TRADOVATE_APP_ID": "a", "TRADOVATE_APP_VERSION": "1",
    "TRADOVATE_CID": "1", "TRADOVATE_SECRET": "s", "TRADOVATE_DEVICE_ID": "d",
    "TRADOVATE_ALLOWED_SYMBOLS": "MESM6",
}


class _FakeRest:
    def __init__(self):
        self.calls: list[tuple[str, str, dict | None]] = []

    def get(self, path, params=None):
        self.calls.append(("GET", path, params))
        return []

    def post(self, path, body):
        self.calls.append(("POST", path, body))
        return {"orderId": 12345, "ordStatus": "Working"}


def _service(dry_run: bool) -> OrderService:
    cfg = TradovateConfig.load(_CREDS)
    risk = RiskManager(cfg)
    rest = _FakeRest()
    return OrderService(rest, risk, dry_run=dry_run, contract_resolver=lambda _s: 9)


def test_dry_run_does_not_call_post():
    svc = _service(dry_run=True)
    req = OrderRequest(account_id=1, symbol="MESM6", side=OrderSide.BUY, qty=1)
    order = svc.place(req)
    assert order.status == "DryRunAccepted"
    assert order.id < 0  # synthetic id space
    assert all(m == "GET" for (m, _, _) in svc._rest.calls)


def test_live_fire_calls_placeorder():
    svc = _service(dry_run=False)
    req = OrderRequest(account_id=1, symbol="MESM6", side=OrderSide.BUY, qty=1)
    order = svc.place(req)
    posts = [c for c in svc._rest.calls if c[0] == "POST"]
    assert len(posts) == 1
    assert posts[0][1] == "/order/placeOrder"
    assert order.id == 12345
    assert order.status == "Working"


def test_client_order_id_auto_generated_and_unique():
    svc = _service(dry_run=True)
    req = OrderRequest(account_id=1, symbol="MESM6", side=OrderSide.BUY, qty=1)
    o1 = svc.place(req)
    o2 = svc.place(req)
    assert o1.client_order_id and o2.client_order_id
    assert o1.client_order_id != o2.client_order_id
