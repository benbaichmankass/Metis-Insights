"""End-to-end test for `BacktestResultsBuilder` against a synthetic SQLite."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from ml.datasets.builder import VersionConflict
from ml.datasets.families.backtest_results import BacktestResultsBuilder
from ml.datasets.validate import validate_dataset


_DDL = """
CREATE TABLE backtest_results (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date            TEXT,
    strategy_version    TEXT,
    start_date          TEXT,
    end_date            TEXT,
    total_trades        INTEGER,
    winning_trades      INTEGER,
    losing_trades       INTEGER,
    win_rate            REAL,
    profit_factor       REAL,
    expectancy          REAL,
    max_drawdown        REAL,
    max_drawdown_pct    REAL,
    sharpe_ratio        REAL,
    total_pnl           REAL,
    total_pnl_pct       REAL,
    avg_win             REAL,
    avg_loss            REAL,
    largest_win         REAL,
    largest_loss        REAL,
    created_at          TEXT DEFAULT (datetime('now'))
);
"""


def _make_db(tmp_path: Path, rows: list[tuple]) -> Path:
    db_path = tmp_path / "trade_journal.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_DDL)
    conn.executemany(
        "INSERT INTO backtest_results (run_date, strategy_version, start_date, "
        "end_date, total_trades, winning_trades, losing_trades, win_rate, "
        "sharpe_ratio, total_pnl, total_pnl_pct, max_drawdown_pct) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()
    return db_path


def test_build_round_trip(tmp_path: Path):
    db_path = _make_db(
        tmp_path,
        [
            ("2026-05-01", "vwap-v2", "2025-01-01", "2025-12-31",
             100, 60, 40, 0.6, 1.2, 1234.5, 0.123, 0.05),
            ("2026-05-02", "vwap-v2", "2025-01-01", "2025-12-31",
             50, 25, 25, 0.5, 0.8, 200.0, 0.02, 0.07),
        ],
    )
    out = tmp_path / "datasets"
    builder = BacktestResultsBuilder()
    paths = builder.build(
        output_dir=out,
        version="v001",
        source=str(db_path),
        commit_sha="deadbeef",
        notes="smoke",
        db_path=db_path,
    )

    assert paths.root == out / "backtest_results" / "all" / "all" / "v001"
    assert paths.metadata.is_file()
    assert paths.data.is_file()

    with paths.data.open() as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    assert len(rows) == 2
    assert rows[0]["strategy_version"] == "vwap-v2"
    assert rows[0]["win_rate"] == 0.6

    metadata = json.loads(paths.metadata.read_text())
    assert metadata["family"] == "backtest_results"
    assert metadata["row_count"] == 2
    assert metadata["generation_commit_sha"] == "deadbeef"
    assert metadata["leakage_test_status"] == "n/a"
    assert "win_rate" in metadata["schema"]

    report = validate_dataset(paths.root)
    assert report.ok, report.errors


def test_build_strategy_version_filter(tmp_path: Path):
    db_path = _make_db(
        tmp_path,
        [
            ("2026-05-01", "vwap-v2", "2025-01-01", "2025-12-31",
             100, 60, 40, 0.6, 1.2, 1234.5, 0.123, 0.05),
            ("2026-05-02", "turtle-v1", "2025-01-01", "2025-12-31",
             50, 25, 25, 0.5, 0.8, 200.0, 0.02, 0.07),
        ],
    )
    out = tmp_path / "datasets"
    builder = BacktestResultsBuilder()
    builder.build(
        output_dir=out,
        version="v001",
        source=str(db_path),
        commit_sha="deadbeef",
        symbol_scope="all",
        timeframe="all",
        db_path=db_path,
        strategy_version="vwap-v2",
    )
    data_path = out / "backtest_results" / "all" / "all" / "v001" / "data.jsonl"
    rows = [json.loads(line) for line in data_path.read_text().splitlines() if line]
    assert len(rows) == 1
    assert rows[0]["strategy_version"] == "vwap-v2"


def test_build_version_conflict(tmp_path: Path):
    db_path = _make_db(
        tmp_path,
        [("2026-05-01", "v", "2025-01-01", "2025-12-31",
          1, 1, 0, 1.0, 0.0, 1.0, 0.0, 0.0)],
    )
    out = tmp_path / "datasets"
    builder = BacktestResultsBuilder()
    builder.build(
        output_dir=out, version="v001", source=str(db_path),
        commit_sha="x", db_path=db_path,
    )
    with pytest.raises(VersionConflict):
        builder.build(
            output_dir=out, version="v001", source=str(db_path),
            commit_sha="x", db_path=db_path,
        )
    builder.build(
        output_dir=out, version="v001", source=str(db_path),
        commit_sha="x", db_path=db_path, overwrite=True,
    )


def test_missing_db_raises(tmp_path: Path):
    out = tmp_path / "datasets"
    builder = BacktestResultsBuilder()
    with pytest.raises(FileNotFoundError):
        builder.build(
            output_dir=out, version="v001", source="missing",
            commit_sha="x", db_path=tmp_path / "nope.db",
        )


def test_registry_round_trip():
    from ml.datasets import get_builder, list_families

    families = list_families()
    assert "backtest_results" in families
    assert isinstance(get_builder("backtest_results"), BacktestResultsBuilder)
