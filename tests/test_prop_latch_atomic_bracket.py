"""PROP-LATCH (2026-10-09): a resting DXtrade REST IF-THEN entry must not read
as ``partial_no_sl_tp`` when its protection is PROVEN, and must when it is not.

velotrade_1's first live REST ticket (2026-10-08 15:28Z) was a protected
resting LIMIT; the REST read carries no SL/TP on the entry (the SL/TP are child
orders), so it read as naked, the repair cancelled the executor's own order and
the AUTO-REVERT latch halted entries ~17h. Protection is proven by the listed
``-S`` / ``-T`` children, or -- when none is listed -- by our own entry id plus
an ``http 200`` on the group POST. Positions always get the full check.
"""
from __future__ import annotations

from typing import Any, Dict, List

from src.prop.platform.base import Position, WorkingOrder
from src.prop.platform.dxtrade_api import DXtradeApiAdapter, client_ids
from src.prop.prop_executor import _atomic, classify_confirmation, match_terminal

from tests.test_prop_platform_dxtrade_api import A, BASE, FakeServer

TID = "prop-manual-df6e8938ab89"
IDS = client_ids(TID)
SPEC = {"ticket_id": TID, "venue_symbol": "ETHUSD", "side": "short", "quantity": 0.82,
        "stop_loss": 2532.16, "take_profit": 2226.65, "order_type": "limit", "limit_price": 2471.31}
ATOMIC = {"atomic_bracket": True, "group_accepted": True}


def _row(cid: str, typ: str, side: str, effect: str, px: float, qty: Any = None) -> Dict[str, Any]:
    leg: Dict[str, Any] = {"positionEffect": effect, "price": px}
    if qty is not None:
        leg["quantity"] = qty
    return {"clientOrderId": cid, "orderCode": "s:" + cid, "type": typ, "instrument": "ETHUSD",
            "status": "WORKING", "finalStatus": False, "side": side, "legs": [leg]}


def _entry() -> Dict[str, Any]:
    return _row(IDS["entry"], "LIMIT", "SELL", "OPEN", 2471.31, qty=0.82)


def _children(sl: float = 2532.16, tp: float = 2226.65) -> List[Dict[str, Any]]:
    return [_row(IDS["sl"], "STOP", "BUY", "CLOSE", sl), _row(IDS["tp"], "LIMIT", "BUY", "CLOSE", tp)]


def _adapter(orders: List[Dict[str, Any]], positions: List[Dict[str, Any]] = ()) -> DXtradeApiAdapter:
    srv = FakeServer()
    srv.on(("GET", A + "/orders"), 200, orders, {"ETag": "e1"})
    srv.on(("GET", A + "/portfolio"), 200, {"portfolios": [{"positions": list(positions)}]})
    a = DXtradeApiAdapter(transport=srv, sleep=lambda s: None)
    a.login(None, BASE, "trader", "pw")
    return a


def _verdict(a: DXtradeApiAdapter, **kw: Any) -> str:
    found = match_terminal(SPEC, a.read_positions(), a.read_orders(), 0.02)
    return classify_confirmation(SPEC, found, 0.02, **kw)


def test_resting_entry_with_matching_children_is_placed():
    assert _verdict(_adapter([_entry()] + _children()), **ATOMIC) == "placed"
    # Children listed and matching prove it even without the POST record.
    assert _verdict(_adapter([_entry()] + _children()), atomic_bracket=True) == "placed"


def test_resting_entry_with_children_missing_is_partial_unless_group_proven():
    # No child listed and no accepted-POST record -> not proven.
    assert _verdict(_adapter([_entry()]), atomic_bracket=True) == "partial_no_sl_tp"
    # No child listed, our own entry id, and the IF-THEN POST recorded http 200.
    assert _verdict(_adapter([_entry()]), **ATOMIC) == "placed"


def test_resting_entry_with_one_child_or_wrong_child_is_partial():
    only_sl = [_entry(), _children()[0]]
    assert _verdict(_adapter(only_sl), **ATOMIC) == "partial_no_sl_tp"
    assert _verdict(_adapter([_entry()] + _children(sl=2600.0)), **ATOMIC) == "partial_no_sl_tp"


def test_foreign_entry_id_is_partial():
    foreign = _entry()
    foreign["clientOrderId"] = "someone-else-E"
    assert _verdict(_adapter([foreign]), **ATOMIC) == "partial_no_sl_tp"


def _position() -> Dict[str, Any]:
    return {"symbol": "ETHUSD", "side": "SELL", "quantity": 0.82, "openPrice": 2471.31, "positionCode": "555"}


def _pos_children() -> List[Dict[str, Any]]:
    out = _children()
    for o in out:
        o["legs"][0]["positionCode"] = "555"
    return out


def test_filled_position_with_close_children_is_open():
    assert _verdict(_adapter(_pos_children(), [_position()]), **ATOMIC) == "open"


def test_filled_position_without_children_is_partial():
    # group_accepted never exempts a POSITION.
    assert _verdict(_adapter([], [_position()]), **ATOMIC) == "partial_no_sl_tp"


def test_non_atomic_adapter_unchanged():
    # A resting entry with no SL/TP is partial for a non-atomic adapter, as before.
    o = WorkingOrder(symbol="ETHUSD", side="short", order_type="limit", quantity=0.82, price=2471.31,
                     order_id=IDS["entry"])
    found = match_terminal(SPEC, [], [o], 0.02)
    assert classify_confirmation(SPEC, found, 0.02) == "partial_no_sl_tp"
    assert classify_confirmation(SPEC, found, 0.02, group_accepted=True) == "partial_no_sl_tp"
    assert _atomic(object(), {"detail": "http 200"}) == {"atomic_bracket": False, "group_accepted": False}
    p = Position(symbol="ETHUSD", side="short", quantity=0.82, entry_price=2471.31,
                 stop_loss=2532.16, take_profit=2226.65)
    assert classify_confirmation(SPEC, match_terminal(SPEC, [p], [], 0.02), 0.02) == "open"


def test_group_accepted_only_on_http_200():
    a = DXtradeApiAdapter()
    assert _atomic(a, {"detail": "http 200"}) == {"atomic_bracket": True, "group_accepted": True}
    for d in ("outcome_unknown {}", "already_placed (409/100 duplicate client order id)", None):
        assert _atomic(a, {"detail": d})["group_accepted"] is False
