"""Tests for the `account_context_snapshots` LEFT JOIN in TradeOutcomesBuilder.

S-MLOPT-S12 Part B: when built with ``include_snapshots=True`` and the
``account_context_snapshots`` table is present, each ``trade_outcomes`` row
gets the five as-of-signal account-state columns (joined via
trades → order_packages → account_context_snapshots). When the flag is off,
or a row has no matching snapshot, the columns serialize as ``None``.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ml.datasets.families.trade_outcomes import TradeOutcomesBuilder
from ml.datasets.validate import validate_dataset

_DDL = """
CREATE TABLE trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT,
    symbol          TEXT,
    direction       TEXT,
    setup_type      TEXT,
    killzone        TEXT,
    bias            TEXT,
    pnl             REAL,
    pnl_percent     REAL,
    status          TEXT,
    is_backtest     INTEGER DEFAULT 0,
    strategy_name   TEXT,
    account_id      TEXT NOT NULL DEFAULT 'live',
    created_at      TEXT DEFAULT (datetime('now'))
);
CREATE TABLE order_packages (
    order_package_id  TEXT PRIMARY KEY,
    linked_trade_id   INTEGER
);
CREATE TABLE account_context_snapshots (
    order_package_id      TEXT,
    account_id            TEXT,
    equity                REAL,
    daily_pnl_realized    REAL,
    daily_equity_high     REAL,
    daily_drawdown_pct    REAL,
    open_trades_count     INTEGER
);
"""

_SNAP_COLS = (
    "equity_at_signal",
    "daily_pnl_realized_at_signal",
    "daily_equity_high_at_signal",
    "daily_drawdown_pct_at_signal",
    "open_trades_count_at_signal",
)


def _make_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "trade_journal.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_DDL)
    # trade 1: closed, has an order package + a matching snapshot
    # trade 2: closed, NO order package (snapshot cols must be None)
    conn.execute(
        "INSERT INTO trades (id, timestamp, symbol, direction, setup_type, "
        "killzone, bias, pnl, pnl_percent, status, is_backtest, "
        "strategy_name, account_id, created_at) VALUES "
        "(1, '2026-05-01T12:00:00Z', 'BTCUSDT', 'LONG', 'FVG', 'NY', "
        "'BULLISH', 12.0, 0.01, 'closed', 0, 'vwap', 'live', "
        "'2026-05-01T12:00:00Z')"
    )
    conn.execute(
        "INSERT INTO trades (id, timestamp, symbol, direction, setup_type, "
        "killzone, bias, pnl, pnl_percent, status, is_backtest, "
        "strategy_name, account_id, created_at) VALUES "
        "(2, '2026-05-01T13:00:00Z', 'ETHUSDT', 'SHORT', 'SWEEP', 'LO', "
        "'BEARISH', -4.0, -0.005, 'closed', 0, 'turtle', 'live', "
        "'2026-05-01T13:00:00Z')"
    )
    conn.execute(
        "INSERT INTO order_packages (order_package_id, linked_trade_id) "
        "VALUES ('pkg-1', 1)"
    )
    conn.execute(
        "INSERT INTO account_context_snapshots (order_package_id, account_id, "
        "equity, daily_pnl_realized, daily_equity_high, daily_drawdown_pct, "
        "open_trades_count) VALUES ('pkg-1', 'live', 1000.0, -50.0, 1100.0, "
        "9.1, 2)"
    )
    conn.commit()
    conn.close()
    return db_path


def test_join_populates_snapshot_columns(tmp_path: Path):
    db_path = _make_db(tmp_path)
    out = tmp_path / "datasets"
    builder = TradeOutcomesBuilder()
    paths = builder.build(
        output_dir=out,
        version="v001",
        source=str(db_path),
        commit_sha="abc123",
        db_path=db_path,
        include_snapshots=True,
    )
    emitted = [
        json.loads(line)
        for line in paths.data.read_text().splitlines()
        if line.strip()
    ]
    by_id = {r["id"]: r for r in emitted}
    assert set(by_id) == {1, 2}

    # trade 1 — snapshot attached
    assert by_id[1]["equity_at_signal"] == 1000.0
    assert by_id[1]["daily_pnl_realized_at_signal"] == -50.0
    assert by_id[1]["daily_equity_high_at_signal"] == 1100.0
    assert by_id[1]["daily_drawdown_pct_at_signal"] == 9.1
    assert by_id[1]["open_trades_count_at_signal"] == 2
    assert by_id[1]["won"] is True

    # trade 2 — no order package → snapshot cols None
    for col in _SNAP_COLS:
        assert by_id[2][col] is None
    assert by_id[2]["won"] is False

    # builder version bumped + dataset still validates
    metadata = json.loads(paths.metadata.read_text())
    assert metadata["builder_version"] == "v2"
    for col in _SNAP_COLS:
        assert col in metadata["schema"]
    report = validate_dataset(paths.root)
    assert report.ok, report.errors


def test_flag_off_leaves_snapshot_columns_none(tmp_path: Path):
    """Default build (include_snapshots=False) still carries the columns as None."""
    db_path = _make_db(tmp_path)
    out = tmp_path / "datasets"
    builder = TradeOutcomesBuilder()
    paths = builder.build(
        output_dir=out,
        version="v001",
        source=str(db_path),
        commit_sha="abc123",
        db_path=db_path,
    )
    emitted = [
        json.loads(line)
        for line in paths.data.read_text().splitlines()
        if line.strip()
    ]
    assert len(emitted) == 2
    for row in emitted:
        for col in _SNAP_COLS:
            assert row[col] is None
    report = validate_dataset(paths.root)
    assert report.ok, report.errors
