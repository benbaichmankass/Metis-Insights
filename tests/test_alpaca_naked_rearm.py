"""Alpaca naked-bracket re-arm (BL-20260629-ALPACA-NAKED-BRACKET).

The Alpaca entry bracket's protective legs are ``time_in_force: day`` and are
cancelled at the RTH close, so a multi-session equity hold goes broker-naked
while its journal row still carries sl/tp — invisible to the DB-driven
``_check_naked_positions``. These tests cover the equity re-arm path:

* ``AlpacaClient.place_protective`` — builds a **GTC OCO** (closing side) and
  cancels resting orders first (no OCO stacking).
* ``AlpacaClient.has_protective_orders`` — True / False / None(read-fail).
* ``_check_broker_naked_equity_positions`` — re-arms a broker-naked Alpaca
  position via the broker-state-as-idempotency sweep.

Request bodies are asserted against the EXPECTED Alpaca contract; live OCO/GTC
acceptance is a documented alpaca_paper verification step (sandbox can't reach
the broker).
"""
from __future__ import annotations

import sqlite3

import pytest

from src.runtime import order_monitor as om
from src.units.accounts.alpaca_client import AlpacaClient


# --------------------------------------------------------------- client unit
def _client():
    return AlpacaClient(api_key="k", api_secret="s", env="paper")


def test_place_protective_builds_gtc_oco_long(monkeypatch):
    """A long position → a closing-side (sell) GTC OCO: limit TP + stop SL."""
    calls = []

    def fake_request(method, path, json_body=None):
        calls.append((method, path, json_body))
        if method == "GET":  # _open_orders_for_symbol (cancel pre-pass)
            return {"retCode": 0, "result": []}
        return {"retCode": 0, "result": {"id": "oco-1"}}

    c = _client()
    monkeypatch.setattr(c, "_request", fake_request)
    resp = c.place_protective(
        {"symbol": "spy", "direction": "long", "qty": 20, "sl": 730.0, "tp": 818.5}
    )
    assert resp["retCode"] == 0
    assert resp["result"]["orderId"] == "oco-1"
    post = [c for c in calls if c[0] == "POST"][-1][2]
    assert post["symbol"] == "SPY"
    assert post["side"] == "sell"          # closing a long
    assert post["order_class"] == "oco"
    assert post["type"] == "limit"
    assert post["time_in_force"] == "gtc"  # persists across RTH close (the fix)
    assert post["qty"] == "20"
    # Alpaca OCO: both legs nested (NOT a top-level limit_price — refused live
    # 2026-06-29 with "oco orders require take_profit.limit_price").
    assert post["take_profit"]["limit_price"] == "818.50"
    assert post["stop_loss"]["stop_price"] == "730.00"
    assert "limit_price" not in post


def test_place_protective_short_uses_buy_side(monkeypatch):
    def fake_request(method, path, json_body=None):
        if method == "GET":
            return {"retCode": 0, "result": []}
        return {"retCode": 0, "result": {"id": "oco-2"}}

    c = _client()
    monkeypatch.setattr(c, "_request", fake_request)
    resp = c.place_protective(
        {"symbol": "GLD", "direction": "short", "qty": 42, "sl": 380.0, "tp": 331.6}
    )
    assert resp["retCode"] == 0


def test_place_protective_requires_both_legs(monkeypatch):
    c = _client()
    monkeypatch.setattr(c, "_request", lambda *a, **k: pytest.fail("no HTTP"))
    r = c.place_protective({"symbol": "SPY", "direction": "long", "qty": 1, "sl": 1.0})
    assert r["retCode"] == -2 and "both" in r["retMsg"].lower()


def test_place_protective_cancels_resting_first(monkeypatch):
    """Idempotency: existing resting legs are DELETEd before the new OCO."""
    calls = []

    def fake_request(method, path, json_body=None):
        calls.append((method, path))
        if method == "GET":
            return {"retCode": 0, "result": [
                {"id": "old-stop", "symbol": "SPY", "type": "stop"},
                {"id": "old-limit", "symbol": "SPY", "type": "limit"},
            ]}
        return {"retCode": 0, "result": {"id": "oco-new"}}

    c = _client()
    monkeypatch.setattr(c, "_request", fake_request)
    c.place_protective(
        {"symbol": "SPY", "direction": "long", "qty": 5, "sl": 1.0, "tp": 2.0}
    )
    deletes = [p for (m, p) in calls if m == "DELETE"]
    assert "/v2/orders/old-stop" in deletes
    assert "/v2/orders/old-limit" in deletes


def test_has_protective_orders_true_false_none(monkeypatch):
    c = _client()
    # a resting stop leg → True
    monkeypatch.setattr(c, "_open_orders_for_symbol",
                        lambda s: [{"id": "1", "symbol": "SPY", "type": "stop"}])
    assert c.has_protective_orders("SPY") is True
    # only an entry market order (no protective leg) → False
    monkeypatch.setattr(c, "_open_orders_for_symbol",
                        lambda s: [{"id": "2", "symbol": "SPY", "type": "market"}])
    assert c.has_protective_orders("SPY") is False
    # read failure → None (caller must not act)
    monkeypatch.setattr(c, "_open_orders_for_symbol", lambda s: None)
    assert c.has_protective_orders("SPY") is None


