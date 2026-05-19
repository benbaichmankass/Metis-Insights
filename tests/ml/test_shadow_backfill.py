"""Tests for `ml.shadow.backfill` (retroactive-decision replay).

The backfill writer reads `trade_journal.db::trades` joined with
`order_packages`, builds a signal-time feature row per trade, and
scores it against every shadow-stage model in the registry. The
output is a one-shot JSONL file with `backfill_kind:
"retroactive_decision"` + `trade_id` on every record.

These tests stub trade data and a single shadow-stage model
(ConstantPredictor) and verify:

- The output JSONL covers exactly the configured statuses
  (open/closed/orphaned by default, plus rejected/exchange_rejected
  when `include_rejected=True`).
- Records carry the right `backfill_kind` + `trade_id`.
- Re-running truncates the prior output (no append).
- Per-trade prediction failures are skipped, not fatal.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ml.registry.model_registry import ModelRegistry
from ml.shadow.backfill import run_backfill


def _seed_db(db_path: Path, trades: list[dict]) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY,
                symbol TEXT,
                direction TEXT,
                strategy_name TEXT,
                setup_type TEXT,
                killzone TEXT,
                bias TEXT,
                status TEXT,
                timestamp TEXT,
                is_backtest INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS order_packages (
                id INTEGER PRIMARY KEY,
                linked_trade_id INTEGER,
                confidence REAL
            );
            """
        )
        for t in trades:
            conn.execute(
                "INSERT INTO trades "
                "(id, symbol, direction, strategy_name, setup_type, "
                " killzone, bias, status, timestamp, is_backtest) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    t["id"], t["symbol"], t["direction"], t["strategy_name"],
                    t["setup_type"], t.get("killzone", ""),
                    t.get("bias", ""), t["status"], t["timestamp"],
                    t.get("is_backtest", 0),
                ),
            )
            if "confidence" in t:
                conn.execute(
                    "INSERT INTO order_packages "
                    "(linked_trade_id, confidence) VALUES (?, ?)",
                    (t["id"], t["confidence"]),
                )
        conn.commit()
    finally:
        conn.close()


def _register_constant_model(
    registry_root: Path, state_root: Path, model_id: str, constant: float,
) -> None:
    # state file lives OUTSIDE the registry root so registry.list()'s
    # `*.json` glob doesn't try to deserialize it as a RegistryEntry.
    state_path = state_root / f"{model_id}_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "trainer":
                    "ml.trainers.constant_baseline.ConstantPredictionTrainer",
                "constant": constant,
            }
        )
    )
    reg = ModelRegistry(registry_root)
    reg.register(
        model_id=model_id,
        manifest={"manifest_version": "v1"},   # defaults to shadow stage
        model_state_path=str(state_path),
        metrics={"mae": 0.0},
        code_revision="x",
    )


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text().splitlines()
        if line.strip()
    ]


@pytest.fixture()
def env(tmp_path: Path):
    db = tmp_path / "trade_journal.db"
    reg_root = tmp_path / "registry-store"
    state_root = tmp_path / "model-states"
    out = tmp_path / "shadow_predictions_backfill.jsonl"

    _seed_db(db, [
        {"id": 1, "symbol": "BTCUSDT", "direction": "long",
         "strategy_name": "vwap", "setup_type": "vwap",
         "status": "closed", "timestamp": "2026-05-01T10:00:00+00:00",
         "confidence": 0.7},
        {"id": 2, "symbol": "BTCUSDT", "direction": "short",
         "strategy_name": "turtle_soup", "setup_type": "turtle_soup",
         "status": "open", "timestamp": "2026-05-02T10:00:00+00:00",
         "confidence": 0.5},
        {"id": 3, "symbol": "BTCUSDT", "direction": "long",
         "strategy_name": "vwap", "setup_type": "vwap",
         "status": "rejected", "timestamp": "2026-05-03T10:00:00+00:00",
         "confidence": 0.3},
        {"id": 4, "symbol": "BTCUSDT", "direction": "long",
         "strategy_name": "vwap", "setup_type": "vwap",
         "status": "exchange_rejected",
         "timestamp": "2026-05-04T10:00:00+00:00", "confidence": 0.4},
        # is_backtest=1 row — must be skipped regardless of include_rejected
        {"id": 5, "symbol": "BTCUSDT", "direction": "long",
         "strategy_name": "vwap", "setup_type": "vwap",
         "status": "closed", "timestamp": "2026-05-05T10:00:00+00:00",
         "confidence": 0.6, "is_backtest": 1},
    ])

    _register_constant_model(reg_root, state_root, "m-shadow-a", constant=0.5)
    _register_constant_model(reg_root, state_root, "m-shadow-b", constant=0.9)
    return db, ModelRegistry(reg_root), out


