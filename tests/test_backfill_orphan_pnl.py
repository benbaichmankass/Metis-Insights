"""Tests for scripts/ops/backfill_orphan_pnl.py.

The script targets the 2026-05-15/16 orphan cluster left behind by
the pre-#1268 UUID-orderid bug — trades that watchdog-orphaned with
``status='orphaned'``, ``exit_reason='stuck_strategy_watchdog'``,
``exit_price=NULL``. PR #1299 makes new orphans of this shape
impossible; this script retroactively recovers the historical rows.

Coverage:
  * candidate filter — only the right rows get picked up
  * happy path — closed-pnl lookup returns a clean record →
    row rewrites to ``status='closed'`` with the real exit price,
    real PnL, audit fields preserved
  * lookup returns ``None`` → row stays orphaned, listed in skip
  * malformed record (``avg_exit_price=0``) → skip, row stays orphaned
  * idempotency — re-running over already-rewritten rows is a no-op
  * dry-run does not write
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_script_module():
    """Load scripts/ops/backfill_orphan_pnl.py as a module.

    The script is in a non-package directory so we import it
    explicitly rather than via the test sys.path.
    """
    path = _REPO_ROOT / "scripts" / "ops" / "backfill_orphan_pnl.py"
    spec = importlib.util.spec_from_file_location(
        "backfill_orphan_pnl", path,
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["backfill_orphan_pnl"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def script():
    return _load_script_module()


@pytest.fixture
def tmp_db(tmp_path) -> Path:
    """Create a minimal trades-table sqlite DB with two orphan rows
    + one already-closed row + one open row. The orphan rows are
    the only candidates the script should consider."""
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
    # Orphan row #1 — long BTCUSDT
    conn.execute("""
        INSERT INTO trades (
            id, timestamp, symbol, direction, entry_price, position_size,
            status, exit_reason, is_backtest, strategy_name, account_id,
            created_at, notes
        ) VALUES (
            1450, '2026-05-15T04:59:00+00:00', 'BTCUSDT', 'long',
            80000.0, 0.005, 'orphaned', 'stuck_strategy_watchdog',
            0, 'vwap', 'bybit_2', '2026-05-15T04:59:00+00:00',
            ?
        )
    """, (json.dumps({
        "orphaned_at": "2026-05-15T06:30:00+00:00",
        "orphaned_by": "strategy_watchdog",
        "orphaned_reason": "DB-open with no matching position",
    }),))
    # Orphan row #2 — short ETHUSDT
    conn.execute("""
        INSERT INTO trades (
            id, timestamp, symbol, direction, entry_price, position_size,
            status, exit_reason, is_backtest, strategy_name, account_id,
            created_at, notes
        ) VALUES (
            1454, '2026-05-15T11:00:00+00:00', 'ETHUSDT', 'short',
            3000.0, 0.1, 'orphaned', 'stuck_strategy_watchdog',
            0, 'vwap', 'bybit_2', '2026-05-15T11:00:00+00:00',
            ?
        )
    """, (json.dumps({
        "orphaned_at": "2026-05-15T12:00:00+00:00",
        "orphaned_by": "strategy_watchdog",
        "orphaned_reason": "DB-open with no matching position",
    }),))
    # Non-candidate: already closed cleanly
    conn.execute("""
        INSERT INTO trades (
            id, timestamp, symbol, direction, entry_price, exit_price,
            position_size, status, exit_reason, pnl, is_backtest,
            strategy_name, account_id, created_at
        ) VALUES (
            1467, '2026-05-16T07:00:00+00:00', 'BTCUSDT', 'long',
            80500.0, 80800.0, 0.005, 'closed', 'reconciler_filled',
            1.5, 0, 'vwap', 'bybit_2', '2026-05-16T07:00:00+00:00'
        )
    """)
    # Non-candidate: open
    conn.execute("""
        INSERT INTO trades (
            id, timestamp, symbol, direction, entry_price,
            position_size, status, is_backtest, strategy_name,
            account_id, created_at
        ) VALUES (
            1500, '2026-05-16T15:00:00+00:00', 'BTCUSDT', 'long',
            81000.0, 0.005, 'open', 0, 'vwap', 'bybit_2',
            '2026-05-16T15:00:00+00:00'
        )
    """)
    # Non-candidate: backtest row that happens to be orphaned
    conn.execute("""
        INSERT INTO trades (
            id, timestamp, symbol, direction, entry_price,
            position_size, status, exit_reason, is_backtest,
            strategy_name, account_id, created_at
        ) VALUES (
            9000, '2026-05-15T04:59:00+00:00', 'BTCUSDT', 'long',
            80000.0, 0.005, 'orphaned', 'stuck_strategy_watchdog',
            1, 'vwap', 'bybit_2', '2026-05-15T04:59:00+00:00'
        )
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def fake_cfgs(monkeypatch):
    """Replace ``load_accounts_dict`` (re-exported into the script's
    namespace via ``from src.config.accounts_loader import ...``)
    with a stub returning a single bybit_2 entry. The script's
    ``account_closed_pnl_for_trade`` mock receives this dict but
    doesn't inspect its contents — any truthy dict will do."""
    stub = {"bybit_2": {
        "account_id": "bybit_2",
        "exchange": "bybit",
        "market_type": "linear",
    }}
    monkeypatch.setattr(
        "backfill_orphan_pnl.load_accounts_dict",
        lambda: stub,
    )
    return stub