# ----------------------------------------------------------- monitor sweep
class _FakeDB:
    def __init__(self, path):
        self.path = str(path)
        conn = sqlite3.connect(self.path)
        conn.executescript(
            """
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY, account_id TEXT, symbol TEXT,
                direction TEXT, position_size REAL, stop_loss REAL,
                take_profit_1 REAL, created_at TEXT, notes TEXT,
                status TEXT, is_backtest INTEGER DEFAULT 0
            );
            CREATE TABLE order_packages (
                order_package_id TEXT, symbol TEXT, direction TEXT,
                sl REAL, tp REAL, created_at TEXT
            );
            """
        )
        conn.commit()
        conn.close()

    def connect(self):
        return sqlite3.connect(self.path)


def _insert(db, **kw):
    conn = sqlite3.connect(db.path)
    conn.execute(
        "INSERT INTO trades (id,account_id,symbol,direction,position_size,"
        "stop_loss,take_profit_1,created_at,status,is_backtest) "
        "VALUES (:id,:account_id,:symbol,:direction,:position_size,:stop_loss,"
        ":take_profit_1,:created_at,:status,0)", kw,
    )
    conn.commit()
    conn.close()


class _FakeAlpaca:
    def __init__(self, protected):
        self._protected = protected
        self.rearmed = []

    def has_protective_orders(self, symbol):
        return self._protected

    def place_protective(self, order):
        self.rearmed.append(order)
        return {"retCode": 0, "result": {"orderId": "oco-x"}}


def test_broker_naked_sweep_rearms_unprotected(tmp_path, monkeypatch):
    db = _FakeDB(tmp_path / "j.db")
    _insert(db, id=1, account_id="alpaca_paper", symbol="SPY", direction="long",
            position_size=20, stop_loss=730.0, take_profit_1=818.5,
            created_at="2026-06-25T00:00:00+00:00", status="open")

    monkeypatch.setattr(
        om, "datetime", __import__("datetime").datetime
    )  # real datetime; created_at is old → past grace
    monkeypatch.setattr(
        "src.bot.data_loaders.list_accounts",
        lambda: [{"account_id": "alpaca_paper", "exchange": "alpaca"}],
    )
    fake = _FakeAlpaca(protected=False)
    monkeypatch.setattr(
        "src.units.accounts.clients.alpaca_client_for", lambda acc: fake
    )
    # _attempt_naked_autoprotect resolves the account + client the same way
    monkeypatch.setattr(
        "src.bot.data_loaders.list_accounts",
        lambda: [{"account_id": "alpaca_paper", "exchange": "alpaca"}],
    )

    summary = om._check_broker_naked_equity_positions(db)
    assert summary["checked"] == 1
    assert summary["broker_naked"] == 1
    assert summary["rearmed"] == 1
    assert fake.rearmed and fake.rearmed[0]["symbol"] == "SPY"
    assert fake.rearmed[0]["sl"] == 730.0 and fake.rearmed[0]["tp"] == 818.5


def test_broker_naked_sweep_skips_protected(tmp_path, monkeypatch):
    db = _FakeDB(tmp_path / "j.db")
    _insert(db, id=1, account_id="alpaca_paper", symbol="TLT", direction="long",
            position_size=231, stop_loss=86.0, take_profit_1=95.0,
            created_at="2026-06-25T00:00:00+00:00", status="open")
    monkeypatch.setattr(
        "src.bot.data_loaders.list_accounts",
        lambda: [{"account_id": "alpaca_paper", "exchange": "alpaca"}],
    )
    fake = _FakeAlpaca(protected=True)  # already has a resting leg
    monkeypatch.setattr(
        "src.units.accounts.clients.alpaca_client_for", lambda acc: fake
    )
    summary = om._check_broker_naked_equity_positions(db)
    assert summary["broker_naked"] == 0
    assert summary["rearmed"] == 0
    assert fake.rearmed == []


def test_broker_naked_sweep_skips_on_read_failure(tmp_path, monkeypatch):
    db = _FakeDB(tmp_path / "j.db")
    _insert(db, id=1, account_id="alpaca_paper", symbol="QQQ", direction="long",
            position_size=16, stop_loss=729.0, take_profit_1=811.0,
            created_at="2026-06-25T00:00:00+00:00", status="open")
    monkeypatch.setattr(
        "src.bot.data_loaders.list_accounts",
        lambda: [{"account_id": "alpaca_paper", "exchange": "alpaca"}],
    )
    fake = _FakeAlpaca(protected=None)  # read failure
    monkeypatch.setattr(
        "src.units.accounts.clients.alpaca_client_for", lambda acc: fake
    )
    summary = om._check_broker_naked_equity_positions(db)
    assert summary["broker_naked"] == 0
    assert summary["rearmed"] == 0
