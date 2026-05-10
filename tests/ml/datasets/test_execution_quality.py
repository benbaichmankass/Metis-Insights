"""Tests for `ExecutionQualityBuilder` (S-AI-WS5-D)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from ml.datasets.families.execution_quality import ExecutionQualityBuilder
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
CREATE TABLE order_packages (
    order_package_id TEXT PRIMARY KEY,
    strategy_name   TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    direction       TEXT NOT NULL,
    entry           REAL NOT NULL,
    sl              REAL NOT NULL,
    tp              REAL NOT NULL,
    confidence      REAL,
    signal_logic    TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'open',
    linked_trade_id INTEGER,
    close_reason    TEXT,
    meta            TEXT
);
"""


def _seed(tmp_path: Path, pairs: list[tuple[dict, dict | None]]) -> Path:
    """Each pair is (trade_row, order_row | None). order_row is linked
    automatically via linked_trade_id once the trade is inserted."""
    db_path = tmp_path / "trade_journal.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_DDL)
    cur = conn.cursor()
    for idx, (trade, order) in enumerate(pairs):
        trade_cols = sorted(trade.keys())
        cur.execute(
            f"INSERT INTO trades ({', '.join(trade_cols)}) VALUES "
            f"({', '.join('?' for _ in trade_cols)})",
            tuple(trade[c] for c in trade_cols),
        )
        trade_id = cur.lastrowid
        if order is not None:
            order = dict(order)
            order.setdefault("order_package_id", f"op-{idx}")
            order.setdefault("strategy_name", trade.get("strategy_name", "vwap"))
            order.setdefault("symbol", trade.get("symbol", "BTCUSDT"))
            order.setdefault("direction", trade.get("direction", "LONG"))
            order.setdefault("sl", 41000.0)
            order.setdefault("tp", 43000.0)
            order.setdefault("created_at", "2026-05-01T11:59:00Z")
            order.setdefault("updated_at", "2026-05-01T12:30:00Z")
            order.setdefault("status", "closed")
            order["linked_trade_id"] = trade_id
            cols = sorted(order.keys())
            cur.execute(
                f"INSERT INTO order_packages ({', '.join(cols)}) VALUES "
                f"({', '.join('?' for _ in cols)})",
                tuple(order[c] for c in cols),
            )
    conn.commit()
    conn.close()
    return db_path


def _trade(**overrides):
    base = dict(
        timestamp="2026-05-01T12:00:00Z",
        symbol="BTCUSDT",
        direction="LONG",
        strategy_name="vwap",
        setup_type="FVG",
        killzone="NY",
        bias="BULLISH",
        entry_price=42000.0,
        pnl=10.0,
        pnl_percent=1.0,
        status="CLOSED",
        is_backtest=0,
        account_id="live",
        created_at="2026-05-01T12:30:00Z",
    )
    base.update(overrides)
    return base


def _order(**overrides):
    base = dict(entry=42000.0, confidence=0.7, signal_logic="{}")
    base.update(overrides)
    return base


