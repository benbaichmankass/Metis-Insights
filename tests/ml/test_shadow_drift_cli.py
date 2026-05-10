"""End-to-end CLI tests for `shadow-drift` (S-AI-WS8-PART-3)."""
from __future__ import annotations

import io
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ml.cli import main


def _capture(argv: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    saved = sys.stdout
    sys.stdout = buf
    try:
        rc = main(argv)
    finally:
        sys.stdout = saved
    return rc, buf.getvalue()


def _seed_log(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def _record(*, model_id: str, score: float, ts: datetime, stage: str = "shadow") -> dict:
    return {
        "predicted_at_utc": ts.isoformat(),
        "model_id": model_id,
        "stage": stage,
        "score": score,
        "row_keys": ["confidence"],
    }


def test_insufficient_data_when_log_empty(tmp_path: Path):
    log = tmp_path / "shadow.jsonl"
    log.write_text("")
    rc, out = _capture([
        "shadow-drift", "--log", str(log), "--model-id", "m-x",
    ])
    assert rc == 0
    payload = json.loads(out)
    assert payload["verdict"] == "insufficient_data"
    assert payload["reference_count"] == 0
    assert payload["current_count"] == 0


def test_full_report_when_both_windows_populated(tmp_path: Path):
    log = tmp_path / "shadow.jsonl"
    now = datetime.now(timezone.utc)
    # Reference: 15 days ago, low scores.
    ref_records = [
        _record(model_id="m-x", score=0.2, ts=now - timedelta(days=15, hours=h))
        for h in range(20)
    ]
    # Current: today, high scores — significant drift.
    cur_records = [
        _record(model_id="m-x", score=0.8, ts=now - timedelta(hours=h))
        for h in range(20)
    ]
    _seed_log(log, ref_records + cur_records)
    rc, out = _capture([
        "shadow-drift", "--log", str(log), "--model-id", "m-x",
        "--reference-days", "30", "--current-days", "7",
    ])
    assert rc == 0
    payload = json.loads(out)
    assert payload["reference_count"] == 20
    assert payload["current_count"] == 20
    assert payload["overall_verdict"] == "significant"
    assert payload["ks"] > 0.5
    assert payload["psi"] > 0.25


def test_filters_by_model_id(tmp_path: Path):
    log = tmp_path / "shadow.jsonl"
    now = datetime.now(timezone.utc)
    records = [
        _record(model_id="wanted", score=0.2, ts=now - timedelta(days=15)),
        _record(model_id="wanted", score=0.8, ts=now - timedelta(hours=1)),
        # noise from another model:
        _record(model_id="other", score=0.99, ts=now - timedelta(hours=2)),
    ]
    _seed_log(log, records)
    rc, out = _capture([
        "shadow-drift", "--log", str(log), "--model-id", "wanted",
    ])
    payload = json.loads(out)
    assert payload["reference_count"] == 1
    assert payload["current_count"] == 1
    # 1 vs 1 still computes — no insufficient_data trigger.
    assert payload["overall_verdict"] in {"no_change", "minor", "moderate", "significant"}


def test_subcommand_present_in_parser():
    # Inspecting the built parser confirms shadow-drift is registered.
    from ml.cli import _build_parser
    parser = _build_parser()
    sub = next(
        action for action in parser._actions
        if action.dest == "cmd"
    )
    assert "shadow-drift" in sub.choices
