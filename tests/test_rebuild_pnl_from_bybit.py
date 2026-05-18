"""Tests for scripts/ops/rebuild_pnl_from_bybit.py.

Verifies the reverse-direction matcher (Bybit record → DB row)
that rewrites every matched DB pnl from ground truth. Critical
because this is the recovery path for the 92 stale rows + 11
stuck rows surfaced in issue #1429's audit — operator approved
running it on live data.

Coverage:
  * happy path — Bybit rec maps to the right DB row by
    (symbol, direction, qty, entry±10bps, temporal ordering)
  * consumed-row exclusion — each Bybit record claims exactly
    one DB row; a second Bybit record can't pick the same row
  * temporal ordering — consecutive trades with same entry get
    distinct closes
  * UPDATE plan — pnl/exit/notes overwrite, audit fields stamped
  * idempotency — re-running on a row whose pnl already matches
    AND already carries rebuilt_by stamp is a no-op
  * Bybit-only-side records — closes that have no DB match
    (partial-fill / retried / DB-only state) listed in skipped
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_script():
    path = _REPO_ROOT / "scripts" / "ops" / "rebuild_pnl_from_bybit.py"
    spec = importlib.util.spec_from_file_location(
        "rebuild_pnl_from_bybit", path,
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["rebuild_pnl_from_bybit"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def script():
    return _load_script()


def _db_row(*, id, symbol="BTCUSDT", direction="long",
            entry=76700.0, qty=0.004,
            created_at_iso="2026-05-18T06:30:00+00:00",
            pnl=None, notes=None):
    """Synthetic DB row dict in the shape the script expects."""
    from datetime import datetime
    dt = datetime.fromisoformat(created_at_iso)
    return {
        "id": id, "symbol": symbol, "direction": direction,
        "entry_price": entry, "exit_price": None,
        "position_size": qty, "status": "closed",
        "exit_reason": "tp_cross",
        "pnl": pnl, "pnl_percent": None,
        "is_backtest": 0, "strategy_name": "vwap",
        "account_id": "bybit_2",
        "created_at": created_at_iso,
        "timestamp": created_at_iso,
        "notes": notes,
        "_created_at_ms": int(dt.timestamp() * 1000),
        "_consumed": False,
    }


def _bybit_rec(*, side="Sell", qty=0.004, entry=76700.0,
               exit_=76977.6, pnl=0.8447, created=1779089200000):
    return {
        "side": side, "qty": str(qty),
        "avgEntryPrice": str(entry), "avgExitPrice": str(exit_),
        "closedPnl": str(pnl),
        "createdTime": str(created),
        "updatedTime": str(created),
        "symbol": "BTCUSDT",
        "orderId": f"order-{created}",
    }


class TestFindMatchingDbRow:
    def test_happy_path_long(self, script):
        rec = _bybit_rec(side="Sell", entry=76700.0, qty=0.004,
                         created=1779100000000)
        db_rows = [
            _db_row(id=1540, direction="long", entry=76700.0, qty=0.004,
                    created_at_iso="2026-05-18T06:00:00+00:00"),
        ]
        row, reason = script._find_matching_db_row(rec, db_rows)
        assert reason is None
        assert row is not None
        assert row["id"] == 1540

    def test_short_uses_buy_close_side(self, script):
        rec = _bybit_rec(side="Buy", entry=77000.0, qty=0.004,
                         pnl=-0.42, created=1779100000000)
        db_rows = [
            _db_row(id=1600, direction="short", entry=77000.0, qty=0.004,
                    created_at_iso="2026-05-18T06:00:00+00:00"),
            _db_row(id=1601, direction="long", entry=77000.0, qty=0.004,
                    created_at_iso="2026-05-18T06:00:00+00:00"),
        ]
        row, reason = script._find_matching_db_row(rec, db_rows)
        assert reason is None
        assert row["id"] == 1600  # short, not the long

    def test_entry_price_outside_tolerance_skips(self, script):
        rec = _bybit_rec(entry=76700.0, qty=0.004)
        db_rows = [
            _db_row(id=1, entry=80000.0, qty=0.004),  # 4.3% off
        ]
        row, reason = script._find_matching_db_row(rec, db_rows)
        assert row is None
        assert "no DB row matches" in reason

    def test_qty_outside_tolerance_skips(self, script):
        rec = _bybit_rec(qty=0.004)
        db_rows = [
            _db_row(id=1, entry=76700.0, qty=0.010),  # 150% off
        ]
        row, reason = script._find_matching_db_row(rec, db_rows)
        assert row is None

    def test_consumed_row_is_skipped(self, script):
        rec = _bybit_rec()
        db_rows = [
            _db_row(id=1, entry=76700.0, qty=0.004),
        ]
        db_rows[0]["_consumed"] = True
        row, reason = script._find_matching_db_row(rec, db_rows)
        assert row is None

    def test_picks_most_recent_open_before_close(self, script):
        """Three open rows that all match (symbol, direction, qty,
        entry). The right pairing is the most recent open before
        the close (smallest gap)."""
        rec = _bybit_rec(created=1779100600000)  # close at t=10min
        db_rows = [
            _db_row(id=1, entry=76700.0, qty=0.004,
                    created_at_iso="2026-05-18T06:00:00+00:00"),
            _db_row(id=2, entry=76700.0, qty=0.004,
                    created_at_iso="2026-05-18T06:30:00+00:00"),
            _db_row(id=3, entry=76700.0, qty=0.004,
                    created_at_iso="2026-05-18T07:00:00+00:00"),
        ]
        # Bybit close at ms=1779100600000 = 2026-05-18T08:36:40Z
        # All three opens are before; #3 is the most recent.
        row, _ = script._find_matching_db_row(rec, db_rows)
        assert row["id"] == 3

    def test_close_before_open_is_rejected(self, script):
        """An open that's AFTER the close can't be its open."""
        # Use a Bybit close at a small fixed ms; the DB open is
        # 1 hour later so the close can never be its close.
        rec = _bybit_rec(created=1779080000000)  # T = X
        db_row = _db_row(
            id=1, entry=76700.0, qty=0.004,
            created_at_iso="2026-05-18T08:00:00+00:00",
        )
        # Verify the DB open ms is well after the close ms.
        assert db_row["_created_at_ms"] > 1779080000000 + 60_000
        row, reason = script._find_matching_db_row(rec, [db_row])
        assert row is None


class TestPlanRewrite:
    def test_rewrites_pnl_and_stamps_notes(self, script):
        rec = _bybit_rec(pnl=0.8447, exit_=76977.6,
                         created=1779089200000)
        row = _db_row(id=1540, entry=76700.0, qty=0.004,
                      pnl=-0.4683, notes='{"strategy":"vwap"}')
        updates = script._plan_rewrite(row, rec)
        assert updates is not None
        assert abs(updates["pnl"] - 0.8447) < 1e-4
        assert abs(updates["exit_price"] - 76977.6) < 1e-3
        # pnl_percent = 0.8447 / (76700 * 0.004) * 100 ≈ 0.2753
        assert abs(updates["pnl_percent"] - 0.2753) < 1e-3
        notes = json.loads(updates["notes"])
        assert notes["rebuilt_by"] == "rebuild_pnl_from_bybit_script"
        assert notes["bybit_closed_pnl"] == 0.8447
        assert notes["pre_rebuild_pnl"] == -0.4683
        assert notes["bybit_close_time"] == "1779089200000"
        assert notes["strategy"] == "vwap"  # pre-existing preserved
        assert notes["exit_price_source"] == "bybit_closed_pnl_rebuild"

    def test_drops_stale_backfill_stamps(self, script):
        """Rows from the failed #1419 backfill carry backfilled_by
        stamps that referenced wrong records. The rebuild scrubs
        them."""
        rec = _bybit_rec(pnl=0.8447)
        notes_in = json.dumps({
            "strategy": "vwap",
            "backfilled_at": "2026-05-18T12:51:00Z",
            "backfilled_by": "backfill_monitor_closed_pnl_script",
            "backfilled_source": "bybit_closed_pnl",
            "original_pnl": 1.03,
        })
        row = _db_row(id=1540, pnl=-0.4683, notes=notes_in)
        updates = script._plan_rewrite(row, rec)
        notes = json.loads(updates["notes"])
        assert "backfilled_by" not in notes
        assert "backfilled_at" not in notes
        assert "backfilled_source" not in notes
        assert "original_pnl" not in notes
        assert notes["strategy"] == "vwap"

    def test_idempotent_noop_when_already_rebuilt_and_matching(
        self, script,
    ):
        """Already-rebuilt row whose pnl already matches Bybit →
        no rewrite."""
        rec = _bybit_rec(pnl=0.8447)
        notes_in = json.dumps({
            "rebuilt_by": "rebuild_pnl_from_bybit_script",
            "bybit_closed_pnl": 0.8447,
        })
        row = _db_row(id=1540, pnl=0.8447, notes=notes_in)
        updates = script._plan_rewrite(row, rec)
        assert updates is None  # no-op

    def test_rewrites_when_already_rebuilt_but_value_drifted(
        self, script,
    ):
        """If a row carries rebuilt_by stamp but its pnl is
        somehow different (manual edit, partial-overwrite), the
        rebuild re-applies. Idempotency is about correctness, not
        the stamp."""
        rec = _bybit_rec(pnl=0.8447)
        notes_in = json.dumps({
            "rebuilt_by": "rebuild_pnl_from_bybit_script",
            "bybit_closed_pnl": 0.8447,
        })
        row = _db_row(id=1540, pnl=0.5, notes=notes_in)  # drifted
        updates = script._plan_rewrite(row, rec)
        assert updates is not None
        assert abs(updates["pnl"] - 0.8447) < 1e-4


class TestEndToEnd:
    """Drive main() against a real sqlite DB and verify the writes."""

    def _make_db(self, tmp_path):
        db_path = tmp_path / "trade_journal.db"
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL,
                stop_loss REAL,
                take_profit_1 REAL,
                position_size REAL NOT NULL,
                setup_type TEXT,
                entry_reason TEXT,
                exit_reason TEXT,
                pnl REAL, pnl_percent REAL,
                status TEXT DEFAULT 'open',
                notes TEXT,
                is_backtest BOOLEAN DEFAULT 1,
                strategy_name TEXT,
                account_id TEXT NOT NULL DEFAULT 'live',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Trade A: long, opened 06:00, with wrong DB pnl (-0.47 vs
        # Bybit-truth +0.84 — the #1540 scenario)
        conn.execute("""
            INSERT INTO trades (
                id, timestamp, symbol, direction, entry_price,
                position_size, status, exit_reason, pnl,
                is_backtest, strategy_name, account_id, created_at, notes
            ) VALUES (
                1540, '2026-05-18T06:00:00+00:00', 'BTCUSDT', 'long',
                76700.0, 0.004, 'closed', 'tp_cross', -0.4683,
                0, 'vwap', 'bybit_2', '2026-05-18T06:00:00+00:00',
                '{"strategy":"vwap"}'
            )
        """)
        conn.commit()
        conn.close()
        return db_path

    def test_rebuild_writes_correct_pnl(
        self, script, tmp_path, monkeypatch, capsys,
    ):
        db_path = self._make_db(tmp_path)
        monkeypatch.setenv("TRADE_JOURNAL_DB", str(db_path))

        # Stub Bybit client + accounts loader.
        rec = _bybit_rec(side="Sell", entry=76700.0, qty=0.004,
                         exit_=76977.6, pnl=0.8447,
                         created=1779111600000)  # 2026-05-18T07:00:00Z
        # NOTE: open is 06:00; close is 07:00 — chronologically valid.

        from unittest.mock import MagicMock
        fake_client = MagicMock()
        fake_client.get_closed_pnl.return_value = {
            "result": {"list": [rec], "nextPageCursor": None},
        }
        monkeypatch.setattr(script, "bybit_client_for",
                            lambda cfg: fake_client)
        monkeypatch.setattr(script, "load_accounts_dict",
                            lambda: {"bybit_2": {
                                "account_id": "bybit_2",
                                "exchange": "bybit",
                                "market_type": "linear",
                            }})
        monkeypatch.setattr(script, "_bybit_category",
                            lambda cfg: "linear")

        monkeypatch.setattr(sys, "argv", [
            "rebuild_pnl_from_bybit.py",
            "--account", "bybit_2", "--days", "7", "--apply",
        ])
        rc = script.main()
        assert rc == 0
        captured = capsys.readouterr()
        assert "would-rewrite:   1" in captured.out
        assert "wrote 1 row(s)" in captured.out

        # Verify DB
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT pnl, exit_price, notes FROM trades WHERE id=1540"
        ).fetchone()
        conn.close()
        assert abs(row["pnl"] - 0.8447) < 1e-4
        assert abs(row["exit_price"] - 76977.6) < 1e-3
        notes = json.loads(row["notes"])
        assert notes["rebuilt_by"] == "rebuild_pnl_from_bybit_script"
        assert notes["pre_rebuild_pnl"] == -0.4683

    def test_dry_run_does_not_write(
        self, script, tmp_path, monkeypatch, capsys,
    ):
        db_path = self._make_db(tmp_path)
        monkeypatch.setenv("TRADE_JOURNAL_DB", str(db_path))
        rec = _bybit_rec(created=1779111600000)
        from unittest.mock import MagicMock
        fake_client = MagicMock()
        fake_client.get_closed_pnl.return_value = {
            "result": {"list": [rec], "nextPageCursor": None},
        }
        monkeypatch.setattr(script, "bybit_client_for",
                            lambda cfg: fake_client)
        monkeypatch.setattr(script, "load_accounts_dict",
                            lambda: {"bybit_2": {
                                "exchange": "bybit",
                                "market_type": "linear",
                            }})
        monkeypatch.setattr(script, "_bybit_category",
                            lambda cfg: "linear")
        monkeypatch.setattr(sys, "argv", [
            "rebuild_pnl_from_bybit.py",
            "--account", "bybit_2",
        ])
        rc = script.main()
        assert rc == 0
        captured = capsys.readouterr()
        assert "dry-run" in captured.out
        # DB untouched
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT pnl FROM trades WHERE id=1540"
        ).fetchone()
        conn.close()
        assert abs(row[0] - (-0.4683)) < 1e-4
