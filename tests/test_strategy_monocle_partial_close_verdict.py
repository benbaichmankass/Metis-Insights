"""Tests for partial-close verdict shape in _apply_update / _apply_partial_close.

Strategy-monocle sprint PR 2/3 — DB-side only. No exchange calls.

Contracts:
1. Full close (close_qty_pct unset / 1.0): existing full-close path unchanged.
2. Partial close (50%): order_packages stays open; trades.position_size halved;
   notes.partial_closes fragment appended.
3. Sequential partials adding to 100%: last close triggers full close of both
   order_packages and trades rows.
4. Invalid pct (> 1.0 or <= 0): rejected with no_change; no DB write.
5. Open-gate from PR 1 continues to refuse new packages while a partial-closed
   trade (package still open) is in flight — verified indirectly by confirming
   the package row stays 'open' after a partial close.
6. No linked_trade_id: partial-close is a no-op (warning) — does not crash.
"""
from __future__ import annotations

import json
import sys
from typing import List, Optional
from unittest.mock import MagicMock


# Stub pandas before importing order_monitor (signal_notifications pulls it).
sys.modules.setdefault("pandas", MagicMock())
sys.modules.setdefault("src.runtime.signal_notifications", MagicMock())
sys.modules.setdefault("src.runtime.signal_writer", MagicMock())

from src.runtime.order_monitor import (  # noqa: E402
    _apply_update,
    _StrategyTickSummary,
)


# ---------------------------------------------------------------------------
# Minimal fake DB
# ---------------------------------------------------------------------------

class _FakeTrade:
    def __init__(self, trade_id, position_size, notes=None):
        self.trade_id = trade_id
        self.position_size = position_size
        self._notes = notes or {}

    def as_row(self):
        return {
            "id": self.trade_id,
            "position_size": self.position_size,
            "notes": json.dumps(self._notes),
            "account_id": "bybit_2",
            "symbol": "BTCUSDT",
            "strategy_name": "vwap",
        }


class _FakeDB:
    """Minimal in-memory stand-in for Database."""

    def __init__(self, trade: Optional[_FakeTrade] = None, pkg_update_ok=True):
        self._trade = trade
        self._pkg_update_ok = pkg_update_ok
        self.pkg_updates: List[tuple] = []
        self.trade_updates: List[tuple] = []

    def update_order_package(self, pkg_id, updates):
        if not self._pkg_update_ok:
            raise RuntimeError("pkg update disabled")
        self.pkg_updates.append((pkg_id, updates))

    def update_trade(self, trade_id, updates):
        self.trade_updates.append((trade_id, updates))
        if self._trade and int(trade_id) == self._trade.trade_id:
            for k, v in updates.items():
                if k == "position_size":
                    self._trade.position_size = v
                elif k == "notes":
                    try:
                        self._trade._notes = json.loads(v) if v else {}
                    except Exception:
                        pass

    def get_trades(self, filters=None, limit=None):
        if self._trade is None:
            return []
        filt = filters or {}
        row = self._trade.as_row()
        if "id" in filt and int(filt["id"]) != self._trade.trade_id:
            return []
        if "status" in filt and filt.get("status") != row.get("status", "open"):
            return []
        return [row]

    def get_order_packages_by_strategy(self, *args, **kwargs):
        return []


def _pkg(linked_trade_id=None):
    return {
        "order_package_id": "pkg-001",
        "strategy_name": "vwap",
        "symbol": "BTCUSDT",
        "linked_trade_id": linked_trade_id,
    }


def _summary():
    return _StrategyTickSummary()


# ---------------------------------------------------------------------------
# 1. Full close (existing path unchanged)
# ---------------------------------------------------------------------------

class TestFullClose:
    def test_full_close_no_pct_closes_package_and_trade(self):
        trade = _FakeTrade(1, 0.001)
        db = _FakeDB(trade)
        verdict = {"action": "close", "reason": "tp_hit"}
        s = _summary()

        _apply_update(db, _pkg(linked_trade_id=1), verdict, s)

        assert s.closed_count == 1
        assert s.error_count == 0
        # Package closed
        assert any(u[1].get("status") == "closed" for u in db.pkg_updates)
        # Trade closed
        assert any(u[1].get("status") == "closed" for u in db.trade_updates)

    def test_full_close_with_pct_1_0_identical_to_no_pct(self):
        trade = _FakeTrade(2, 0.002)
        db = _FakeDB(trade)
        verdict = {"action": "close", "close_qty_pct": 1.0, "reason": "sl_hit"}
        s = _summary()

        _apply_update(db, _pkg(linked_trade_id=2), verdict, s)

        assert s.closed_count == 1
        assert any(u[1].get("status") == "closed" for u in db.pkg_updates)


# ---------------------------------------------------------------------------
# 2. Partial close (50%)
# ---------------------------------------------------------------------------

