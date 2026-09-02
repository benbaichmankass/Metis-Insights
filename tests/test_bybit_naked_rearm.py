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

import json
import sqlite3

import pytest

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
# 2026-07-30: this reader returns COVERAGE, not a boolean. It used to return
# ``(size, any(<an SL leg exists>))``, which under BYBIT_TPSL_MODE=partial let a
# netted position with only SOME of its per-trade qty-scoped legs read as fully
# PROTECTED — so the sweep skipped a partially-unprotected real position. Tests
# below pin the quantity semantics.
def test_position_protection_full_mode_stop():
    c = _FakeBybit(positions={"XRPUSDT": {"size": "157.7", "stopLoss": "1.085"}})
    st = om._bybit_position_protection(c, "linear", "XRPUSDT")
    # A Full-mode position stop genuinely covers the WHOLE net position.
    assert st["size"] == 157.7
    assert st["covered_qty"] == 157.7
    assert st["source"] == "full_position_stop"


def test_position_protection_partial_leg_covering_whole_size():
    c = _FakeBybit(
        positions={"XRPUSDT": {"size": "15.8", "stopLoss": ""}},
        stop_legs={"XRPUSDT": [
            {"stopOrderType": "PartialStopLoss", "qty": "15.8", "orderId": "a"},
        ]},
    )
    st = om._bybit_position_protection(c, "linear", "XRPUSDT")
    assert st["size"] == 15.8 and st["covered_qty"] == 15.8
    assert st["source"] == "partial_sl_legs" and st["sl_leg_ids"] == {"a"}


def test_position_protection_partial_legs_SUM_toward_coverage():
    """Two qty-scoped legs on one netted position add up."""
    c = _FakeBybit(
        positions={"XRPUSDT": {"size": "10", "stopLoss": ""}},
        stop_legs={"XRPUSDT": [
            {"stopOrderType": "PartialStopLoss", "qty": "4", "orderId": "a"},
            {"stopOrderType": "PartialStopLoss", "qty": "6", "orderId": "b"},
        ]},
    )
    st = om._bybit_position_protection(c, "linear", "XRPUSDT")
    assert st["covered_qty"] == 10.0 and st["sl_leg_ids"] == {"a", "b"}


def test_position_protection_PARTIAL_COVERAGE_is_visible():
    """THE regression this change exists to prevent.

    One surviving 4-qty leg on a 10-qty netted position: the old ``any()``
    boolean called this PROTECTED. Coverage must show the 6-qty hole.
    """
    c = _FakeBybit(
        positions={"BNBUSDT": {"size": "10", "stopLoss": ""}},
        stop_legs={"BNBUSDT": [
            {"stopOrderType": "PartialStopLoss", "qty": "4", "orderId": "a"},
        ]},
    )
    st = om._bybit_position_protection(c, "linear", "BNBUSDT")
    assert st["size"] == 10.0
    assert st["covered_qty"] == 4.0          # NOT 10 — the hole is measured
    assert st["size"] - st["covered_qty"] == 6.0


def test_position_protection_naked():
    """Live size, no Full stop, no SL leg (only a TP leg) → zero coverage."""
    c = _FakeBybit(
        positions={"XRPUSDT": {"size": "157.7", "stopLoss": "0"}},
        stop_legs={"XRPUSDT": [{"stopOrderType": "PartialTakeProfit",
                                "qty": "157.7"}]},
    )
    st = om._bybit_position_protection(c, "linear", "XRPUSDT")
    # A resting TAKE-PROFIT leg is not protection.
    assert st["size"] == 157.7 and st["covered_qty"] == 0.0


def test_position_protection_unparseable_leg_qty_is_flagged():
    """An SL leg with no readable qty must not be counted as coverage."""
    c = _FakeBybit(
        positions={"XRPUSDT": {"size": "10", "stopLoss": ""}},
        stop_legs={"XRPUSDT": [{"stopOrderType": "PartialStopLoss",
                                "orderId": "a"}]},  # no qty
    )
    st = om._bybit_position_protection(c, "linear", "XRPUSDT")
    assert st["covered_qty"] == 0.0
    assert st["unknown_qty_sl_legs"] == 1


def test_position_protection_flat():
    st = om._bybit_position_protection(_FakeBybit(positions={}), "linear", "X")
    assert st["size"] == 0.0 and st["source"] == "flat"
    st2 = om._bybit_position_protection(
        _FakeBybit(positions={"X": {"size": "0", "stopLoss": ""}}), "linear", "X")
    assert st2["size"] == 0.0 and st2["source"] == "flat"


def test_sl_leg_qty_parser():
    assert om._bybit_sl_leg_qty({"qty": "4"}) == 4.0
    assert om._bybit_sl_leg_qty({"triggerQty": "2.5"}) == 2.5
    assert om._bybit_sl_leg_qty({}) is None
    assert om._bybit_sl_leg_qty({"qty": "0"}) is None
    assert om._bybit_sl_leg_qty({"qty": "nope"}) is None


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
        positions={"XRPUSDT": {"size": "157.7", "stopLoss": "",
                               "side": "Sell", "positionIdx": 0}},
        stop_legs={"XRPUSDT": []},  # NO resting leg → naked
    )
    _patch_accounts(monkeypatch, client)
    om._TICK_ACTIVE_CLOSE_AT.clear()

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
    om._TICK_ACTIVE_CLOSE_AT.clear()

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
    om._TICK_ACTIVE_CLOSE_AT.clear()

    summary = om._check_broker_naked_bybit_positions(db)
    assert summary["broker_naked"] == 0 and summary["rearmed"] == 0
    assert client.stops_set == []


def test_bybit_sweep_skips_active_close(tmp_path, monkeypatch):
    db = _FakeDB(tmp_path / "j.db")
    _insert(db, id=1, account_id="bybit_2", symbol="XRPUSDT", direction="short",
            position_size=100, stop_loss=1.085, take_profit_1=0.95,
            created_at="2026-07-01T00:00:00+00:00", status="open")
    client = _FakeBybit(
        positions={"XRPUSDT": {"size": "100", "stopLoss": "",
                               "side": "Sell", "positionIdx": 0}},
        stop_legs={"XRPUSDT": []})  # naked, WOULD re-arm
    _patch_accounts(monkeypatch, client)
    om._TICK_ACTIVE_CLOSE_AT.clear()
    om.mark_active_close("bybit_2", "XRPUSDT")
    try:
        summary = om._check_broker_naked_bybit_positions(db)
    finally:
        om._TICK_ACTIVE_CLOSE_AT.clear()
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


