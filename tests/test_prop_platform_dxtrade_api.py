"""dxtrade_api adapter (VELOTRADE-API-EXEC, 2026-10-05) against a mocked REST server.

Pins the spec behaviours the design memo depends on
(docs/research/velotrade-api-executor-design-2026-10-05.md § 2): the bracket is
ONE IF-THEN POST; a 409/100 duplicate client id is ALREADY PLACED; a 412 stale
ETag is re-read and retried ONCE; a 429 is backed off; dry builds and never
sends; flatten is a one-instrument Bulk Close; nothing secret is printed.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

import pytest

from src.prop.platform import API_PLATFORMS, adapter_for_platform, load_platform_config
from src.prop.platform.base import BracketSpec, FeasibilityError, Position
from src.prop.platform.dxtrade_api import (
    VENUE_SPECS, DXtradeApiAdapter, build_bracket_body, check_spec, client_ids, round_to_step)

BASE = "https://dx.example/dxsca-web"
ACCT = "default:SECRETACCT77"
TOKEN = "tok-SECRET-123"


class FakeServer:
    """Scripted responses keyed by (METHOD, path-without-query); each key holds
    a queue, the last entry repeats. Records every request."""

    def __init__(self) -> None:
        self.routes: Dict[Tuple[str, str], List[Tuple[int, Dict[str, str], Any]]] = {}
        self.requests: List[Dict[str, Any]] = []
        self.on(("POST", "/login"), 200, {"sessionToken": TOKEN, "timeout": "30:00"})
        self.on(("GET", "/users/trader%40default"), 200,
                {"userDetails": [{"login": "trader", "accounts": [{"account": ACCT, "positionBased": True}]}]})
        self.on(("POST", "/logout"), 200, {})

    def on(self, key: Tuple[str, str], status: int, body: Any, headers: Optional[Dict[str, str]] = None,
           *, then: bool = False) -> None:
        q = self.routes.setdefault(key, [])
        if not then:
            q.clear()
        q.append((status, headers or {}, body))

    def __call__(self, method, url, data, headers, timeout):
        path = url[len(BASE):].split("?", 1)[0]
        self.requests.append({"method": method, "path": path, "url": url,
                              "body": json.loads(data) if data else None, "headers": dict(headers)})
        q = self.routes.get((method, path))
        if not q:
            return 404, {}, json.dumps({"errorCode": 2, "description": "Entity not found at server"})
        status, hdrs, body = q.pop(0) if len(q) > 1 else q[0]
        return status, {k.lower(): v for k, v in hdrs.items()}, json.dumps(body)

    def sent(self, method: str, suffix: str) -> List[Dict[str, Any]]:
        return [r for r in self.requests if r["method"] == method and r["path"].endswith(suffix)]


A = "/accounts/default%3ASECRETACCT77"


def _adapter(server: FakeServer) -> Tuple[DXtradeApiAdapter, List[float]]:
    slept: List[float] = []
    a = DXtradeApiAdapter(transport=server, sleep=slept.append)
    a.login(None, BASE, "trader", "pw-SECRET")
    return a, slept


def _spec(**kw) -> BracketSpec:
    d = dict(ticket_id="T-1", venue_symbol="ETHUSD", side="long", quantity=0.01,
             stop_loss=2000.0, take_profit=3000.0, order_type="market")
    d.update(kw)
    return BracketSpec(**d)


def _open_orders(sl: float = 2000.0, tp: float = 3000.0) -> List[Dict[str, Any]]:
    def o(cid, typ, px):
        return {"clientOrderId": cid, "orderCode": "s:" + cid, "type": typ, "instrument": "ETHUSD",
                "status": "WORKING", "finalStatus": False, "side": "SELL",
                "legs": [{"positionEffect": "CLOSE", "positionCode": "555", "price": px}]}
    return [o("T-1-S", "STOP", sl), o("T-1-T", "LIMIT", tp)]


def _position() -> Position:
    return Position(symbol="ETHUSD", side="long", quantity=0.01, entry_price=2500.0,
                    raw={"positionCode": "555"})


# ── registry / config ──────────────────────────────────────────────────────


def test_registered_and_velotrade_1_uses_it():
    assert "dxtrade_api" in API_PLATFORMS
    assert isinstance(adapter_for_platform("dxtrade_api"), DXtradeApiAdapter)
    v = load_platform_config("velotrade_1")
    assert v["platform"] == "dxtrade_api"
    assert v["login_url"] == "https://dx.velotrade.com/dxsca-web"
    assert set(v["executor"]["enabled_venue_symbols"]) <= set(v["executor"]["lots"])   # only measured venues


def test_api_platform_needs_an_explicit_login_url(tmp_path):
    p = tmp_path / "pp.yaml"
    p.write_text("accounts:\n  x:\n    platform: dxtrade_api\n")
    with pytest.raises(ValueError, match="explicit login_url"):
        load_platform_config("x", p)


def test_config_lots_match_the_measured_specs():
    lots = load_platform_config("velotrade_1")["executor"]["lots"]
    for sym, vs in VENUE_SPECS.items():
        assert lots[sym]["min_lots"] == vs["min_qty"] and lots[sym]["lot_step"] == vs["qty_step"]
        assert lots[sym]["price_step"] == vs["price_step"] and lots[sym]["lot_units"] == 1


# ── login ──────────────────────────────────────────────────────────────────


def test_login_finds_the_one_account():
    s = FakeServer()
    a, _ = _adapter(s)
    assert a.account == ACCT and a.token == TOKEN
    assert s.requests[0]["body"] == {"username": "trader", "domain": "default", "password": "pw-SECRET"}
    assert s.sent("GET", "/users/trader%40default")[0]["headers"]["Authorization"] == f"DXAPI {TOKEN}"


def test_login_refuses_two_accounts():
    s = FakeServer()
    s.on(("GET", "/users/trader%40default"), 200, {"accounts": [{"account": "a:1"}, {"account": "a:2"}]})
    with pytest.raises(FeasibilityError) as e:
        _adapter(s)
    assert e.value.reason == "ambiguous_account"


def test_login_rejected_is_a_feasibility_finding():
    s = FakeServer()
    s.on(("POST", "/login"), 401, {"errorCode": 1, "description": "bad pw-SECRET"})
    with pytest.raises(FeasibilityError) as e:
        _adapter(s)
    assert e.value.reason == "login_rejected" and "pw-SECRET" not in e.value.detail


# ── bracket ────────────────────────────────────────────────────────────────


def test_bracket_body_is_one_if_then_group():
    body = build_bracket_body(_spec())
    assert body["contingencyType"] == "IF-THEN"
    parent, sl, tp = body["orders"]
    assert parent == {"orderCode": "T-1-E", "type": "MARKET", "instrument": "ETHUSD", "quantity": "0.01",
                      "positionEffect": "OPEN", "side": "BUY", "tif": "GTC"}
    for child in (sl, tp):
        assert child["positionEffect"] == "CLOSE" and child["side"] == "SELL" and child["tif"] == "GTC"
        assert "quantity" not in child          # "position attached"
    assert (sl["type"], sl["stopPrice"], tp["type"], tp["limitPrice"]) == ("STOP", "2000", "LIMIT", "3000")


def test_short_limit_bracket_sides_and_price_rounding():
    body = build_bracket_body(_spec(side="short", order_type="limit", limit_price=2500.004,
                                    stop_loss=2600.006, take_profit=2400.0))
    parent, sl, tp = body["orders"]
    assert parent["side"] == "SELL" and parent["type"] == "LIMIT" and parent["limitPrice"] == "2500"
    assert sl["side"] == tp["side"] == "BUY" and sl["stopPrice"] == "2600.01"


def test_dry_place_sends_nothing():
    s = FakeServer()
    a, _ = _adapter(s)
    n = len(s.requests)
    att = a.place_bracket(None, _spec())
    assert att.stage == "form_verified" and not att.submitted
    assert att.form["request"]["contingencyType"] == "IF-THEN"
    assert len(s.requests) == n


def test_armed_place_is_exactly_one_post():
    s = FakeServer()
    s.on(("POST", f"{A}/orders"), 200, {"orderResponses": [{"orderId": 1, "updateOrderId": 1}] * 3})
    a, _ = _adapter(s)
    att = a.place_bracket(None, _spec(), arm=True)
    assert att.submitted and att.detail == "http 200"
    posts = s.sent("POST", "/orders")
    assert len(posts) == 1 and len(posts[0]["body"]["orders"]) == 3


def test_duplicate_client_id_is_already_placed_not_replaced():
    s = FakeServer()
    s.on(("POST", f"{A}/orders"), 409, {"errorCode": 100, "description": "Order with this id already exists (T-1-E)"})
    a, _ = _adapter(s)
    att = a.place_bracket(None, _spec(), arm=True)
    assert att.submitted and att.detail.startswith("already_placed")
    assert len(s.sent("POST", "/orders")) == 1     # never a second attempt


def test_pre_issue_rejection_is_refused_not_submitted():
    s = FakeServer()
    s.on(("POST", f"{A}/orders"), 409, {"errorCode": 31, "description": "Not enough margin"})
    a, _ = _adapter(s)
    att = a.place_bracket(None, _spec(), arm=True)
    assert att.stage == "refused" and not att.submitted


def test_unknown_outcome_reads_as_submitted_so_the_caller_rereads():
    s = FakeServer()
    s.on(("POST", f"{A}/orders"), 502, {"description": "gateway"})
    a, _ = _adapter(s)
    att = a.place_bracket(None, _spec(), arm=True)
    assert att.submitted and att.detail.startswith("outcome_unknown")


def test_same_ticket_same_client_ids():
    assert client_ids("T-1") == client_ids("T-1")
    assert all(len(v) <= 64 for v in client_ids("x" * 200).values())


@pytest.mark.parametrize("kw, frag", [
    (dict(venue_symbol="ADAUSD"), "no measured venue spec"),
    (dict(venue_symbol="SOLUSD", quantity=0.01), "below the venue minimum"),
    (dict(venue_symbol="XRPUSD", quantity=15), "not a multiple"),
    (dict(stop_loss=3100.0), "stop_loss must be below"),
    (dict(take_profit=0.0), "both required"),
])
def test_check_spec_refusals(kw, frag):
    assert any(frag in b for b in check_spec(_spec(**kw)))


def test_round_to_step():
    assert round_to_step(2000.004, 0.01) == 2000.0
    assert round_to_step(1.234567, 0.00001) == 1.23457


# ── rate limit ─────────────────────────────────────────────────────────────


def test_429_is_backed_off_then_succeeds():
    s = FakeServer()
    s.on(("POST", f"{A}/orders"), 429, {"description": "Too Many Requests"})
    s.on(("POST", f"{A}/orders"), 429, {}, then=True)
    s.on(("POST", f"{A}/orders"), 200, {"orderResponses": []}, then=True)
    a, slept = _adapter(s)
    att = a.place_bracket(None, _spec(), arm=True)
    assert att.detail == "http 200" and slept == [1.0, 2.0]
    bodies = [r["body"]["orders"][0]["orderCode"] for r in s.sent("POST", "/orders")]
    assert bodies == ["T-1-E"] * 3      # same client id each time: a repeat can only 409/100


def test_429_gives_up_after_three_retries():
    s = FakeServer()
    s.on(("GET", f"{A}/metrics"), 429, {})
    a, slept = _adapter(s)
    snap = a.read_account()
    assert snap.balance is None and "balance" in snap.unparsed and slept == [1.0, 2.0, 4.0]


# ── modify (ETag) ──────────────────────────────────────────────────────────


def test_modify_sends_if_match_from_the_open_orders_read():
    s = FakeServer()
    s.on(("GET", f"{A}/orders"), 200, _open_orders(), {"ETag": "v1"})
    s.on(("PUT", f"{A}/orders"), 200, {"orderId": 2, "updateOrderId": 3}, {"ETag": "v2"})
    a, _ = _adapter(s)
    r = a.modify_bracket(None, _position(), 2000.01, None, arm=True)
    assert r["ok"] and r["clicked"] and r["legs"][0]["etag_changed"]
    put = s.sent("PUT", "/orders")[0]
    assert put["headers"]["If-Match"] == "v1"
    assert put["body"] == {"orderCode": "T-1-S", "instrument": "ETHUSD", "positionEffect": "CLOSE",
                           "positionCode": "555", "side": "SELL", "stopPrice": "2000.01", "tif": "GTC"}


def test_stale_etag_412_rereads_and_retries_once():
    s = FakeServer()
    s.on(("GET", f"{A}/orders"), 200, _open_orders(), {"ETag": "v1"})
    s.on(("GET", f"{A}/orders"), 200, _open_orders(), {"ETag": "v2"}, then=True)
    s.on(("PUT", f"{A}/orders"), 412, None)
    s.on(("PUT", f"{A}/orders"), 200, {"orderId": 2, "updateOrderId": 3}, {"ETag": "v3"}, then=True)
    a, _ = _adapter(s)
    r = a.modify_bracket(None, _position(), 2000.01, None, arm=True)
    puts = s.sent("PUT", "/orders")
    assert r["ok"] and r["legs"][0]["attempts"] == 2
    assert [p["headers"]["If-Match"] for p in puts] == ["v1", "v2"]


def test_second_412_is_a_failure_not_a_loop():
    s = FakeServer()
    s.on(("GET", f"{A}/orders"), 200, _open_orders(), {"ETag": "v1"})
    s.on(("PUT", f"{A}/orders"), 412, None)
    a, _ = _adapter(s)
    r = a.modify_bracket(None, _position(), 2000.01, None, arm=True)
    assert not r["ok"] and r["clicked"] and len(s.sent("PUT", "/orders")) == 2


def test_dry_modify_sends_no_put():
    s = FakeServer()
    s.on(("GET", f"{A}/orders"), 200, _open_orders(), {"ETag": "v1"})
    a, _ = _adapter(s)
    r = a.modify_bracket(None, _position(), 2000.01, 2999.99)
    assert r["ok"] and not r["clicked"] and len(r["would_send"]) == 2 and not s.sent("PUT", "/orders")


def test_modify_without_a_protection_order_refuses():
    s = FakeServer()
    s.on(("GET", f"{A}/orders"), 200, [], {"ETag": "v1"})
    a, _ = _adapter(s)
    r = a.modify_bracket(None, _position(), 2000.01, None, arm=True)
    assert not r["ok"] and not r["clicked"]


# ── flatten / cancel ───────────────────────────────────────────────────────


def test_flatten_is_one_instrument_bulk_close():
    s = FakeServer()
    s.on(("POST", f"{A}/close"), 200, {})
    a, _ = _adapter(s)
    assert not a.flatten(None, None, arm=True)["ok"]          # never close-all
    r = a.flatten(None, "ethusd", arm=True)
    assert r["ok"] and r["clicked"]
    assert s.sent("POST", "/close")[0]["body"] == {"instrument": "ETHUSD", "closePositions": True,
                                                   "cancelOrders": True, "comment": "metis flatten"}


def test_flatten_non_200_is_reported_not_ok():
    s = FakeServer()
    s.on(("POST", f"{A}/close"), 500, {"errorCode": 110, "description": "bulk close failed"})
    a, _ = _adapter(s)
    r = a.flatten(None, "ETHUSD", arm=True)
    assert not r["ok"] and r["clicked"]


def test_flatten_checks_the_stated_position_first():
    s = FakeServer()
    s.on(("GET", f"{A}/portfolio"), 200, {"portfolios": [{"positions": [
        {"symbol": "ETHUSD", "side": "SELL", "quantity": 0.01, "openPrice": 2500, "positionCode": "9"}]}]})
    a, _ = _adapter(s)
    r = a.flatten(None, "ETHUSD", arm=True, side="long", quantity=0.01)
    assert not r["ok"] and "side mismatch" in r["why"] and not s.sent("POST", "/close")


def test_dry_flatten_and_cancel_send_nothing():
    s = FakeServer()
    a, _ = _adapter(s)
    n = len(s.requests)
    assert not a.flatten(None, "ETHUSD")["clicked"]
    from src.prop.platform.base import WorkingOrder
    assert not a.cancel_order(None, WorkingOrder(symbol="ETHUSD", order_id="T-1-S"))["clicked"]
    assert len(s.requests) == n


def test_cancel_retries_once_on_412():
    s = FakeServer()
    s.on(("GET", f"{A}/orders"), 200, _open_orders(), {"ETag": "v1"})
    s.on(("DELETE", f"{A}/orders/T-1-S"), 412, None)
    s.on(("DELETE", f"{A}/orders/T-1-S"), 200, {"orderId": 1, "updateOrderId": 2}, then=True)
    a, _ = _adapter(s)
    from src.prop.platform.base import WorkingOrder
    r = a.cancel_order(None, WorkingOrder(symbol="ETHUSD", order_id="T-1-S"), arm=True)
    assert r["ok"] and len(s.sent("DELETE", "/orders/T-1-S")) == 2


# ── reads ──────────────────────────────────────────────────────────────────


def test_reads_parse_the_spec_shapes():
    s = FakeServer()
    s.on(("GET", f"{A}/metrics"), 200, {"metrics": [{"balance": 5000, "equity": 4999.5, "openPl": -0.5,
                                                      "margin": 4.2, "availableFunds": 4995}]})
    s.on(("GET", f"{A}/portfolio"), 200, {"portfolios": [{"positions": [
        {"symbol": "ETHUSD", "side": "BUY", "quantity": 0.01, "openPrice": 2500.5, "positionCode": "555",
         "stopLossPrice": 2000, "takeProfitPrice": 3000}]}]})
    s.on(("GET", f"{A}/orders"), 200, _open_orders(), {"ETag": "v1"})
    s.on(("POST", "/marketdata"), 200, {"events": [{"type": "Quote", "symbol": "ETHUSD", "bid": 2500.1, "ask": 2500.4}]})
    a, _ = _adapter(s)
    snap = a.read_account()
    assert (snap.balance, snap.equity, snap.unrealized) == (5000.0, 4999.5, -0.5)
    assert "realized_today" in snap.unparsed              # "could not look", not 0
    (p,) = a.read_positions()
    assert (p.side, p.quantity, p.entry_price, p.stop_loss, p.take_profit) == ("long", 0.01, 2500.5, 2000.0, 3000.0)
    orders = a.read_orders()
    assert [o.order_id for o in orders] == ["T-1-S", "T-1-T"] and a.orders_etag == "v1"
    assert a.read_quote(None, "ETHUSD") == {"bid": 2500.1, "ask": 2500.4}


# ── secrets ────────────────────────────────────────────────────────────────


def test_call_log_and_redaction_carry_no_secret():
    s = FakeServer()
    s.on(("GET", f"{A}/metrics"), 200, {"metrics": [{"balance": 1, "equity": 1}]})
    a, _ = _adapter(s)
    a.read_account()
    blob = json.dumps(a.calls)
    assert "SECRETACCT77" not in blob and "{a}" in blob
    text = a.redact(f"{ACCT} {TOKEN} pw-SECRET trader")
    for secret in ("SECRETACCT77", TOKEN, "pw-SECRET"):
        assert secret not in text


# ── the REST feed + tick (VELOTRADE-GOLIVE): no browser on a REST platform ─


def _rest_adapter_factory(server):
    real = load_platform_config("velotrade_1")["login_url"].rstrip("/")

    def transport(method, url, data, headers, timeout):
        return server(method, url.replace(real, BASE), data, headers, timeout)

    return lambda platform: DXtradeApiAdapter(transport=transport, sleep=lambda _: None)


def _flat(server):
    server.on(("GET", f"{A}/metrics"), 200, {"metrics": [{"balance": 5000, "equity": 5000, "openPl": 0}]})
    server.on(("GET", f"{A}/portfolio"), 200, {"portfolios": [{"positions": []}]})
    server.on(("GET", f"{A}/orders"), 200, [], {"ETag": "v1"})
    return server


def test_feed_reads_and_emits_status_over_rest(monkeypatch, capsys):
    from scripts.prop import breakout_login_check as blc
    s = _flat(FakeServer())
    monkeypatch.setenv("VELOTRADE_DX_USERNAME", "trader")
    monkeypatch.setenv("VELOTRADE_DX_PASSWORD", "pw-SECRET")
    monkeypatch.setattr(blc, "adapter_for_platform", _rest_adapter_factory(s))
    posted = []
    monkeypatch.setattr(blc, "post_status", lambda report, base: posted.append(report) or {"id": 1})
    assert blc.main(["--account", "velotrade_1", "--emit-status"]) == blc.EXIT_OK
    out = capsys.readouterr().out
    assert "login: ok (rest)" in out and "positions: 0" in out and "emit_status: ok id=1" in out
    assert len(posted) == 1 and posted[0]["balance"] == 5000.0
    assert "pw-SECRET" not in out and "SECRETACCT77" not in out
    assert s.sent("POST", "/logout")


def test_tick_runs_a_read_only_cycle_over_rest_without_a_browser(monkeypatch, capsys, tmp_path):
    from scripts.prop import prop_executor_tick as tick
    s = _flat(FakeServer())
    monkeypatch.setenv("VELOTRADE_DX_USERNAME", "trader")
    monkeypatch.setenv("VELOTRADE_DX_PASSWORD", "pw-SECRET")
    monkeypatch.setattr(tick, "adapter_for_platform", _rest_adapter_factory(s))
    monkeypatch.setattr(tick.pe, "pending_live_tickets", lambda *a, **k: [])
    calls = []
    monkeypatch.setattr(tick, "run_cycle_and_trail", lambda **kw: calls.append(kw) or tick.EXIT_OK)
    rc = tick.main(["--account", "velotrade_1", "--login", "reuse", "--state-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == tick.EXIT_OK and '"session": "rest_login"' in out
    assert len(calls) == 1 and calls[0]["page"] is None and calls[0]["mode"] == "read_only"
    assert not s.sent("POST", "/orders") and s.sent("POST", "/logout")


def test_rest_tick_refuses_browser_measurement_modes(monkeypatch, capsys, tmp_path):
    from scripts.prop import prop_executor_tick as tick
    monkeypatch.setenv("VELOTRADE_DX_USERNAME", "u")
    monkeypatch.setenv("VELOTRADE_DX_PASSWORD", "p")
    monkeypatch.setattr(tick, "adapter_for_platform",
                        lambda p: (_ for _ in ()).throw(AssertionError("no adapter for a refused mode")))
    rc = tick.main(["--account", "velotrade_1", "--login", "fresh", "--probe-ticket", "ETHUSD",
                    "--state-dir", str(tmp_path)])
    assert rc == tick.EXIT_FEASIBILITY and '"feasibility": "api_platform"' in capsys.readouterr().out


# ── read_trade_history (VELOTRADE-RECON) ────────────────────────────────
# Fixture follows the REST spec's Order model (orders[].legs[], executions[]
# with lastPrice / lastQuantity). Re-record from the live read (system-action
# velotrade-trade-history-read) if a field name differs.
HISTORY = {"orders": [
    {"clientOrderId": "vtrt-E", "orderCode": "s:1", "type": "MARKET", "instrument": "ETHUSD", "side": "BUY",
     "status": "COMPLETED", "finalStatus": "COMPLETED", "issueTime": "2026-10-06T10:18:01Z",
     "legs": [{"positionEffect": "OPEN", "filledQuantity": 0.01, "averagePrice": 2450.5}],
     "executions": [{"lastPrice": "2450.5", "lastQuantity": "0.01", "executionTime": "2026-10-06T10:18:02.250Z"}]},
    {"clientOrderId": "x-Bulk", "orderCode": "s:2", "type": "MARKET", "instrument": "ETHUSD", "side": "SELL",
     "status": "COMPLETED", "finalStatus": "COMPLETED", "issueTime": "2026-10-06T10:18:30Z",
     "legs": [{"positionEffect": "CLOSE", "filledQuantity": 0.01, "averagePrice": 2449.9}],
     "executions": [{"lastPrice": 2449.9, "lastQuantity": 0.01, "commission": 0.0147,
                     "executionTime": "2026-10-06T10:18:31+00:00"}]},
    {"clientOrderId": "tp-never", "type": "LIMIT", "instrument": "ETHUSD", "side": "SELL", "status": "CANCELED",
     "finalStatus": "CANCELED", "legs": [{"positionEffect": "CLOSE"}], "executions": []},
]}


def test_read_trade_history_returns_the_browser_row_shape_read_only():
    s = FakeServer()
    s.on(("GET", f"{A}/orders/history"), 200, HISTORY)
    a, _ = _adapter(s)
    rows = a.read_trade_history(None)
    assert [r["effect"] for r in rows] == ["opening", "closing"]      # canceled TP has no execution
    close = rows[1]
    assert (close["symbol"], close["side"], close["volume"], close["price"]) == ("ETHUSD", "short", 0.01, 2449.9)
    assert close["ts"].isoformat() == "2026-10-06T10:18:31+00:00" and close["commission"] == 0.0147
    assert close["net_closed_pnl"] is None and close["closed_pnl"] is None   # never fabricated
    assert rows[0]["side"] == "long" and rows[0]["ts"].minute == 18
    assert [r["method"] for r in s.requests if "history" in r["path"]] == ["GET"]
    assert "SECRETACCT77" not in json.dumps(rows, default=str)


def test_read_trade_history_feeds_match_exit_and_exit_reason():
    from datetime import datetime, timezone
    from src.prop.prop_executor import exit_reason, match_exit
    s = FakeServer()
    s.on(("GET", f"{A}/orders/history"), 200, HISTORY)
    a, _ = _adapter(s)
    j = {"symbol": "ETHUSDT", "direction": "long", "qty": 0.01, "opened_at": "2026-10-06T10:18:05+00:00",
         "sl": 1960.0, "tp": 2940.0}
    hit, why = match_exit(j, a.read_trade_history(None), datetime(2026, 10, 6, 10, 30, tzinfo=timezone.utc))
    assert why == "matched" and hit["price"] == 2449.9
    assert exit_reason("long", hit["price"], 1960.0, 2940.0) == "manual"   # a Bulk Close, between SL and TP
    assert exit_reason("long", 1959.0, 1960.0, 2940.0) == "sl" and exit_reason("long", 2941.0, 1960.0, 2940.0) == "tp"


def test_read_trade_history_non_200_raises_and_empty_is_empty():
    s = FakeServer()
    s.on(("GET", f"{A}/orders/history"), 500, {"errorCode": 1})
    a, _ = _adapter(s)
    with pytest.raises(RuntimeError):
        a.read_trade_history(None)
    s.on(("GET", f"{A}/orders/history"), 200, {"orders": []})
    assert a.read_trade_history(None) == []


def test_unparseable_execution_is_skipped_not_defaulted():
    from src.prop.platform.dxtrade_api import trade_history_rows
    rows = trade_history_rows({"orders": [{"instrument": "ETHUSD", "side": "SELL", "legs": [{"positionEffect": "CLOSE"}],
                                           "executions": [{"lastPrice": "n/a", "lastQuantity": 1},
                                                          {"lastPrice": 5, "lastQuantity": 0}]}]})
    assert rows == []
