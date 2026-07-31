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
        status="closed",
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


class TestProvenanceExclusion:
    """`exclude_fabricated_pnl` on the family `trade-outcome-lgbm-v1` uses.

    The filter was originally built only into `setup_labels`, while the one
    feature-LEARNING trade-outcome manifest trains on THIS family — so option A
    ("retrain excluding fabricated labels") would have retrained on the
    fabricated labels anyway. That gap is what these tests pin.

    `won` IS `pnl > 0`, so a fabricated pnl does not make a noisy label, it
    makes a WRONG one.
    """

    def _n(self, **kw):
        return json.dumps(kw)

    def test_fabricated_live_row_is_dropped(self, tmp_path: Path):
        db = _make_db(tmp_path, [
            _row(pnl=1.0, notes=self._n(exit_price_source="bybit_closed_pnl")),
            _row(pnl=2.0, notes=self._n(exit_price_source="local_markprice")),
        ])
        out = list(TradeOutcomesBuilder().iter_rows(
            db_path=db, exclude_fabricated_pnl=True))
        assert len(out) == 1
        assert out[0]["pnl"] == 1.0

    def test_unverified_is_dropped_too(self, tmp_path: Path):
        """No provenance recorded is NOT evidence of measurement."""
        db = _make_db(tmp_path, [_row(pnl=3.0, notes=None)])
        assert list(TradeOutcomesBuilder().iter_rows(
            db_path=db, exclude_fabricated_pnl=True)) == []

    def test_estimated_survives(self, tmp_path: Path):
        """A close anchored to its own bar is a defensible label."""
        db = _make_db(tmp_path, [
            _row(pnl=4.0, notes=self._n(exit_price_source="candle_at_close")),
        ])
        out = list(TradeOutcomesBuilder().iter_rows(
            db_path=db, exclude_fabricated_pnl=True))
        assert len(out) == 1

    def test_backtest_rows_survive_a_NON_JSON_notes_blob(self, tmp_path: Path):
        """THE regression: run_tag is a bare string, so json.loads fails on it.

        Without the carve-out every simulated row is dropped while the log
        blames "fabrication" — a wrong result with a confident wrong reason.
        """
        db = _make_db(tmp_path, [
            _row(pnl=1.0, is_backtest=1, notes="sweep-run-2026-07-01"),
            _row(pnl=2.0, is_backtest=1, notes="sweep-run-2026-07-01"),
        ])
        out = list(TradeOutcomesBuilder().iter_rows(
            db_path=db, exclude_fabricated_pnl=True, include_backtest=True))
        assert len(out) == 2, "simulated rows must not be dropped as fabricated"
        assert all(r["source"] == "backtest" for r in out)

    def test_the_filter_is_off_by_default(self, tmp_path: Path):
        db = _make_db(tmp_path, [
            _row(pnl=1.0, notes=self._n(exit_price_source="local_markprice")),
        ])
        assert len(list(TradeOutcomesBuilder().iter_rows(db_path=db))) == 1

    def test_notes_never_reaches_the_emitted_row(self, tmp_path: Path):
        """`notes` is read to JUDGE provenance, then dropped.

        Emitting it would put a blob carrying `exit_price_source` into the
        feature space — this module's leakage rule, applied to provenance.
        """
        db = _make_db(tmp_path, [
            _row(pnl=1.0, notes=self._n(exit_price_source="bybit_closed_pnl")),
        ])
        emitted = list(TradeOutcomesBuilder().iter_rows(
            db_path=db, exclude_fabricated_pnl=True))
        assert emitted, "fixture should survive the filter"
        assert all("notes" not in r for r in emitted)
        assert "notes" not in TradeOutcomesBuilder.schema


def test_default_path_does_not_require_a_notes_column(tmp_path: Path):
    """Regression: `notes` must only be SELECTed when the filter is on.

    Selecting it unconditionally raised `no such column: t.notes` for every
    caller that never asked for the filter — a new failure mode bolted onto the
    default path by a feature nobody had enabled.
    """
    db = tmp_path / "j.db"
    c = sqlite3.connect(str(db))
    c.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, timestamp TEXT,"
        " symbol TEXT, direction TEXT, strategy_name TEXT, setup_type TEXT,"
        " killzone TEXT, bias TEXT, pnl REAL, pnl_percent REAL,"
        " account_id TEXT, created_at TEXT, status TEXT, is_backtest INT)"
    )
    c.execute(
        "INSERT INTO trades VALUES (1,'t','BTCUSDT','long','vwap','FVG',"
        "'NY','BULL',5.0,1.0,'bybit_1','2026-07-01T00:00:00Z','closed',0)"
    )
    c.commit()
    c.close()
    out = list(TradeOutcomesBuilder().iter_rows(db_path=db))
    assert len(out) == 1 and out[0]["won"] is True