# ---------------------------------------------- 2026-07-30 anomaly detection
# Both of these were found LIVE by the bybit-bracket-audit action and were
# invisible to every pre-existing check. They are detect-only in the sweep
# (loud ERROR + a counter) — remediation is deliberately separate.
def test_sweep_flags_leg_OVER_accumulation(tmp_path, monkeypatch, caplog):
    """bybit_1 XRPUSDT, live: position 32557.2, resting SL legs 144789.3 (444.7%).

    Legs piled up instead of tracking the position. A trip would OVER-close and
    strand the rest. Coverage alone reads "protected", so this needs its own
    signal.
    """
    db = _FakeDB(tmp_path / "j.db")
    _insert(db, id=1, account_id="bybit_2", symbol="XRPUSDT", direction="short",
            position_size=32557.2, stop_loss=1.094, take_profit_1=1.054,
            created_at="2026-07-01T00:00:00+00:00", status="open")
    # ⚠️ `side` / `positionIdx` are on this fixture DELIBERATELY (added
    # 2026-09-02). A real Bybit position row always carries both, and the
    # over-cover page now BRANCHES on them to say which book the legs act on.
    # Without them the fixture would exercise the `position_side_unreadable`
    # path — a schema production does not have, the failure shape CLAUDE.md
    # records for the pairs `order_packages` tests. A SHORT is protected by
    # BUY reduce-only legs, so that is what the legs carry here.
    client = _FakeBybit(
        positions={"XRPUSDT": {"size": "32557.2", "stopLoss": "",
                               "side": "Sell", "positionIdx": 0}},
        stop_legs={"XRPUSDT": [
            {"stopOrderType": "PartialStopLoss", "side": "Buy",
             "qty": "58686.8", "orderId": "a"},
            {"stopOrderType": "PartialStopLoss", "side": "Buy",
             "qty": "86102.5", "orderId": "b"},
        ]},
    )
    _patch_accounts(monkeypatch, client)
    om._TICK_ACTIVE_CLOSE_AT.clear()

    with caplog.at_level("ERROR"):
        summary = om._check_broker_naked_bybit_positions(db)
    assert summary["over_covered"] == 1
    # Over-covered is still "covered" — no re-arm, nothing placed.
    assert summary["rearmed"] == 0 and summary["topped_up"] == 0
    assert client.stops_set == []
    assert "LEG OVER-ACCUMULATION" in caplog.text
    # ...and it is named as the SAME-BOOK condition, not the other-book one
    # the 2026-09-02 BTCUSDT page was really describing. This is the CONTROL
    # for that fix: a genuine same-side pile-up must still read as one.
    assert "SAME-BOOK LEG OVER-ACCUMULATION" in caplog.text
    assert "OPPOSITE book" not in caplog.text
    assert summary["over_cover_other_book"] == 0
    assert summary["over_cover_split_ungraded"] == 0


def test_sweep_flags_journal_vs_broker_qty_divergence(tmp_path, monkeypatch, caplog):
    """bybit_1 BTCUSDT, live: journal rows sum 1.553 vs exchange size 0.01.

    The 1.543 row is a phantom — the broker has no such position — yet analytics
    and risk sizing both read the journal.
    """
    db = _FakeDB(tmp_path / "j.db")
    _insert(db, id=1, account_id="bybit_2", symbol="BTCUSDT", direction="long",
            position_size=0.01, stop_loss=31757.85, take_profit_1=127031.4,
            created_at="2026-07-01T00:00:00+00:00", status="open")
    _insert(db, id=2, account_id="bybit_2", symbol="BTCUSDT", direction="long",
            position_size=1.543, stop_loss=64110.32, take_profit_1=65528.26,
            created_at="2026-07-01T00:00:00+00:00", status="open")
    # ⚠️ `side` / `positionIdx` on the position and `side` on each leg are on
    # this fixture DELIBERATELY (2026-09-02, extending #10739's precedent to
    # the RE-ARM path). A real Bybit position row always carries both and
    # every order row carries a side; the sweep now BRANCHES on them to grade
    # coverage of the book it is actually protecting. Without them the fixture
    # would exercise `position_side_unreadable` — a schema production does not
    # have, the failure shape CLAUDE.md records for the pairs `order_packages`
    # tests. A LONG is protected by SELL reduce-only legs and a SHORT by BUY.
    client = _FakeBybit(
        positions={"BTCUSDT": {"size": "0.01", "stopLoss": "",
                               "side": "Buy", "positionIdx": 0}},
        stop_legs={"BTCUSDT": [
            {"stopOrderType": "PartialStopLoss", "side": "Sell",
             "qty": "0.01", "orderId": "a"},
        ]},
    )
    _patch_accounts(monkeypatch, client)
    om._TICK_ACTIVE_CLOSE_AT.clear()

    with caplog.at_level("ERROR"):
        summary = om._check_broker_naked_bybit_positions(db)
    # Flagged exactly ONCE for the symbol even though 2 rows reference it.
    assert summary["journal_qty_divergent"] == 1
    assert "JOURNAL/BROKER QTY DIVERGENCE" in caplog.text
    # The real 0.01 position IS covered, so no re-arm fires.
    assert summary["rearmed"] == 0 and client.stops_set == []


def test_sweep_tops_up_a_real_partial_gap(tmp_path, monkeypatch):
    """A genuine coverage hole: 10 qty position, one surviving 4-qty leg.

    The old any()-boolean skipped this entirely. Now it top-ups a qty-scoped
    Partial SL leg for exactly the uncovered 6.
    """
    db = _FakeDB(tmp_path / "j.db")
    _insert(db, id=1, account_id="bybit_2", symbol="BNBUSDT", direction="short",
            position_size=10.0, stop_loss=1149.8, take_profit_1=287.5,
            created_at="2026-07-01T00:00:00+00:00", status="open")
    # ⚠️ `side` / `positionIdx` on the position and `side` on each leg are on
    # this fixture DELIBERATELY (2026-09-02, extending #10739's precedent to
    # the RE-ARM path). A real Bybit position row always carries both and
    # every order row carries a side; the sweep now BRANCHES on them to grade
    # coverage of the book it is actually protecting. Without them the fixture
    # would exercise `position_side_unreadable` — a schema production does not
    # have, the failure shape CLAUDE.md records for the pairs `order_packages`
    # tests. A LONG is protected by SELL reduce-only legs and a SHORT by BUY.
    client = _FakeBybit(
        positions={"BNBUSDT": {"size": "10", "stopLoss": "",
                               "side": "Sell", "positionIdx": 0}},
        stop_legs={"BNBUSDT": [
            {"stopOrderType": "PartialStopLoss", "side": "Buy",
             "qty": "4", "orderId": "a"},
        ]},
    )
    _patch_accounts(monkeypatch, client)
    om._TICK_ACTIVE_CLOSE_AT.clear()

    calls = {}

    def _fake_modify(cl, acc, *, symbol, sl, tp, qty, sl_order_id, tp_order_id):
        calls.update(symbol=symbol, sl=sl, tp=tp, qty=qty,
                     sl_order_id=sl_order_id)
        return {"ok": True, "error": None}

    monkeypatch.setattr(
        "src.units.accounts.execute.modify_open_order", _fake_modify)

    summary = om._check_broker_naked_bybit_positions(db)
    assert summary["partially_naked"] == 1
    assert summary["topped_up"] == 1
    # Exactly the uncovered qty, and NO tracked leg id (so execute.py takes its
    # add-a-leg Partial branch rather than amending someone else's leg).
    assert calls["qty"] == 6.0
    assert calls["sl_order_id"] is None
    assert calls["tp"] is None          # protection only; don't burn a TP leg
    assert calls["symbol"] == "BNBUSDT"
    # The Full-mode whole-position re-arm must NOT also fire.
    assert summary["rearmed"] == 0 and client.stops_set == []


