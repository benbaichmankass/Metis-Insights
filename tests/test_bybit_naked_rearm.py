"""Bybit broker-naked re-arm (BL-20260729-BYBIT-NAKED-POSITION-BLINDSPOT).

A real-money bybit_2 XRPUSDT position was observed live with no TP/SL bracket.
Under ``BYBIT_TPSL_MODE=partial`` the per-trade qty-scoped SL/TP legs desync
from the netted one-way exchange position (intent_reduce legs add none; a close
cancels its own trade's legs; the 20-leg cap can block an amend), so a Bybit
position can go broker-naked while its journal row still carries sl/tp —
invisible to the DB-driven ``_check_naked_positions``. The earlier design
assumed Bybit "attaches SL/TP atomically at entry, so a naked orphan can't
occur"; that is false. These tests cover the new Bybit broker-state sweep:

* ``_bybit_position_protection`` — Full-mode position stop OR resting Partial SL
  leg → protected; neither → naked; read failure → None.
* ``_check_broker_naked_bybit_positions`` — re-arms a broker-naked Bybit
  position via ``_attempt_naked_autoprotect`` (broker-state-as-idempotency).
* ``_attempt_naked_autoprotect`` bybit branch — a Full-mode ``set_trading_stop``.

Request shapes are asserted against the expected Bybit V5 contract; live
acceptance is a documented bybit_1 (demo) verification step.
"""
from __future__ import annotations

import sqlite3

from src.runtime import order_monitor as om


# --------------------------------------------------------------- fake client
class _FakeBybit:
    """Minimal pybit-shaped stub. ``positions`` maps SYMBOL -> position dict
    (with size/stopLoss); ``stop_legs`` maps SYMBOL -> list of resting
    StopOrder dicts. Any of get_positions/get_open_orders may be set to raise
    to simulate a read failure. ``set_trading_stop`` records its call."""

    def __init__(self, positions=None, stop_legs=None, raise_pos=False,
                 raise_oo=False):
        self._positions = positions or {}
        self._stop_legs = stop_legs or {}
        self._raise_pos = raise_pos
        self._raise_oo = raise_oo
        self.stops_set = []

    def get_positions(self, category=None, symbol=None):
        if self._raise_pos:
            raise RuntimeError("pos read boom")
        pos = self._positions.get(symbol)
        return {"retCode": 0, "result": {"list": [pos] if pos else []}}

    def get_open_orders(self, category=None, symbol=None, orderFilter=None):
        if self._raise_oo:
            raise RuntimeError("oo read boom")
        return {"retCode": 0,
                "result": {"list": self._stop_legs.get(symbol, [])}}

    def set_trading_stop(self, **kw):
        self.stops_set.append(kw)
        return {"retCode": 0, "result": {}}


# --------------------------------------------------------- protection reader
def test_position_protection_full_mode_stop():
    c = _FakeBybit(positions={"XRPUSDT": {"size": "157.7", "stopLoss": "1.085"}})
    assert om._bybit_position_protection(c, "linear", "XRPUSDT") == (157.7, True)


def test_position_protection_partial_leg():
    c = _FakeBybit(
        positions={"XRPUSDT": {"size": "15.8", "stopLoss": ""}},
        stop_legs={"XRPUSDT": [{"stopOrderType": "PartialStopLoss"}]},
    )
    size, protected = om._bybit_position_protection(c, "linear", "XRPUSDT")
    assert size == 15.8 and protected is True


def test_position_protection_naked():
    """Live size, no Full stop, no SL leg (only a TP leg) → naked."""
    c = _FakeBybit(
        positions={"XRPUSDT": {"size": "157.7", "stopLoss": "0"}},
        stop_legs={"XRPUSDT": [{"stopOrderType": "PartialTakeProfit"}]},
    )
    assert om._bybit_position_protection(c, "linear", "XRPUSDT") == (157.7, False)


def test_position_protection_flat():
    c = _FakeBybit(positions={})  # no open position
    assert om._bybit_position_protection(c, "linear", "XRPUSDT") == (0.0, True)


def test_position_protection_read_failure_returns_none():
    assert om._bybit_position_protection(
        _FakeBybit(raise_pos=True), "linear", "XRPUSDT") is None
    c2 = _FakeBybit(positions={"XRPUSDT": {"size": "1", "stopLoss": ""}},
                    raise_oo=True)
    assert om._bybit_position_protection(c2, "linear", "XRPUSDT") is None


# ------------------------------------------------------------ monitor sweep
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


_ACC = {"account_id": "bybit_2", "exchange": "bybit", "market_type": "linear"}


def _patch_accounts(monkeypatch, client):
    monkeypatch.setattr("src.bot.data_loaders.list_accounts", lambda: [_ACC])
    monkeypatch.setattr(
        "src.units.accounts.clients.bybit_client_for", lambda acc: client)


