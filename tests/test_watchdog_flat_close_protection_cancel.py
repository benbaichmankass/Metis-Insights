"""CA-A01-001 — a flat-FINALISE close must cancel the closing trade's OWN
resting protection, and nobody else's.

Measured 2026-09-27 on ``ib_paper``: the stuck-strategy watchdog finalised trade
5836 (``mes_trend_long_1d``, long 15 MES) as ``stuck_strategy_watchdog`` on
2026-09-19 and left its keyed group ``oca-protect-t5836`` — SELL STP 15 @7602.5
+ SELL LMT 15 @8390.5, GTC — resting on a FLAT MES book. Either leg filling
opens an unintended 15-lot short. ``_cancel_resting_protection_after_flat`` was
wired only into ``_reconcile_orphan_exchange_positions``.

Two layers are pinned:

* the watchdog → ``_cancel_resting_protection_after_flat(..., trade_id=<id>)``
  → the client's ``cancel_trade_protection(symbol, <id>)`` wiring, with a fake
  IB client; and
* ``IBClient.cancel_trade_protection`` itself cancelling ONLY
  ``oca-protect-t<id>`` while a sibling trade's keyed group survives.
"""
# collapsed-state: verified — these tests stub the `verified` envelope to pin
# the watchdog's cancel wiring; unverified / not_attempted are pinned in
# tests/test_ib_cancel_verification.py.
from __future__ import annotations

import json
import threading
import types
from unittest.mock import patch

import pytest

from src.runtime import order_monitor as om
from src.units.accounts import ib_client as ibc
from src.units.db.database import Database


# ── fakes ────────────────────────────────────────────────────────────────────

class FakeMonitorIBClient:
    """What ``_build_account_client`` hands the monitor for an IB account."""

    def __init__(self):
        self.group_cancels = []
        self.symbol_wide_cancels = []

    def cancel_trade_protection(self, symbol, oca_key):
        self.group_cancels.append((symbol, str(oca_key)))
        return {"retCode": 0, "retMsg": "OK",
                "result": {"oca_group": f"oca-protect-t{oca_key}",
                           "verify_state": "verified", "still_resting": []}}

    def cancel_resting_protection(self, symbol):  # must NOT be used here
        self.symbol_wide_cancels.append(symbol)
        return {"retCode": 0}


class FakeBybitClient:
    def __init__(self):
        self.cancelled = []

    def cancel_order(self, *, category, symbol, orderId):
        self.cancelled.append((category, symbol, orderId))
        return {"retCode": 0}


class _Order:
    def __init__(self, order_id, oca_group, order_type):
        self.orderId = order_id
        self.ocaGroup = oca_group
        self.orderType = order_type
        self.permId = 600000000 + order_id


class _Trade:
    def __init__(self, order_id, oca_group, order_type, symbol="MES"):
        self.order = _Order(order_id, oca_group, order_type)
        self.contract = types.SimpleNamespace(symbol=symbol)


class FakeIB:
    def __init__(self, trades):
        self._trades = list(trades)
        self.cancelled = []

    def cancelOrder(self, order):
        self.cancelled.append(order.orderId)
        self._trades = [t for t in self._trades if t.order.orderId != order.orderId]

    def openTrades(self):
        return list(self._trades)

    def reqAllOpenOrders(self):
        return list(self._trades)


# ── helpers ──────────────────────────────────────────────────────────────────

def _insert_trade(db, *, account_id, symbol, direction="long", size=15.0,
                  sl_order_id=None, tp_order_id=None):
    tid = db.insert_trade({
        "timestamp": "2026-09-19T00:00:00+00:00",
        "symbol": symbol,
        "direction": direction,
        "entry_price": 7700.0,
        "stop_loss": 7602.5,
        "take_profit_1": 8390.5,
        "position_size": size,
        "status": "open",
        "account_id": account_id,
        "strategy_name": "mes_trend_long_1d",
        "notes": json.dumps({"trade_id": "123456789"}),
    })
    if sl_order_id or tp_order_id:
        conn = db.connect()
        try:
            conn.execute("UPDATE trades SET sl_order_id=?, tp_order_id=? WHERE id=?",
                         (sl_order_id, tp_order_id, tid))
            conn.commit()
        finally:
            conn.close()
    return tid