# ------------------------------------------------- journal/broker divergence
# 2026-08-06 (netting P0, decision #1). The `journal_qty_divergent` detector
# shipped in PR #8000 sat AFTER the loop's `if size <= 0: continue`, and
# `_bybit_position_protection` returns size 0.0 when Bybit reports no position
# — so the MAXIMAL-divergence case (exchange flat, journal rows still open;
# the W1 155x finding) was skipped before anything was compared and reported
# clean. It reported clean because it never looked: CLAUDE.md diagnostic
# provenance, sub-class C. It was also keyed (account, symbol) with no
# direction, so an opposite-side phantom could cancel out against a genuine
# same-side excess.
def test_norm_position_side():
    assert om._norm_position_side("Buy") == "long"
    assert om._norm_position_side("long") == "long"
    assert om._norm_position_side("Sell") == "short"
    assert om._norm_position_side("SHORT") == "short"
    # Unknown/flat is ungradeable — never silently a match.
    assert om._norm_position_side(None) == ""
    assert om._norm_position_side("") == ""
    assert om._norm_position_side("None") == ""


def test_divergence_fires_when_exchange_is_FLAT(tmp_path, monkeypatch):
    """The regression: journal open, exchange flat → every row is a phantom.

    This is the case the detector exists to catch and the one it used to miss.
    """
    db = _FakeDB(tmp_path / "j.db")
    _insert(db, id=1, account_id="bybit_2", symbol="BTCUSDT", direction="long",
            position_size=1.553, stop_loss=90000.0, take_profit_1=99000.0,
            created_at="2026-01-01T00:00:00+00:00", status="open")
    client = _FakeBybit(positions={})           # exchange holds NOTHING
    _patch_accounts(monkeypatch, client)

    summary = om._check_broker_naked_bybit_positions(db)
    assert summary["journal_qty_divergent"] == 1
    # Flat means nothing to protect — it must not try to re-arm on air.
    assert summary["rearmed"] == 0 and client.stops_set == []


def test_divergence_fires_on_OPPOSITE_side_rows(tmp_path, monkeypatch):
    """A short row against a live LONG position is a phantom by construction.

    Sized so the OLD symbol-pooled key reads exactly CLEAN: journal long 60 +
    short 40 = 100 = the netted size, so the pooled sum matches and no
    divergence is reported — while the 40 short is a phantom (one-way netting
    means only one side can be live) and the long side is 40 SHORT of the
    position it supposedly backs. Two errors cancelling is precisely what
    keying by symbol alone hides, so this test discriminates the direction fix
    rather than merely exercising it.
    """
    db = _FakeDB(tmp_path / "j.db")
    _insert(db, id=1, account_id="bybit_2", symbol="XRPUSDT", direction="long",
            position_size=60.0, stop_loss=1.0, take_profit_1=1.2,
            created_at="2026-01-01T00:00:00+00:00", status="open")
    _insert(db, id=2, account_id="bybit_2", symbol="XRPUSDT", direction="short",
            position_size=40.0, stop_loss=1.2, take_profit_1=1.0,
            created_at="2026-01-01T00:00:00+00:00", status="open")
    # Exchange: long 100. Pooled journal = 100 → old code sees no divergence.
    client = _FakeBybit(
        positions={"XRPUSDT": {"size": "100", "side": "Buy",
                               "stopLoss": "1.0"}})
    _patch_accounts(monkeypatch, client)

    summary = om._check_broker_naked_bybit_positions(db)
    assert summary["journal_qty_divergent"] == 1


def test_no_divergence_when_journal_matches_broker(tmp_path, monkeypatch):
    """The negative control — a correct book must stay quiet."""
    db = _FakeDB(tmp_path / "j.db")
    _insert(db, id=1, account_id="bybit_2", symbol="XRPUSDT", direction="long",
            position_size=60.0, stop_loss=1.0, take_profit_1=1.2,
            created_at="2026-01-01T00:00:00+00:00", status="open")
    _insert(db, id=2, account_id="bybit_2", symbol="XRPUSDT", direction="long",
            position_size=40.0, stop_loss=1.0, take_profit_1=1.2,
            created_at="2026-01-01T00:00:00+00:00", status="open")
    client = _FakeBybit(
        positions={"XRPUSDT": {"size": "100", "side": "Buy",
                               "stopLoss": "1.0"}})
    _patch_accounts(monkeypatch, client)

    summary = om._check_broker_naked_bybit_positions(db)
    assert summary["journal_qty_divergent"] == 0


def test_overcover_detector_still_fires_alongside_divergence(tmp_path, monkeypatch):
    """Guard-set separation: hoisting divergence must not retire this detector.

    The divergence check adds the symbol to `anomaly_checked`; if the leg
    check shared that set it would become unreachable — silently retiring a
    live detector, the same never-looked failure one level up.
    """
    db = _FakeDB(tmp_path / "j.db")
    # Journal 200 vs exchange 100 → divergence AND over-accumulated legs.
    _insert(db, id=1, account_id="bybit_2", symbol="XRPUSDT", direction="long",
            position_size=200.0, stop_loss=1.0, take_profit_1=1.2,
            created_at="2026-01-01T00:00:00+00:00", status="open")
    client = _FakeBybit(
        positions={"XRPUSDT": {"size": "100", "side": "Buy", "stopLoss": "",
                               "positionIdx": 0}},
        stop_legs={"XRPUSDT": [
            {"stopOrderType": "StopLoss", "side": "Sell",
             "orderId": "a", "qty": "300"},
            {"stopOrderType": "StopLoss", "side": "Sell",
             "orderId": "b", "qty": "300"},
        ]},
    )
    _patch_accounts(monkeypatch, client)

    summary = om._check_broker_naked_bybit_positions(db)
    assert summary["journal_qty_divergent"] == 1
    assert summary["over_covered"] == 1