def _read_row(db_path: Path, trade_id: int) -> sqlite3.Row:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,))
    row = cur.fetchone()
    conn.close()
    return row


class TestCandidateFilter:
    """``_candidate_rows`` should yield exactly the orphan rows
    we want to backfill — not the closed ones, not the open ones,
    not the backtest ones."""

    def test_yields_only_orphan_non_backtest_rows(self, script, tmp_db):
        conn = sqlite3.connect(tmp_db)
        conn.row_factory = sqlite3.Row
        rows = script._candidate_rows(conn)
        ids = {r["id"] for r in rows}
        assert ids == {1450, 1454}


class TestPlanRow:
    """Per-row planning: given a candidate row + mocked closed-pnl
    lookup, the planner produces an UPDATE dict OR a skip reason."""

    def test_happy_path_long_returns_full_update(
        self, script, tmp_db, fake_cfgs,
    ):
        row = _read_row(tmp_db, 1450)
        with patch.object(
            script, "account_closed_pnl_for_trade",
            return_value={
                "avg_exit_price": 79235.7,
                "avg_entry_price": 80000.0,
                "closed_pnl": -3.82,
                "qty": 0.005,
                "side": "Sell",
                "closed_at": "1762620000000",
            },
        ):
            updates, reason = script._plan_row(row, fake_cfgs["bybit_2"])
        assert reason is None
        assert updates is not None
        assert updates["status"] == "closed"
        assert updates["exit_reason"] == "backfill_closed_pnl_recovery"
        assert abs(updates["exit_price"] - 79235.7) < 1e-6
        assert abs(updates["pnl"] - (-3.82)) < 1e-6
        # pnl_percent = -3.82 / (80000 * 0.005) * 100 = -0.955
        assert abs(updates["pnl_percent"] - (-0.955)) < 1e-3
        notes = json.loads(updates["notes"])
        # Backfill audit fields written
        assert notes["exit_price_source"] == "bybit_closed_pnl_backfill"
        assert notes["backfilled_source"] == "bybit_closed_pnl"
        assert notes["backfilled_pnl"] == -3.82
        # Original orphan audit fields preserved
        assert notes["orphaned_by"] == "strategy_watchdog"
        assert notes["orphaned_reason"] == "DB-open with no matching position"

    def test_lookup_returns_none_skips_row(
        self, script, tmp_db, fake_cfgs,
    ):
        row = _read_row(tmp_db, 1450)
        with patch.object(
            script, "account_closed_pnl_for_trade", return_value=None,
        ):
            updates, reason = script._plan_row(row, fake_cfgs["bybit_2"])
        assert updates is None
        assert "returned None" in reason

    def test_zero_exit_price_skips_row(
        self, script, tmp_db, fake_cfgs,
    ):
        row = _read_row(tmp_db, 1450)
        with patch.object(
            script, "account_closed_pnl_for_trade",
            return_value={
                "avg_exit_price": 0.0,
                "closed_pnl": 0.0,
                "qty": 0.005,
                "side": "Sell",
                "closed_at": None,
            },
        ):
            updates, reason = script._plan_row(row, fake_cfgs["bybit_2"])
        assert updates is None
        assert "degenerate" in reason

    def test_missing_cfg_skips_row(self, script, tmp_db):
        row = _read_row(tmp_db, 1450)
        updates, reason = script._plan_row(row, None)
        assert updates is None
        assert "no account cfg" in reason