def _insert_stuck_pkg(db, *, pkg_id, trade_id, symbol, age_minutes=45):
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO order_packages "
            "(order_package_id, strategy_name, symbol, direction, entry, sl, tp, "
            " confidence, status, linked_trade_id, meta, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?, datetime('now', ? || ' minutes'), "
            " datetime('now', ? || ' minutes'))",
            (pkg_id, "mes_trend_long_1d", symbol, "long", 7700.0, 7602.5, 8390.5,
             0.5, "open", trade_id, "{}", f"-{age_minutes}", f"-{age_minutes}"),
        )
        conn.commit()
    finally:
        conn.close()


def _status(db, tid):
    conn = db.connect()
    try:
        return conn.execute("SELECT status, exit_reason FROM trades WHERE id=?",
                            (tid,)).fetchone()
    finally:
        conn.close()


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "trade_journal.db"
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db_path))
    db = Database(db_path=str(db_path))
    cfgs = {
        "ib_paper": {"account_id": "ib_paper", "exchange": "interactive_brokers",
                     "mode": "live"},
        "bybit_2": {"account_id": "bybit_2", "exchange": "bybit",
                    "api_key_env": "BYBIT_KEY_2", "api_secret_env": None,
                    "mode": "live"},
    }
    monkeypatch.setattr("src.runtime.order_monitor._load_account_cfgs_for_reconcile",
                        lambda: cfgs)
    return db


@pytest.fixture(autouse=True)
def _confirm_window(monkeypatch, tmp_path):
    monkeypatch.setenv("RECONCILER_CLOSE_CONFIRM_SECONDS", "0")
    monkeypatch.setattr("src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
                        tmp_path / "pings")
    om._PENDING_WATCHDOG_FLAT_CONFIRM.clear()
    yield
    om._PENDING_WATCHDOG_FLAT_CONFIRM.clear()


def _run_watchdog_flat(db, fake_client, cfg):
    """Two flat observations (the 2-observation confirm) → finalise."""
    with patch("src.units.accounts.clients.account_open_positions",
               return_value=[]), \
         patch.object(om, "_recover_close_from_broker_pnl", return_value=None), \
         patch.object(om, "_build_account_client",
                      return_value=(fake_client, cfg)):
        om._watchdog_stuck_strategies(db)
        return om._watchdog_stuck_strategies(db)


# ── watchdog wiring ──────────────────────────────────────────────────────────

def test_watchdog_flat_close_cancels_the_closing_trades_keyed_group(tmp_db):
    """The CA-A01-001 shape: the watchdog finalises an IB trade on a flat book
    and must cancel ``oca-protect-t<that trade id>`` — scoped, never the
    symbol-wide sweep."""
    tid = _insert_trade(tmp_db, account_id="ib_paper", symbol="MES")
    _insert_stuck_pkg(tmp_db, pkg_id="pkg-5836", trade_id=tid, symbol="MES")
    fake = FakeMonitorIBClient()
    summary = _run_watchdog_flat(
        tmp_db, fake, {"account_id": "ib_paper", "exchange": "interactive_brokers",
                       "mode": "live"})

    assert summary["closed_local_unmatched"] == 1
    assert tuple(_status(tmp_db, tid)) == ("closed", "stuck_strategy_watchdog")
    assert fake.group_cancels == [("MES", str(tid))]
    assert fake.symbol_wide_cancels == []