def test_over_accumulation_PAGES_the_operator_not_just_the_journal(
        tmp_path, monkeypatch, caplog):
    """The detection was correct and INVISIBLE for four weeks.

    `summary["over_covered"]` and a `logger.error` reach the systemd journal and
    nothing else — not `outcomes.jsonl`, so not Telegram, not
    `/api/bot/notifications`, not `/api/bot/logs?level=error`. Measured
    2026-08-26 over the 401-row operator ERROR+ feed spanning
    2026-08-20T09:42Z-2026-08-26T00:33Z: **zero** Bybit rows, against three
    `ib_stop_over_cover` rows in the same feed — a positive control proving the
    probe finds a positive, so the silence is the Bybit page's absence and not
    an empty feed.

    ⚠️ THIS TEST EXISTS BECAUSE THE UNIT TESTS DID NOT COVER THE WIRING.
    `tests/test_bybit_over_cover_alert.py` calls the pager directly, so deleting
    the sweep's call to it left all ten of those green — a mechanism that exists
    and is never exercised, which is the exact class this whole change is about.
    """
    db = _FakeDB(tmp_path / "j.db")
    _insert(db, id=1, account_id="bybit_2", symbol="XRPUSDT", direction="short",
            position_size=32557.2, stop_loss=1.094, take_profit_1=1.054,
            created_at="2026-07-01T00:00:00+00:00", status="open")
    # ⚠️ `side` / `positionIdx` on the position and `side` on each leg are on
    # this fixture DELIBERATELY (2026-09-02, extending #10739's precedent to
    # the RE-ARM path). A real Bybit position row always carries both and
    # every order row carries a side; the sweep now BRANCHES on them to grade
    # coverage of the book it is actually protecting. Without them the fixture
    # would exercise `position_side_unreadable` — a schema production does not
    # have, the failure shape CLAUDE.md records for the pairs `order_packages`
    # tests. A LONG is protected by SELL reduce-only legs and a SHORT by BUY.
    client = _FakeBybit(
        positions={"XRPUSDT": {"size": "32557.2", "stopLoss": "",
                               "side": "Sell", "positionIdx": 0}},
        stop_legs={"XRPUSDT": [
            {"stopOrderType": "PartialStopLoss", "side": "Buy",
             "qty": "58686.8", "orderId": "a"},
            {"stopOrderType": "PartialStopLoss", "side": "Buy",
             "qty": "86102.5", "orderId": "b"},
        ]},
    )
    _patch_accounts(monkeypatch, client)
    om._TICK_ACTIVE_CLOSE_AT.clear()

    # Durable latch into tmp_path so a real state file cannot suppress this.
    monkeypatch.setattr(om, "_alert_state_path",
                        lambda kind: tmp_path / f"{kind}_alert_state.json")
    sent = []
    import src.runtime.outcomes as outcomes
    monkeypatch.setattr(outcomes, "report", lambda *a, **k: sent.append((a, k)))

    with caplog.at_level("ERROR"):
        summary = om._check_broker_naked_bybit_positions(db)

    assert summary["over_covered"] == 1
    assert summary["over_cover_alerted"] == 1, (
        "the sweep must CALL the pager, not merely count the condition")
    assert [a[0] for a, _ in sent] == ["bybit_over_cover"]
    kwargs = sent[0][1]
    assert kwargs["account_id"] == "bybit_2" and kwargs["symbol"] == "XRPUSDT"
    assert kwargs["sl_leg_count"] == 2
    # Still detect-only: it pages, it cancels nothing.
    assert summary["rearmed"] == 0 and summary["topped_up"] == 0
    assert client.stops_set == []


def test_a_healthy_symbol_pages_nothing(tmp_path, monkeypatch):
    """The denominator for the test above: the sweep must not page on a
    correctly-covered position, or the page is noise rather than a signal."""
    db = _FakeDB(tmp_path / "j.db")
    _insert(db, id=1, account_id="bybit_2", symbol="XRPUSDT", direction="short",
            position_size=100.0, stop_loss=1.094, take_profit_1=1.054,
            created_at="2026-07-01T00:00:00+00:00", status="open")
    # ⚠️ `side` / `positionIdx` on the position and `side` on each leg are on
    # this fixture DELIBERATELY (2026-09-02, extending #10739's precedent to
    # the RE-ARM path). A real Bybit position row always carries both and
    # every order row carries a side; the sweep now BRANCHES on them to grade
    # coverage of the book it is actually protecting. Without them the fixture
    # would exercise `position_side_unreadable` — a schema production does not
    # have, the failure shape CLAUDE.md records for the pairs `order_packages`
    # tests. A LONG is protected by SELL reduce-only legs and a SHORT by BUY.
    client = _FakeBybit(
        positions={"XRPUSDT": {"size": "100.0", "stopLoss": "",
                               "side": "Sell", "positionIdx": 0}},
        stop_legs={"XRPUSDT": [
            {"stopOrderType": "PartialStopLoss", "side": "Buy",
             "qty": "100.0", "orderId": "a"}]},
    )
    _patch_accounts(monkeypatch, client)
    om._TICK_ACTIVE_CLOSE_AT.clear()
    monkeypatch.setattr(om, "_alert_state_path",
                        lambda kind: tmp_path / f"{kind}_alert_state.json")
    sent = []
    import src.runtime.outcomes as outcomes
    monkeypatch.setattr(outcomes, "report", lambda *a, **k: sent.append((a, k)))

    summary = om._check_broker_naked_bybit_positions(db)
    assert summary["over_covered"] == 0
    assert summary["over_cover_alerted"] == 0
    assert sent == []


def test_the_page_receives_the_COMBINED_leg_count_not_just_the_stops(
        tmp_path, monkeypatch):
    """Bybit's cap is 20 COMBINED TP+SL legs, so the sweep must hand the page
    the combined count.

    ⚠️ THIS TEST EXISTS BECAUSE THE WIRING WAS UNCOVERED, for the second time in
    this file: deleting `protective_leg_count=state.get(...)` from the call site
    left all 37 pager+sweep tests green. The pager's own tests pass the value
    directly, so only a sweep-level test can prove the sweep supplies it.

    The `StopOrder` filter returns PartialTakeProfit rows alongside
    PartialStopLoss — which is why counting them costs no extra broker call, and
    why they were being silently discarded before anything counted them.
    """
    db = _FakeDB(tmp_path / "j.db")
    _insert(db, id=1, account_id="bybit_2", symbol="XRPUSDT", direction="short",
            position_size=32557.2, stop_loss=1.094, take_profit_1=1.054,
            created_at="2026-07-01T00:00:00+00:00", status="open")
    # ⚠️ `side` / `positionIdx` on the position and `side` on each leg are on
    # this fixture DELIBERATELY (2026-09-02, extending #10739's precedent to
    # the RE-ARM path). A real Bybit position row always carries both and
    # every order row carries a side; the sweep now BRANCHES on them to grade
    # coverage of the book it is actually protecting. Without them the fixture
    # would exercise `position_side_unreadable` — a schema production does not
    # have, the failure shape CLAUDE.md records for the pairs `order_packages`
    # tests. A LONG is protected by SELL reduce-only legs and a SHORT by BUY.
    client = _FakeBybit(
        positions={"XRPUSDT": {"size": "32557.2", "stopLoss": "",
                               "side": "Sell", "positionIdx": 0}},
        stop_legs={"XRPUSDT": [
            {"stopOrderType": "PartialStopLoss", "side": "Buy",
             "qty": "58686.8", "orderId": "a"},
            {"stopOrderType": "PartialStopLoss", "side": "Buy",
             "qty": "86102.5", "orderId": "b"},
            # The target legs occupy the SAME cap and were previously discarded.
            {"stopOrderType": "PartialTakeProfit", "side": "Buy",
             "qty": "58686.8", "orderId": "c"},
            {"stopOrderType": "PartialTakeProfit", "side": "Buy",
             "qty": "86102.5", "orderId": "d"},
            {"stopOrderType": "PartialTakeProfit", "side": "Buy",
             "qty": "100.0", "orderId": "e"},
        ]},
    )
    _patch_accounts(monkeypatch, client)
    om._TICK_ACTIVE_CLOSE_AT.clear()
    monkeypatch.setattr(om, "_alert_state_path",
                        lambda kind: tmp_path / f"{kind}_alert_state.json")
    sent = []
    import src.runtime.outcomes as outcomes
    monkeypatch.setattr(outcomes, "report", lambda *a, **k: sent.append((a, k)))

    summary = om._check_broker_naked_bybit_positions(db)

    assert summary["over_cover_alerted"] == 1
    kwargs = sent[0][1]
    assert kwargs["sl_leg_count"] == 2, "coverage stays an SL-only question"
    assert kwargs["protective_leg_count"] == 5, (
        "the sweep must hand the page the COMBINED TP+SL count")
    assert kwargs["leg_cap_headroom"] == 15          # 20 - 5, not 20 - 2
    assert "COMBINED" in kwargs["reason"]


