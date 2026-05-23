"""Tests for the trainer-store sidecar ingester
(``src.units.db.trainer_store``).

Proves the trainer-mirror JSONL/JSON files are ingested into the
federated sidecar DB with promoted columns + a full ``data`` blob, that
the ingest is idempotent, and that ``build_if_stale`` only rebuilds when
the mirror actually changed (and never materializes an empty sidecar when
there is no mirror).
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from src.units.db import trainer_store as ts


def _make_mirror(root: Path) -> Path:
    mirror = root / "trainer_mirror"
    (mirror / "trainer").mkdir(parents=True)
    (mirror / "training_cycle.jsonl").write_text(
        '{"ts":"2026-05-14T15:53:36+00:00","status":"pulled","head":"eacb751"}\n'
        '{"ts":"2026-05-14T15:53:38+00:00","status":"sync_ok"}\n',
        encoding="utf-8",
    )
    (mirror / "trainer" / "dataset_builds.jsonl").write_text(
        '{"ts":"2026-05-13T14:17:09+00:00","status":"build_start","version":"v001"}\n'
        '{"ts":"2026-05-13T14:17:09+00:00","status":"building","family":"backtest_results"}\n',
        encoding="utf-8",
    )
    (mirror / "trainer" / "db_pulls.jsonl").write_text(
        '{"ts":"2026-05-13T13:30:41+00:00","status":"ok","artifact":"trade_journal.db","size_bytes":123}\n',
        encoding="utf-8",
    )
    (mirror / "registry.jsonl").write_text(
        json.dumps({
            "model_id": "btc-regime-5m-baseline-v1",
            "status": "candidate",
            "target_deployment_stage": "shadow",
            "created_at": "2026-05-22T10:14:14+00:00",
            "manifest": {"model_family": "classification_baseline"},
            "metrics": {"accuracy": 0.7},
        }) + "\n",
        encoding="utf-8",
    )
    run_dir = mirror / "experiments-runs" / "m1" / "20260523T004649Z"
    run_dir.mkdir(parents=True)
    (run_dir / "metrics.json").write_text('{"mae": 0.18, "n_eval": 15.0}', encoding="utf-8")
    (run_dir / "manifest.json").write_text('{"model_id": "m1"}', encoding="utf-8")
    sweep = mirror / "backtests" / "2026-05-17"
    sweep.mkdir(parents=True)
    (sweep / "SUMMARY.md").write_text("# sweep\n", encoding="utf-8")
    (sweep / "all_metrics.json").write_text('{"vwap": {"V_BASELINE": {"trades": 10}}}', encoding="utf-8")
    return mirror


def test_ingest_populates_all_tables(tmp_path):
    mirror = _make_mirror(tmp_path)
    db = tmp_path / "trainer_store.db"
    counts = ts.ingest_trainer_store(mirror_root=mirror, db_path=str(db))

    assert counts["training_cycle"] == 2
    assert counts["dataset_builds"] == 2
    assert counts["db_pulls"] == 1
    assert counts["model_registry"] == 1
    assert counts["experiment_runs"] == 1
    assert counts["backtest_sweeps"] == 1

    conn = sqlite3.connect(str(db))
    try:
        # promoted columns are populated
        row = conn.execute(
            "SELECT model_id, status, target_deployment_stage, model_family "
            "FROM model_registry"
        ).fetchone()
        assert row == (
            "btc-regime-5m-baseline-v1", "candidate", "shadow",
            "classification_baseline",
        )
        # full record preserved in data blob
        data = json.loads(conn.execute("SELECT data FROM model_registry").fetchone()[0])
        assert data["metrics"]["accuracy"] == 0.7
        # experiment run metrics captured
        m = conn.execute(
            "SELECT metrics FROM experiment_runs WHERE model_id='m1'"
        ).fetchone()[0]
        assert json.loads(m)["mae"] == 0.18
        # backtest sweep summary captured
        s = conn.execute("SELECT summary_md FROM backtest_sweeps").fetchone()[0]
        assert "sweep" in s
    finally:
        conn.close()


def test_ingest_is_idempotent(tmp_path):
    mirror = _make_mirror(tmp_path)
    db = tmp_path / "trainer_store.db"
    ts.ingest_trainer_store(mirror_root=mirror, db_path=str(db))
    ts.ingest_trainer_store(mirror_root=mirror, db_path=str(db))  # twice
    conn = sqlite3.connect(str(db))
    try:
        assert conn.execute("SELECT COUNT(*) FROM training_cycle").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM model_registry").fetchone()[0] == 1
    finally:
        conn.close()


def test_build_if_stale_rebuilds_only_on_change(tmp_path):
    mirror = _make_mirror(tmp_path)
    db = tmp_path / "trainer_store.db"

    assert ts.build_if_stale(mirror_root=mirror, db_path=str(db)) is True   # first build
    assert ts.build_if_stale(mirror_root=mirror, db_path=str(db)) is False  # already fresh

    # Mutate the mirror → next call rebuilds.
    time.sleep(0.01)
    new_file = mirror / "training_cycle.jsonl"
    new_file.write_text(
        new_file.read_text(encoding="utf-8")
        + '{"ts":"2026-05-14T16:00:00+00:00","status":"cycle_end"}\n',
        encoding="utf-8",
    )
    assert ts.build_if_stale(mirror_root=mirror, db_path=str(db)) is True
    conn = sqlite3.connect(str(db))
    try:
        assert conn.execute("SELECT COUNT(*) FROM training_cycle").fetchone()[0] == 3
    finally:
        conn.close()


def test_build_if_stale_no_mirror_is_noop(tmp_path):
    """No trainer_mirror/ → no rebuild, no stray sidecar created."""
    db = tmp_path / "trainer_store.db"
    assert ts.build_if_stale(mirror_root=tmp_path / "absent", db_path=str(db)) is False
    assert not db.exists()
