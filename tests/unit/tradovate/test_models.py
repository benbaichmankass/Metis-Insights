"""Internal models tolerate sparse/extra fields and translate cleanly."""
from src.units.accounts.tradovate.models import (
    Account, Contract, Fill, Order, OrderRequest, OrderSide, OrderType, Position,
)


def test_account_from_api_minimal():
    a = Account.from_api({"id": 42, "name": "DEMO123", "active": True})
    assert a.id == 42 and a.name == "DEMO123" and a.active


def test_contract_from_api():
    c = Contract.from_api({"id": 9, "name": "MESM6"})
    assert c.id == 9 and c.name == "MESM6"


def test_order_request_to_api_market():
    req = OrderRequest(account_id=1, symbol="MESM6", side=OrderSide.BUY, qty=2)
    body = req.to_api(contract_id=99)
    assert body == {
        "accountId": 1, "action": "Buy", "symbol": "MESM6",
        "orderQty": 2, "orderType": "Market", "isAutomated": True,
        "timeInForce": "Day", "contractId": 99,
    }


def test_order_request_to_api_limit_carries_price():
    req = OrderRequest(account_id=1, symbol="MESM6", side=OrderSide.SELL,
                       qty=1, order_type=OrderType.LIMIT, limit_price=5123.25,
                       client_order_id="abc")
    body = req.to_api(contract_id=99)
    assert body["orderType"] == "Limit"
    assert body["price"] == 5123.25
    assert body["text"] == "abc"


def test_order_from_api_handles_unknown_status():
    o = Order.from_api({"id": 7, "accountId": 1, "ordStatus": "Working",
                        "symbol": "MESM6", "action": "Buy", "orderQty": 2,
                        "cumQty": 1, "avgPx": 5100.0})
    assert o.id == 7 and o.status == "Working" and o.side is OrderSide.BUY
    assert o.qty == 2 and o.filled_qty == 1 and o.avg_price == 5100.0


def test_fill_from_api():
    f = Fill.from_api({"id": 1, "orderId": 7, "accountId": 1, "qty": 1,
                       "price": 5100.0, "action": "Buy",
                       "timestamp": "2026-06-02T13:00:00Z"})
    assert f.order_id == 7 and f.price == 5100.0
    assert f.ts is not None and f.ts.tzinfo is not None


def test_position_from_api_short():
    p = Position.from_api({"accountId": 1, "contractId": 9, "netPos": -2,
                            "netPrice": 5100.0})
    assert p.net_pos == -2