# ==========================================================================
# 2026-09-02 — THE RE-ARM DECISION MUST GRADE THE BOOK IT IS PROTECTING
# ==========================================================================
# #10739 fixed what the over-cover PAGE says and deliberately left the ORDER
# PATH alone, naming this as the separate Tier-2 sibling: `covered_qty` sums
# every resting SL leg on the symbol regardless of which book it can reduce,
# and the sweep decides `if covered + eps >= size: continue`. Since HEDGE mode
# was armed on bybit_1/bybit_2 (2026-08-30) one symbol can carry legs for TWO
# books in that one sum, so an OTHER-book leg can push the total past `size` on
# a position whose OWN stop is gone — and the sweep skips a genuinely naked
# position as "fully covered".
#
# ⚠️ n = 1, CONSTRUCTED. The shape below is the live bybit_1/BTCUSDT read of
# 2026-09-02T03:30:33Z (`/api/diag/bybit_open_orders`, trader git_sha
# 68e73de8) with the position's OWN leg removed. **NO LIVE INSTANCE OF THE
# MASKING HAS BEEN OBSERVED** — this is a construction over a real venue
# reading, not a sighting, and these tests do not upgrade it to one.
#
# Four controls, because a change tested only on the case it was written for
# has been shown to FIRE, not to DISCRIMINATE:
#   1. the fix fires        — own stop gone + other-book leg  -> RE-ARMED
#   2. the mirror           — covered by its OWN legs         -> still skipped
#   3. the union is intact  — the other-book condition still trips over-cover
#   4. we-did-not-look      — an ungradeable side re-arms NEITHER way
_LIVE_BTC_POS = {"symbol": "BTCUSDT", "size": "0.018", "stopLoss": "",
                 "side": "Buy", "positionIdx": 1}
# Buy reduce-only: can only shrink a SHORT, so it is NOT this long's protection.
_OTHER_BOOK_SL = {"stopOrderType": "PartialStopLoss", "side": "Buy",
                  "qty": "0.46", "orderId": "other-book"}
# Sell reduce-only: this long's own stop.
_OWN_SL = {"stopOrderType": "PartialStopLoss", "side": "Sell",
           "qty": "0.018", "orderId": "own"}


def _btc_db(tmp_path, size=0.018):
    db = _FakeDB(tmp_path / "j.db")
    _insert(db, id=1, account_id="bybit_2", symbol="BTCUSDT", direction="long",
            position_size=size, stop_loss=38698.6, take_profit_1=44000.0,
            created_at="2026-07-01T00:00:00+00:00", status="open")
    return db


def _run(db, client, monkeypatch, tmp_path, *, mode=None, accounts=None):
    """Run the sweep. **Defaults to the SHIPPED, UNARMED state**, deliberately.

    ``BYBIT_GRADED_COVERAGE_MODE`` defaults to ``annotate`` and
    ``BYBIT_GRADED_COVERAGE_ACCOUNTS`` ships EMPTY, which for this knob means
    NONE — so out of the box the graded figure is measured and does not bind.
    A helper that armed by default would make every test below read as proof of
    live behaviour that is not, in fact, live; arming is explicit and per-test.

    Both keys are cleared when not passed, so an ambient value in the runner's
    environment can never quietly arm a test that means to assert the default.
    """
    _patch_accounts(monkeypatch, client)
    om._TICK_ACTIVE_CLOSE_AT.clear()
    monkeypatch.setattr(om, "_alert_state_path",
                        lambda kind: tmp_path / f"{kind}_alert_state.json")
    for key, val in (("BYBIT_GRADED_COVERAGE_MODE", mode),
                     ("BYBIT_GRADED_COVERAGE_ACCOUNTS", accounts)):
        if val is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, val)
    monkeypatch.setattr(
        "src.utils.paths.runtime_logs_dir", lambda: tmp_path, raising=False)
    return om._check_broker_naked_bybit_positions(db)


#: The staged arm the operator chose (2026-09-02): mode `apply`, allowlist
#: naming exactly the account under test. Tests that assert BINDING behaviour
#: pass this; tests that assert the default must not.
_ARMED = {"mode": "apply", "accounts": "bybit_2"}


# ---- CONTROL 1: the fix FIRES -------------------------------------------
def test_naked_long_masked_by_an_other_book_leg_is_now_REARMED(
        tmp_path, monkeypatch):
    """THE DEFECT. Position Buy 0.018 with its own stop GONE; a Buy 0.46 leg
    rests on the short book. Side-blind `covered_qty` reads 0.46 >= 0.018, so
    before this change the sweep skipped a naked long as fully covered."""
    db = _btc_db(tmp_path)
    client = _FakeBybit(positions={"BTCUSDT": dict(_LIVE_BTC_POS)},
                        stop_legs={"BTCUSDT": [dict(_OTHER_BOOK_SL)]})
    summary = _run(db, client, monkeypatch, tmp_path, **_ARMED)

    # The side-blind sum still reads 0.46 — that is exactly what used to mask it.
    st = om._bybit_position_protection(client, "linear", "BTCUSDT")
    assert st["covered_qty"] == 0.46 and st["size"] == 0.018

    assert summary["broker_naked"] == 1
    assert summary["rearmed"] == 1
    assert summary["coverage_side_ungradeable"] == 0
    # A real Full-mode bracket went to the venue at the JOURNAL's levels.
    assert client.stops_set and client.stops_set[0]["tpslMode"] == "Full"
    assert client.stops_set[0]["stopLoss"] == "38698.6"
    # ...and it is NOT graded as a partial gap: this book had ZERO coverage,
    # so the whole-position re-arm is right and a qty-scoped top-up is not.
    assert summary["partially_naked"] == 0 and summary["topped_up"] == 0


