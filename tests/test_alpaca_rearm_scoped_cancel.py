"""FIX-SA-03 step 3 (PI-20260929-PR6YRTQY-0001): a re-arm cancels only its OWN legs.

`AlpacaClient.place_protective` used to DELETE every order on the symbol, then
place an OCO sized to ONE journal row. Alpaca nets per symbol, so on a symbol
more than one row holds, the sibling's protection was stripped and not put back.

Payload: `_LIVE_SPY` is alpaca_paper/SPY as /api/diag/alpaca_open_orders
returned it at 2026-09-29T11:09:45Z (19-share long, two OCO groups 8 + 11),
rendered back into Alpaca's raw order shape (`type`, string `qty`, the held stop
nested under its working limit parent). Ids, sides, qtys, prices and statuses
are the captured values.
"""
from __future__ import annotations

from src.units.accounts.alpaca_client import AlpacaClient

_OCO8_STOP = {"id": "9fe49151-6667-4c1f-99ec-84c49c280099", "symbol": "SPY",
              "order_class": "oco", "type": "stop", "side": "sell", "qty": "8",
              "stop_price": "763.69", "limit_price": None,
              "time_in_force": "gtc", "status": "held", "legs": None}
_OCO8 = {"id": "e21f6ca9-3aa3-4cec-abd6-e105df003b35", "symbol": "SPY",
         "order_class": "oco", "type": "limit", "side": "sell", "qty": "8",
         "stop_price": None, "limit_price": "840.65", "time_in_force": "gtc",
         "status": "new", "legs": [_OCO8_STOP]}
_OCO11_STOP = {"id": "8b4956ce-1782-47cc-a6f0-379bddbe4aba", "symbol": "SPY",
               "order_class": "oco", "type": "stop", "side": "sell", "qty": "11",
               "stop_price": "744.48", "limit_price": None,
               "time_in_force": "gtc", "status": "held", "legs": None}
_OCO11 = {"id": "6682fcfd-ed19-41ab-984e-674189d2f8d1", "symbol": "SPY",
          "order_class": "oco", "type": "limit", "side": "sell", "qty": "11",
          "stop_price": None, "limit_price": "830.43", "time_in_force": "gtc",
          "status": "new", "legs": [_OCO11_STOP]}
_LIVE_SPY = [_OCO8, _OCO11]


class _Venue(AlpacaClient):
    def __init__(self, open_rows, post_results=None):
        self.api_key, self.api_secret = "k", "s"
        self.open_rows = open_rows
        self.post_results = list(post_results or [{"retCode": 0, "result": {"id": "oco-new"}}])
        self.calls: list = []

    def _request(self, method, path, json_body=None):  # type: ignore[override]
        self.calls.append((method, path, json_body))
        if method == "GET" and "status=open" in path:
            return {"retCode": 0, "result": self.open_rows}
        if method == "GET" and "status=closed" in path:
            return {"retCode": 0, "result": []}
        if method == "DELETE":
            return {"retCode": 0, "result": {}}
        if method == "POST":
            return self.post_results.pop(0) if self.post_results else {"retCode": 0, "result": {"id": "x"}}
        return {"retCode": 500, "retMsg": "unrouted"}

    def deletes(self):
        return sorted(p.rsplit("/", 1)[1] for m, p, _ in self.calls if m == "DELETE")


def _rearm(venue, qty, direction="long"):
    return venue.place_protective(
        {"symbol": "SPY", "direction": direction, "qty": qty, "sl": 760.0, "tp": 845.0})


def test_rearming_the_8_share_row_leaves_the_11_share_sibling_intact():
    v = _Venue(_LIVE_SPY)
    assert _rearm(v, 8)["retCode"] == 0
    assert v.deletes() == sorted([_OCO8["id"], _OCO8_STOP["id"]])
    assert _OCO11["id"] not in v.deletes() and _OCO11_STOP["id"] not in v.deletes()


def test_rearming_the_11_share_row_leaves_the_8_share_sibling_intact():
    v = _Venue(_LIVE_SPY)
    assert _rearm(v, 11)["retCode"] == 0
    assert v.deletes() == sorted([_OCO11["id"], _OCO11_STOP["id"]])


def test_opening_side_entry_order_is_never_cancelled():
    entry = {"id": "11111111-aaaa-4bbb-8ccc-000000000001", "symbol": "SPY",
             "order_class": "simple", "type": "limit", "side": "buy",
             "qty": "8", "limit_price": "740.00", "status": "new", "legs": None}
    v = _Venue([entry])
    assert _rearm(v, 8)["retCode"] == 0
    assert v.deletes() == []


def test_venue_refusal_releases_sibling_targets_but_never_a_sibling_stop():
    """Stop-naked position, a sibling TARGET-only leg reserving shares, venue
    refuses the OCO -> release the sibling target (not any stop) and retry once."""
    sibling_tp = {"id": "22222222-aaaa-4bbb-8ccc-000000000002", "symbol": "SPY",
                  "order_class": "simple", "type": "limit", "side": "sell",
                  "qty": "11", "limit_price": "830.43", "status": "new", "legs": None}
    v = _Venue([sibling_tp], post_results=[
        {"retCode": 403, "retMsg": "insufficient qty available for order (requested: 8, available: 8)"},
        {"retCode": 0, "result": {"id": "oco-new"}},
    ])
    r = _rearm(v, 19)
    assert r["retCode"] == 0 and r["result"]["orderId"] == "oco-new"
    assert v.deletes() == [sibling_tp["id"]]
    assert sum(1 for m, *_ in v.calls if m == "POST") == 2


def test_refusal_with_only_a_sibling_stop_resting_is_reported_not_stripped():
    v = _Venue([_OCO11], post_results=[{"retCode": 403, "retMsg": "insufficient qty"}])
    r = _rearm(v, 8)
    assert r["retCode"] == 403
    # The 11-share OCO's limit is a TARGET leg in a group WITH a stop; Alpaca
    # cancels the group together, so releasing it would strip the stop.
    assert _OCO11_STOP["id"] not in v.deletes()
    assert _OCO11["id"] not in v.deletes()


def test_open_orders_read_failure_cancels_nothing_but_still_arms():
    class _Down(_Venue):
        def _request(self, method, path, json_body=None):  # type: ignore[override]
            if method == "GET":
                self.calls.append((method, path, json_body))
                return {"retCode": 503, "retMsg": "down"}
            return super()._request(method, path, json_body)
    v = _Down(_LIVE_SPY)
    assert _rearm(v, 8)["retCode"] == 0
    assert v.deletes() == []
