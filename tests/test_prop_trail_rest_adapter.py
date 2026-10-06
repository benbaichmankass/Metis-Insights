"""PROP-TRAIL over the dxtrade_api REST adapter (VELOTRADE-GOLIVE): the trail step
the REST tick runs with ``page=None`` must really amend the SL through
``DXtradeApiAdapter.modify_bracket`` (a conditional PUT), confirm it on re-read,
and leave the DOM-only rollout latch clear (the REST modify never records it)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.prop import prop_trail as pt
from src.prop.platform.dxtrade_api import DXtradeApiAdapter
from tests.test_prop_platform_dxtrade_api import A, BASE, FakeServer
from tests.test_prop_trail import SOL, STEP_SL, Api, bars, cfg, now_after


def _order(cid, typ, px):
    return {"clientOrderId": cid, "orderCode": cid, "type": typ, "instrument": "SOLUSD",
            "status": "WORKING", "finalStatus": False, "side": "SELL",
            "legs": [{"positionEffect": "CLOSE", "positionCode": "555", "price": px}]}


class VenueServer(FakeServer):
    """A one-position SOLUSD venue whose SL moves when a PUT succeeds."""

    def __init__(self):
        super().__init__()
        self.sl = 95.0

    def __call__(self, method, url, data, headers, timeout):
        path = url[len(BASE):].split("?", 1)[0]
        if method == "GET" and path == f"{A}/portfolio":
            body = {"portfolios": [{"positions": [{
                "symbol": "SOLUSD", "side": "BUY", "quantity": 1, "openPrice": 100, "positionCode": "555",
                "stopLossPrice": self.sl, "takeProfitPrice": 130}]}]}
            self.requests.append({"method": method, "path": path, "body": None, "headers": dict(headers), "url": url})
            return 200, {}, json.dumps(body)
        if method == "GET" and path == f"{A}/orders":
            self.requests.append({"method": method, "path": path, "body": None, "headers": dict(headers), "url": url})
            return 200, {"etag": "v1"}, json.dumps([_order("t1-S", "STOP", self.sl), _order("t1-T", "LIMIT", 130)])
        if method == "POST" and path == "/marketdata":
            self.requests.append({"method": method, "path": path, "body": None, "headers": dict(headers), "url": url})
            return 200, {}, json.dumps({"events": [{"symbol": "SOLUSD", "bid": 109.0, "ask": 109.02}]})
        if method == "PUT" and path == f"{A}/orders":
            body = json.loads(data)
            self.requests.append({"method": method, "path": path, "body": body, "headers": dict(headers), "url": url})
            self.sl = float(body["stopPrice"])
            return 200, {"etag": "v2"}, json.dumps({"orderId": 2, "updateOrderId": 3})
        return super().__call__(method, url, data, headers, timeout)


def run(adapter, api, mode, tmp_path):
    c = bars([(110, 99, 109)], forming=(109, 108, 108.5))
    return pt.run_trail_step(adapter=adapter, page=None, api=api, cfg=cfg(), mode=mode,
                             state_dir=tmp_path, candles_fn=lambda s, tf: c, now=now_after(1),
                             legs={"trend_donchian_sol_prop": SOL})


def _adapter(server):
    a = DXtradeApiAdapter(transport=server, sleep=lambda _: None)
    a.login(None, BASE, "trader", "pw-SECRET")
    return a


def test_live_trail_amends_the_sl_over_rest_and_confirms(tmp_path):
    s, api = VenueServer(), Api()
    res = run(_adapter(s), api, "live", tmp_path)
    puts = s.sent("PUT", "/orders")
    assert len(puts) == 1 and puts[0]["headers"]["If-Match"] == "v1"
    assert puts[0]["body"]["orderCode"] == "t1-S" and float(puts[0]["body"]["stopPrice"]) == pytest.approx(STEP_SL)
    assert s.sl == pytest.approx(STEP_SL)                       # the venue SL really moved
    assert api.posted and api.posted[0]["kind"] == "amend" and api.posted[0]["sl"] == pytest.approx(STEP_SL)
    assert not res.alerts


def test_rollout_latch_stays_clear_so_the_trail_keeps_stepping(tmp_path):
    s, api = VenueServer(), Api()
    a = _adapter(s)
    for _ in range(6):
        run(a, api, "live", tmp_path)
    assert pt.ModifyRollout(Path(tmp_path) / pt.ROLLOUT_FILE).blocked() is None
    assert s.sl == pytest.approx(103.0)                         # walked bounded steps to the replay target
    n = len(s.sent("PUT", "/orders"))
    run(a, api, "live", tmp_path)
    assert len(s.sent("PUT", "/orders")) == n                   # at target: no further amend


def test_read_only_trail_sends_no_put(tmp_path):
    s, api = VenueServer(), Api()
    run(_adapter(s), api, "read_only", tmp_path)
    assert not s.sent("PUT", "/orders") and api.posted == [] and s.sl == 95.0