def test_partial_gap_masked_by_an_other_book_leg_tops_up_the_REAL_hole(
        tmp_path, monkeypatch):
    """The sharper form: 0.010 of its own stop survives on an 0.018 position.

    Side-blind coverage is 0.470 — comfortably over `size`, so the old code
    skipped. The top-up must be sized off the GRADED book (0.008), never off
    the side-blind sum, which would compute a NEGATIVE quantity."""
    db = _btc_db(tmp_path)
    client = _FakeBybit(
        positions={"BTCUSDT": dict(_LIVE_BTC_POS)},
        stop_legs={"BTCUSDT": [
            dict(_OTHER_BOOK_SL),
            {"stopOrderType": "PartialStopLoss", "side": "Sell",
             "qty": "0.010", "orderId": "own-partial"},
        ]},
    )
    calls = {}

    def _fake_modify(cl, acc, *, symbol, sl, tp, qty, sl_order_id, tp_order_id):
        calls.update(symbol=symbol, qty=qty, sl=sl)
        return {"ok": True, "error": None}

    monkeypatch.setattr(
        "src.units.accounts.execute.modify_open_order", _fake_modify)
    summary = _run(db, client, monkeypatch, tmp_path, **_ARMED)

    assert summary["partially_naked"] == 1 and summary["topped_up"] == 1
    assert calls["qty"] == pytest.approx(0.008)   # 0.018 - 0.010, NOT 0.018-0.470
    assert summary["rearmed"] == 0


# ---- CONTROL 2: the MIRROR — a genuinely covered book is STILL skipped ----
def test_position_covered_by_its_OWN_legs_is_still_skipped(tmp_path, monkeypatch):
    """The live 2026-09-02T03:30:33Z shape, unaltered: the long IS protected
    exactly 1.00x by its own Sell 0.018 leg. A change tested only on control 1
    has been shown to fire, not to discriminate — this is what stops it firing
    on a healthy book, which would cancel and re-place a live bracket."""
    db = _btc_db(tmp_path)
    client = _FakeBybit(
        positions={"BTCUSDT": dict(_LIVE_BTC_POS)},
        stop_legs={"BTCUSDT": [dict(_OWN_SL), dict(_OTHER_BOOK_SL)]},
    )
    summary = _run(db, client, monkeypatch, tmp_path)

    assert summary["broker_naked"] == 0
    assert summary["rearmed"] == 0 and summary["topped_up"] == 0
    assert summary["coverage_side_ungradeable"] == 0
    assert client.stops_set == [], "no order may reach the venue on a covered book"


# ---- CONTROL 3: the UNION did not shrink ---------------------------------
def test_the_other_book_condition_still_TRIPS_over_cover_and_pages(
        tmp_path, monkeypatch):
    """⚠️ THE TRIP THRESHOLD IS DELIBERATELY STILL SIDE-BLIND.

    The over-cover check is the UNION of two conditions — genuine same-book
    pile-up AND other-book legs resting on the symbol. Narrowing it to the
    graded book would make the second stop tripping and go SILENT, which is
    worse than the mislabelling #10739 fixed. Same fixture as control 2, so
    this proves the two coexist: nothing is re-armed AND the operator is paged.
    """
    db = _btc_db(tmp_path)
    client = _FakeBybit(
        positions={"BTCUSDT": dict(_LIVE_BTC_POS)},
        stop_legs={"BTCUSDT": [dict(_OWN_SL), dict(_OTHER_BOOK_SL)]},
    )
    sent = []
    import src.runtime.outcomes as outcomes
    monkeypatch.setattr(outcomes, "report", lambda *a, **k: sent.append((a, k)))
    summary = _run(db, client, monkeypatch, tmp_path)

    assert summary["over_covered"] == 1, "the trip must stay side-blind"
    assert summary["over_cover_other_book"] == 1
    assert summary["over_cover_alerted"] == 1
    assert [a[0] for a, _ in sent] == ["bybit_over_cover"]
    # ...and it still says which book, rather than claiming over-protection.
    assert "OPPOSITE book" in sent[0][1]["reason"]
    assert summary["rearmed"] == 0 and client.stops_set == []


def test_same_book_pile_up_still_trips_it_too(tmp_path, monkeypatch):
    """The other half of the union — a genuine same-book pile-up. Both arms
    must keep tripping or the check has been narrowed by accident."""
    db = _btc_db(tmp_path)
    client = _FakeBybit(
        positions={"BTCUSDT": dict(_LIVE_BTC_POS)},
        stop_legs={"BTCUSDT": [
            {"stopOrderType": "PartialStopLoss", "side": "Sell",
             "qty": "0.018", "orderId": "own-a"},
            {"stopOrderType": "PartialStopLoss", "side": "Sell",
             "qty": "0.018", "orderId": "own-b"},
        ]},
    )
    summary = _run(db, client, monkeypatch, tmp_path)
    assert summary["over_covered"] == 1
    assert summary["over_cover_other_book"] == 0
    # Over-covered on its own book is still COVERED — no re-arm.
    assert summary["rearmed"] == 0 and summary["topped_up"] == 0


# ---- CONTROL 4: "we could not look" re-arms NEITHER way -------------------
# ⚠️ WHY REFUSING IS THE SAFE DIRECTION, since fail-safe is not obvious here.
# The two available errors are NOT symmetric. Reading an ungraded split as
# NOT-covered drives a Full-mode re-arm, which is a LIVE ORDER that cancels the
# resting bracket and stamps ONE trade's levels over the whole netted position
# — and under hedge mode it would target a book we just failed to identify;
# that can make a correctly-protected position worse. Reading it as COVERED
# would be silent. Refusing does neither: it leaves whatever protection
# actually rests untouched and is LOUD (a WARNING plus its own counter), so the
# refusal is a reportable condition rather than a quiet skip. It is also the
# posture this function already takes for an unparseable leg QTY. It is NOT a
# claim the position is protected — only that we will not act on a read we
# could not grade.
def test_unreadable_POSITION_side_refuses_the_rearm_and_says_so(
        tmp_path, monkeypatch, caplog):
    db = _btc_db(tmp_path)
    pos = dict(_LIVE_BTC_POS)
    pos.pop("side")
    client = _FakeBybit(positions={"BTCUSDT": pos},
                        stop_legs={"BTCUSDT": [dict(_OTHER_BOOK_SL)]})
    with caplog.at_level("WARNING"):
        summary = _run(db, client, monkeypatch, tmp_path, **_ARMED)

    assert summary["coverage_side_ungradeable"] == 1
    assert summary["rearmed"] == 0 and summary["topped_up"] == 0
    assert summary["broker_naked"] == 0
    assert client.stops_set == [], "no live order on a read we could not grade"
    assert "coverage of the GRADED book is ungradeable" in caplog.text
    assert "position_side_ungraded" in caplog.text
    # Distinct from the unparseable-QTY refusal, which has its own counter.
    assert summary["unconfirmed"] == 0


