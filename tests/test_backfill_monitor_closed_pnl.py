"""Tests for scripts/ops/backfill_monitor_closed_pnl.py.

The script targets the cluster of closed-but-fee-blind rows that
the pre-PR-#1409 monitor close path produced — rows where status is
already 'closed' with a real exit_reason (tp_cross etc.) but pnl
holds the gross (no-fees) value the deleted ``_compute_close_pnl``
formula wrote. Visible example: trade #1540, closed at +$1.03 gross
vs Bybit-net of ~+$0.57.

Coverage:
  * candidate filter — picks up closed rows missing the
    bybit_closed_pnl notes stamp, skips backtest / stamped / open /
    >7d-old rows
  * happy path — closed-pnl lookup returns a clean record →
    pnl + exit_price + notes rewrite; status / exit_reason preserved
  * lookup returns None → row stays as-is, listed in skipped
  * malformed record (avg_exit_price=0) → skip
  * idempotency — re-running over already-stamped rows is a no-op
    (the WHERE guard re-checks the notes filter)
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
    path = _REPO_ROOT / "scripts" / "ops" / "backfill_monitor_closed_pnl.py"
    spec = importlib.util.spec_from_file_location(
        "backfill_monitor_closed_pnl", path,
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["backfill_monitor_closed_pnl"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def script():
    return _load_script_module()


@pytest.fixture
def tmp_db(tmp_path) -> Path:
    """Trades table with:
      * #1540 — fee-blind gross row (status=closed, pnl=1.03, no
        bybit_closed_pnl in notes) — the candidate
      * #1541 — already correctly back-filled (notes carries
        bybit_closed_pnl) — must NOT be touched
      * #1542 — open row — must NOT be touched
      * #1543 — backtest row matching the filter shape — must NOT
        be touched
      * #1544 — stale row (created_at > 7 days ago) — must NOT be
        touched
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
    # Candidate #1540 — the bug shape: gross PnL written, no Bybit stamp.
    conn.execute("""
        INSERT INTO trades (
            id, timestamp, symbol, direction, entry_price, exit_price,
            position_size, status, exit_reason, pnl, pnl_percent,
            is_backtest, strategy_name, account_id, created_at, notes
        ) VALUES (
            1540, datetime('now', '-30 minutes'), 'BTCUSDT', 'long',
            76700.0, 76977.6, 0.004, 'closed', 'tp_cross',
            1.03, 0.336, 0, 'vwap', 'bybit_2',
            datetime('now', '-30 minutes'),
            '{"strategy": "vwap"}'
        )
    """)
    # Already back-filled — notes carry bybit_closed_pnl.
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
    # Open row.
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
    # Backtest row matching the would-be candidate shape.
    conn.execute("""
        INSERT INTO trades (
            id, timestamp, symbol, direction, entry_price, exit_price,
            position_size, status, exit_reason, pnl,
            is_backtest, strategy_name, account_id, created_at
        ) VALUES (
            1543, datetime('now', '-20 minutes'), 'BTCUSDT', 'long',
            76700.0, 76900.0, 0.004, 'closed', 'tp_cross', 0.80,
            1, 'vwap', 'bybit_2', datetime('now', '-20 minutes')
        )
    """)
    # Stale row — outside Bybit's 7-day retention window.
    conn.execute("""
        INSERT INTO trades (
            id, timestamp, symbol, direction, entry_price, exit_price,
            position_size, status, exit_reason, pnl,
            is_backtest, strategy_name, account_id, created_at
        ) VALUES (
            1544, datetime('now', '-10 days'), 'BTCUSDT', 'long',
            70000.0, 70500.0, 0.004, 'closed', 'tp_cross', 2.00,
            0, 'vwap', 'bybit_2', datetime('now', '-10 days')
        )
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def fake_cfgs(monkeypatch):
    stub = {"bybit_2": {
        "account_id": "bybit_2",
        "exchange": "bybit",
        "market_type": "linear",
    }}
    monkeypatch.setattr(
        "backfill_monitor_closed_pnl.load_accounts_dict",
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
    """Only #1540 (the fee-blind gross row) should be a candidate.
    The already-stamped, open, backtest, and stale rows are filtered
    out at the SQL layer."""

    def test_yields_only_unstamped_recent_closed_rows(self, script, tmp_db):
        conn = sqlite3.connect(tmp_db)
        conn.row_factory = sqlite3.Row
        rows = script._candidate_rows(conn)
        ids = {r["id"] for r in rows}
        assert ids == {1540}


class TestPlanRow:
    def test_happy_path_writes_net_pnl_and_preserves_exit_reason(
        self, script, tmp_db, fake_cfgs,
    ):
        row = _read_row(tmp_db, 1540)
        with patch.object(
            script, "account_closed_pnl_for_trade",
            return_value={
                "avg_exit_price": 76977.6,
                "closed_pnl": 0.5723,  # Bybit-truth net, replacing gross 1.03
                "qty": 0.004,
                "side": "Sell",
                "closed_at": "1763300000000",
            },
        ):
            updates, reason = script._plan_row(row, fake_cfgs["bybit_2"])
        assert reason is None
        assert updates is not None
        # status / exit_reason intentionally NOT in updates — the
        # monitor close already booked them correctly.
        assert "status" not in updates
        assert "exit_reason" not in updates
        # PnL and exit_price corrected from Bybit
        assert abs(updates["pnl"] - 0.5723) < 1e-6
        assert abs(updates["exit_price"] - 76977.6) < 1e-6
        # pnl_percent = 0.5723 / (76700 * 0.004) * 100 ≈ 0.1866
        assert abs(updates["pnl_percent"] - 0.1866) < 1e-3
        notes = json.loads(updates["notes"])
        assert notes["exit_price_source"] == "bybit_closed_pnl_backfill"
        assert notes["backfilled_source"] == "bybit_closed_pnl"
        assert notes["bybit_closed_pnl"] == 0.5723
        # Original (wrong) pnl preserved as audit trail
        assert notes["original_pnl"] == 1.03
        # Pre-existing notes field preserved
        assert notes["strategy"] == "vwap"

    def test_lookup_returns_none_skips_row(
        self, script, tmp_db, fake_cfgs,
    ):
        row = _read_row(tmp_db, 1540)
        with patch.object(
            script, "account_closed_pnl_for_trade", return_value=None,
        ):
            updates, reason = script._plan_row(row, fake_cfgs["bybit_2"])
        assert updates is None
        assert "returned None" in reason

    def test_zero_exit_price_skips_row(
        self, script, tmp_db, fake_cfgs,
    ):
        row = _read_row(tmp_db, 1540)
        with patch.object(
            script, "account_closed_pnl_for_trade",
            return_value={
                "avg_exit_price": 0.0,
                "closed_pnl": 0.5,
                "qty": 0.004, "side": "Sell", "closed_at": None,
            },
        ):
            updates, reason = script._plan_row(row, fake_cfgs["bybit_2"])
        assert updates is None
        assert "degenerate" in reason

    def test_missing_cfg_skips_row(self, script, tmp_db):
        row = _read_row(tmp_db, 1540)
        updates, reason = script._plan_row(row, None)
        assert updates is None
        assert "no account cfg" in reason

    def test_closed_pnl_none_skips_row(
        self, script, tmp_db, fake_cfgs,
    ):
        """Bybit returned a record but closed_pnl field is missing —
        treat as no usable data, leave the row alone."""
        row = _read_row(tmp_db, 1540)
        with patch.object(
            script, "account_closed_pnl_for_trade",
            return_value={
                "avg_exit_price": 76977.6,
                "closed_pnl": None,
                "qty": 0.004, "side": "Sell", "closed_at": None,
            },
        ):
            updates, reason = script._plan_row(row, fake_cfgs["bybit_2"])
        assert updates is None
        assert "closed_pnl=None" in reason


class TestEndToEnd:
    def test_apply_writes_recovered_rows(
        self, script, tmp_db, fake_cfgs, monkeypatch, capsys,
    ):
        monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_db))
        monkeypatch.setattr(sys, "argv", [
            "backfill_monitor_closed_pnl.py", "--apply",
        ])

        with patch.object(
            script, "account_closed_pnl_for_trade",
            return_value={
                "avg_exit_price": 76977.6,
                "closed_pnl": 0.5723,
                "qty": 0.004, "side": "Sell",
                "closed_at": "1763300000000",
            },
        ):
            rc = script.main()
        assert rc == 0

        captured = capsys.readouterr()
        assert "candidates: 1" in captured.out
        assert "recoverable: 1" in captured.out
        assert "wrote 1 row(s)" in captured.out

        # Candidate row rewritten
        fixed = _read_row(tmp_db, 1540)
        assert abs(fixed["pnl"] - 0.5723) < 1e-6
        assert fixed["status"] == "closed"  # preserved
        assert fixed["exit_reason"] == "tp_cross"  # preserved
        assert "bybit_closed_pnl" in fixed["notes"]
        assert "original_pnl" in fixed["notes"]

        # Already-stamped row untouched
        already_filled = _read_row(tmp_db, 1541)
        assert abs(already_filled["pnl"] - 0.20254788) < 1e-6
        assert already_filled["exit_reason"] == "reconciler_filled"

        # Open / backtest / stale rows untouched
        open_row = _read_row(tmp_db, 1542)
        assert open_row["status"] == "open"
        backtest_row = _read_row(tmp_db, 1543)
        assert abs(backtest_row["pnl"] - 0.80) < 1e-6
        stale_row = _read_row(tmp_db, 1544)
        assert abs(stale_row["pnl"] - 2.00) < 1e-6

    def test_dry_run_does_not_write(
        self, script, tmp_db, fake_cfgs, monkeypatch, capsys,
    ):
        monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_db))
        monkeypatch.setattr(sys, "argv", ["backfill_monitor_closed_pnl.py"])
        with patch.object(
            script, "account_closed_pnl_for_trade",
            return_value={
                "avg_exit_price": 76977.6,
                "closed_pnl": 0.5723,
                "qty": 0.004, "side": "Sell", "closed_at": None,
            },
        ):
            rc = script.main()
        assert rc == 0
        captured = capsys.readouterr()
        assert "dry-run" in captured.out
        # Pnl untouched in dry-run
        row = _read_row(tmp_db, 1540)
        assert abs(row["pnl"] - 1.03) < 1e-6

    def test_idempotent_rerun_is_noop(
        self, script, tmp_db, fake_cfgs, monkeypatch, capsys,
    ):
        """First --apply rewrites #1540; second --apply finds no
        candidates because the notes now contain bybit_closed_pnl."""
        monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_db))
        monkeypatch.setattr(sys, "argv", [
            "backfill_monitor_closed_pnl.py", "--apply",
        ])
        rec = {
            "avg_exit_price": 76977.6,
            "closed_pnl": 0.5723,
            "qty": 0.004, "side": "Sell", "closed_at": None,
        }
        with patch.object(
            script, "account_closed_pnl_for_trade", return_value=rec,
        ):
            script.main()
            capsys.readouterr()  # clear
            rc2 = script.main()
        assert rc2 == 0
        out2 = capsys.readouterr().out
        assert "nothing to backfill" in out2
