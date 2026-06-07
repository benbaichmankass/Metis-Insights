"""Tests for `AccountContextBuilder` (S-AI-WS5-F)."""
from __future__ import annotations

import json
import sqlite3
import textwrap
from pathlib import Path

import pytest

from ml.datasets.families.account_context import AccountContextBuilder
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


_PROP_YAML = textwrap.dedent(
    """
    accounts:
      prop_velotrade_1:
        type: prop
        account_state: evaluation
        phase_requirements:
          target_profit_pct: 0.05
          min_active_days: 4
          min_daily_profit_pct: 0.005
        prop_state:
          cumulative_pnl_pct: 0.0
        overnight_restricted: true
        risk:
          max_dd_pct: 0.02
          daily_usd: 50
          pos_size: 200
          risk_pct: 0.005
      prop_velotrade_2:
        type: prop
        account_state: funded
        phase_requirements:
          target_profit_pct: 0.0
          min_active_days: 0
          min_daily_profit_pct: 0.0
        overnight_restricted: false
        risk:
          max_dd_pct: 0.05
          daily_usd: 200
          pos_size: 1000
          risk_pct: 0.01
      live:
        type: live
        risk:
          max_dd_pct: 0.10
          daily_usd: 500
          pos_size: 2000
    """
).strip()


def _make_db(tmp_path: Path, rows: list[dict]) -> Path:
    db_path = tmp_path / "trade_journal.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_DDL)
    cols = sorted({k for r in rows for k in r.keys()})
    conn.executemany(
        f"INSERT INTO trades ({', '.join(cols)}) VALUES "
        f"({', '.join('?' for _ in cols)})",
        [tuple(r.get(c) for c in cols) for r in rows],
    )
    conn.commit()
    conn.close()
    return db_path


def _make_yaml(tmp_path: Path, content: str = _PROP_YAML) -> Path:
    p = tmp_path / "accounts.yaml"
    p.write_text(content)
    return p


def _row(**overrides):
    base = dict(
        timestamp="2026-05-01T12:00:00Z",
        symbol="BTCUSDT",
        direction="LONG",
        strategy_name="vwap",
        setup_type="FVG",
        killzone="NY",
        bias="BULLISH",
        position_size=0.01,
        pnl=0.0,
        pnl_percent=0.0,
        status="open",
        is_backtest=0,
        account_id="prop_velotrade_1",
        created_at="2026-05-01T12:30:00Z",
        entry_reason="vwap signal",
    )
    base.update(overrides)
    return base