class TestPartialClose50Pct:
    def test_package_stays_open(self):
        trade = _FakeTrade(3, 0.010)
        db = _FakeDB(trade)
        verdict = {"action": "close", "close_qty_pct": 0.5, "reason": "partial_tp"}
        s = _summary()

        _apply_update(db, _pkg(linked_trade_id=3), verdict, s)

        # Package must NOT be closed
        assert not db.pkg_updates, "order_packages should not be touched on partial close"
        assert s.error_count == 0
        assert s.updated_count == 1

    def test_position_size_halved(self):
        trade = _FakeTrade(4, 0.010)
        db = _FakeDB(trade)
        verdict = {"action": "close", "close_qty_pct": 0.5, "reason": "partial_tp"}
        s = _summary()

        _apply_update(db, _pkg(linked_trade_id=4), verdict, s)

        # position_size should be ~0.005 (50% remaining)
        assert len(db.trade_updates) == 1
        new_ps = db.trade_updates[0][1]["position_size"]
        assert abs(new_ps - 0.005) < 1e-8

    def test_partial_closes_fragment_in_notes(self):
        trade = _FakeTrade(5, 0.010)
        db = _FakeDB(trade)
        verdict = {"action": "close", "close_qty_pct": 0.5, "reason": "partial_tp"}
        s = _summary()

        _apply_update(db, _pkg(linked_trade_id=5), verdict, s)

        raw_notes = db.trade_updates[0][1].get("notes", "{}")
        notes = json.loads(raw_notes)
        partials = notes.get("partial_closes", [])
        assert len(partials) == 1
        assert partials[0]["qty"] == 0.5
        assert partials[0]["reason"] == "partial_tp"

    def test_original_position_size_stored_in_notes(self):
        trade = _FakeTrade(6, 0.010)
        db = _FakeDB(trade)
        verdict = {"action": "close", "close_qty_pct": 0.5, "reason": "partial_tp"}
        s = _summary()

        _apply_update(db, _pkg(linked_trade_id=6), verdict, s)

        notes = json.loads(db.trade_updates[0][1]["notes"])
        assert abs(notes["original_position_size"] - 0.010) < 1e-8


# ---------------------------------------------------------------------------
# 3. Sequential partials adding to 100%
# ---------------------------------------------------------------------------

class TestSequentialPartials:
    def _two_partials(self, db, pkg):
        """Fire two 50% partials; the second should trigger a full close."""
        s = _summary()
        _apply_update(db, pkg, {"action": "close", "close_qty_pct": 0.5, "reason": "p1"}, s)
        s2 = _summary()
        _apply_update(db, pkg, {"action": "close", "close_qty_pct": 0.5, "reason": "p2"}, s2)
        return s, s2

    def test_second_partial_closes_package(self):
        trade = _FakeTrade(7, 0.010)
        db = _FakeDB(trade)
        pkg = _pkg(linked_trade_id=7)

        s1, s2 = self._two_partials(db, pkg)

        # First partial: no pkg update
        assert s1.updated_count == 1 and s1.closed_count == 0
        # Second partial: full close
        assert s2.closed_count == 1 and s2.error_count == 0
        assert any(u[1].get("status") == "closed" for u in db.pkg_updates)

    def test_second_partial_closes_trade(self):
        trade = _FakeTrade(8, 0.010)
        db = _FakeDB(trade)
        pkg = _pkg(linked_trade_id=8)

        self._two_partials(db, pkg)

        # At least one trade update should set status=closed
        assert any(u[1].get("status") == "closed" for u in db.trade_updates)

    def test_partial_closes_list_has_two_entries_on_full_close(self):
        trade = _FakeTrade(9, 0.010)
        db = _FakeDB(trade)
        pkg = _pkg(linked_trade_id=9)

        self._two_partials(db, pkg)

        # The final trade-close update should carry both partial fragments.
        # Look for the update that sets status=closed.
        close_notes = None
        for _, upd in db.trade_updates:
            if upd.get("status") == "closed" and upd.get("notes"):
                close_notes = json.loads(upd["notes"])
                break
        assert close_notes is not None
        assert len(close_notes.get("partial_closes", [])) == 2


# ---------------------------------------------------------------------------
# 4. Invalid pct
# ---------------------------------------------------------------------------

class TestInvalidPct:
    def _assert_no_write(self, verdict):
        trade = _FakeTrade(10, 0.010)
        db = _FakeDB(trade)
        s = _summary()
        _apply_update(db, _pkg(linked_trade_id=10), verdict, s)
        assert not db.pkg_updates
        assert not db.trade_updates
        assert s.no_change_count == 1
        assert s.error_count == 0

    def test_pct_greater_than_1_rejected(self):
        self._assert_no_write({"action": "close", "close_qty_pct": 1.5, "reason": "x"})

    def test_pct_zero_rejected(self):
        self._assert_no_write({"action": "close", "close_qty_pct": 0.0, "reason": "x"})

    def test_pct_negative_rejected(self):
        self._assert_no_write({"action": "close", "close_qty_pct": -0.1, "reason": "x"})

    def test_pct_non_numeric_rejected(self):
        self._assert_no_write({"action": "close", "close_qty_pct": "bad", "reason": "x"})


# ---------------------------------------------------------------------------
# 5. Open-gate from PR 1 (indirect: package stays open after partial close)
# ---------------------------------------------------------------------------

class TestOpenGateInvariant:
    def test_package_open_after_partial_so_gate_still_blocks(self):
        """After a partial close, order_packages.status remains 'open'.
        The PR 1 open-gate queries status='open', so it will still
        block new packages — verified here by confirming no pkg writes."""
        trade = _FakeTrade(11, 0.010)
        db = _FakeDB(trade)
        verdict = {"action": "close", "close_qty_pct": 0.3, "reason": "partial"}
        s = _summary()

        _apply_update(db, _pkg(linked_trade_id=11), verdict, s)

        assert not db.pkg_updates, (
            "package must stay open after partial close so the PR-1 gate blocks new packages"
        )


# ---------------------------------------------------------------------------
# 6. No linked_trade_id — no-op
# ---------------------------------------------------------------------------

class TestNoLinkedTrade:
    def test_partial_close_without_linked_trade_id_is_noop(self):
        db = _FakeDB()
        verdict = {"action": "close", "close_qty_pct": 0.5, "reason": "x"}
        s = _summary()

        _apply_update(db, _pkg(linked_trade_id=None), verdict, s)

        assert not db.pkg_updates
        assert not db.trade_updates
        assert s.no_change_count == 1
        assert s.error_count == 0
