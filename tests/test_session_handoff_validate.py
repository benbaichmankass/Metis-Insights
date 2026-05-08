"""Tests for scripts/session_handoff/validate_handoff.py.

The validator is the single source of truth used by:
  * the close_session.py helper before commit
  * .github/workflows/continue-work.yml inside the runner
  * humans running it locally as a CLI

These tests cover malformed JSON, missing required fields, the
``ready_for_continue=false`` invariant, schema-version drift, sprint-id
mismatch, and the happy path on the example artifact.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "session_handoff" / "validate_handoff.py"
SCHEMA = REPO_ROOT / "automation" / "session_handoff" / "schema" / "handoff.schema.json"
EXAMPLE = REPO_ROOT / "automation" / "session_handoff" / "examples" / "example_handoff.json"
LIVE = REPO_ROOT / "automation" / "session_handoff" / "next_session.json"


def _load_module():
    spec = importlib.util.spec_from_file_location("validate_handoff", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["validate_handoff"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


VH = _load_module()


def _write(tmp_path: Path, payload: dict | str, name: str = "h.json") -> Path:
    path = tmp_path / name
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _good_payload() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def test_example_handoff_validates():
    """The shipped example file is the canonical happy path."""
    data = VH.validate_path(EXAMPLE, schema_path=SCHEMA)
    assert data["sprint_id"] == "S-061-session-handoff"
    assert data["ready_for_continue"] is True


def test_live_next_session_validates():
    """The live `next_session.json` shipped with this PR must validate."""
    data = VH.validate_path(LIVE, schema_path=SCHEMA)
    assert data["schema_version"] == 1
    assert data["sprint_id"]


def test_malformed_json_rejected(tmp_path: Path):
    bad = _write(tmp_path, "not-json-at-all{")
    with pytest.raises(VH.HandoffError) as excinfo:
        VH.validate_path(bad, schema_path=SCHEMA)
    assert excinfo.value.exit_code == 1
    assert "malformed JSON" in str(excinfo.value)


def test_missing_required_field_rejected(tmp_path: Path):
    payload = _good_payload()
    payload.pop("sprint_id")
    bad = _write(tmp_path, payload)
    with pytest.raises(VH.HandoffError) as excinfo:
        VH.validate_path(bad, schema_path=SCHEMA)
    assert excinfo.value.exit_code == 1


def test_wrong_schema_version_rejected(tmp_path: Path):
    payload = _good_payload()
    payload["schema_version"] = 99
    bad = _write(tmp_path, payload)
    with pytest.raises(VH.HandoffError) as excinfo:
        VH.validate_path(bad, schema_path=SCHEMA)
    assert excinfo.value.exit_code == 1


def test_ready_for_continue_false_blocks_when_required(tmp_path: Path):
    payload = _good_payload()
    payload["ready_for_continue"] = False
    closed = _write(tmp_path, payload)
    # No-op without --require-ready.
    VH.validate_path(closed, schema_path=SCHEMA)
    # Blocks under workflow's --require-ready path.
    with pytest.raises(VH.HandoffError) as excinfo:
        VH.validate_path(closed, schema_path=SCHEMA, require_ready=True)
    assert excinfo.value.exit_code == 2


def test_sprint_id_mismatch_rejected(tmp_path: Path):
    payload = _good_payload()
    good = _write(tmp_path, payload)
    with pytest.raises(VH.HandoffError) as excinfo:
        VH.validate_path(good, schema_path=SCHEMA, expect_sprint_id="some-other-sprint")
    assert excinfo.value.exit_code == 2


def test_handoff_reason_other_requires_note(tmp_path: Path):
    payload = _good_payload()
    payload["handoff_reason"] = "other"
    payload["handoff_reason_note"] = ""
    bad = _write(tmp_path, payload)
    with pytest.raises(VH.HandoffError) as excinfo:
        VH.validate_path(bad, schema_path=SCHEMA)
    assert excinfo.value.exit_code == 2


def test_missing_file_rejected(tmp_path: Path):
    nope = tmp_path / "does-not-exist.json"
    with pytest.raises(VH.HandoffError) as excinfo:
        VH.validate_path(nope, schema_path=SCHEMA)
    assert excinfo.value.exit_code == 1


def test_cli_main_returns_zero_on_good(tmp_path: Path, capsys: pytest.CaptureFixture):
    rc = VH.main([str(EXAMPLE), "--schema", str(SCHEMA)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "handoff OK" in out


def test_cli_main_returns_nonzero_on_bad(tmp_path: Path, capsys: pytest.CaptureFixture):
    payload = _good_payload()
    payload.pop("sprint_id")
    bad = _write(tmp_path, payload)
    rc = VH.main([str(bad), "--schema", str(SCHEMA)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "handoff validation failed" in err