def test_watchdog_does_not_cancel_a_sibling_trades_group(tmp_db):
    """A second, still-open trade on the same contract keeps its protection:
    the only cancel issued names the CLOSING trade's group."""
    sibling = _insert_trade(tmp_db, account_id="ib_paper", symbol="MES")
    closing = _insert_trade(tmp_db, account_id="ib_paper", symbol="MES")
    _insert_stuck_pkg(tmp_db, pkg_id="pkg-closing", trade_id=closing, symbol="MES")
    fake = FakeMonitorIBClient()
    _run_watchdog_flat(
        tmp_db, fake, {"account_id": "ib_paper", "exchange": "interactive_brokers",
                       "mode": "live"})

    assert fake.group_cancels == [("MES", str(closing))]
    assert ("MES", str(sibling)) not in fake.group_cancels
    assert _status(tmp_db, sibling)[0] == "open"


def test_watchdog_bybit_flat_close_cancels_tracked_legs(tmp_db):
    tid = _insert_trade(tmp_db, account_id="bybit_2", symbol="XRPUSDT",
                        size=46.3, sl_order_id="sl-abc", tp_order_id="tp-def")
    _insert_stuck_pkg(tmp_db, pkg_id="pkg-bybit", trade_id=tid, symbol="XRPUSDT")
    fake = FakeBybitClient()
    _run_watchdog_flat(tmp_db, fake, {"account_id": "bybit_2", "exchange": "bybit",
                                      "mode": "live", "market_type": "linear"})
    assert [c[2] for c in fake.cancelled] == ["sl-abc", "tp-def"]
    assert {c[1] for c in fake.cancelled} == {"XRPUSDT"}


def test_legacy_symbol_wide_sweep_unchanged_without_trade_id():
    fake = FakeMonitorIBClient()
    with patch.object(om, "_build_account_client",
                      return_value=(fake, {"exchange": "interactive_brokers"})):
        om._cancel_resting_protection_after_flat("ib_paper", "MES")
    assert fake.symbol_wide_cancels == ["MES"]
    assert fake.group_cancels == []


# ── IBClient.cancel_trade_protection scoping ─────────────────────────────────