class TestEndToEnd:
    """Drive ``main()`` with mocked Bybit + a real tmp DB and
    verify the rows get rewritten as expected."""

    def test_apply_writes_recovered_rows(
        self, script, tmp_db, fake_cfgs, monkeypatch, capsys,
    ):
        monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_db))
        monkeypatch.setattr(sys, "argv", [
            "backfill_orphan_pnl.py", "--apply",
        ])

        def fake_lookup(account, *, symbol, direction, **kwargs):
            # Symbol-dispatched fake — long BTC → small loss,
            # short ETH → small gain.
            if symbol == "BTCUSDT" and direction == "long":
                return {
                    "avg_exit_price": 79235.7,
                    "closed_pnl": -3.82,
                    "qty": 0.005,
                    "side": "Sell",
                    "closed_at": "1762620000000",
                }
            if symbol == "ETHUSDT" and direction == "short":
                return {
                    "avg_exit_price": 2995.0,
                    "closed_pnl": 0.5,
                    "qty": 0.1,
                    "side": "Buy",
                    "closed_at": "1762630000000",
                }
            return None

        with patch.object(
            script, "account_closed_pnl_for_trade",
            side_effect=fake_lookup,
        ):
            rc = script.main()
        assert rc == 0

        captured = capsys.readouterr()
        assert "candidates: 2" in captured.out
        assert "recoverable: 2" in captured.out
        assert "wrote 2 row(s)" in captured.out

        # Verify both rows now closed with recovered fields
        long_row = _read_row(tmp_db, 1450)
        assert long_row["status"] == "closed"
        assert long_row["exit_reason"] == "backfill_closed_pnl_recovery"
        assert abs(long_row["exit_price"] - 79235.7) < 1e-6
        assert abs(long_row["pnl"] - (-3.82)) < 1e-6

        short_row = _read_row(tmp_db, 1454)
        assert short_row["status"] == "closed"
        assert abs(short_row["exit_price"] - 2995.0) < 1e-6
        assert abs(short_row["pnl"] - 0.5) < 1e-6

        # Untouched control rows
        closed_row = _read_row(tmp_db, 1467)
        assert closed_row["exit_reason"] == "reconciler_filled"  # unchanged
        open_row = _read_row(tmp_db, 1500)
        assert open_row["status"] == "open"  # unchanged
        backtest_row = _read_row(tmp_db, 9000)
        assert backtest_row["status"] == "orphaned"  # unchanged

    def test_dry_run_does_not_write(
        self, script, tmp_db, fake_cfgs, monkeypatch, capsys,
    ):
        monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_db))
        monkeypatch.setattr(sys, "argv", ["backfill_orphan_pnl.py"])
        with patch.object(
            script, "account_closed_pnl_for_trade",
            return_value={
                "avg_exit_price": 79235.7,
                "closed_pnl": -3.82,
                "qty": 0.005,
                "side": "Sell",
                "closed_at": None,
            },
        ):
            rc = script.main()
        assert rc == 0
        captured = capsys.readouterr()
        assert "dry-run" in captured.out

        row = _read_row(tmp_db, 1450)
        assert row["status"] == "orphaned"  # untouched
        assert row["exit_price"] is None

    def test_idempotent_rerun_is_noop(
        self, script, tmp_db, fake_cfgs, monkeypatch, capsys,
    ):
        """After a successful --apply, a second --apply finds zero
        candidates and exits clean."""
        monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_db))
        monkeypatch.setattr(sys, "argv", [
            "backfill_orphan_pnl.py", "--apply",
        ])
        with patch.object(
            script, "account_closed_pnl_for_trade",
            return_value={
                "avg_exit_price": 79235.7,
                "closed_pnl": -3.82,
                "qty": 0.005,
                "side": "Sell",
                "closed_at": None,
            },
        ):
            script.main()  # first run — writes
            capsys.readouterr()
            rc = script.main()  # second run — no-op
        assert rc == 0
        captured = capsys.readouterr()
        assert "nothing to backfill" in captured.out
