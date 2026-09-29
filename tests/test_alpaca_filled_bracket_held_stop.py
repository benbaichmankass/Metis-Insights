"""FIX-SA-03: a FILLED bracket's `held` stop leg must be visible to the sweep.

E75 audit: `naked_rearm` fired 6-7 min after 17 of 20 Alpaca entries since
2026-09-15 (all 3 `alpaca_live`). Root cause: `GET /v2/orders?status=open&
nested=true` returns a `held` leg ONLY nested under its parent. For an OCO the
parent is the working limit leg, so the stop is reached. For a BRACKET the
parent is the market ENTRY, which is `filled` -- not open -- so its held stop
child was never returned, `protection_state` graded `stop: False` at the first
post-grace sweep, and the re-arm tore the real bracket down.

Payload shapes:
* `_LIVE_QQQ_OCO_*` -- ids, prices, qty, class, type, side, tif, status taken
  from /api/diag/alpaca_open_orders?account_id=alpaca_paper, captured
  2026-09-29T10:38:22Z (trade 6260's re-armed OCO). The held stop appears ONLY
  under the parent's `legs`, never at top level -- the observation the fix
  rests on (9 of 9 held stops in that read).
* `_FILLED_BRACKET` -- the same Alpaca order object shape for a filled bracket
  parent with its two children (TP `new`, SL `held`), as the venue returns it
  under `status=closed&nested=true`. Trade 6260's broker_order_id is used for
  the parent. ⚠️ The child ids/prices are illustrative, not captured: the
  history read that would capture them is #14115, not yet deployed.
"""
from __future__ import annotations

from src.units.accounts.alpaca_client import AlpacaClient

_OCO_PARENT = "e7cfccdb-444a-4641-93f9-e0c885d3a324"
_LIVE_QQQ_OCO_STOP = {
    "id": "1f88c953-7f6b-412e-8f83-77f5a48d58ce",
    "client_order_id": "fd4dd779-3310-43a6-9bf6-cafb24133f71",
    "symbol": "QQQ", "order_class": "oco", "type": "stop", "side": "sell",
    "qty": "54", "filled_qty": "0", "stop_price": "725.67",
    "limit_price": None, "time_in_force": "gtc", "status": "held",
    "submitted_at": "2026-09-28T15:10:26.357132Z", "legs": None,
}
_LIVE_QQQ_OCO_PARENT = {
    "id": _OCO_PARENT,
    "client_order_id": "de4d31df-0293-431f-9456-a9bfb3a3c427",
    "symbol": "QQQ", "order_class": "oco", "type": "limit", "side": "sell",
    "qty": "54", "filled_qty": "0", "stop_price": None,
    "limit_price": "806.02", "time_in_force": "gtc", "status": "new",
    "submitted_at": "2026-09-28T15:10:26.361604Z",
    "legs": [_LIVE_QQQ_OCO_STOP],
}

_BRACKET_PARENT = "2ffe81cc-4155-4bfc-b080-7e926c54e0a9"   # trade 6260
_TP_LEG = {
    "id": "aaaaaaaa-0000-4000-8000-000000000001", "symbol": "QQQ",
    "order_class": "bracket", "type": "limit", "side": "sell", "qty": "54",
    "filled_qty": "0", "limit_price": "806.02", "stop_price": None,
    "time_in_force": "gtc", "status": "new", "legs": None,
}
_SL_LEG = {
    "id": "aaaaaaaa-0000-4000-8000-000000000002", "symbol": "QQQ",
    "order_class": "bracket", "type": "stop", "side": "sell", "qty": "54",
    "filled_qty": "0", "limit_price": None, "stop_price": "725.67",
    "time_in_force": "gtc", "status": "held", "legs": None,
}
_FILLED_BRACKET = {
    "id": _BRACKET_PARENT, "symbol": "QQQ", "order_class": "bracket",
    "type": "market", "side": "buy", "qty": "54", "filled_qty": "54",
    "filled_avg_price": "765.10", "time_in_force": "gtc", "status": "filled",
    "created_at": "2026-09-28T15:03:36Z", "filled_at": "2026-09-28T15:03:36Z",
    "legs": [_TP_LEG, _SL_LEG],
}


class _Venue(AlpacaClient):
    """Real `_open_orders_for_symbol`; only the HTTP hop is faked."""

    def __init__(self, open_rows, closed_rows, closed_rc=0):
        self.open_rows = open_rows
        self.closed_rows = closed_rows
        self.closed_rc = closed_rc
        self.calls: list = []

    def _request(self, method, path, json_body=None):  # type: ignore[override]
        self.calls.append((method, path))
        if method == "GET" and "status=open" in path:
            return {"retCode": 0, "result": self.open_rows}
        if method == "GET" and "status=closed" in path:
            if self.closed_rc:
                return {"retCode": self.closed_rc, "retMsg": "down"}
            return {"retCode": 0, "result": self.closed_rows}
        if method == "DELETE":
            return {"retCode": 0, "result": {}}
        return {"retCode": 500, "retMsg": f"unrouted {method} {path}"}


