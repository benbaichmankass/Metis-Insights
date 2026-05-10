"""End-to-end test for `TradeOutcomesBuilder`."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from ml.datasets.families.trade_outcomes import TradeOutcomesBuilder
from ml.datasets.validate import validate_dataset

_DDL = """
CREATE TABLE trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT,
    symbol          TEXT,
    direction       TEXT,
    entry_price     REAL,
    exit_price      REAL,
    stop_loss       REAL,
    take_profit_1   REAL,
    take_profit_2   REAL,
    take_profit_3   REAL,
    position_size   REAL,
    setup_type      TEXT,
    killzone        TEXT,
    bias            TEXT,
    entry_reason    TEXT,
    exit_reason     TEXT,
    pnl             REAL,
    pnl_percent     REAL,
    status          TEXT,
    notes           TEXT,
    is_backtest     INTEGER DEFAULT 0,
    strategy_name   TEXT,
    account_id      TEXT NOT NULL DEFAULT 'live',
    created_at      TEXT DEFAULT (datetime('now'))
);
"""


def _make_db(tmp_path: Path, rows: list[dict]) -> Path:
    db_path = tmp_path / "trade_journal.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_DDL)
    cols = sorted({k for row in rows for k in row.keys()})
    placeholders = ", ".join("?" for _ in cols)
    sql = f"INSERT INTO trades ({', '.join(cols)}) VALUES ({placeholders})"
    conn.executemany(sql, [tuple(row.get(c) for c in cols) for row in rows])
    conn.commit()
    conn.close()
    return db_path


def _row(**overrides):
    base = dict(
        timestamp="2026-05-01T12:00:00Z",
        symbol="BTCUSDT",
        direction="LONG",
        strategy_name="vwap",
        setup_type="FVG",
        killzone="NY",
        bias="BULLISH",
        pnl=10.0,
        pnl_percent=0.01,
        status="CLOSED",
        is_backtest=0,
        strategy_name_explicit=False,
        account_id="live",
        created_at="2026-05-01T12:30:00Z",
    )
    base.update(overrides)
    base.pop("strategy_name_explicit", None)
    return base


def test_build_round_trip(tmp_path: Path):
    rows = [
        _row(strategy_name="vwap", pnl=10.0),
        _row(strategy_name="vwap", pnl=-5.0),
        _row(strategy_name="turtle", pnl=20.0),
        # OPEN trade — must be skipped
        _row(status="OPEN", pnl=0.0),
        # is_backtest=1 — must be skipped
        _row(is_backtest=1, pnl=15.0),
        # CLOSED but pnl is null — must be skipped
        _row(pnl=None),
    ]
    db_path = _make_db(tmp_path, rows)
    out = tmp_path / "datasets"
    builder = TradeOutcomesBuilder()
    paths = builder.build(
        output_dir=out,
        version="v001",
        source=str(db_path),
        commit_sha="abc123",
        db_path=db_path,
    )

    assert paths.root == out / "trade_outcomes" / "all" / "all" / "v001"
    assert paths.metadata.is_file()
    assert paths.data.is_file()

    with paths.data.open() as fh:
        emitted = [json.loads(line) for line in fh if line.strip()]
    # 3 rows are CLOSED, non-backtest, non-null-pnl
    assert len(emitted) == 3
    assert emitted[0]["won"] is True
    assert emitted[1]["won"] is False
    assert emitted[2]["won"] is True

    metadata = json.loads(paths.metadata.read_text())
    assert metadata["family"] == "trade_outcomes"
    assert metadata["row_count"] == 3
    assert metadata["leakage_test_status"] == "skipped"
    assert metadata["label_version"] == "won-from-pnl-v1"
    assert metadata["schema"]["won"] == "bool"

    report = validate_dataset(paths.root)
    assert report.ok, report.errors


def test_build_filters_strategy(tmp_path: Path):
    rows = [
        _row(strategy_name="vwap", pnl=10.0),
        _row(strategy_name="turtle", pnl=-5.0),
    ]
    db_path = _make_db(tmp_path, rows)
    out = tmp_path / "datasets"
    builder = TradeOutcomesBuilder()
    builder.build(
        output_dir=out,
        version="v001",
        source=str(db_path),
        commit_sha="x",
        db_path=db_path,
        strategy_name="vwap",
    )
    data_path = out / "trade_outcomes" / "all" / "all" / "v001" / "data.jsonl"
    emitted = [json.loads(line) for line in data_path.read_text().splitlines() if line]
    assert len(emitted) == 1
    assert emitted[0]["strategy_name"] == "vwap"


def test_null_strategy_name_normalised_to_empty(tmp_path: Path):
    rows = [_row(strategy_name=None, pnl=5.0)]
    db_path = _make_db(tmp_path, rows)
    out = tmp_path / "datasets"
    builder = TradeOutcomesBuilder()
    builder.build(
        output_dir=out, version="v001", source=str(db_path),
        commit_sha="x", db_path=db_path,
    )
    data_path = out / "trade_outcomes" / "all" / "all" / "v001" / "data.jsonl"
    emitted = [json.loads(line) for line in data_path.read_text().splitlines() if line]
    assert emitted[0]["strategy_name"] == ""


def test_missing_db_raises(tmp_path: Path):
    builder = TradeOutcomesBuilder()
    with pytest.raises(FileNotFoundError):
        builder.build(
            output_dir=tmp_path / "out",
            version="v001",
            source="missing",
            commit_sha="x",
            db_path=tmp_path / "nope.db",
        )


def test_registry_includes_family():
    from ml.datasets import list_families, get_builder

    assert "trade_outcomes" in list_families()
    assert isinstance(get_builder("trade_outcomes"), TradeOutcomesBuilder)
