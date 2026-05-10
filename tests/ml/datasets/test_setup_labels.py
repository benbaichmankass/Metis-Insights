"""Tests for `SetupLabelsBuilder` (S-AI-WS5-C)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from ml.datasets.families.setup_labels import SetupLabelsBuilder
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
        pnl_percent=1.0,
        status="CLOSED",
        is_backtest=0,
        account_id="live",
        created_at="2026-05-01T12:30:00Z",
    )
    base.update(overrides)
    return base


class TestSetupLabelsBuilder:
    def test_build_round_trip(self, tmp_path: Path):
        rows = [
            _row(setup_type="FVG", pnl=15.0, pnl_percent=1.5),
            _row(setup_type="OB", pnl=-8.0, pnl_percent=-0.8),
            _row(setup_type="LIQ_SWEEP", pnl=20.0, pnl_percent=2.0),
        ]
        db_path = _make_db(tmp_path, rows)
        out = tmp_path / "datasets"
        builder = SetupLabelsBuilder()
        paths = builder.build(
            output_dir=out,
            version="v001",
            source=str(db_path),
            commit_sha="abc123",
            db_path=db_path,
        )
        assert paths.root == out / "setup_labels" / "all" / "all" / "v001"

        with paths.data.open() as fh:
            emitted = [json.loads(line) for line in fh if line.strip()]
        assert len(emitted) == 3
        # r_multiple = pnl_percent / risk_pct (default 1.0).
        assert emitted[0]["r_multiple"] == pytest.approx(1.5)
        assert emitted[1]["r_multiple"] == pytest.approx(-0.8)
        assert emitted[2]["r_multiple"] == pytest.approx(2.0)
        # `won` matches sign of pnl.
        assert emitted[0]["won"] is True
        assert emitted[1]["won"] is False
        assert emitted[2]["won"] is True

        metadata = json.loads(paths.metadata.read_text())
        assert metadata["family"] == "setup_labels"
        assert metadata["leakage_test_status"] == "skipped"
        assert metadata["label_version"] == "r-multiple-from-pnl-pct-v1"
        assert metadata["schema"]["r_multiple"] == "float"

        report = validate_dataset(paths.root)
        assert report.ok, report.errors

    def test_filters_empty_setup_type(self, tmp_path: Path):
        rows = [
            _row(setup_type="FVG", pnl_percent=1.0),
            _row(setup_type="", pnl_percent=2.0),     # empty: drop
            _row(setup_type=None, pnl_percent=3.0),   # null: drop
            _row(setup_type="   ", pnl_percent=4.0),  # whitespace: drop
        ]
        db_path = _make_db(tmp_path, rows)
        out = tmp_path / "datasets"
        builder = SetupLabelsBuilder()
        paths = builder.build(
            output_dir=out, version="v001", source=str(db_path),
            commit_sha="x", db_path=db_path,
        )
        emitted = [
            json.loads(line)
            for line in paths.data.read_text().splitlines()
            if line
        ]
        assert len(emitted) == 1
        assert emitted[0]["setup_type"] == "FVG"

    def test_filters_open_and_backtest(self, tmp_path: Path):
        rows = [
            _row(pnl_percent=1.0),
            _row(status="OPEN", pnl_percent=2.0),
            _row(is_backtest=1, pnl_percent=3.0),
            _row(pnl=None, pnl_percent=4.0),  # null pnl: drop
        ]
        db_path = _make_db(tmp_path, rows)
        builder = SetupLabelsBuilder()
        paths = builder.build(
            output_dir=tmp_path / "out", version="v001", source=str(db_path),
            commit_sha="x", db_path=db_path,
        )
        emitted = [
            json.loads(line)
            for line in paths.data.read_text().splitlines()
            if line
        ]
        assert len(emitted) == 1

    def test_r_multiple_capped(self, tmp_path: Path):
        rows = [
            _row(setup_type="FVG", pnl_percent=12.0),    # cap at +3R
            _row(setup_type="FVG", pnl_percent=-9.5),    # cap at -3R
            _row(setup_type="FVG", pnl_percent=2.5),     # no cap
        ]
        db_path = _make_db(tmp_path, rows)
        builder = SetupLabelsBuilder()
        paths = builder.build(
            output_dir=tmp_path / "out", version="v001", source=str(db_path),
            commit_sha="x", db_path=db_path, r_cap=3.0,
        )
        emitted = [
            json.loads(line)
            for line in paths.data.read_text().splitlines()
            if line
        ]
        assert emitted[0]["r_multiple"] == 3.0
        assert emitted[1]["r_multiple"] == -3.0
        assert emitted[2]["r_multiple"] == pytest.approx(2.5)

    def test_risk_pct_scales_r_multiple(self, tmp_path: Path):
        rows = [_row(setup_type="FVG", pnl_percent=1.5)]
        db_path = _make_db(tmp_path, rows)
        # risk_pct=0.5 → r_multiple = 1.5 / 0.5 = 3.0 (also at cap).
        builder = SetupLabelsBuilder()
        paths = builder.build(
            output_dir=tmp_path / "out", version="v001", source=str(db_path),
            commit_sha="x", db_path=db_path, risk_pct=0.5, r_cap=5.0,
        )
        emitted = [
            json.loads(line)
            for line in paths.data.read_text().splitlines()
            if line
        ]
        assert emitted[0]["r_multiple"] == pytest.approx(3.0)

    def test_invalid_risk_pct_raises(self, tmp_path: Path):
        builder = SetupLabelsBuilder()
        with pytest.raises(ValueError, match="risk_pct"):
            list(builder.iter_rows(db_path=tmp_path, risk_pct=0))
        with pytest.raises(ValueError, match="risk_pct"):
            list(builder.iter_rows(db_path=tmp_path, risk_pct=-1.0))
        with pytest.raises(ValueError, match="r_cap"):
            list(builder.iter_rows(db_path=tmp_path, r_cap=0))

    def test_missing_db_raises(self, tmp_path: Path):
        builder = SetupLabelsBuilder()
        with pytest.raises(FileNotFoundError):
            list(
                builder.iter_rows(db_path=tmp_path / "nope.db")
            )

    def test_strategy_filter(self, tmp_path: Path):
        rows = [
            _row(strategy_name="vwap", setup_type="FVG"),
            _row(strategy_name="turtle", setup_type="FVG"),
        ]
        db_path = _make_db(tmp_path, rows)
        builder = SetupLabelsBuilder()
        paths = builder.build(
            output_dir=tmp_path / "out", version="v001", source=str(db_path),
            commit_sha="x", db_path=db_path, strategy_name="vwap",
        )
        emitted = [
            json.loads(line)
            for line in paths.data.read_text().splitlines()
            if line
        ]
        assert len(emitted) == 1
        assert emitted[0]["strategy_name"] == "vwap"


def test_registry_includes_setup_labels():
    from ml.datasets import list_families, get_builder

    assert "setup_labels" in list_families()
    assert isinstance(get_builder("setup_labels"), SetupLabelsBuilder)
