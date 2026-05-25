"""End-to-end CLI tests for model-attribution / gate-check / stage-guard."""
from __future__ import annotations

import io
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ml.cli import _build_parser, main
from ml.registry.model_registry import ModelRegistry


def _capture(argv: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    saved = sys.stdout
    sys.stdout = buf
    try:
        rc = main(argv)
    finally:
        sys.stdout = saved
    return rc, buf.getvalue()


def _seed_db(path: Path):
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, symbol TEXT, pnl REAL, "
        "pnl_percent REAL, status TEXT, timestamp TEXT, notes TEXT, "
        "is_backtest INT, is_demo INT)"
    )
    conn.execute(
        "CREATE TABLE order_packages (id INTEGER PRIMARY KEY, linked_trade_id INT, "
        "updated_at TEXT)"
    )
    now = datetime.now(timezone.utc)
    conn.execute(
        "INSERT INTO trades VALUES (1,'BTCUSDT',12.0,0.5,'closed',?,NULL,0,0)",
        ((now - timedelta(hours=3)).isoformat(),),
    )
    conn.execute("INSERT INTO order_packages VALUES (10,1,?)", (now.isoformat(),))
    conn.commit()
    conn.close()


def _seed_log(path: Path):
    now = datetime.now(timezone.utc)
    path.write_text(json.dumps({
        "predicted_at_utc": (now - timedelta(hours=3)).isoformat(),
        "model_id": "m", "stage": "shadow", "score": 0.9,
        "row_keys": ["symbol"], "feature_row": {"symbol": "BTCUSDT"},
    }) + "\n")


def test_subcommands_registered():
    parser = _build_parser()
    sub = next(a for a in parser._actions if a.dest == "cmd")
    for cmd in ("model-attribution", "gate-check", "stage-guard"):
        assert cmd in sub.choices


def test_model_attribution_cli(tmp_path: Path):
    db = tmp_path / "j.db"
    _seed_db(db)
    log = tmp_path / "shadow.jsonl"
    _seed_log(log)
    rc, out = _capture(["model-attribution", "--db", str(db), "--shadow-log", str(log)])
    assert rc == 0
    payload = json.loads(out)
    assert payload[0]["model_id"] == "m"
    assert payload[0]["n"] >= 1


def test_gate_check_cli_reports_not_ready(tmp_path: Path):
    reg = tmp_path / "registry-store"
    registry = ModelRegistry(reg)
    registry.register(
        model_id="m",
        manifest={"model_id": "m", "target_deployment_stage": "shadow"},
        model_state_path="x", metrics={"macro_f1": 0.7, "n_eval": 10},
        code_revision="a",
    )
    db = tmp_path / "j.db"
    _seed_db(db)
    log = tmp_path / "shadow.jsonl"
    _seed_log(log)
    rc, out = _capture([
        "gate-check", "m", "--registry-root", str(reg),
        "--db", str(db), "--shadow-log", str(log),
    ])
    assert rc == 0
    payload = json.loads(out)
    assert payload["model_id"] == "m"
    assert payload["ready"] is False  # tiny sample → not promotable
    assert "gates" in payload


def test_gate_check_unknown_model_errors(tmp_path: Path):
    reg = tmp_path / "registry-store"
    ModelRegistry(reg)  # empty
    rc, _ = _capture(["gate-check", "nope", "--registry-root", str(reg)])
    assert rc == 1


def test_stage_guard_cli(tmp_path: Path):
    reg = tmp_path / "registry-store"
    registry = ModelRegistry(reg)
    registry.register(
        model_id="m",
        manifest={"model_id": "m", "target_deployment_stage": "shadow"},
        model_state_path="x", metrics={"macro_f1": 0.7}, code_revision="a",
    )
    db = tmp_path / "j.db"
    _seed_db(db)
    log = tmp_path / "shadow.jsonl"
    _seed_log(log)
    rc, out = _capture([
        "stage-guard", "--registry-root", str(reg),
        "--db", str(db), "--shadow-log", str(log),
    ])
    assert rc == 0
    payload = json.loads(out)
    assert payload["summary"]["total"] == 1
    assert payload["proposals"][0]["model_id"] == "m"