class TestAccountContextBuilder:
    def test_build_round_trip(self, tmp_path: Path):
        rows = [
            _row(status="open"),
            _row(
                status="rejected",
                entry_reason="REJECTED: SKIP_MISSION_MET | vwap signal",
                position_size=0.0,
            ),
        ]
        db_path = _make_db(tmp_path, rows)
        yaml_path = _make_yaml(tmp_path)
        out = tmp_path / "datasets"
        builder = AccountContextBuilder()
        paths = builder.build(
            output_dir=out,
            version="v001",
            source=str(db_path),
            commit_sha="abc",
            db_path=db_path,
            accounts_yaml_path=yaml_path,
        )
        assert paths.root == out / "account_context" / "all" / "all" / "v001"

        emitted = [
            json.loads(line)
            for line in paths.data.read_text().splitlines()
            if line
        ]
        assert len(emitted) == 2
        taken, rejected = emitted
        assert taken["was_taken"] is True
        assert taken["skip_reason"] == ""
        assert rejected["was_taken"] is False
        assert rejected["skip_reason"] == "SKIP_MISSION_MET"
        # Mission rules attached.
        assert taken["account_state"] == "evaluation"
        assert taken["target_profit_pct"] == pytest.approx(0.05)
        assert taken["max_dd_pct"] == pytest.approx(0.02)
        assert taken["daily_usd_cap"] == pytest.approx(50.0)
        assert taken["overnight_restricted"] is True

        metadata = json.loads(paths.metadata.read_text())
        assert metadata["family"] == "account_context"
        assert metadata["leakage_test_status"] == "skipped"
        assert metadata["label_version"] == "was-taken-from-status-v1"

        report = validate_dataset(paths.root)
        assert report.ok, report.errors

    def test_status_to_was_taken(self, tmp_path: Path):
        rows = [
            _row(status="open"),
            _row(status="closed"),
            _row(status="CLOSED"),
            _row(status="rejected", entry_reason="REJECTED: X | y"),
            _row(status="exchange_rejected", entry_reason="REJECTED: Y | y"),
            _row(status="REJECTED", entry_reason="REJECTED: Z | y"),
        ]
        db_path = _make_db(tmp_path, rows)
        yaml_path = _make_yaml(tmp_path)
        builder = AccountContextBuilder()
        paths = builder.build(
            output_dir=tmp_path / "out", version="v001", source="x",
            commit_sha="x", db_path=db_path, accounts_yaml_path=yaml_path,
        )
        emitted = [
            json.loads(line)
            for line in paths.data.read_text().splitlines()
            if line
        ]
        was = [r["was_taken"] for r in emitted]
        assert was == [True, True, True, False, False, False]

    def test_unknown_status_dropped(self, tmp_path: Path):
        # Status that is neither taken nor rejected (e.g. legacy "dry_run")
        # should not appear in the dataset — we only know how to label
        # taken/rejected.
        rows = [
            _row(status="open"),
            _row(status="dry_run"),
            _row(status="something_else"),
        ]
        db_path = _make_db(tmp_path, rows)
        yaml_path = _make_yaml(tmp_path)
        builder = AccountContextBuilder()
        paths = builder.build(
            output_dir=tmp_path / "out", version="v001", source="x",
            commit_sha="x", db_path=db_path, accounts_yaml_path=yaml_path,
        )
        emitted = [
            json.loads(line)
            for line in paths.data.read_text().splitlines()
            if line
        ]
        assert len(emitted) == 1

    def test_only_prop_accounts_emitted(self, tmp_path: Path):
        # Live account should be filtered out — no mission rules apply.
        rows = [
            _row(account_id="prop_velotrade_1"),
            _row(account_id="live"),
            _row(account_id="prop_velotrade_2"),
        ]
        db_path = _make_db(tmp_path, rows)
        yaml_path = _make_yaml(tmp_path)
        builder = AccountContextBuilder()
        paths = builder.build(
            output_dir=tmp_path / "out", version="v001", source="x",
            commit_sha="x", db_path=db_path, accounts_yaml_path=yaml_path,
        )
        emitted = [
            json.loads(line)
            for line in paths.data.read_text().splitlines()
            if line
        ]
        ids = sorted(r["account_id"] for r in emitted)
        assert ids == ["prop_velotrade_1", "prop_velotrade_2"]

    def test_account_filter(self, tmp_path: Path):
        rows = [
            _row(account_id="prop_velotrade_1"),
            _row(account_id="prop_velotrade_2"),
        ]
        db_path = _make_db(tmp_path, rows)
        yaml_path = _make_yaml(tmp_path)
        builder = AccountContextBuilder()
        paths = builder.build(
            output_dir=tmp_path / "out", version="v001", source="x",
            commit_sha="x", db_path=db_path, accounts_yaml_path=yaml_path,
            account_id="prop_velotrade_1",
        )
        emitted = [
            json.loads(line)
            for line in paths.data.read_text().splitlines()
            if line
        ]
        assert len(emitted) == 1
        assert emitted[0]["account_id"] == "prop_velotrade_1"

    def test_mission_rules_per_account(self, tmp_path: Path):
        # Different prop accounts pick up different mission rules.
        rows = [
            _row(account_id="prop_velotrade_1"),
            _row(account_id="prop_velotrade_2"),
        ]
        db_path = _make_db(tmp_path, rows)
        yaml_path = _make_yaml(tmp_path)
        builder = AccountContextBuilder()
        paths = builder.build(
            output_dir=tmp_path / "out", version="v001", source="x",
            commit_sha="x", db_path=db_path, accounts_yaml_path=yaml_path,
        )
        emitted = {
            r["account_id"]: r
            for r in (
                json.loads(line)
                for line in paths.data.read_text().splitlines()
                if line
            )
        }
        assert emitted["prop_velotrade_1"]["max_dd_pct"] == pytest.approx(0.02)
        assert emitted["prop_velotrade_1"]["overnight_restricted"] is True
        assert emitted["prop_velotrade_2"]["max_dd_pct"] == pytest.approx(0.05)
        assert emitted["prop_velotrade_2"]["overnight_restricted"] is False

    def test_skip_reason_parser(self, tmp_path: Path):
        rows = [
            _row(
                status="rejected",
                entry_reason="REJECTED: DAILY_LOSS_CAP | turtle_soup signal",
            ),
            _row(
                status="rejected",
                entry_reason="REJECTED: INTRADAY_DRAWDOWN",
            ),
            _row(
                status="rejected",
                entry_reason="rejected: unusual format",  # lowercase prefix
            ),
            _row(
                status="rejected",
                entry_reason="not a rejection prefix",
            ),
        ]
        db_path = _make_db(tmp_path, rows)
        yaml_path = _make_yaml(tmp_path)
        builder = AccountContextBuilder()
        paths = builder.build(
            output_dir=tmp_path / "out", version="v001", source="x",
            commit_sha="x", db_path=db_path, accounts_yaml_path=yaml_path,
        )
        emitted = [
            json.loads(line)
            for line in paths.data.read_text().splitlines()
            if line
        ]
        skip_reasons = [r["skip_reason"] for r in emitted]
        assert skip_reasons[0] == "DAILY_LOSS_CAP"
        assert skip_reasons[1] == "INTRADAY_DRAWDOWN"
        assert skip_reasons[2] == "unusual format"
        assert skip_reasons[3] == ""

    def test_filters_backtest_rows(self, tmp_path: Path):
        rows = [
            _row(),
            _row(is_backtest=1),
        ]
        db_path = _make_db(tmp_path, rows)
        yaml_path = _make_yaml(tmp_path)
        builder = AccountContextBuilder()
        paths = builder.build(
            output_dir=tmp_path / "out", version="v001", source="x",
            commit_sha="x", db_path=db_path, accounts_yaml_path=yaml_path,
        )
        emitted = [
            json.loads(line)
            for line in paths.data.read_text().splitlines()
            if line
        ]
        assert len(emitted) == 1

    def test_no_prop_accounts_yields_nothing(self, tmp_path: Path):
        # YAML without any prop accounts.
        live_only = textwrap.dedent(
            """
            accounts:
              live:
                type: live
                risk:
                  max_dd_pct: 0.10
            """
        ).strip()
        rows = [_row()]
        db_path = _make_db(tmp_path, rows)
        yaml_path = _make_yaml(tmp_path, content=live_only)
        builder = AccountContextBuilder()
        paths = builder.build(
            output_dir=tmp_path / "out", version="v001", source="x",
            commit_sha="x", db_path=db_path, accounts_yaml_path=yaml_path,
        )
        emitted = [
            line
            for line in paths.data.read_text().splitlines()
            if line
        ]
        assert emitted == []

    def test_missing_db_raises(self, tmp_path: Path):
        yaml_path = _make_yaml(tmp_path)
        builder = AccountContextBuilder()
        with pytest.raises(FileNotFoundError):
            list(
                builder.iter_rows(
                    db_path=tmp_path / "nope.db",
                    accounts_yaml_path=yaml_path,
                )
            )

    def test_missing_yaml_raises(self, tmp_path: Path):
        rows = [_row()]
        db_path = _make_db(tmp_path, rows)
        builder = AccountContextBuilder()
        with pytest.raises(FileNotFoundError):
            list(
                builder.iter_rows(
                    db_path=db_path,
                    accounts_yaml_path=tmp_path / "nope.yaml",
                )
            )


