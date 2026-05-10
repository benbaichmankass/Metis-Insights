"""End-to-end CLI tests for `shadow-inspect` + `shadow-stats`
(S-AI-WS8-PART-1)."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

from ml.cli import main


def _capture_main(argv: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    saved = sys.stdout
    sys.stdout = buf
    try:
        rc = main(argv)
    finally:
        sys.stdout = saved
    return rc, buf.getvalue()


def _seed_log(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def _record(
    *,
    model_id: str,
    score: float,
    ts: str = "2026-05-10T12:00:00+00:00",
    stage: str = "shadow",
) -> dict:
    return {
        "predicted_at_utc": ts,
        "model_id": model_id,
        "stage": stage,
        "score": score,
        "row_keys": ["confidence", "direction"],
    }


def test_shadow_inspect_default_output(tmp_path: Path):
    log = tmp_path / "audit.jsonl"
    _seed_log(log, [
        _record(model_id="m-a", score=0.1, ts="2026-05-10T11:00:00+00:00"),
        _record(model_id="m-b", score=0.9, ts="2026-05-10T13:00:00+00:00"),
    ])
    rc, out = _capture_main(["shadow-inspect", "--log", str(log)])
    assert rc == 0
    # Newest-first ordering: m-b (13:00) appears before m-a (11:00).
    assert out.index("m-b") < out.index("m-a")


def test_shadow_inspect_filters(tmp_path: Path):
    log = tmp_path / "audit.jsonl"
    _seed_log(log, [
        _record(model_id="m-a", score=0.1),
        _record(model_id="m-b", score=0.9),
        _record(model_id="m-a", score=0.2),
    ])
    rc, out = _capture_main([
        "shadow-inspect", "--log", str(log), "--model-id", "m-a",
    ])
    assert rc == 0
    assert "m-a" in out
    assert "m-b" not in out


def test_shadow_inspect_no_records(tmp_path: Path):
    rc, out = _capture_main([
        "shadow-inspect", "--log", str(tmp_path / "missing.jsonl"),
    ])
    assert rc == 0
    assert "no shadow predictions matched" in out


def test_shadow_stats_aggregate(tmp_path: Path):
    log = tmp_path / "audit.jsonl"
    _seed_log(log, [
        _record(model_id="m-a", score=0.1),
        _record(model_id="m-a", score=0.5),
        _record(model_id="m-b", score=0.9),
    ])
    rc, out = _capture_main(["shadow-stats", "--log", str(log)])
    assert rc == 0
    assert "m-a" in out
    assert "m-b" in out
    # Header should mention `count` and `mean`.
    header = out.splitlines()[0]
    assert "count" in header
    assert "mean" in header


def test_shadow_stats_with_since(tmp_path: Path):
    log = tmp_path / "audit.jsonl"
    _seed_log(log, [
        _record(model_id="m-a", score=0.1, ts="2026-05-10T10:00:00+00:00"),
        _record(model_id="m-b", score=0.9, ts="2026-05-10T13:00:00+00:00"),
    ])
    rc, out = _capture_main([
        "shadow-stats", "--log", str(log),
        "--since", "2026-05-10T12:00:00+00:00",
    ])
    assert rc == 0
    assert "m-b" in out
    assert "m-a" not in out


def test_shadow_inspect_bad_since(tmp_path: Path, capsys):
    log = tmp_path / "audit.jsonl"
    _seed_log(log, [_record(model_id="m-a", score=0.1)])
    import pytest as _pytest
    with _pytest.raises(SystemExit):
        _capture_main([
            "shadow-inspect", "--log", str(log),
            "--since", "definitely-not-a-timestamp",
        ])
