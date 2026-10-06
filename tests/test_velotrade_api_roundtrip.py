"""velotrade-api-roundtrip (VELOTRADE-API-EXEC): dry sends nothing; a non-200
place trips the stop rule -> Bulk Close; the one-shot latch refuses a rerun."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from scripts.prop import velotrade_api_roundtrip as rt
from tests.test_prop_platform_dxtrade_api import A, FakeServer, _adapter

NOW = datetime(2026, 10, 5, 18, 0, tzinfo=timezone.utc)


def _flat_server() -> FakeServer:
    s = FakeServer()
    s.on(("GET", f"{A}/metrics"), 200, {"metrics": [{"balance": 5000, "equity": 5000}]})
    s.on(("GET", f"{A}/portfolio"), 200, {"portfolios": [{"positions": []}]})
    s.on(("GET", f"{A}/orders"), 200, [], {"ETag": "v1"})
    s.on(("POST", "/marketdata"), 200, {"events": [{"symbol": "ETHUSD", "bid": 2500.0, "ask": 2500.5}]})
    return s


def test_dry_run_sends_no_order(tmp_path):
    s = _flat_server()
    a, _ = _adapter(s)
    out = []
    rc = rt.round_trip(a, live=False, latch=tmp_path / "l", out=out.append, sleep=lambda _: None, now=NOW)
    assert rc == rt.EXIT_OK and not s.sent("POST", "/orders") and not s.sent("POST", "/close")
    body = [json.loads(x) for x in out if '"dry_body"' in x][0]["request"]
    assert body["contingencyType"] == "IF-THEN" and body["orders"][0]["quantity"] == "0.01"
    assert not (tmp_path / "l").exists()


def test_non_200_place_flattens_and_stops(tmp_path):
    s = _flat_server()
    s.on(("POST", f"{A}/orders"), 409, {"errorCode": 31, "description": "rejected"})
    s.on(("POST", f"{A}/close"), 200, {})
    a, _ = _adapter(s)
    rc = rt.round_trip(a, live=True, latch=tmp_path / "l", out=lambda _: None, sleep=lambda _: None, now=NOW)
    assert rc == rt.EXIT_STOPPED_FLAT
    assert len(s.sent("POST", "/orders")) == 1 and len(s.sent("POST", "/close")) == 1
    assert (tmp_path / "l").exists()


def test_latch_refuses_a_second_live_run(tmp_path):
    (tmp_path / "l").write_text("{}")
    s = _flat_server()
    a, _ = _adapter(s)
    rc = rt.round_trip(a, live=True, latch=tmp_path / "l", out=lambda _: None, sleep=lambda _: None, now=NOW)
    assert rc == rt.EXIT_REFUSED and not s.sent("POST", "/orders")


def test_not_flat_account_is_refused(tmp_path):
    s = _flat_server()
    s.on(("GET", f"{A}/portfolio"), 200, {"portfolios": [{"positions": [
        {"symbol": "ETHUSD", "side": "BUY", "quantity": 0.01, "openPrice": 1, "positionCode": "1"}]}]})
    a, _ = _adapter(s)
    rc = rt.round_trip(a, live=True, latch=tmp_path / "l", out=lambda _: None, sleep=lambda _: None, now=NOW)
    assert rc == rt.EXIT_REFUSED and not s.sent("POST", "/orders")