@pytest.fixture
def ib_client(monkeypatch):
    c = ibc.IBClient.__new__(ibc.IBClient)
    c._usage_lock = threading.RLock()
    c.readonly = False
    c.symbol = "MES"
    monkeypatch.setattr(c, "_verify_cancel_effect",
                        lambda *a, **k: {"verify_state": "verified",
                                         "still_resting": [], "confirmed_gone": [],
                                         "account_wide_seen": None}, raising=False)
    monkeypatch.setattr(c, "_cancel_error_capture",
                        lambda ib: ({}, {}, lambda: None), raising=False)
    monkeypatch.setattr(c, "_log_cancel_verdict", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(c, "_leg_descriptor",
                        lambda trade: {"id": trade.order.orderId}, raising=False)
    return c


def test_ib_cancel_trade_protection_cancels_only_its_own_group(ib_client, monkeypatch):
    ib = FakeIB([
        _Trade(906, "oca-protect-t5836", "STP"),
        _Trade(907, "oca-protect-t5836", "LMT"),
        _Trade(950, "oca-protect-t5900", "STP"),   # sibling trade — must survive
        _Trade(951, "oca-protect-t5900", "LMT"),
        _Trade(954, "oca-protect-t5836", "LMT", symbol="MGC"),  # other symbol
    ])
    monkeypatch.setattr(ib_client, "connect", lambda: ib, raising=False)
    resp = ib_client.cancel_trade_protection("MES", 5836)

    assert resp["retCode"] == 0
    assert resp["result"]["oca_group"] == "oca-protect-t5836"
    assert sorted(ib.cancelled) == [906, 907]
    assert {t.order.orderId for t in ib.openTrades()} == {950, 951, 954}


def test_ib_cancel_trade_protection_refuses_without_a_key(ib_client, monkeypatch):
    ib = FakeIB([_Trade(906, "oca-protect-t5836", "STP")])
    monkeypatch.setattr(ib_client, "connect", lambda: ib, raising=False)
    for key in (None, "", "  "):
        assert ib_client.cancel_trade_protection("MES", key)["retCode"] == 1
    assert ib.cancelled == []


# ── Alpaca (FIX-CA-01 follow-up, PI-20260927-01CGDPN9-0002) ──────────────────
# AlpacaClient had no per-trade cancel, so an Alpaca row finalised flat by the
# watchdog left its OCO legs resting — and a resting SELL stop on a flat equity
# OPENS A SHORT. The row's own legs are identified by reducing side + unique
# size, exactly as the re-arm's scoped cancel does.

from src.units.accounts.alpaca_client import AlpacaClient  # noqa: E402


def _alpaca_venue(monkeypatch, legs):
    venue = AlpacaClient(api_key="k", api_secret="s", env="paper")
    deleted = []
    monkeypatch.setattr(venue, "_open_orders_for_symbol",
                        lambda sym: None if legs is None else list(legs))

    def _req(method, path, json_body=None):
        if method == "DELETE":
            deleted.append(path.rsplit("/", 1)[-1])
        return {"retCode": 0}
    monkeypatch.setattr(venue, "_request", _req)
    return venue, deleted


_SPY_LEGS = [
    {"id": "stp-own", "type": "stop", "side": "sell", "qty": "16"},
    {"id": "lmt-own", "type": "limit", "side": "sell", "qty": "16"},
    {"id": "stp-sib", "type": "stop", "side": "sell", "qty": "9"},
    {"id": "entry", "type": "limit", "side": "buy", "qty": "16"},
]
_ALPACA_CFG = {"account_id": "alpaca_live", "exchange": "alpaca", "mode": "live"}


def test_watchdog_alpaca_flat_close_cancels_only_the_rows_own_legs(tmp_db, monkeypatch):
    monkeypatch.setattr("src.runtime.order_monitor._load_account_cfgs_for_reconcile",
                        lambda: {"alpaca_live": dict(_ALPACA_CFG)})
    tid = _insert_trade(tmp_db, account_id="alpaca_live", symbol="SPY", size=16)
    _insert_stuck_pkg(tmp_db, pkg_id="pkg-spy", trade_id=tid, symbol="SPY")
    venue, deleted = _alpaca_venue(monkeypatch, _SPY_LEGS)
    _run_watchdog_flat(tmp_db, venue, _ALPACA_CFG)

    assert _status(tmp_db, tid)[0] == "closed"
    assert sorted(deleted) == ["lmt-own", "stp-own"]   # never the sibling or entry


def test_alpaca_same_size_sibling_cancels_nothing(tmp_db, monkeypatch):
    _insert_trade(tmp_db, account_id="alpaca_live", symbol="SPY", size=16)  # open sibling
    tid = _insert_trade(tmp_db, account_id="alpaca_live", symbol="SPY", size=16)
    conn = tmp_db.connect()  # _insert_trade leaves is_backtest at its default
    try:
        conn.execute("UPDATE trades SET is_backtest=0")
        conn.commit()
    finally:
        conn.close()
    venue, deleted = _alpaca_venue(monkeypatch, _SPY_LEGS)
    with patch.object(om, "_build_account_client",
                      return_value=(venue, _ALPACA_CFG)):
        om._cancel_closed_row_protection(
            {"id": tid, "account_id": "alpaca_live", "symbol": "SPY",
             "position_size": 16, "direction": "long"}, tmp_db)
    assert deleted == []


def test_alpaca_unreadable_siblings_or_orders_cancel_nothing(monkeypatch):
    venue, deleted = _alpaca_venue(monkeypatch, _SPY_LEGS)
    r = venue.cancel_row_protection("SPY", qty=16, direction="long", sibling_qtys=None)
    assert deleted == [] and r["retCode"] == 0
    venue2, deleted2 = _alpaca_venue(monkeypatch, None)
    venue2.cancel_row_protection("SPY", qty=16, direction="long", sibling_qtys=[])
    assert deleted2 == []
