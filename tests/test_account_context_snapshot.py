"""Tests for the S-MLOPT-S12 Part B account-context snapshot writer
(`src.units.accounts.context_snapshot`).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.units.accounts.context_snapshot import (
    AccountContextSnapshot,
    daily_state_for,
    drawdown_pct,
    ensure_schema,
    open_trades_count_for,
    write_snapshots,
)
# Build the daily_risk_state fixture from the SAME canonical DDL the
# RiskManager uses in production, so the test schema can never drift from
# the real one again. The prior hand-rolled fixture used a `utc_date`
# column that production never had — masking the real bug where
# daily_state_for queried a non-existent column.
from src.units.accounts.risk import _CREATE_DAILY_RISK_STATE


def _seed_live_db(path: Path) -> None:
    """Create the bits of trade_journal.db the helpers read."""
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, account_id TEXT, "
        "status TEXT, is_backtest INT)"
    )
    conn.execute(_CREATE_DAILY_RISK_STATE)  # canonical schema (PK account_id, date)
    conn.commit()
    conn.close()


def test_ensure_schema_is_idempotent(tmp_path: Path):
    db = tmp_path / "snap.db"
    conn = sqlite3.connect(str(db))
    ensure_schema(conn)
    ensure_schema(conn)  # second call must not raise
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='account_context_snapshots'"
    ).fetchall()
    assert rows and rows[0][0] == "account_context_snapshots"
    conn.close()


def test_write_snapshots_persists_and_dedupes(tmp_path: Path):
    db = tmp_path / "snap.db"
    now = datetime(2026, 6, 7, 12, tzinfo=timezone.utc)
    snap = AccountContextSnapshot(
        captured_at_utc=now,
        order_package_id="pkg-1",
        account_id="prop_1",
        strategy_name="vwap",
        symbol="BTCUSDT",
        direction="long",
        equity=10000.0,
        daily_pnl_realized=-150.0,
        daily_equity_high=10200.0,
        daily_drawdown_pct=0.0196,
        open_trades_count=2,
    )
    n_first = write_snapshots(db, [snap])
    assert n_first == 1
    # Re-writing the same (order_package_id, account_id) is a no-op
    # thanks to the unique key + INSERT OR IGNORE.
    n_second = write_snapshots(db, [snap])
    assert n_second == 0

    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT order_package_id, account_id, equity, open_trades_count, "
        "writer_version FROM account_context_snapshots"
    ).fetchall()
    conn.close()
    assert rows == [("pkg-1", "prop_1", 10000.0, 2, "v1")]


def test_write_snapshots_empty_batch_is_noop(tmp_path: Path):
    assert write_snapshots(tmp_path / "snap.db", []) == 0


def test_write_snapshots_swallows_bad_db_path(tmp_path: Path):
    # Best-effort: a bad path must NOT raise — the trader's flow can
    # never be blocked on a snapshot write failure.
    rc = write_snapshots(
        tmp_path / "does-not-exist" / "nested" / "snap.db",
        [
            AccountContextSnapshot(
                captured_at_utc=datetime.now(timezone.utc),
                order_package_id="x", account_id="y",
            )
        ],
    )
    assert rc == 0


def test_open_trades_count_for_returns_count(tmp_path: Path):
    db = tmp_path / "j.db"
    _seed_live_db(db)
    conn = sqlite3.connect(str(db))
    conn.executemany(
        "INSERT INTO trades(account_id, status, is_backtest) VALUES (?, ?, ?)",
        [
            ("prop_1", "open", 0),
            ("prop_1", "open", 0),
            ("prop_1", "closed", 0),
            ("prop_1", "open", 1),  # backtest row excluded
            ("prop_2", "open", 0),  # other account excluded
        ],
    )
    conn.commit()
    assert open_trades_count_for(conn, "prop_1") == 2
    assert open_trades_count_for(conn, "prop_2") == 1
    assert open_trades_count_for(conn, "absent") == 0
    conn.close()


def test_open_trades_count_returns_none_when_table_missing(tmp_path: Path):
    # A test/dev DB without `trades` should yield None (not 0), so the
    # snapshot lands NULL rather than misleading-zero.
    db = tmp_path / "empty.db"
    conn = sqlite3.connect(str(db))
    assert open_trades_count_for(conn, "prop_1") is None
    conn.close()


def test_daily_state_for_reads_running_totals(tmp_path: Path):
    db = tmp_path / "j.db"
    _seed_live_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO daily_risk_state(account_id, date, daily_pnl, daily_high_equity)"
        " VALUES (?, ?, ?, ?)",
        ("prop_1", "2026-06-07", -120.5, 10300.0),
    )
    conn.commit()
    pnl, peak = daily_state_for(conn, "prop_1", utc_date="2026-06-07")
    assert pnl == -120.5
    assert peak == 10300.0
    # Date that doesn't exist → both None (fresh-boot semantics).
    assert daily_state_for(conn, "prop_1", utc_date="2026-06-06") == (None, None)
    assert daily_state_for(conn, "absent", utc_date="2026-06-07") == (None, None)
    conn.close()


def test_daily_state_returns_none_when_table_missing(tmp_path: Path):
    db = tmp_path / "empty.db"
    conn = sqlite3.connect(str(db))
    assert daily_state_for(conn, "prop_1", utc_date="2026-06-07") == (None, None)
    conn.close()


def test_drawdown_pct_handles_edges():
    assert drawdown_pct(9800.0, 10000.0) == 0.02
    # Below peak by a hair → positive
    assert drawdown_pct(10001.0, 10000.0) == 0.0  # clamped at 0 (above peak)
    # Missing inputs → None (not 0.0 — preserve unknown).
    assert drawdown_pct(None, 10000.0) is None
    assert drawdown_pct(10000.0, None) is None
    # Non-positive peak → None (can't compute a meaningful pct).
    assert drawdown_pct(10000.0, 0) is None
    assert drawdown_pct(10000.0, -1) is None