def test_bybit_sweep_rearms_naked(tmp_path, monkeypatch):
    db = _FakeDB(tmp_path / "j.db")
    _insert(db, id=1, account_id="bybit_2", symbol="XRPUSDT", direction="short",
            position_size=157.7, stop_loss=1.085, take_profit_1=0.956,
            created_at="2026-07-01T00:00:00+00:00", status="open")
    client = _FakeBybit(
        positions={"XRPUSDT": {"size": "157.7", "stopLoss": ""}},
        stop_legs={"XRPUSDT": []},  # NO resting leg → naked
    )
    _patch_accounts(monkeypatch, client)
    om._TICK_ACTIVE_CLOSE_SYMBOLS.clear()

    summary = om._check_broker_naked_bybit_positions(db)
    assert summary["checked"] == 1
    assert summary["broker_naked"] == 1
    assert summary["rearmed"] == 1
    # Full-mode position bracket re-armed with the journal levels.
    assert client.stops_set and client.stops_set[0]["tpslMode"] == "Full"
    assert client.stops_set[0]["symbol"] == "XRPUSDT"
    assert client.stops_set[0]["stopLoss"] == "1.085"
    assert client.stops_set[0]["takeProfit"] == "0.956"


def test_bybit_sweep_skips_protected_full(tmp_path, monkeypatch):
    db = _FakeDB(tmp_path / "j.db")
    _insert(db, id=1, account_id="bybit_2", symbol="ETHUSDT", direction="short",
            position_size=0.06, stop_loss=1979.0, take_profit_1=1725.0,
            created_at="2026-07-01T00:00:00+00:00", status="open")
    client = _FakeBybit(
        positions={"ETHUSDT": {"size": "0.06", "stopLoss": "1979.0"}})
    _patch_accounts(monkeypatch, client)
    om._TICK_ACTIVE_CLOSE_SYMBOLS.clear()

    summary = om._check_broker_naked_bybit_positions(db)
    assert summary["broker_naked"] == 0 and summary["rearmed"] == 0
    assert client.stops_set == []


def test_bybit_sweep_skips_on_read_failure(tmp_path, monkeypatch):
    db = _FakeDB(tmp_path / "j.db")
    _insert(db, id=1, account_id="bybit_2", symbol="XRPUSDT", direction="short",
            position_size=100, stop_loss=1.085, take_profit_1=0.95,
            created_at="2026-07-01T00:00:00+00:00", status="open")
    client = _FakeBybit(raise_pos=True)  # unconfirmed → never re-arm
    _patch_accounts(monkeypatch, client)
    om._TICK_ACTIVE_CLOSE_SYMBOLS.clear()

    summary = om._check_broker_naked_bybit_positions(db)
    assert summary["broker_naked"] == 0 and summary["rearmed"] == 0
    assert client.stops_set == []


def test_bybit_sweep_skips_active_close(tmp_path, monkeypatch):
    db = _FakeDB(tmp_path / "j.db")
    _insert(db, id=1, account_id="bybit_2", symbol="XRPUSDT", direction="short",
            position_size=100, stop_loss=1.085, take_profit_1=0.95,
            created_at="2026-07-01T00:00:00+00:00", status="open")
    client = _FakeBybit(positions={"XRPUSDT": {"size": "100", "stopLoss": ""}},
                        stop_legs={"XRPUSDT": []})  # naked, WOULD re-arm
    _patch_accounts(monkeypatch, client)
    om._TICK_ACTIVE_CLOSE_SYMBOLS.clear()
    om._TICK_ACTIVE_CLOSE_SYMBOLS.add(("bybit_2", "XRPUSDT"))
    try:
        summary = om._check_broker_naked_bybit_positions(db)
    finally:
        om._TICK_ACTIVE_CLOSE_SYMBOLS.clear()
    assert summary["broker_naked"] == 0 and summary["rearmed"] == 0
    assert client.stops_set == []


def test_attempt_autoprotect_bybit_branch(monkeypatch):
    """The re-arm helper now has a Bybit branch (was a hard `return False`)."""
    client = _FakeBybit()
    monkeypatch.setattr("src.bot.data_loaders.list_accounts", lambda: [_ACC])
    monkeypatch.setattr(
        "src.units.accounts.clients.bybit_client_for", lambda acc: client)
    row = {"id": 9, "account_id": "bybit_2", "symbol": "XRPUSDT",
           "direction": "short", "position_size": 50}
    assert om._attempt_naked_autoprotect(row, 1.085, 0.95) is True
    assert client.stops_set[0]["tpslMode"] == "Full"
    assert client.stops_set[0]["positionIdx"] == 0
