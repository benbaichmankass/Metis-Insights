"""Tests for `SetupLabelsAuditBuilder` (S-AI-WS5-C-FU)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from ml.datasets.families.setup_labels_audit import SetupLabelsAuditBuilder
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


def _trade(**overrides):
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


def _make_audit(tmp_path: Path, events: list[dict]) -> Path:
    p = tmp_path / "signal_audit.jsonl"
    with p.open("w", encoding="utf-8") as fh:
        for evt in events:
            fh.write(json.dumps(evt) + "\n")
    return p


def _audit_evt(**overrides):
    base = dict(
        event="pipeline_result",
        strategy="vwap",
        symbol="BTCUSDT",
        side="BUY",
        pattern="FVG",
        confidence=0.72,
        price=42000.0,
        status="ok",
        logged_at_utc="2026-05-01T12:29:55Z",
    )
    base.update(overrides)
    return base


class TestSetupLabelsAuditBuilder:
    def test_build_round_trip(self, tmp_path: Path):
        trades = [_trade(setup_type="FVG", pnl=15.0, pnl_percent=1.5)]
        events = [_audit_evt(pattern="FVG", confidence=0.9)]
        db_path = _make_db(tmp_path, trades)
        audit_path = _make_audit(tmp_path, events)
        out = tmp_path / "datasets"
        builder = SetupLabelsAuditBuilder()
        paths = builder.build(
            output_dir=out,
            version="v001",
            source=str(db_path),
            commit_sha="abc",
            db_path=db_path,
            audit_log_path=audit_path,
        )
        assert paths.root == (
            out / "setup_labels_audit" / "all" / "all" / "v001"
        )

        emitted = [
            json.loads(line)
            for line in paths.data.read_text().splitlines()
            if line
        ]
        assert len(emitted) == 1
        row = emitted[0]
        assert row["audit_pattern"] == "FVG"
        assert row["audit_confidence"] == pytest.approx(0.9)
        assert row["r_multiple"] == pytest.approx(1.5)
        assert row["won"] is True
        assert row["match_offset_seconds"] == pytest.approx(5.0)

        metadata = json.loads(paths.metadata.read_text())
        assert metadata["family"] == "setup_labels_audit"
        assert metadata["leakage_test_status"] == "skipped"
        assert metadata["label_version"] == "r-multiple-from-pnl-pct-v1"

        report = validate_dataset(paths.root)
        assert report.ok, report.errors

    def test_drops_rejected_audits(self, tmp_path: Path):
        trades = [_trade()]
        events = [
            _audit_evt(stage_rejections=["confluence_fail"], pattern="FVG"),
            _audit_evt(pattern="OB", confidence=0.5),
        ]
        db_path = _make_db(tmp_path, trades)
        audit_path = _make_audit(tmp_path, events)
        builder = SetupLabelsAuditBuilder()
        paths = builder.build(
            output_dir=tmp_path / "out", version="v001", source=str(db_path),
            commit_sha="x", db_path=db_path, audit_log_path=audit_path,
        )
        emitted = [
            json.loads(line)
            for line in paths.data.read_text().splitlines()
            if line
        ]
        assert len(emitted) == 1
        assert emitted[0]["audit_pattern"] == "OB"

    def test_drops_audit_without_entry_or_price(self, tmp_path: Path):
        trades = [_trade()]
        events = [
            _audit_evt(price=None, pattern="X1"),
            _audit_evt(pattern="X2"),
        ]
        # Drop "entry" too in first event by replacing the dict.
        events[0].pop("price", None)
        db_path = _make_db(tmp_path, trades)
        audit_path = _make_audit(tmp_path, events)
        builder = SetupLabelsAuditBuilder()
        paths = builder.build(
            output_dir=tmp_path / "out", version="v001", source=str(db_path),
            commit_sha="x", db_path=db_path, audit_log_path=audit_path,
        )
        emitted = [
            json.loads(line)
            for line in paths.data.read_text().splitlines()
            if line
        ]
        assert len(emitted) == 1
        assert emitted[0]["audit_pattern"] == "X2"

    def test_match_window_filters_far_audits(self, tmp_path: Path):
        # Trade at 12:30:00; audit at 12:00:00 → 1800s gap.
        trades = [_trade(created_at="2026-05-01T12:30:00Z")]
        events = [_audit_evt(logged_at_utc="2026-05-01T12:00:00Z")]
        db_path = _make_db(tmp_path, trades)
        audit_path = _make_audit(tmp_path, events)
        builder = SetupLabelsAuditBuilder()
        # window=60 → no match
        paths = builder.build(
            output_dir=tmp_path / "out_a", version="v001", source=str(db_path),
            commit_sha="x", db_path=db_path, audit_log_path=audit_path,
            match_window_seconds=60,
        )
        emitted = [
            json.loads(line)
            for line in paths.data.read_text().splitlines()
            if line
        ]
        assert len(emitted) == 0

        # window=3600 → match
        paths = builder.build(
            output_dir=tmp_path / "out_b", version="v001", source=str(db_path),
            commit_sha="x", db_path=db_path, audit_log_path=audit_path,
            match_window_seconds=3600,
        )
        emitted = [
            json.loads(line)
            for line in paths.data.read_text().splitlines()
            if line
        ]
        assert len(emitted) == 1

    def test_picks_nearest_audit_per_trade(self, tmp_path: Path):
        # Trade at 12:30:00. Two audit events: 30s before and 10s after.
        # The later one (smaller |offset|) should be matched.
        trades = [_trade(created_at="2026-05-01T12:30:00Z")]
        events = [
            _audit_evt(
                logged_at_utc="2026-05-01T12:29:30Z", pattern="FAR"
            ),
            _audit_evt(
                logged_at_utc="2026-05-01T12:30:10Z", pattern="NEAR"
            ),
        ]
        db_path = _make_db(tmp_path, trades)
        audit_path = _make_audit(tmp_path, events)
        builder = SetupLabelsAuditBuilder()
        paths = builder.build(
            output_dir=tmp_path / "out", version="v001", source=str(db_path),
            commit_sha="x", db_path=db_path, audit_log_path=audit_path,
        )
        emitted = [
            json.loads(line)
            for line in paths.data.read_text().splitlines()
            if line
        ]
        assert len(emitted) == 1
        assert emitted[0]["audit_pattern"] == "NEAR"

    def test_one_audit_to_one_trade(self, tmp_path: Path):
        # Two trades at the same time, one audit event. Only one row.
        trades = [
            _trade(created_at="2026-05-01T12:30:00Z"),
            _trade(created_at="2026-05-01T12:30:00Z"),
        ]
        events = [_audit_evt(logged_at_utc="2026-05-01T12:30:00Z")]
        db_path = _make_db(tmp_path, trades)
        audit_path = _make_audit(tmp_path, events)
        builder = SetupLabelsAuditBuilder()
        paths = builder.build(
            output_dir=tmp_path / "out", version="v001", source=str(db_path),
            commit_sha="x", db_path=db_path, audit_log_path=audit_path,
        )
        emitted = [
            json.loads(line)
            for line in paths.data.read_text().splitlines()
            if line
        ]
        assert len(emitted) == 1

    def test_strategy_or_symbol_mismatch_drops(self, tmp_path: Path):
        trades = [
            _trade(strategy_name="vwap", symbol="BTCUSDT"),
            _trade(strategy_name="turtle", symbol="BTCUSDT"),
            _trade(strategy_name="vwap", symbol="ETHUSDT"),
        ]
        events = [_audit_evt(strategy="vwap", symbol="BTCUSDT")]
        db_path = _make_db(tmp_path, trades)
        audit_path = _make_audit(tmp_path, events)
        builder = SetupLabelsAuditBuilder()
        paths = builder.build(
            output_dir=tmp_path / "out", version="v001", source=str(db_path),
            commit_sha="x", db_path=db_path, audit_log_path=audit_path,
        )
        emitted = [
            json.loads(line)
            for line in paths.data.read_text().splitlines()
            if line
        ]
        assert len(emitted) == 1
        assert emitted[0]["strategy_name"] == "vwap"
        assert emitted[0]["symbol"] == "BTCUSDT"

    def test_filters_open_and_backtest(self, tmp_path: Path):
        trades = [
            _trade(),
            _trade(status="OPEN"),
            _trade(is_backtest=1),
            _trade(setup_type=""),
        ]
        events = [_audit_evt() for _ in range(4)]
        db_path = _make_db(tmp_path, trades)
        audit_path = _make_audit(tmp_path, events)
        builder = SetupLabelsAuditBuilder()
        paths = builder.build(
            output_dir=tmp_path / "out", version="v001", source=str(db_path),
            commit_sha="x", db_path=db_path, audit_log_path=audit_path,
        )
        emitted = [
            json.loads(line)
            for line in paths.data.read_text().splitlines()
            if line
        ]
        assert len(emitted) == 1

    def test_invalid_match_window_raises(self, tmp_path: Path):
        builder = SetupLabelsAuditBuilder()
        with pytest.raises(ValueError, match="match_window_seconds"):
            list(
                builder.iter_rows(
                    db_path=tmp_path,
                    audit_log_path=tmp_path,
                    match_window_seconds=0,
                )
            )

    def test_missing_audit_log_raises(self, tmp_path: Path):
        trades = [_trade()]
        db_path = _make_db(tmp_path, trades)
        builder = SetupLabelsAuditBuilder()
        with pytest.raises(FileNotFoundError, match="signal_audit.jsonl"):
            list(
                builder.iter_rows(
                    db_path=db_path,
                    audit_log_path=tmp_path / "missing.jsonl",
                )
            )

    def test_handles_garbled_audit_lines(self, tmp_path: Path):
        # A non-JSON line + a non-dict JSON line should be skipped.
        audit_path = tmp_path / "signal_audit.jsonl"
        with audit_path.open("w", encoding="utf-8") as fh:
            fh.write("not json at all\n")
            fh.write("[]\n")
            fh.write(json.dumps(_audit_evt()) + "\n")
        trades = [_trade()]
        db_path = _make_db(tmp_path, trades)
        builder = SetupLabelsAuditBuilder()
        paths = builder.build(
            output_dir=tmp_path / "out", version="v001", source=str(db_path),
            commit_sha="x", db_path=db_path, audit_log_path=audit_path,
        )
        emitted = [
            json.loads(line)
            for line in paths.data.read_text().splitlines()
            if line
        ]
        assert len(emitted) == 1


def test_registry_includes_setup_labels_audit():
    from ml.datasets import list_families, get_builder

    assert "setup_labels_audit" in list_families()
    assert isinstance(get_builder("setup_labels_audit"), SetupLabelsAuditBuilder)
