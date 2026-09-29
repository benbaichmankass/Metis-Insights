"""GET /api/diag/alpaca_order_history + ``account_alpaca_order_history``.

FIX-SA-03 step 1 (E75 audit, SA-AUD-1-alpaca-order-history-unreadable). The
naked sweep re-armed protection 6-7 min after 17 of 20 Alpaca entries and no
surface could say whether the entry bracket's stop leg was resting in between.
This route reads Alpaca's order HISTORY so it can be.

Under test:

1. **Three-state** -- ``not_alpaca`` / ``could_not_look`` (``result: null``) /
   ``orders_read``; ``order_count`` is ``null`` when the list was not read.
2. **Legs and their lifecycle survive** -- a bracket parent's ``held`` stop
   child, its ``canceled_at`` and ``replaced_by`` are returned unreduced; they
   ARE the answer to "did the stop rest".
3. **Per-id lookups fail independently** -- a 404 is ``not_found``, a broker
   error is ``could_not_look``; neither blinds the other ids.
4. **Arguments are vetted** -- a malformed id/symbol/timestamp is a 400 and
   never reaches the broker URL.

The payload below is the shape Alpaca returns for a FILLED bracket entry with
``nested=true``: the parent is ``filled`` and its two children sit under
``legs`` -- take-profit ``new`` (working), stop-loss ``held`` (OCO sibling).
Field names/values follow the live ``/v2/orders`` payload the trader already
consumes (see ``tests/test_alpaca_wiring.py``'s byte-exact open-orders sample).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.units.accounts import clients as accounts_clients
from src.web.api import main as api_main

_TOKEN = "t" * 64
_PARENT = "bb415f6f-c9ab-48bd-89cf-7233c9bbe3a1"
_TP_LEG = "0b6a2f59-8a0c-4c9e-9d57-1c1b1f7a1111"
_SL_LEG = "0b6a2f59-8a0c-4c9e-9d57-1c1b1f7a2222"

#: A filled bracket, nested -- the entry that 6097 (alpaca_live/IAUM) placed.
FILLED_BRACKET = {
    "id": _PARENT, "client_order_id": "c-6097", "symbol": "IAUM",
    "order_class": "bracket", "type": "market", "side": "buy", "qty": "2",
    "filled_qty": "2", "filled_avg_price": "57.31", "stop_price": None,
    "limit_price": None, "time_in_force": "gtc", "status": "filled",
    "created_at": "2026-09-23T13:31:26.9Z", "submitted_at": "2026-09-23T13:31:26.9Z",
    "updated_at": "2026-09-23T13:31:27.1Z", "filled_at": "2026-09-23T13:31:27.0Z",
    "canceled_at": None, "expired_at": None, "replaced_at": None,
    "failed_at": None, "replaced_by": None, "replaces": None,
    "legs": [
        {"id": _TP_LEG, "symbol": "IAUM", "order_class": "bracket",
         "type": "limit", "side": "sell", "qty": "2", "filled_qty": "0",
         "limit_price": "60.10", "stop_price": None, "time_in_force": "gtc",
         "status": "canceled", "created_at": "2026-09-23T13:31:26.9Z",
         "canceled_at": "2026-09-23T13:38:00.4Z", "replaced_by": None,
         "legs": None},
        {"id": _SL_LEG, "symbol": "IAUM", "order_class": "bracket",
         "type": "stop", "side": "sell", "qty": "2", "filled_qty": "0",
         "limit_price": None, "stop_price": "55.80", "time_in_force": "gtc",
         "status": "canceled", "created_at": "2026-09-23T13:31:26.9Z",
         "canceled_at": "2026-09-23T13:38:00.4Z", "replaced_by": None,
         "legs": None},
    ],
}


class _Fake:
    def __init__(self, routes):
        self.routes = routes
        self.calls: list = []

    def _request(self, method, path, json_body=None):
        self.calls.append((method, path))
        assert method == "GET", "the history reader must never write"
        for prefix, env in self.routes.items():
            if path.startswith(prefix):
                return env
        return {"retCode": 500, "retMsg": "unrouted"}


_ALPACA = {"account_id": "alpaca_live", "exchange": "alpaca", "mode": "live",
           "account_class": "real_money"}


def _reader(monkeypatch, routes):
    fake = _Fake(routes)
    monkeypatch.setattr(accounts_clients, "alpaca_client_for", lambda acc: fake)
    return fake


# ---------------------------------------------------------------- the reader
def test_list_read_keeps_legs_and_their_lifecycle(monkeypatch):
    fake = _reader(monkeypatch, {"/v2/orders?": {"retCode": 0, "result": [FILLED_BRACKET]}})
    out = accounts_clients.account_alpaca_order_history(
        _ALPACA, after="2026-09-23T13:00:00Z", symbols=["iaum"])
    assert out["list_state"] == "orders_read" and out["order_count"] == 1
    (_m, path), = fake.calls
    assert "status=all" in path and "nested=true" in path
    assert "after=2026-09-23T13:00:00Z" in path and "symbols=IAUM" in path
    parent = out["orders"][0]
    assert parent["status"] == "filled" and parent["order_class"] == "bracket"
    sl = next(leg for leg in parent["legs"] if leg["order_type"] == "stop")
    assert sl["status"] == "canceled"
    assert sl["canceled_at"] == "2026-09-23T13:38:00.4Z"
    assert sl["stop_price"] == 55.80
    # an unset price is None, never 0.0
    assert sl["limit_price"] is None


def test_list_read_failure_is_none_not_empty(monkeypatch):
    _reader(monkeypatch, {"/v2/orders?": {"retCode": 503, "retMsg": "down"}})
    assert accounts_clients.account_alpaca_order_history(
        _ALPACA, after="2026-09-23") is None


def test_per_id_lookups_fail_independently(monkeypatch):
    other = "11111111-2222-3333-4444-555555555555"
    gone = "99999999-2222-3333-4444-555555555555"
    _reader(monkeypatch, {
        f"/v2/orders/{_PARENT}": {"retCode": 0, "result": FILLED_BRACKET},
        f"/v2/orders/{gone}": {"retCode": 404, "retMsg": "order not found"},
        f"/v2/orders/{other}": {"retCode": 500, "retMsg": "boom"},
    })
    out = accounts_clients.account_alpaca_order_history(
        _ALPACA, order_ids=[_PARENT, gone, other])
    assert out["list_state"] == "not_requested" and out["order_count"] is None
    assert out["by_id"][_PARENT]["read_state"] == "order_read"
    assert len(out["by_id"][_PARENT]["order"]["legs"]) == 2
    assert out["by_id"][gone]["read_state"] == "not_found"
    assert out["by_id"][other]["read_state"] == "could_not_look"


@pytest.mark.parametrize("kw", [
    {"order_ids": ["../v2/account"]},
    {"symbols": ["SPY&status=open"]},
    {"after": "yesterday"},
    {"until": "2026-09-23T13:00:00Z&x=1"},
])
def test_malformed_arguments_are_refused_before_the_broker(monkeypatch, kw):
    fake = _reader(monkeypatch, {})
    with pytest.raises(ValueError):
        accounts_clients.account_alpaca_order_history(_ALPACA, **kw)
    assert fake.calls == []


def test_non_alpaca_account_is_none():
    assert accounts_clients.account_alpaca_order_history(
        {"account_id": "bybit_2", "exchange": "bybit"}) is None


# ----------------------------------------------------------------- the route
@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("DIAG_READ_TOKEN", _TOKEN)
    monkeypatch.setenv("JWT_SIGNING_KEY", "x" * 64)
    monkeypatch.setenv("ALLOWED_EMAIL", "test@example.com")
    monkeypatch.setenv("WEBAPP_PASSWORD_SHA256", "deadbeef")
    import src.units.ui.data_loaders as dl
    monkeypatch.setattr(dl, "list_accounts", lambda: [
        _ALPACA, {"account_id": "bybit_2", "exchange": "bybit", "mode": "live"}])
    return TestClient(api_main.app, raise_server_exceptions=False)


def _hdr():
    return {"Authorization": f"Bearer {_TOKEN}"}


def test_route_requires_token(client):
    assert client.get("/api/diag/alpaca_order_history?account_id=alpaca_live").status_code == 401


def test_route_orders_read(client, monkeypatch):
    _reader(monkeypatch, {f"/v2/orders/{_PARENT}": {"retCode": 0, "result": FILLED_BRACKET}})
    r = client.get(f"/api/diag/alpaca_order_history?account_id=alpaca_live&order_ids={_PARENT}",
                   headers=_hdr())
    assert r.status_code == 200
    body = r.json()
    assert body["read_state"] == "orders_read"
    assert body["result"]["by_id"][_PARENT]["order"]["legs"][1]["status"] == "canceled"


def test_route_could_not_look(client, monkeypatch):
    _reader(monkeypatch, {"/v2/orders?": {"retCode": 503, "retMsg": "down"}})
    body = client.get("/api/diag/alpaca_order_history?account_id=alpaca_live&after=2026-09-23",
                      headers=_hdr()).json()
    assert body["read_state"] == "could_not_look" and body["result"] is None


def test_route_not_alpaca(client):
    body = client.get("/api/diag/alpaca_order_history?account_id=bybit_2", headers=_hdr()).json()
    assert body["read_state"] == "not_alpaca"


def test_route_bad_argument_is_400(client, monkeypatch):
    _reader(monkeypatch, {})
    r = client.get("/api/diag/alpaca_order_history?account_id=alpaca_live&order_ids=nope",
                   headers=_hdr())
    assert r.status_code == 400


def test_route_unknown_account_is_404(client):
    r = client.get("/api/diag/alpaca_order_history?account_id=nope", headers=_hdr())
    assert r.status_code == 404