# What the venue returns for a freshly FILLED bracket under status=open: the
# working TP leg surfaces, the held SL does not (it is only under the filled
# parent, which status=open excludes).
_OPEN_AFTER_BRACKET_FILL = [_TP_LEG]


def test_the_defect_open_read_alone_cannot_see_the_held_stop():
    """Pin the pre-fix view: with only the open read, the stop is unseen."""
    rows = _OPEN_AFTER_BRACKET_FILL
    flat = [o for r in rows for o in [r, *(r.get("legs") or [])]]
    assert not any(o["type"] == "stop" for o in flat)


def test_filled_bracket_held_stop_is_seen_and_graded_stop_covered():
    venue = _Venue(_OPEN_AFTER_BRACKET_FILL, [_FILLED_BRACKET])
    st = venue.protection_state("QQQ")
    assert st["stop"] is True and st["target"] is True
    assert st["stop_prices"] == [725.67]
    assert st["legs"] == 2   # TP once (deduped), SL once
    closed_calls = [p for _m, p in venue.calls if "status=closed" in p]
    assert closed_calls and "nested=true" in closed_calls[0]
    assert "symbols=QQQ" in closed_calls[0]


def test_quantity_coverage_counts_the_held_stop():
    venue = _Venue(_OPEN_AFTER_BRACKET_FILL, [_FILLED_BRACKET])
    cov = venue.protection_coverage("QQQ", position={"qty": "54", "side": "long"})
    assert cov["stop_qty"] == 54.0 and cov["target_qty"] == 54.0
    assert cov["unknown_qty_legs"] == 0


def test_live_oco_shape_still_graded_from_the_open_read():
    """The live OCO (held stop nested under a working limit parent) was always
    visible; it must stay so, and no closed parent adds a duplicate."""
    venue = _Venue([_LIVE_QQQ_OCO_PARENT], [])
    st = venue.protection_state("QQQ")
    assert st["stop"] is True and st["target"] is True and st["legs"] == 2


def test_closed_parent_whose_legs_are_done_adds_nothing():
    """A bracket that already exited (legs filled/canceled) is not protection."""
    done = dict(_FILLED_BRACKET, legs=[
        dict(_TP_LEG, status="canceled"), dict(_SL_LEG, status="filled")])
    venue = _Venue([], [done])
    st = venue.protection_state("QQQ")
    assert st["stop"] is False and st["legs"] == 0


def test_pending_cancel_leg_is_not_counted_as_protection():
    leaving = dict(_FILLED_BRACKET, legs=[dict(_SL_LEG, status="pending_cancel")])
    venue = _Venue([], [leaving])
    assert venue.protection_state("QQQ")["stop"] is False


def test_canceled_parent_legs_are_not_read():
    """Only a FILLED (or partially filled) parent's children can rest."""
    cancelled = dict(_FILLED_BRACKET, status="canceled")
    venue = _Venue([], [cancelled])
    assert venue.protection_state("QQQ")["stop"] is False


def test_closed_read_failure_is_could_not_look_not_naked():
    """Collapsed-state rule: if the second read fails we did not look.
    `None` makes the sweep skip rather than re-arm (or report naked)."""
    venue = _Venue(_OPEN_AFTER_BRACKET_FILL, [], closed_rc=503)
    assert venue.protection_state("QQQ") is None
    assert venue.protection_coverage(
        "QQQ", position={"qty": "54", "side": "long"}) is None


def test_other_symbols_legs_are_filtered_out():
    other = dict(_FILLED_BRACKET, symbol="SPY",
                 legs=[dict(_SL_LEG, symbol="SPY")])
    venue = _Venue([], [other])
    assert venue.protection_state("QQQ")["stop"] is False


def test_cancel_pass_reaches_the_held_stop_directly():
    """The re-arm pre-cancel now names the held stop too, rather than relying
    on the venue's OCO cascade from cancelling the TP leg."""
    venue = _Venue(_OPEN_AFTER_BRACKET_FILL, [_FILLED_BRACKET])
    outcome = venue._cancel_open_orders_detailed("QQQ")
    deleted = sorted(p for m, p in venue.calls if m == "DELETE")
    assert deleted == [f"/v2/orders/{_TP_LEG['id']}", f"/v2/orders/{_SL_LEG['id']}"]
    assert outcome.accepted == 2
