"""Tests for scripts/ops/revert_backfill_monitor_closed_pnl.py.

The script reverses the 2026-05-18 dispatch of
backfill-monitor-closed-pnl (issue #1411). Key invariant: it must
restore each backfilled row to its pre-backfill state using only
data the backfill itself preserved in ``notes`` (original_pnl +
the gross-PnL inverse formula).

Coverage:
  * candidate filter — only rows carrying the backfill stamp
  * happy path — pnl + pnl_percent + exit_price + notes all
    restored; reverted_at audit stamp added
  * round-trip — apply backfill output as input to revert →
    pnl is back to the pre-backfill value
  * original_pnl=null branch — pre-backfill row was already at
    NULL pnl (the SSOT shape from PR #1409); revert restores
    NULL on all derived fields
  * skip paths — malformed notes, missing original_pnl,
    non-numeric original_pnl
  * idempotency — re-running on already-reverted rows is a no-op
  * dry-run does not write
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_script_module():
    path = _REPO_ROOT / "scripts" / "ops" / "revert_backfill_monitor_closed_pnl.py"
    spec = importlib.util.spec_from_file_location(
        "revert_backfill_monitor_closed_pnl", path,
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["revert_backfill_monitor_closed_pnl"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def script():
    return _load_script_module()


@pytest.fixture
def tmp_db(tmp_path) -> Path:
    """Trades table with:
      * #1540 — fully-backfilled row that we want to revert. The
        notes carry the audit trail from the backfill.
      * #1541 — a row carrying bybit_closed_pnl but NOT the
        backfilled_by stamp (i.e. it was Bybit-truth from the
        reconciler path, not the bad backfill). Must NOT be touched.
      * #1542 — open row. Must NOT be touched.
      * #1543 — a backfilled row whose pre-backfill pnl was NULL
        (because the row was closed under the new SSOT model
        before the backfill ran, then backfill matched the wrong
        Bybit record). Revert should restore pnl=NULL.
    """
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
            take_profit_2 REAL,
            take_profit_3 REAL,
            position_size REAL NOT NULL,
            setup_type TEXT,
            killzone TEXT,
            bias TEXT,
            entry_reason TEXT,
            exit_reason TEXT,
            pnl REAL,
            pnl_percent REAL,
            status TEXT DEFAULT 'open',
            notes TEXT,
            is_backtest BOOLEAN DEFAULT 1,
            strategy_name TEXT,
            account_id TEXT NOT NULL DEFAULT 'live',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # #1540 — backfilled row with original_pnl=1.03 (gross) and
    # current pnl=0.5723 (the wrong Bybit-truth from the broken
    # matcher). Long BTCUSDT, entry=76700, size=0.004.
    # gross formula: exit = entry + pnl/size = 76700 + 1.03/0.004 = 76957.5
    conn.execute("""
        INSERT INTO trades (
            id, timestamp, symbol, direction, entry_price, exit_price,
            position_size, status, exit_reason, pnl, pnl_percent,
            is_backtest, strategy_name, account_id, created_at, notes
        ) VALUES (
            1540, datetime('now', '-30 minutes'), 'BTCUSDT', 'long',
            76700.0, 76977.6, 0.004, 'closed', 'tp_cross',
            0.5723, 0.1866, 0, 'vwap', 'bybit_2',
            datetime('now', '-30 minutes'),
            ?
        )
    """, (json.dumps({
        "strategy": "vwap",
        "backfilled_at": "2026-05-18T12:06:50Z",
        "backfilled_by": "backfill_monitor_closed_pnl_script",
        "backfilled_source": "bybit_closed_pnl",
        "bybit_closed_pnl": 0.5723,
        "original_pnl": 1.03,
        "exit_price_source": "bybit_closed_pnl_backfill",
        "closed_at": "1763300000000",
    }),))
    # #1541 — reconciler-path row, NOT backfilled. The bybit_closed_pnl
    # stamp exists but the backfilled_by stamp does not.
    conn.execute("""
        INSERT INTO trades (
            id, timestamp, symbol, direction, entry_price, exit_price,
            position_size, status, exit_reason, pnl, pnl_percent,
            is_backtest, strategy_name, account_id, created_at, notes
        ) VALUES (
            1541, datetime('now', '-25 minutes'), 'BTCUSDT', 'long',
            76600.0, 76700.0, 0.004, 'closed', 'reconciler_filled',
            0.20254788, 0.066, 0, 'vwap', 'bybit_2',
            datetime('now', '-25 minutes'),
            ?
        )
    """, (json.dumps({"bybit_closed_pnl": 0.20254788}),))
    # #1542 — open row.
    conn.execute("""
        INSERT INTO trades (
            id, timestamp, symbol, direction, entry_price,
            position_size, status, is_backtest, strategy_name,
            account_id, created_at
        ) VALUES (
            1542, datetime('now', '-5 minutes'), 'BTCUSDT', 'long',
            77000.0, 0.004, 'open', 0, 'vwap', 'bybit_2',
            datetime('now', '-5 minutes')
        )
    """)
    # #1543 — backfilled row whose pre-backfill pnl was NULL (the
    # SSOT shape from PR #1409). Revert should restore pnl=NULL +
    # null pnl_percent + null exit_price.
    conn.execute("""
        INSERT INTO trades (
            id, timestamp, symbol, direction, entry_price, exit_price,
            position_size, status, exit_reason, pnl, pnl_percent,
            is_backtest, strategy_name, account_id, created_at, notes
        ) VALUES (
            1543, datetime('now', '-10 minutes'), 'ETHUSDT', 'short',
            3000.0, 2995.0, 0.1, 'closed', 'tp_cross',
            0.5, 0.166, 0, 'vwap', 'bybit_2',
            datetime('now', '-10 minutes'),
            ?
        )
    """, (json.dumps({
        "backfilled_at": "2026-05-18T12:06:51Z",
        "backfilled_by": "backfill_monitor_closed_pnl_script",
        "backfilled_source": "bybit_closed_pnl",
        "bybit_closed_pnl": 0.5,
        "original_pnl": None,  # pre-backfill row was already NULL
        "exit_price_source": "bybit_closed_pnl_backfill",
    }),))
    conn.commit()
    conn.close()
    return db_path


def _read_row(db_path: Path, trade_id: int) -> sqlite3.Row:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,))
    row = cur.fetchone()
    conn.close()
    return row


class TestCandidateFilter:
    def test_yields_only_backfill_stamped_rows(self, script, tmp_db):
        conn = sqlite3.connect(tmp_db)
        conn.row_factory = sqlite3.Row
        rows = script._candidate_rows(conn)
        ids = {r["id"] for r in rows}
        # #1540 + #1543 carry the backfilled_by stamp.
        # #1541 carries bybit_closed_pnl but no backfilled_by — skip.
        # #1542 is open and has no notes — skip.
        assert ids == {1540, 1543}


class TestPlanRow:
    def test_happy_path_restores_pnl_and_derives_exit_price(
        self, script, tmp_db,
    ):
        row = _read_row(tmp_db, 1540)
        updates, reason = script._plan_row(row)
        assert reason is None
        assert updates is not None
        # Restored pnl = original_pnl = 1.03 (the gross value)
        assert abs(updates["pnl"] - 1.03) < 1e-6
        # exit_price derived from gross formula:
        # exit = entry + pnl/size = 76700 + 1.03/0.004 = 76957.5
        assert abs(updates["exit_price"] - 76957.5) < 1e-3
        # pnl_percent = 1.03 / (76700 * 0.004) * 100 = 0.3358
        assert abs(updates["pnl_percent"] - 0.3358) < 1e-3
        # Notes scrubbed of backfill stamps but pre-existing fields
        # ("strategy") preserved; reverted_at/reverted_by added.
        notes = json.loads(updates["notes"])
        assert "backfilled_by" not in notes
        assert "backfilled_at" not in notes
        assert "backfilled_source" not in notes
        assert "bybit_closed_pnl" not in notes
        assert "original_pnl" not in notes
        assert "exit_price_source" not in notes
        assert "closed_at" not in notes
        assert notes["strategy"] == "vwap"  # preserved
        assert notes["reverted_by"] == "revert_backfill_monitor_closed_pnl_script"
        assert "reverted_at" in notes

    def test_null_original_pnl_restores_null_fields(
        self, script, tmp_db,
    ):
        """The pre-backfill row was already in PR #1409's SSOT
        NULL-pnl state. Revert returns it to that state — pnl,
        pnl_percent, exit_price all NULL. The live sweep will then
        refill from Bybit on the next tick (with the correct
        record this time, assuming the matcher is fixed)."""
        row = _read_row(tmp_db, 1543)
        updates, reason = script._plan_row(row)
        assert reason is None
        assert updates is not None
        assert updates["pnl"] is None
        assert updates["pnl_percent"] is None
        assert updates["exit_price"] is None
        notes = json.loads(updates["notes"])
        assert "bybit_closed_pnl" not in notes

    def test_short_direction_exit_price_math(self, script, tmp_db):
        """Short side: exit = entry - pnl/size. Construct a short
        row with original_pnl=0.4 and verify exit_price algebra."""
        # Reuse #1543's slot via a fresh manual row.
        conn = sqlite3.connect(tmp_db)
        conn.execute("""
            INSERT INTO trades (
                id, timestamp, symbol, direction, entry_price,
                exit_price, position_size, status, exit_reason,
                pnl, is_backtest, strategy_name, account_id,
                created_at, notes
            ) VALUES (
                1600, datetime('now', '-15 minutes'), 'ETHUSDT', 'short',
                3000.0, 2995.0, 0.1, 'closed', 'tp_cross',
                0.5, 0, 'vwap', 'bybit_2',
                datetime('now', '-15 minutes'),
                ?
            )
        """, (json.dumps({
            "backfilled_by": "backfill_monitor_closed_pnl_script",
            "original_pnl": 0.4,  # gross: pre-backfill value
        }),))
        conn.commit()
        conn.close()

        row = _read_row(tmp_db, 1600)
        updates, _ = script._plan_row(row)
        # exit = entry - pnl/size = 3000 - 0.4/0.1 = 2996.0
        assert abs(updates["exit_price"] - 2996.0) < 1e-3
        assert abs(updates["pnl"] - 0.4) < 1e-6

    def test_missing_backfilled_by_skips(self, script):
        # Build a fake row dict without backfilled_by.
        class FakeRow(dict):
            def __getitem__(self, k): return dict.__getitem__(self, k)
        row = FakeRow(
            entry_price=100.0, position_size=1.0, direction="long",
            pnl=0.5, notes='{"foo": "bar"}',
        )
        updates, reason = script._plan_row(row)
        assert updates is None
        assert "no backfilled_by" in reason

    def test_missing_original_pnl_skips(self, script):
        class FakeRow(dict):
            def __getitem__(self, k): return dict.__getitem__(self, k)
        row = FakeRow(
            entry_price=100.0, position_size=1.0, direction="long",
            pnl=0.5, notes=json.dumps({
                "backfilled_by": "backfill_monitor_closed_pnl_script",
                # original_pnl deliberately missing
            }),
        )
        updates, reason = script._plan_row(row)
        assert updates is None
        assert "no original_pnl" in reason

    def test_malformed_notes_skips(self, script):
        class FakeRow(dict):
            def __getitem__(self, k): return dict.__getitem__(self, k)
        # Truncated JSON — backfill's 500-char cap may have left
        # some rows with unparseable notes.
        row = FakeRow(
            entry_price=100.0, position_size=1.0, direction="long",
            pnl=0.5,
            notes='{"backfilled_by": "backfill_monitor_closed_p',  # truncated
        )
        updates, reason = script._plan_row(row)
        assert updates is None
        assert "malformed" in reason or "empty" in reason


class TestEndToEnd:
    def test_apply_reverts_stamped_rows(
        self, script, tmp_db, monkeypatch, capsys,
    ):
        monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_db))
        monkeypatch.setattr(sys, "argv", [
            "revert_backfill_monitor_closed_pnl.py", "--apply",
        ])
        rc = script.main()
        assert rc == 0
        captured = capsys.readouterr()
        assert "candidates: 2" in captured.out
        assert "revertable: 2" in captured.out
        assert "wrote 2 row(s)" in captured.out

        # #1540 restored to gross PnL
        r1540 = _read_row(tmp_db, 1540)
        assert abs(r1540["pnl"] - 1.03) < 1e-6
        # exit_price derived: 76700 + 1.03/0.004 = 76957.5
        assert abs(r1540["exit_price"] - 76957.5) < 1e-3
        assert r1540["status"] == "closed"  # preserved
        assert r1540["exit_reason"] == "tp_cross"  # preserved

        # #1543 restored to NULL pnl
        r1543 = _read_row(tmp_db, 1543)
        assert r1543["pnl"] is None
        assert r1543["exit_price"] is None
        assert r1543["pnl_percent"] is None

        # #1541 untouched (no backfill stamp)
        r1541 = _read_row(tmp_db, 1541)
        assert abs(r1541["pnl"] - 0.20254788) < 1e-6
        # #1542 untouched (open)
        r1542 = _read_row(tmp_db, 1542)
        assert r1542["status"] == "open"

    def test_idempotent_rerun_is_noop(
        self, script, tmp_db, monkeypatch, capsys,
    ):
        monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_db))
        monkeypatch.setattr(sys, "argv", [
            "revert_backfill_monitor_closed_pnl.py", "--apply",
        ])
        script.main()
        capsys.readouterr()
        rc2 = script.main()
        assert rc2 == 0
        out2 = capsys.readouterr().out
        assert "nothing to revert" in out2

    def test_dry_run_does_not_write(
        self, script, tmp_db, monkeypatch, capsys,
    ):
        monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_db))
        monkeypatch.setattr(sys, "argv",
                            ["revert_backfill_monitor_closed_pnl.py"])
        rc = script.main()
        assert rc == 0
        captured = capsys.readouterr()
        assert "dry-run" in captured.out
        # Backfill-state values untouched
        r1540 = _read_row(tmp_db, 1540)
        assert abs(r1540["pnl"] - 0.5723) < 1e-6