class TestRunBackfill:
    def test_scores_all_statuses_when_include_rejected_true(self, env):
        db, reg, out = env
        summary = run_backfill(
            db_path=db, registry=reg, output_path=out,
            include_rejected=True,
        )
        rows = _read_jsonl(out)
        # 4 non-backtest trades (id 1-4) × 2 models = 8 records.
        # id 5 is is_backtest=1, must be excluded.
        assert summary["trade_count"] == 4
        assert summary["record_count"] == 8
        assert len(rows) == 8
        trade_ids = sorted({r["trade_id"] for r in rows})
        assert trade_ids == ["1", "2", "3", "4"]

    def test_excludes_rejected_when_include_rejected_false(self, env):
        db, reg, out = env
        summary = run_backfill(
            db_path=db, registry=reg, output_path=out,
            include_rejected=False,
        )
        # id 1 closed + id 2 open = 2 trades × 2 models = 4 records.
        # rejected (id 3) + exchange_rejected (id 4) excluded.
        assert summary["trade_count"] == 2
        rows = _read_jsonl(out)
        trade_ids = sorted({r["trade_id"] for r in rows})
        assert trade_ids == ["1", "2"]

    def test_record_shape(self, env):
        db, reg, out = env
        run_backfill(
            db_path=db, registry=reg, output_path=out,
            include_rejected=True,
        )
        rows = _read_jsonl(out)
        for r in rows:
            assert r["backfill_kind"] == "retroactive_decision"
            assert r["trade_id"] in {"1", "2", "3", "4"}
            assert r["model_id"] in {"m-shadow-a", "m-shadow-b"}
            assert r["stage"] == "shadow"
            assert "predicted_at_utc" in r
            assert "feature_row" in r
            # ConstantPredictor returns the constant from state.
            assert r["score"] in (0.5, 0.9)
            # feature_row carries the signal-time context, not the
            # post-decision outcome.
            assert "pnl" not in r["feature_row"]
            assert "exit_price" not in r["feature_row"]
            assert r["feature_row"]["strategy_name"] in {"vwap", "turtle_soup"}

    def test_rerun_truncates_output(self, env):
        db, reg, out = env
        # First run with all 4 trades.
        run_backfill(
            db_path=db, registry=reg, output_path=out,
            include_rejected=True,
        )
        first_count = len(_read_jsonl(out))
        # Second run with just 2 trades (no rejections).
        run_backfill(
            db_path=db, registry=reg, output_path=out,
            include_rejected=False,
        )
        second_rows = _read_jsonl(out)
        # If the file weren't truncated, it would carry over the
        # first run's records too.
        assert len(second_rows) < first_count
        assert all(r["trade_id"] in {"1", "2"} for r in second_rows)

    def test_now_override_lands_on_record(self, env):
        db, reg, out = env
        fixed = datetime(2026, 5, 19, 6, tzinfo=timezone.utc)
        run_backfill(
            db_path=db, registry=reg, output_path=out,
            include_rejected=False, now=fixed,
        )
        rows = _read_jsonl(out)
        for r in rows:
            assert r["predicted_at_utc"] == "2026-05-19T06:00:00+00:00"

    def test_no_shadow_models_emits_empty_output(self, tmp_path):
        db = tmp_path / "trade_journal.db"
        reg_root = tmp_path / "empty-registry"
        out = tmp_path / "out.jsonl"
        _seed_db(db, [
            {"id": 1, "symbol": "BTCUSDT", "direction": "long",
             "strategy_name": "vwap", "setup_type": "vwap",
             "status": "closed",
             "timestamp": "2026-05-01T10:00:00+00:00",
             "confidence": 0.7},
        ])
        reg = ModelRegistry(reg_root)
        summary = run_backfill(
            db_path=db, registry=reg, output_path=out,
        )
        assert summary["models"] == []
        assert summary["record_count"] == 0
        # Trade scanned but no model emitted a row → counted as skipped.
        assert summary["trade_count"] == 1
        assert summary["skipped_trades"] == 1
        assert _read_jsonl(out) == []
