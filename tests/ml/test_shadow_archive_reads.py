"""FIX-CA-20 — every windowed / minimum-n ml/ consumer of the shadow log reads
through rotated archives, not the active log alone.

``ict-shadow-log-rotate.timer`` moves ``shadow_predictions.jsonl`` to a dated
``.jsonl.gz`` archive every ~25-29 days and touches a fresh active file. An
active-only read truncates any window at the last rotation boundary
(CA-B01-shadow-gates-read-active-log-only / PI-20260927-3WM5HADW-0001; the API
half was #13146). Each test plants the same shape the audit repro used: a
short active log holding only the CURRENT window, and a gzipped archive
beside it holding the REFERENCE window, then asserts the consumer saw the
archived rows.
"""
from __future__ import annotations

import gzip
import io
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ml.shadow.inspector import iter_records_with_archives

_NOW = datetime.now(timezone.utc)


def _row(model_id: str, ts: datetime, score: float, **extra) -> str:
    return json.dumps({
        "predicted_at_utc": ts.isoformat(), "model_id": model_id,
        "stage": extra.pop("stage", "advisory"), "score": score,
        "row_keys": ["symbol"], "feature_row": extra.pop("feature_row", {"symbol": "BTCUSDT"}),
        **extra,
    }) + "\n"


def _plant(tmp_path: Path, model_id: str = "m") -> Path:
    """Active log = 20 rows in the last 3 days; archive = 60 rows 10-30 days
    ago (inside a 30d reference window). Returns the active path."""
    active = tmp_path / "shadow_predictions.jsonl"
    active.write_text("".join(
        _row(model_id, _NOW - timedelta(days=3) + timedelta(hours=i * 3), 0.5)
        for i in range(20)
    ))
    archive = tmp_path / "shadow_predictions.2026-09-26.jsonl.gz"
    with gzip.open(archive, "wt", encoding="utf-8") as fh:
        for i in range(60):
            fh.write(_row(model_id, _NOW - timedelta(days=30) + timedelta(hours=i * 8), 0.5))
    return active


def test_helper_reads_archives_and_active(tmp_path: Path):
    active = _plant(tmp_path)
    assert len(list(iter_records_with_archives(active))) == 80


def test_helper_since_filters_rows_and_skips_old_archives(tmp_path: Path):
    active = _plant(tmp_path)
    since = _NOW - timedelta(days=5)
    rows = list(iter_records_with_archives(active, since=since))
    assert len(rows) == 20 and all(r.predicted_at_utc >= since for r in rows)
    # An archive whose mtime predates `since` is skipped unopened.
    old = tmp_path / "shadow_predictions.2026-01-01.jsonl.gz"
    with gzip.open(old, "wt", encoding="utf-8") as fh:
        fh.write(_row("m", _NOW, 0.5))  # would pass the row filter if opened
    past = (_NOW - timedelta(days=90)).timestamp()
    os.utime(old, (past, past))
    assert len(list(iter_records_with_archives(active, since=since))) == 20


def _stage_guard_registry(tmp_path: Path) -> Path:
    from ml.registry.model_registry import ModelRegistry

    reg = tmp_path / "registry-store"
    ModelRegistry(reg).register(
        model_id="m", manifest={"model_id": "m", "target_deployment_stage": "advisory"},
        model_state_path="x", metrics={"macro_f1": 0.7}, code_revision="a",
    )
    return reg


def _empty_db(path: Path) -> Path:
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, symbol TEXT, pnl REAL, "
        "pnl_percent REAL, status TEXT, timestamp TEXT, notes TEXT, "
        "is_backtest INT, is_demo INT)"
    )
    conn.execute(
        "CREATE TABLE order_packages (order_package_id TEXT PRIMARY KEY, "
        "linked_trade_id INT, updated_at TEXT)"
    )
    conn.commit()
    conn.close()
    return path


def test_stage_guard_drift_includes_archived_reference_window(tmp_path: Path):
    from ml.promotion.stage_guard import run_stage_guard

    active = _plant(tmp_path)
    proposals = run_stage_guard(
        registry_root=_stage_guard_registry(tmp_path),
        db_path=_empty_db(tmp_path / "j.db"), shadow_log=active,
    )
    # Active-only: the reference window (8-37d ago) is empty → drift None.
    assert proposals[0].evidence["drift_verdict"] is not None


def test_gate_check_drift_includes_archived_reference_window(tmp_path: Path):
    from ml.cli import main
    from ml.registry.model_registry import ModelRegistry

    reg = tmp_path / "registry-store"
    ModelRegistry(reg).register(
        model_id="m", manifest={"model_id": "m", "target_deployment_stage": "shadow"},
        model_state_path="x", metrics={"macro_f1": 0.7, "n_eval": 10}, code_revision="a",
    )
    active = _plant(tmp_path)
    buf, saved = io.StringIO(), sys.stdout
    sys.stdout = buf
    try:
        rc = main(["gate-check", "m", "--registry-root", str(reg),
                   "--shadow-log", str(active)])
    finally:
        sys.stdout = saved
    assert rc == 0
    gates = {g["name"]: g for g in json.loads(buf.getvalue())["gates"]}
    assert gates["drift_clean"]["status"] != "insufficient_data", gates["drift_clean"]