def test_unreadable_LEG_side_refuses_the_rearm_and_says_so(
        tmp_path, monkeypatch, caplog):
    """Even ONE ungradeable leg refuses. A partial grade is a LOWER BOUND on
    coverage, and a lower bound compared against `size` under-reports — which
    drives a re-arm on a position that may already be protected."""
    db = _btc_db(tmp_path)
    client = _FakeBybit(
        positions={"BTCUSDT": dict(_LIVE_BTC_POS)},
        stop_legs={"BTCUSDT": [
            dict(_OWN_SL),
            {"stopOrderType": "PartialStopLoss", "qty": "0.005",
             "orderId": "sideless"},          # no `side` on the leg
        ]},
    )
    with caplog.at_level("WARNING"):
        summary = _run(db, client, monkeypatch, tmp_path, **_ARMED)

    assert summary["coverage_side_ungradeable"] == 1
    assert summary["rearmed"] == 0 and summary["topped_up"] == 0
    assert client.stops_set == []
    assert "leg_side_ungraded" in caplog.text


def test_ungradeable_side_does_NOT_bank_the_side_blind_sum_as_coverage(
        tmp_path, monkeypatch):
    """The other direction of the same refusal, and the one a reader is most
    likely to assume away: an ungraded read must not SILENTLY skip on the
    strength of the side-blind total either. `covered_qty` here is 0.46 against
    a size of 0.018, so a silent skip would be indistinguishable from a healthy
    book. The refusal counter is what separates them.

    ARMED, deliberately: the refusal is a property of the BINDING basis. On a
    held-back account the sweep must keep skipping on the side-blind sum, which
    is what `test_held_back_ungradeable_adds_no_refusal` asserts."""
    db = _btc_db(tmp_path)
    pos = dict(_LIVE_BTC_POS)
    pos["side"] = "garbage"
    client = _FakeBybit(positions={"BTCUSDT": pos},
                        stop_legs={"BTCUSDT": [dict(_OTHER_BOOK_SL)]})
    summary = _run(db, client, monkeypatch, tmp_path, **_ARMED)

    assert summary["coverage_side_ungradeable"] == 1, (
        "an ungraded read must be REPORTED, not silently treated as covered")
    assert summary["coverage_ungradeable_refused"] == 1, (
        "and where the graded basis BINDS, reporting it must also refuse")
    assert summary["broker_naked"] == 0 and summary["rearmed"] == 0


def test_full_mode_stop_is_unaffected_by_the_split(tmp_path, monkeypatch):
    """A Full-mode position stop returns BEFORE the legs are read, so there is
    no split to grade — `covered_qty == size` is the measurement and the sweep
    must skip, not refuse. Otherwise every Full-mode position on the fleet
    would start reporting `coverage_side_ungradeable`.

    ARMED, so this asserts the graded basis specifically. Unarmed it would pass
    for the uninteresting reason that the graded figure never binds at all."""
    db = _btc_db(tmp_path)
    pos = dict(_LIVE_BTC_POS)
    pos["stopLoss"] = "38698.6"
    client = _FakeBybit(positions={"BTCUSDT": pos})
    summary = _run(db, client, monkeypatch, tmp_path, **_ARMED)

    assert summary["coverage_side_ungradeable"] == 0
    assert summary["broker_naked"] == 0 and summary["rearmed"] == 0
    assert client.stops_set == []


def test_a_second_journal_row_on_the_same_symbol_does_not_rearm_twice(
        tmp_path, monkeypatch):
    """In-tick idempotency. A netted symbol holds MANY journal rows and ONE
    exchange position; the sweep marks the cached state fully covered after a
    re-arm. That marker had to learn about the GRADED figure too, or every
    sibling row would fire another live bracket."""
    db = _btc_db(tmp_path)
    _insert(db, id=2, account_id="bybit_2", symbol="BTCUSDT", direction="long",
            position_size=0.009, stop_loss=38000.0, take_profit_1=44000.0,
            created_at="2026-07-01T00:00:00+00:00", status="open")
    client = _FakeBybit(positions={"BTCUSDT": dict(_LIVE_BTC_POS)},
                        stop_legs={"BTCUSDT": [dict(_OTHER_BOOK_SL)]})
    summary = _run(db, client, monkeypatch, tmp_path, **_ARMED)

    assert summary["rearmed"] == 1, "exactly one re-arm for one exchange position"
    assert len(client.stops_set) == 1


# =========================================================================
# THE STAGING CONTRACT (Tier-2, operator decision 2026-09-02)
# -------------------------------------------------------------------------
# "Stage it on bybit_1 (demo) first" has to be a property of the system, not a
# sentence in a doc. Every test above that asserts the graded basis passes
# `**_ARMED`; these assert what happens when it is NOT armed — which is the
# state the change actually SHIPS in.
# =========================================================================

def _soak_rows(tmp_path):
    """Read the coverage soak `_run` redirected into *tmp_path*."""
    path = tmp_path / "bybit_coverage_soak.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_at_the_SHIPPED_default_the_masked_naked_position_is_NOT_rearmed(
        tmp_path, monkeypatch):
    """⚠️ THE SHIPPED STATE. Same book as the CONTROL test at the top of this
    file — a naked long masked by an other-book leg — but with no allowlist.

    The operator chose to stage on bybit_1 and explicitly accepted that
    bybit_2 (real money) stays exposed to this masking during the soak. So the
    honest assertion is that NOTHING is re-armed here: landing this PR changes
    no live behaviour anywhere until both env keys are set.
    """
    db = _btc_db(tmp_path)
    client = _FakeBybit(positions={"BTCUSDT": dict(_LIVE_BTC_POS)},
                        stop_legs={"BTCUSDT": [dict(_OTHER_BOOK_SL)]})
    summary = _run(db, client, monkeypatch, tmp_path)

    assert summary["rearmed"] == 0 and summary["topped_up"] == 0
    assert summary["broker_naked"] == 0
    assert client.stops_set == [], "no live order may leave the unarmed path"
    assert summary["coverage_graded_basis_bound"] == 0


