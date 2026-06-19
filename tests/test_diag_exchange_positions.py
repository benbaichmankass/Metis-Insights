"""Tests for GET /api/diag/exchange_positions (BL-20260619 — broker-truth read).

Read-only endpoint that surfaces each account's EXCHANGE-side open positions
(not the journal) so a session can confirm whether a journal orphan actually
exists on the broker before any cleanup. Mirrors get_account_balances' pattern.
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


def _patch_loaders(monkeypatch, accounts, positions_by_id):
    import src.units.ui.data_loaders as dl

    def _list_accounts():
        return accounts

    def _aop(acc):
        val = positions_by_id.get(acc.get("account_id"), KeyError)
        if val is KeyError:
            raise RuntimeError("boom")
        return val

    monkeypatch.setattr(dl, "list_accounts", _list_accounts)
    monkeypatch.setattr(dl, "account_open_positions", _aop)


_ACCOUNTS = [
    {"account_id": "ib_paper", "exchange": "interactive_brokers"},
    {"account_id": "bybit_2", "exchange": "bybit"},
    {"account_id": "breakout_1", "exchange": "breakout"},
]


def test_requires_token(client, monkeypatch):
    _patch_loaders(monkeypatch, _ACCOUNTS, {})
    assert client.get("/api/diag/exchange_positions").status_code == 401


def test_distinguishes_live_flat_and_unreadable(client, monkeypatch):
    # ib_paper holds a live position; bybit_2 is flat ([]); breakout_1 can't be
    # read (None). None must be preserved as "could-not-read", NOT coerced to [].
    _patch_loaders(monkeypatch, _ACCOUNTS, {
        "ib_paper": [{"symbol": "MGC", "side": "short", "size": 232,
                      "entry_price": 4303.8, "unrealised_pnl": -12.0}],
        "bybit_2": [],
        "breakout_1": None,
    })
    resp = client.get("/api/diag/exchange_positions", headers=_bearer())
    assert resp.status_code == 200
    body = resp.json()
    by_id = {a["account_id"]: a for a in body["accounts"]}
    assert by_id["ib_paper"]["count"] == 1
    assert by_id["ib_paper"]["positions"][0]["symbol"] == "MGC"
    assert by_id["bybit_2"]["positions"] == [] and by_id["bybit_2"]["count"] == 0
    assert by_id["breakout_1"]["positions"] is None  # could-not-read, not flat
    assert by_id["breakout_1"]["count"] is None


def test_account_id_filter(client, monkeypatch):
    _patch_loaders(monkeypatch, _ACCOUNTS, {"ib_paper": [], "bybit_2": [], "breakout_1": None})
    resp = client.get("/api/diag/exchange_positions?account_id=ib_paper", headers=_bearer())
    assert resp.status_code == 200
    body = resp.json()
    assert body["requested_account_id"] == "ib_paper"
    assert [a["account_id"] for a in body["accounts"]] == ["ib_paper"]


def test_one_account_raising_does_not_fail_the_call(client, monkeypatch):
    # account_open_positions raising for one account must not 500 the endpoint;
    # that account reports error + positions=null, the rest still return.
    _patch_loaders(monkeypatch, _ACCOUNTS, {"ib_paper": [], "bybit_2": []})  # breakout_1 missing -> raises
    resp = client.get("/api/diag/exchange_positions", headers=_bearer())
    assert resp.status_code == 200
    by_id = {a["account_id"]: a for a in resp.json()["accounts"]}
    assert by_id["breakout_1"]["positions"] is None
    assert by_id["breakout_1"]["error"] and "boom" in by_id["breakout_1"]["error"]
    assert by_id["ib_paper"]["positions"] == []