def test_attribution_n_includes_archived_rows(tmp_path: Path):
    from ml.promotion.attribution import compute_attribution

    db = _empty_db(tmp_path / "j.db")
    conn = sqlite3.connect(str(db))
    measured = '{"exit_price_source": "recorded_exit_price"}'
    trade_ts = _NOW - timedelta(days=20)  # inside the ARCHIVED span only
    conn.execute("INSERT INTO trades VALUES (1,'BTCUSDT',12.0,0.5,'closed',?,?,0,0)",
                 (trade_ts.isoformat(), measured))
    conn.execute("INSERT INTO order_packages VALUES (10,1,?)",
                 ((trade_ts + timedelta(hours=2)).isoformat(),))
    conn.commit()
    conn.close()
    active = tmp_path / "shadow_predictions.jsonl"
    active.write_text(_row("m", _NOW - timedelta(hours=1), 0.9))
    with gzip.open(tmp_path / "shadow_predictions.2026-09-26.jsonl.gz", "wt") as fh:
        fh.write(_row("m", trade_ts + timedelta(minutes=5), 0.9))
    attrs = compute_attribution(db_path=db, shadow_log=active)
    assert attrs and attrs[0].n == 1


def test_drift_retrain_n_includes_archived_rows(tmp_path: Path):
    from ml.shadow.drift_retrain import evaluate_models

    active = _plant(tmp_path)
    decisions = evaluate_models(
        registry_root=_stage_guard_registry(tmp_path),
        shadow_log=active, configs_root=tmp_path / "configs",
    )
    assert decisions[0].n_observations == 80


def test_live_parity_n_includes_archived_rows(tmp_path: Path):
    from ml.promotion.live_parity import compute_live_parity
    from ml.registry.model_registry import ModelRegistry

    state = tmp_path / "state.json"
    state.write_text(json.dumps({
        "trainer": "ml.trainers.constant_baseline.ConstantPredictionTrainer",
        "constant": 0.5,
    }))
    reg = tmp_path / "registry-store"
    registry = ModelRegistry(reg)
    registry.register(
        model_id="m", model_state_path=str(state), metrics={}, code_revision="x",
        manifest={
            "manifest_version": "v1", "model_id": "m", "model_family": "regime",
            "trainer": "ml.trainers.constant_baseline.ConstantPredictionTrainer",
            "trainer_config": {"target_column": "y"},
            "dataset": {"family": "market_features", "symbol_scope": "BTCUSDT",
                        "timeframe": "15m", "version": "v1"},
            "evaluator": "ml.evaluators.regression.RegressionEvaluator",
            "evaluator_config": {"target_column": "y", "time_column": "ts"},
            "target_deployment_stage": "shadow",
        },
    )
    active = _plant(tmp_path)
    res = compute_live_parity(
        registry.get("m"), shadow_log=active, datasets_root=tmp_path / "ds",
        registry_root=reg,
    )
    assert res.n_live_rows == 80


# Call sites allowed to read ONE shadow-log path. Everything else in ml/ that
# reads the shadow log must go through `iter_records_with_archives`.
_SINGLE_PATH_ALLOWED = {
    # the helpers themselves
    ("ml/shadow/inspector.py", "iter_records"),
    # attribution's per-path chain: used for the separate backfill log only
    ("ml/promotion/attribution.py", "iter_records"),
    ("ml/promotion/attribution.py", "iter_shadow_records"),
    # operator inspection tools with no window or n: `shadow-inspect` /
    # `shadow-stats` show what is in the file they are pointed at
    ("ml/cli.py", "_cmd_shadow_inspect"),
    ("ml/cli.py", "_cmd_shadow_stats"),
}


def test_no_ml_consumer_reads_the_active_log_alone():
    """Detector (FIX-CA-20): a new ml/ call of `iter_records(` /
    `iter_shadow_records(` must be a deliberate single-file read listed
    above, not a windowed gate that silently stops at the rotation boundary."""
    import ast

    repo = Path(__file__).resolve().parents[2]
    offenders = []
    for path in sorted((repo / "ml").rglob("*.py")):
        rel = path.relative_to(repo).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call):
                    continue
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if name not in ("iter_records", "iter_shadow_records"):
                    continue
                if (rel, fn.name) in _SINGLE_PATH_ALLOWED or (rel, name) in _SINGLE_PATH_ALLOWED:
                    continue
                offenders.append(f"{rel}:{node.lineno} {fn.name}() calls {name}()")
    assert not offenders, offenders


def test_helper_archives_since_prunes_files_not_rows(tmp_path: Path):
    active = tmp_path / "shadow_predictions.jsonl"
    old_ts = datetime(2026, 6, 1, tzinfo=timezone.utc)
    active.write_text(_row("m", old_ts, 0.5))  # old row in the ACTIVE log
    old = tmp_path / "shadow_predictions.2026-01-01.jsonl.gz"
    with gzip.open(old, "wt", encoding="utf-8") as fh:
        fh.write(_row("m", old_ts, 0.5))
    past = (_NOW - timedelta(days=90)).timestamp()
    os.utime(old, (past, past))
    rows = list(iter_records_with_archives(
        active, archives_since=_NOW - timedelta(days=60),
    ))
    assert len(rows) == 1  # active row kept, stale archive not opened