class TestExecutionQualityBuilder:
    def test_build_round_trip(self, tmp_path: Path):
        # LONG: actual 42100, intended 42000 → +100 / 42000 = +23.81 bps
        # (positive = paid worse than intended).
        pairs = [
            (_trade(direction="LONG", entry_price=42100.0),
             _order(entry=42000.0)),
        ]
        db_path = _seed(tmp_path, pairs)
        out = tmp_path / "datasets"
        builder = ExecutionQualityBuilder()
        paths = builder.build(
            output_dir=out,
            version="v001",
            source=str(db_path),
            commit_sha="abc",
            db_path=db_path,
        )
        assert paths.root == (
            out / "execution_quality" / "all" / "all" / "v001"
        )

        emitted = [
            json.loads(line)
            for line in paths.data.read_text().splitlines()
            if line
        ]
        assert len(emitted) == 1
        row = emitted[0]
        assert row["intended_entry"] == 42000.0
        assert row["actual_entry"] == 42100.0
        assert row["entry_slippage_bps"] == pytest.approx(23.8095, abs=1e-3)
        # Order at 11:59:00, trade at 12:00:00 → 60s
        assert row["fill_latency_seconds"] == pytest.approx(60.0)

        metadata = json.loads(paths.metadata.read_text())
        assert metadata["family"] == "execution_quality"
        assert metadata["leakage_test_status"] == "skipped"
        assert metadata["label_version"] == "entry-slippage-bps-v1"

        report = validate_dataset(paths.root)
        assert report.ok, report.errors

    def test_short_direction_inverts_sign(self, tmp_path: Path):
        # SHORT: actual 41900, intended 42000 → raw -100/42000 = -23.81 bps
        # but "positive = worse" for SHORT means selling LOWER is bad,
        # so the sign flips: result = +23.81 bps.
        pairs = [
            (_trade(direction="SHORT", entry_price=41900.0),
             _order(entry=42000.0)),
        ]
        db_path = _seed(tmp_path, pairs)
        builder = ExecutionQualityBuilder()
        paths = builder.build(
            output_dir=tmp_path / "out", version="v001", source=str(db_path),
            commit_sha="x", db_path=db_path,
        )
        emitted = [
            json.loads(line)
            for line in paths.data.read_text().splitlines()
            if line
        ]
        assert emitted[0]["entry_slippage_bps"] == pytest.approx(
            23.8095, abs=1e-3
        )

    def test_better_fill_is_negative(self, tmp_path: Path):
        # LONG, paid LESS than intended → negative (good for trader).
        pairs = [
            (_trade(direction="LONG", entry_price=41900.0),
             _order(entry=42000.0)),
        ]
        db_path = _seed(tmp_path, pairs)
        builder = ExecutionQualityBuilder()
        paths = builder.build(
            output_dir=tmp_path / "out", version="v001", source=str(db_path),
            commit_sha="x", db_path=db_path,
        )
        emitted = [
            json.loads(line)
            for line in paths.data.read_text().splitlines()
            if line
        ]
        assert emitted[0]["entry_slippage_bps"] < 0
        assert emitted[0]["entry_slippage_bps"] == pytest.approx(
            -23.8095, abs=1e-3
        )

    def test_slippage_cap(self, tmp_path: Path):
        # +5 % slippage = 500 bps; cap=200 → 200.
        pairs = [
            (_trade(direction="LONG", entry_price=44100.0),
             _order(entry=42000.0)),
            (_trade(direction="LONG", entry_price=39900.0),
             _order(entry=42000.0)),
        ]
        db_path = _seed(tmp_path, pairs)
        builder = ExecutionQualityBuilder()
        paths = builder.build(
            output_dir=tmp_path / "out", version="v001", source=str(db_path),
            commit_sha="x", db_path=db_path, slippage_cap_bps=200.0,
        )
        emitted = [
            json.loads(line)
            for line in paths.data.read_text().splitlines()
            if line
        ]
        assert emitted[0]["entry_slippage_bps"] == pytest.approx(200.0)
        assert emitted[1]["entry_slippage_bps"] == pytest.approx(-200.0)

    def test_drops_unjoined_trades(self, tmp_path: Path):
        # No order_package linked → row should not appear.
        pairs = [
            (_trade(), None),
            (_trade(), _order()),
        ]
        db_path = _seed(tmp_path, pairs)
        builder = ExecutionQualityBuilder()
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

    def test_filters_open_and_backtest(self, tmp_path: Path):
        pairs = [
            (_trade(), _order()),
            (_trade(status="OPEN"), _order()),
            (_trade(is_backtest=1), _order()),
            (_trade(entry_price=None), _order()),
        ]
        db_path = _seed(tmp_path, pairs)
        builder = ExecutionQualityBuilder()
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

    def test_drops_zero_or_null_intended_entry(self, tmp_path: Path):
        pairs = [
            (_trade(), _order(entry=0.0)),
            (_trade(), _order()),
        ]
        db_path = _seed(tmp_path, pairs)
        builder = ExecutionQualityBuilder()
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

    def test_fill_latency_seconds(self, tmp_path: Path):
        # Intent at 12:00:00, fill at 12:05:30 → 330 seconds.
        pairs = [
            (
                _trade(timestamp="2026-05-01T12:05:30Z"),
                _order(created_at="2026-05-01T12:00:00Z"),
            ),
        ]
        db_path = _seed(tmp_path, pairs)
        builder = ExecutionQualityBuilder()
        paths = builder.build(
            output_dir=tmp_path / "out", version="v001", source=str(db_path),
            commit_sha="x", db_path=db_path,
        )
        emitted = [
            json.loads(line)
            for line in paths.data.read_text().splitlines()
            if line
        ]
        assert emitted[0]["fill_latency_seconds"] == pytest.approx(330.0)

    def test_strategy_filter(self, tmp_path: Path):
        pairs = [
            (_trade(strategy_name="vwap"), _order()),
            (_trade(strategy_name="turtle"), _order()),
        ]
        db_path = _seed(tmp_path, pairs)
        builder = ExecutionQualityBuilder()
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

    def test_invalid_cap_raises(self, tmp_path: Path):
        builder = ExecutionQualityBuilder()
        with pytest.raises(ValueError, match="slippage_cap_bps"):
            list(
                builder.iter_rows(db_path=tmp_path, slippage_cap_bps=0)
            )
        with pytest.raises(ValueError, match="slippage_cap_bps"):
            list(
                builder.iter_rows(db_path=tmp_path, slippage_cap_bps=-1.0)
            )

    def test_missing_db_raises(self, tmp_path: Path):
        builder = ExecutionQualityBuilder()
        with pytest.raises(FileNotFoundError):
            list(builder.iter_rows(db_path=tmp_path / "nope.db"))


def test_registry_includes_execution_quality():
    from ml.datasets import list_families, get_builder

    assert "execution_quality" in list_families()
    assert isinstance(get_builder("execution_quality"), ExecutionQualityBuilder)
