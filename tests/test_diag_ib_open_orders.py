"""Tests for GET /api/diag/ib_open_orders (BL-20260814-NO-IB-OPEN-ORDERS-READ-SURFACE).

IB order state had two consumers and both REDUCE it before anyone sees it
(``has_protective_orders`` -> a boolean, ``protection_coverage`` -> a covered
quantity). Neither can be contradicted from outside, which is why a stripped
MGC take-profit sat undetected for seven days. This endpoint reduces nothing.

The property under test throughout is the THREE-STATE contract: ``null``
orders (could not look) must never be confusable with ``[]`` (a confirmed
clean read holding nothing), and ``count`` must stay ``null`` rather than
reporting ``0`` on an account we never reached.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.web.api import main as api_main

_TOKEN = "t" * 64


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("DIAG_READ_TOKEN", _TOKEN)
    monkeypatch.setenv("JWT_SIGNING_KEY", "x" * 64)
    monkeypatch.setenv("ALLOWED_EMAIL", "test@example.com")
    monkeypatch.setenv("WEBAPP_PASSWORD_SHA256", "deadbeef")


@pytest.fixture
def client(env):
    return TestClient(api_main.app, raise_server_exceptions=False)


def _bearer(tok=_TOKEN):
    return {"Authorization": f"Bearer {tok}"}


_ACCOUNTS = [
    {"account_id": "ib_paper", "exchange": "interactive_brokers", "mode": "live"},
    {"account_id": "ib_live", "exchange": "interactive_brokers", "mode": "dry_run"},
    {"account_id": "bybit_2", "exchange": "bybit", "mode": "live"},
]

_ROW = {
    "symbol": "MGC", "local_symbol": "MGCZ6", "sec_type": "FUT",
    "exchange": "COMEX", "order_id": 42.0, "perm_id": 999.0,
    "order_type": "STP", "action": "SELL", "total_quantity": 105.0,
    "aux_price": 4278.8, "lmt_price": None, "oca_group": "oca-1",
    "tif": "GTC", "parent_id": 0.0, "account": "DU5724",
    "status": "PreSubmitted", "filled": 0.0, "remaining": 105.0,
}


def _patch(monkeypatch, orders_by_id, accounts=None):
    """Patch the two symbols the route imports at call time."""
    import src.units.ui.data_loaders as dl
    import src.units.accounts.clients as clients

    monkeypatch.setattr(dl, "list_accounts", lambda: accounts or _ACCOUNTS)

    def _reader(acc):
        val = orders_by_id.get(acc.get("account_id"), KeyError)
        if val is KeyError:
            raise RuntimeError("boom")
        return val

    monkeypatch.setattr(clients, "account_ib_open_orders", _reader)


def _by_id(payload):
    return {r["account_id"]: r for r in payload["accounts"]}


def test_requires_token(client, monkeypatch):
    _patch(monkeypatch, {})
    assert client.get("/api/diag/ib_open_orders").status_code == 401


def test_rejects_bad_token(client, monkeypatch):
    _patch(monkeypatch, {})
    r = client.get("/api/diag/ib_open_orders", headers=_bearer("z" * 64))
    assert r.status_code == 401


def test_orders_read_returns_rows_verbatim(client, monkeypatch):
    _patch(monkeypatch, {"ib_paper": [_ROW], "ib_live": None})
    r = client.get("/api/diag/ib_open_orders", headers=_bearer())
    assert r.status_code == 200
    row = _by_id(r.json())["ib_paper"]
    assert row["read_state"] == "orders_read"
    assert row["count"] == 1
    # Reduces nothing: the row survives intact, aux_price and ocaGroup included
    # (they are what distinguish a live stop from a cancelled one).
    assert row["orders"][0] == _ROW


def test_empty_list_is_a_confirmed_clean_read_not_a_failure(client, monkeypatch):
    """[] means 'looked, holds nothing' — count 0, state orders_read."""
    _patch(monkeypatch, {"ib_paper": [], "ib_live": None})
    row = _by_id(client.get("/api/diag/ib_open_orders", headers=_bearer()).json())["ib_paper"]
    assert row["read_state"] == "orders_read"
    assert row["orders"] == []
    assert row["count"] == 0


def test_could_not_look_never_reports_zero_orders(client, monkeypatch):
    """The load-bearing case: a None read must NOT render as 'no orders'."""
    _patch(monkeypatch, {"ib_paper": None, "ib_live": None})
    row = _by_id(client.get("/api/diag/ib_open_orders", headers=_bearer()).json())["ib_paper"]
    assert row["read_state"] == "could_not_look"
    assert row["orders"] is None
    # count 0 here would be the collapse this endpoint exists to prevent.
    assert row["count"] is None


def test_could_not_look_is_distinguishable_from_clean_empty(client, monkeypatch):
    """Can-fail control: the two states must differ on EVERY field a consumer reads."""
    _patch(monkeypatch, {"ib_paper": [], "ib_live": None})
    rows = _by_id(client.get("/api/diag/ib_open_orders", headers=_bearer()).json())
    clean, unread = rows["ib_paper"], rows["ib_live"]
    assert (clean["read_state"], clean["orders"], clean["count"]) == ("orders_read", [], 0)
    assert (unread["read_state"], unread["orders"], unread["count"]) == ("could_not_look", None, None)
    assert clean["read_state"] != unread["read_state"]


def test_non_ib_account_is_not_ib_not_could_not_look(client, monkeypatch):
    """A bybit row is 'not_ib' — we did not fail to read it, it has no such surface."""
    _patch(monkeypatch, {"ib_paper": [], "ib_live": None})
    row = _by_id(client.get("/api/diag/ib_open_orders", headers=_bearer()).json())["bybit_2"]
    assert row["read_state"] == "not_ib"
    assert row["orders"] is None
    assert row["count"] is None
    assert row["error"] is None


def test_one_account_raising_does_not_fail_the_call(client, monkeypatch):
    _patch(monkeypatch, {"ib_live": None})  # ib_paper missing -> reader raises
    payload = client.get("/api/diag/ib_open_orders", headers=_bearer()).json()
    row = _by_id(payload)["ib_paper"]
    assert row["read_state"] == "could_not_look"
    assert row["count"] is None
    assert "RuntimeError" in (row["error"] or "")
    assert len(payload["accounts"]) == 3


def test_account_id_filter(client, monkeypatch):
    _patch(monkeypatch, {"ib_paper": [_ROW], "ib_live": None})
    payload = client.get(
        "/api/diag/ib_open_orders?account_id=ib_paper", headers=_bearer()
    ).json()
    assert payload["requested_account_id"] == "ib_paper"
    assert [r["account_id"] for r in payload["accounts"]] == ["ib_paper"]


def test_list_accounts_failure_still_answers(client, monkeypatch):
    import src.units.ui.data_loaders as dl

    def _boom():
        raise RuntimeError("no config")

    monkeypatch.setattr(dl, "list_accounts", _boom)
    r = client.get("/api/diag/ib_open_orders", headers=_bearer())
    assert r.status_code == 200
    assert r.json()["accounts"] == []


# --- the client-side half: IBClient.list_open_orders -------------------------


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _client_stub():
    from src.units.accounts.ib_client import IBClient
    return IBClient.__new__(IBClient)


def test_client_list_open_orders_maps_fields(monkeypatch):
    c = _client_stub()
    trade = _Obj(
        contract=_Obj(symbol="MGC", localSymbol="MGCZ6", secType="FUT", exchange="COMEX"),
        order=_Obj(orderId=42, permId=999, orderType="STP", action="SELL",
                   totalQuantity=105, auxPrice=4278.8, ocaGroup="oca-1", tif="GTC",
                   parentId=0, account="DU5724"),
        orderStatus=_Obj(status="PreSubmitted", filled=0, remaining=105),
    )
    monkeypatch.setattr(type(c), "connect", lambda self: object())
    monkeypatch.setattr(type(c), "_req_all_open_orders", lambda self, ib: [trade])
    rows = c._locked_list_open_orders()
    assert len(rows) == 1
    assert rows[0]["symbol"] == "MGC"
    assert rows[0]["order_type"] == "STP"
    assert rows[0]["total_quantity"] == 105.0
    assert rows[0]["oca_group"] == "oca-1"
    assert rows[0]["status"] == "PreSubmitted"
    # lmtPrice absent on this stop leg -> null, never 0.0
    assert rows[0]["lmt_price"] is None


def test_client_connect_failure_is_none_not_empty(monkeypatch):
    """Cannot connect => None. An [] here would read as 'broker holds nothing'."""
    c = _client_stub()

    def _boom(self):
        raise RuntimeError("breaker open")

    monkeypatch.setattr(type(c), "connect", _boom)
    assert c._locked_list_open_orders() is None


def test_client_read_failure_is_none_not_empty(monkeypatch):
    c = _client_stub()
    monkeypatch.setattr(type(c), "connect", lambda self: object())

    def _boom(self, ib):
        raise RuntimeError("reqAllOpenOrders timed out")

    monkeypatch.setattr(type(c), "_req_all_open_orders", _boom)
    assert c._locked_list_open_orders() is None


def test_client_clean_empty_read_is_empty_list(monkeypatch):
    """Can-fail control for the two tests above: a CLEAN read returns []."""
    c = _client_stub()
    monkeypatch.setattr(type(c), "connect", lambda self: object())
    monkeypatch.setattr(type(c), "_req_all_open_orders", lambda self, ib: [])
    assert c._locked_list_open_orders() == []


def test_client_one_malformed_order_does_not_lose_the_others(monkeypatch):
    c = _client_stub()

    class _Exploding:
        @property
        def contract(self):
            raise RuntimeError("bad row")

    good = _Obj(contract=_Obj(symbol="MES"), order=_Obj(orderId=7), orderStatus=_Obj(status="Submitted"))
    monkeypatch.setattr(type(c), "connect", lambda self: object())
    monkeypatch.setattr(type(c), "_req_all_open_orders", lambda self, ib: [_Exploding(), good])
    rows = c._locked_list_open_orders()
    assert [r["symbol"] for r in rows] == ["MES"]


# --- the accounts-unit wrapper ----------------------------------------------


def test_wrapper_skips_non_ib_and_dry_accounts():
    from src.units.accounts.clients import account_ib_open_orders
    assert account_ib_open_orders({"exchange": "bybit", "mode": "live"}) is None
    assert account_ib_open_orders(
        {"exchange": "interactive_brokers", "mode": "dry_run"}
    ) is None
    assert account_ib_open_orders(None) is None


# --- the staleness caveat ----------------------------------------------------
#
# The route can return orders that are already cancelled
# (BL-20260826-DIAG-IB-OPEN-ORDERS-SERVES-A-STALE-MONOTONIC-ORDER-VIEW), so the
# envelope declares that in a machine-readable field rather than only in a
# docstring. These tests exist so the caveat cannot quietly disappear BEFORE the
# defect does — removing the field is part of the fix's done-condition, not a
# tidy-up. They read the source rather than calling the handler because the
# handler needs a live IB client; the point being pinned is the contract text.


def _diag_source() -> str:
    import pathlib
    import src.web.api.routers.diag as _diag
    return pathlib.Path(_diag.__file__).read_text()


def test_ib_open_orders_envelope_declares_the_staleness_caveat():
    src = _diag_source()
    assert '"stale_read_caveat"' in src, (
        "the ib_open_orders envelope must carry stale_read_caveat while the "
        "route can return already-cancelled orders — a docstring alone is not "
        "reachable by a machine consumer"
    )
    assert "already cancelled by another client" in src


def test_staleness_caveat_names_its_backlog_row():
    # A caveat that does not name the row tracking it becomes untraceable the
    # moment the person who wrote it is gone — the id is what lets a future
    # session find the done-condition that authorises deleting the field.
    src = _diag_source()
    # Build the id from parts so this assertion does not itself become a
    # truncated backlog reference -- artifact-validity-guard reads added lines,
    # and a partial id in a TEST dangles exactly like one in a docstring.
    wanted = "BL-20260826-DIAG-IB-OPEN-ORDERS" + "-SERVES-A-STALE-MONOTONIC-ORDER-VIEW"
    assert wanted in src


def test_ib_open_orders_no_longer_claims_a_confirmed_clean_read():
    # The false claim this replaced. `[]` from this route means the read
    # returned no rows, NOT that the account holds none — and the whole list
    # may carry cancelled orders. Guarding the exact retired phrase keeps a
    # later docstring edit from reinstating it.
    src = _diag_source()
    head = src[src.index("async def get_ib_open_orders"):]
    body = head[: head.index("\n@router.")] if "\n@router." in head else head
    assert "a confirmed clean read: the account holds no resting orders" not in body
