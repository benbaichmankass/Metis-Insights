"""Naked-position auto-protect (BL-20260612-001 follow-up).

Covers the monitor-side pieces that attach reverse-side GTC SL/TP to a naked
open position (e.g. a reconciler-adopted IB orphan that lost its bracket):

* ``_base_futures_symbol`` — normalizes an adopted specific-contract symbol
  (``MHGN6``) back to the base root (``MHG``) the rest of the system speaks.
* ``_resolve_protective_levels`` — recovers (sl, tp) from the originating
  order package when the adopted row carries NULL levels.
* ``_check_naked_positions`` — UNCONDITIONAL attach-vs-alert: every naked open
  position gets a re-arm attempt each tick (no enable flag — a position with no
  stop is an unacceptable state the system must always fix). On a successful
  attach the trade row is updated and stamped idempotently; when the attach is
  unavailable (non-IB account / IB place failed) it falls back to a one-shot
  alert.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from src.runtime import order_monitor as om


class _FakeDB:
    """Minimal stand-in exposing the ``connect()`` + ``update_trade()`` surface
    ``_check_naked_positions`` uses, backed by a real on-disk sqlite file."""

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

    def update_trade(self, trade_id, fields):
        conn = sqlite3.connect(self.path)
        cols = ", ".join(f"{k}=?" for k in fields)
        conn.execute(
            f"UPDATE trades SET {cols} WHERE id=?",
            (*fields.values(), trade_id),
        )
        conn.commit()
        conn.close()

    def fetch(self, trade_id):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
        conn.close()
        return row


@pytest.mark.parametrize(
    "raw, base",
    [
        ("MHGN6", "MHG"),   # July-2026 micro copper -> base root
        ("MESZ5", "MES"),   # Dec-2025 micro S&P
        ("MGCM26", "MGC"),  # 2-digit year
        ("MES", "MES"),     # already a base root — unchanged
        ("BTCUSDT", "BTCUSDT"),  # crypto — no month suffix
        ("", ""),
        (None, ""),
    ],
)
def test_base_futures_symbol(raw, base):
    assert om._base_futures_symbol(raw) == base


def test_resolve_protective_levels_matches_base_symbol(tmp_path):
    db = _FakeDB(tmp_path / "j.db")
    conn = db.connect()
    # The package was logged under the base root MHG; the adopted orphan is
    # the specific contract MHGN6 — resolution must bridge the two.
    conn.execute(
        "INSERT INTO order_packages VALUES (?,?,?,?,?,?)",
        ("pkg-1", "MHG", "long", 6.045, 7.029, "2026-06-11T14:11:00+00:00"),
    )
    conn.commit()
    conn.close()
    sl, tp = om._resolve_protective_levels(db, "MHGN6", "long")
    assert (sl, tp) == (6.045, 7.029)


def test_resolve_protective_levels_none_when_no_match(tmp_path):
    db = _FakeDB(tmp_path / "j.db")
    assert om._resolve_protective_levels(db, "MHGN6", "long") == (None, None)


def _insert_naked(db, *, trade_id=2540, symbol="MHGN6", notes="{}"):
    conn = db.connect()
    conn.execute(
        "INSERT INTO trades (id, account_id, symbol, direction, position_size, "
        "stop_loss, take_profit_1, created_at, notes, status, is_backtest) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,0)",
        (trade_id, "ib_paper", symbol, "long", 3, None, None,
         "2026-06-12T04:41:22+00:00", notes, "open"),
    )
    # Originating package so levels resolve.
    conn.execute(
        "INSERT INTO order_packages VALUES (?,?,?,?,?,?)",
        ("pkg-1", "MHG", "long", 6.045, 7.029, "2026-06-12T00:02:00+00:00"),
    )
    conn.commit()
    conn.close()


def test_autoprotect_is_unconditional(tmp_path, monkeypatch):
    # There is NO enable flag: a naked position is always an attach attempt.
    # Re-arming a missing stop is baseline correctness, not an opt-in.
    db = _FakeDB(tmp_path / "j.db")
    _insert_naked(db)
    monkeypatch.delenv("NAKED_POSITION_AUTOPROTECT", raising=False)
    calls = []
    monkeypatch.setattr(om, "_attempt_naked_autoprotect",
                        lambda *a, **k: calls.append(a) or True)
    # Silence the real alert path.
    import src.runtime.execution_diagnostics as ed
    monkeypatch.setattr(ed, "enqueue_naked_position_alert", lambda **k: None)

    summary = om._check_naked_positions(db)
    assert len(calls) == 1             # no gate → always tries to place
    assert summary["protected"] == 1


def test_autoprotect_attaches_and_stamps(tmp_path, monkeypatch):
    db = _FakeDB(tmp_path / "j.db")
    _insert_naked(db)
    seen = {}
    def _fake_attach(row, sl, tp):
        seen["levels"] = (sl, tp)
        return True
    monkeypatch.setattr(om, "_attempt_naked_autoprotect", _fake_attach)

    summary = om._check_naked_positions(db)
    assert summary["protected"] == 1
    # Levels were recovered from the base-symbol package.
    assert seen["levels"] == (6.045, 7.029)
    # Row updated + stamped idempotently.
    row = db.fetch(2540)
    assert row["stop_loss"] == 6.045 and row["take_profit_1"] == 7.029
    assert json.loads(row["notes"]).get("naked_sltp_attached_at")

    # Second pass is a no-op (idempotent — already stamped).
    summary2 = om._check_naked_positions(db)
    assert summary2["protected"] == 0


def test_autoprotect_attaches_even_if_previously_alerted(tmp_path, monkeypatch):
    # The #2540 case: a position alerted on a prior tick (before levels could be
    # attached) must still get protected — "alerted" is not "protected".
    db = _FakeDB(tmp_path / "j.db")
    _insert_naked(db, notes=json.dumps({"naked_sltp_alerted_at": "2026-06-12T04:50:00+00:00"}))
    monkeypatch.setattr(om, "_attempt_naked_autoprotect", lambda row, sl, tp: True)

    summary = om._check_naked_positions(db)
    assert summary["protected"] == 1
    row = db.fetch(2540)
    assert row["stop_loss"] == 6.045 and json.loads(row["notes"]).get("naked_sltp_attached_at")


def test_attach_failure_alerts_once_on_first_sighting(tmp_path, monkeypatch):
    # Attach unavailable (non-IB account / IB place failed) and not yet alerted
    # → fall back to a single naked-position alert.
    db = _FakeDB(tmp_path / "j.db")
    _insert_naked(db)
    monkeypatch.setattr(om, "_attempt_naked_autoprotect", lambda row, sl, tp: False)
    alerts = []
    import src.runtime.execution_diagnostics as ed
    monkeypatch.setattr(ed, "enqueue_naked_position_alert", lambda **k: alerts.append(k))

    summary = om._check_naked_positions(db)
    assert summary["protected"] == 0 and summary["alerted"] == 1 and len(alerts) == 1


def test_attach_failure_does_not_realert_previously_alerted(tmp_path, monkeypatch):
    # The IB place fails on a previously-alerted position: must NOT be re-alerted
    # every tick (it waits for the next attach attempt).
    db = _FakeDB(tmp_path / "j.db")
    _insert_naked(db, notes=json.dumps({"naked_sltp_alerted_at": "2026-06-12T04:50:00+00:00"}))
    monkeypatch.setattr(om, "_attempt_naked_autoprotect", lambda row, sl, tp: False)
    alerts = []
    import src.runtime.execution_diagnostics as ed
    monkeypatch.setattr(ed, "enqueue_naked_position_alert", lambda **k: alerts.append(k))

    summary = om._check_naked_positions(db)
    assert summary["protected"] == 0 and summary["alerted"] == 0 and alerts == []