def test_the_default_still_MEASURES_what_arming_would_have_changed(
        tmp_path, monkeypatch):
    """⚠️ THE ALLOWLIST SCOPES THE BINDING, NEVER THE MEASUREMENT.

    This is the correction NETTING_ATTRIBUTION_ACCOUNTS needed on 2026-08-09:
    intersecting the account set at the top of the pass made the account being
    staged TOWARD invisible in exactly the rows a reviewer needs to widen to
    it. Here the finding must be visible on the held-back account.
    """
    db = _btc_db(tmp_path)
    client = _FakeBybit(positions={"BTCUSDT": dict(_LIVE_BTC_POS)},
                        stop_legs={"BTCUSDT": [dict(_OTHER_BOOK_SL)]})
    summary = _run(db, client, monkeypatch, tmp_path,
                   mode="apply", accounts="bybit_1")  # bybit_2 is held back

    assert summary["coverage_basis_would_differ"] == 1, (
        "the reviewer's evidence must accrue on the account being staged toward")
    rows = _soak_rows(tmp_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["account_id"] == "bybit_2"
    assert row["verdicts_differ"] is True
    assert row["verdict_side_blind"] == "covered"
    assert row["verdict_graded"] == "uncovered"
    # ...and it is unmistakably a HELD-BACK row, not an applied one.
    assert row["mode"] == "annotate"
    assert row["global_mode"] == "apply"
    assert row["apply_scope"] == "not_allowlisted"
    assert row["basis"] == "side_blind"
    assert row["binding"] is False


@pytest.mark.parametrize("accounts", ["", "   ", "bybit_1", "bybit_portfolio"])
def test_apply_binds_NOTHING_without_this_account_in_the_allowlist(
        tmp_path, monkeypatch, accounts):
    """⚠️ AN EMPTY ALLOWLIST MEANS NONE. If someone ever harmonises this toward
    CONVICTION_SIZING_ACCOUNTS / NETTING_ATTRIBUTION_ACCOUNTS (where empty
    means ALL), `accounts=""` places a live bracket on real-money bybit_2 here
    and this test fails."""
    db = _btc_db(tmp_path)
    client = _FakeBybit(positions={"BTCUSDT": dict(_LIVE_BTC_POS)},
                        stop_legs={"BTCUSDT": [dict(_OTHER_BOOK_SL)]})
    summary = _run(db, client, monkeypatch, tmp_path,
                   mode="apply", accounts=accounts)

    assert summary["rearmed"] == 0
    assert client.stops_set == []


def test_armed_the_soak_row_says_the_graded_basis_governed(
        tmp_path, monkeypatch):
    db = _btc_db(tmp_path)
    client = _FakeBybit(positions={"BTCUSDT": dict(_LIVE_BTC_POS)},
                        stop_legs={"BTCUSDT": [dict(_OTHER_BOOK_SL)]})
    summary = _run(db, client, monkeypatch, tmp_path, **_ARMED)

    assert summary["rearmed"] == 1
    assert summary["coverage_graded_basis_bound"] == 1
    row = _soak_rows(tmp_path)[0]
    assert row["mode"] == "apply"
    assert row["apply_scope"] == "allowlisted"
    assert row["basis"] == "graded"
    assert row["binding"] is True
    assert row["bound_qty"] == 0.0
    assert row["side_blind_qty"] == 0.46
    assert row["decision"] == "rearm_indicated"
    # Context a reviewer needs to read the row cold.
    assert row["other_book_qty"] == 0.46
    assert row["other_book_state"] == "possible_hedge"


def test_off_writes_no_soak_row_and_grades_nothing(tmp_path, monkeypatch):
    """`off` stays byte-for-byte the pre-gate behaviour, on disk as well as in
    the order path — the discipline stray_oca_soak / prop_ticket_risk_soak
    follow. Note `graded_qty` is absent rather than 0.0: we did not look."""
    db = _btc_db(tmp_path)
    client = _FakeBybit(positions={"BTCUSDT": dict(_LIVE_BTC_POS)},
                        stop_legs={"BTCUSDT": [dict(_OTHER_BOOK_SL)]})
    summary = _run(db, client, monkeypatch, tmp_path,
                   mode="off", accounts="bybit_2")

    assert _soak_rows(tmp_path) == []
    assert summary["rearmed"] == 0
    assert summary["coverage_graded_basis_bound"] == 0
    assert summary["coverage_side_ungradeable"] == 0, (
        "`off` means we never looked — NOT that a read failed")


def test_held_back_ungradeable_adds_no_refusal(tmp_path, monkeypatch):
    """An `annotate` mode that introduced a new refusal would not be an
    annotation. The ungradeable state is RECORDED on a held-back account and
    changes nothing about what the sweep does."""
    db = _btc_db(tmp_path)
    pos = dict(_LIVE_BTC_POS)
    pos["side"] = "garbage"
    client = _FakeBybit(positions={"BTCUSDT": pos},
                        stop_legs={"BTCUSDT": [dict(_OTHER_BOOK_SL)]})
    summary = _run(db, client, monkeypatch, tmp_path)

    assert summary["coverage_side_ungradeable"] == 1, "still observed"
    assert summary["coverage_ungradeable_refused"] == 0, "but it refused nothing"
    row = _soak_rows(tmp_path)[0]
    assert row["graded_qty"] is None
    assert row["coverage_state"] == "position_side_ungraded"
    assert row["decision"] == "skip_covered"


def test_one_soak_row_per_symbol_per_sweep_not_one_per_journal_row(
        tmp_path, monkeypatch):
    """A netted symbol holds MANY journal rows against ONE exchange position,
    and after a re-arm the cache is rewritten to say "covered". Recording every
    row would inflate the count AND persist that synthetic marker as if it were
    a venue reading."""
    db = _btc_db(tmp_path)
    _insert(db, id=2, account_id="bybit_2", symbol="BTCUSDT", direction="long",
            position_size=0.009, stop_loss=38000.0, take_profit_1=44000.0,
            created_at="2026-07-01T00:00:00+00:00", status="open")
    client = _FakeBybit(positions={"BTCUSDT": dict(_LIVE_BTC_POS)},
                        stop_legs={"BTCUSDT": [dict(_OTHER_BOOK_SL)]})
    _run(db, client, monkeypatch, tmp_path, **_ARMED)

    rows = _soak_rows(tmp_path)
    assert len(rows) == 1, "one exchange position, one coverage decision row"
    assert rows[0]["bound_qty"] == 0.0, (
        "and it is the FIRST decision, not the post-re-arm cache marker")


def test_the_soak_is_reachable_on_the_diag_surface():
    """⚠️ A soak a Tier-2 reviewer is told to read and cannot reach is the
    BL-20260825 shape. Registered in the SAME commit as the writer."""
    from src.runtime import bybit_coverage_soak
    from src.web.api.routers import diag

    assert "bybit_coverage_soak" in diag._LOG_FILES
    assert (diag._LOG_FILES["bybit_coverage_soak"].name
            == bybit_coverage_soak.SOAK_LOG_NAME)


def test_both_env_keys_are_readable_with_get_env():
    """A two-key arm: `apply` with an empty allowlist binds nothing, so reading
    either key alone cannot say whether the gate is live
    (BL-20260813-ENV-VARS-SHIP-WITHOUT-A-READ-SURFACE)."""
    import importlib.util
    import pathlib
    spec = importlib.util.spec_from_file_location(
        "_get_env_probe",
        pathlib.Path(__file__).resolve().parents[1] / "scripts/ops/get_env.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert "BYBIT_GRADED_COVERAGE_MODE" in mod.ALLOWED_KEYS
    assert "BYBIT_GRADED_COVERAGE_ACCOUNTS" in mod.ALLOWED_KEYS