def test_registry_includes_account_context():
    from ml.datasets import list_families, get_builder

    assert "account_context" in list_families()
    assert isinstance(get_builder("account_context"), AccountContextBuilder)


class TestSnapshotJoin:
    """S-MLOPT-S12 Part B: optional LEFT JOIN against
    `account_context_snapshots`. Default-off path is unchanged; opt-in
    path attaches five `*_at_signal` columns (None when no match)."""

    def _seed_with_snapshots(self, tmp_path: Path) -> tuple[Path, Path]:
        rows = [
            _row(status="open"),
            _row(
                status="rejected",
                entry_reason="REJECTED: SKIP_MISSION_MET | vwap signal",
                position_size=0.0,
            ),
        ]
        db_path = _make_db(tmp_path, rows)
        # The family JOIN goes trades.id → order_packages.linked_trade_id →
        # snapshots.order_package_id, so we need both tables. Snapshot
        # the FIRST trade only — the second should land with None columns.
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE order_packages (id TEXT PRIMARY KEY, "
            "linked_trade_id INTEGER)"
        )
        conn.executemany(
            "INSERT INTO order_packages(id, linked_trade_id) VALUES (?, ?)",
            [("pkg-1", 1), ("pkg-2", 2)],
        )
        # Apply the snapshot writer's schema + insert one row matching
        # trade #1.
        from src.units.accounts.context_snapshot import (
            ensure_schema as _ensure_schema,
        )
        _ensure_schema(conn)
        conn.execute(
            "INSERT INTO account_context_snapshots ("
            "captured_at_utc, order_package_id, account_id, "
            "strategy_name, symbol, direction, "
            "equity, daily_pnl_realized, daily_equity_high, "
            "daily_drawdown_pct, open_trades_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "2026-05-01T12:00:00.000000+00:00",
                "pkg-1",
                "prop_velotrade_1",
                "vwap",
                "BTCUSDT",
                "long",
                10000.0,
                -150.5,
                10200.0,
                0.0196,
                2,
            ),
        )
        conn.commit()
        conn.close()
        yaml_path = _make_yaml(tmp_path)
        return db_path, yaml_path

    def test_default_off_emits_none_snapshot_columns(self, tmp_path: Path):
        """Without `include_snapshots`, the SQL doesn't touch
        order_packages / snapshots, and the five new columns serialize
        as None — preserving v1's byte-identical behaviour on existing
        builds."""
        db_path, yaml_path = self._seed_with_snapshots(tmp_path)
        builder = AccountContextBuilder()
        rows = list(builder.iter_rows(
            db_path=db_path, accounts_yaml_path=yaml_path,
        ))
        assert len(rows) == 2
        for r in rows:
            assert r["equity_at_signal"] is None
            assert r["daily_pnl_realized_at_signal"] is None
            assert r["open_trades_count_at_signal"] is None

    def test_include_snapshots_joins_when_table_present(self, tmp_path: Path):
        db_path, yaml_path = self._seed_with_snapshots(tmp_path)
        builder = AccountContextBuilder()
        rows = list(builder.iter_rows(
            db_path=db_path, accounts_yaml_path=yaml_path,
            include_snapshots=True,
        ))
        assert len(rows) == 2
        first, second = rows
        # Trade #1 has a matching snapshot.
        assert first["equity_at_signal"] == 10000.0
        assert first["daily_pnl_realized_at_signal"] == -150.5
        assert first["daily_equity_high_at_signal"] == 10200.0
        assert first["daily_drawdown_pct_at_signal"] == pytest.approx(0.0196)
        assert first["open_trades_count_at_signal"] == 2
        # Trade #2 has no matching snapshot → all None.
        assert second["equity_at_signal"] is None
        assert second["open_trades_count_at_signal"] is None

    def test_include_snapshots_no_table_gracefully_falls_back(
        self, tmp_path: Path,
    ):
        """If the caller opts in but the live DB hasn't been
        instrumented yet (the snapshot table is absent), the JOIN is
        suppressed and the rows fall through with None columns."""
        rows = [_row(status="open")]
        db_path = _make_db(tmp_path, rows)
        yaml_path = _make_yaml(tmp_path)
        builder = AccountContextBuilder()
        emitted = list(builder.iter_rows(
            db_path=db_path, accounts_yaml_path=yaml_path,
            include_snapshots=True,  # opt-in honoured, but no table → no crash
        ))
        assert len(emitted) == 1
        assert emitted[0]["equity_at_signal"] is None
        assert emitted[0]["status"] == "open"

    def test_builder_version_bumped_to_v2(self):
        assert AccountContextBuilder.builder_version == "v2"

    def test_schema_includes_snapshot_columns(self):
        for col in (
            "equity_at_signal",
            "daily_pnl_realized_at_signal",
            "daily_equity_high_at_signal",
            "daily_drawdown_pct_at_signal",
            "open_trades_count_at_signal",
        ):
            assert col in AccountContextBuilder.schema
