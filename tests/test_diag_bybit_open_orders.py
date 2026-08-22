"""GET /api/diag/bybit_open_orders + ``account_bybit_open_orders``.

BL-20260820-PROTECTION-COVERAGE-IS-PRICE-BLIND criterion 5. That row was
written about IB and explicitly left Bybit unchecked. Bybit carries the same
blindness -- ``_bybit_position_protection``'s Full-mode branch returns
``covered_qty == size`` on any ``stopLoss`` string that is non-empty and not
``"0"`` -- and unlike the IB instance (``ib_paper``), ``bybit_2`` is mainnet.

Three properties are under test, each of which would let a wrong answer through
if it regressed:

1. **The three-state contract.** ``null`` (could not look) must never be
   confusable with a clean read holding nothing, and the counts must stay
   ``null`` rather than reporting ``0`` for an account never reached.
2. **An unset price is ``None``, never ``0.0``.** Bybit reports no-stop as
   ``""`` or ``"0"``. A zero would compare against a declared level as a huge
   divergence, when the truth is that no stop is set at all.
3. **BOTH collections are read.** Full mode puts the stop on the POSITION row
   with no resting order; Partial mode puts it in a conditional leg. Reading
   one collection reports a protected position as naked, or vice versa.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.units.accounts import clients as accounts_clients
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
    {"account_id": "bybit_2", "exchange": "bybit", "mode": "live",
     "account_class": "real_money", "market_type": "linear"},
    {"account_id": "bybit_1", "exchange": "bybit", "mode": "live",
     "account_class": "paper", "market_type": "linear"},
    {"account_id": "ib_paper", "exchange": "interactive_brokers", "mode": "live"},
]


def _patch_route(monkeypatch, by_id, accounts=None):
    import src.units.ui.data_loaders as dl
    monkeypatch.setattr(dl, "list_accounts", lambda: accounts or _ACCOUNTS)

    def _reader(acc):
        val = by_id.get(acc.get("account_id"), KeyError)
        if val is KeyError:
            raise RuntimeError("boom")
        return val

    monkeypatch.setattr(accounts_clients, "account_bybit_open_orders", _reader)


def _by_id(payload):
    return {r["account_id"]: r for r in payload["accounts"]}


# --------------------------------------------------------------------------
# The route: the three-state contract
# --------------------------------------------------------------------------

def test_requires_token(client, monkeypatch):
    _patch_route(monkeypatch, {})
    assert client.get("/api/diag/bybit_open_orders").status_code == 401


def test_non_bybit_account_is_not_bybit_not_a_failure(client, monkeypatch):
    """An IB account is `not_bybit` -- nothing to read, NOT a failed read."""
    _patch_route(monkeypatch, {"bybit_2": {"category": "linear", "positions": [], "orders": []},
                               "bybit_1": None})
    row = _by_id(client.get("/api/diag/bybit_open_orders", headers=_bearer()).json())["ib_paper"]
    assert row["read_state"] == "not_bybit"
    assert row["result"] is None
    assert row["position_count"] is None and row["order_count"] is None


def test_could_not_look_reports_null_counts_never_zero(client, monkeypatch):
    """The collapse this route exists to prevent: an unreached account must not
    report `0 orders`, which reads identically to a confirmed-empty book."""
    _patch_route(monkeypatch, {"bybit_2": None,
                               "bybit_1": {"category": "linear", "positions": [], "orders": []}})
    row = _by_id(client.get("/api/diag/bybit_open_orders", headers=_bearer()).json())["bybit_2"]
    assert row["read_state"] == "could_not_look"
    assert row["order_count"] is None, "0 would read as 'confirmed no orders'"
    assert row["position_count"] is None


def test_clean_empty_read_is_orders_read_with_zero(client, monkeypatch):
    """The mirror image: a confirmed clean read holding nothing IS 0, not null."""
    _patch_route(monkeypatch, {"bybit_2": {"category": "linear", "positions": [], "orders": []},
                               "bybit_1": None})
    row = _by_id(client.get("/api/diag/bybit_open_orders", headers=_bearer()).json())["bybit_2"]
    assert row["read_state"] == "orders_read"
    assert row["order_count"] == 0 and row["position_count"] == 0


def test_one_account_raising_does_not_fail_the_call(client, monkeypatch):
    """bybit_1 is absent from the map, so the reader raises for it."""
    _patch_route(monkeypatch, {"bybit_2": {"category": "linear", "positions": [], "orders": []}})
    payload = client.get("/api/diag/bybit_open_orders", headers=_bearer()).json()
    rows = _by_id(payload)
    assert rows["bybit_2"]["read_state"] == "orders_read"
    assert rows["bybit_1"]["read_state"] == "could_not_look"
    assert rows["bybit_1"]["error"] and "boom" in rows["bybit_1"]["error"]


def test_rows_survive_intact(client, monkeypatch):
    """Reduces nothing: the trigger price is what distinguishes a stop at the
    declared level from one 69 ticks away, so it must reach the consumer."""
    result = {"category": "linear",
              "positions": [{"symbol": "XRPUSDT", "side": "Buy", "size": 21.3,
                             "entry_price": 1.4983, "stop_loss": 1.41,
                             "take_profit": None, "tpsl_mode": "Full"}],
              "orders": [{"symbol": "XRPUSDT", "order_id": "abc",
                          "trigger_price": 1.41, "stop_order_type": "StopLoss"}]}
    _patch_route(monkeypatch, {"bybit_2": result, "bybit_1": None})
    row = _by_id(client.get("/api/diag/bybit_open_orders", headers=_bearer()).json())["bybit_2"]
    assert row["result"] == result
    assert row["result"]["positions"][0]["stop_loss"] == 1.41
