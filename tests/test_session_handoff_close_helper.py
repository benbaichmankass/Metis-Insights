"""Tests for scripts/session_handoff/close_session.py.

Covers the in-process behaviours that don't talk to git or `gh`:
field updates, validation pass-through, idempotent no-ops, and the
``--validate-only`` short-circuit. The git/dispatch paths are exercised
by the workflow itself in CI; here we confine ourselves to the safe
units.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER = REPO_ROOT / "scripts" / "session_handoff" / "close_session.py"
SCHEMA = REPO_ROOT / "automation" / "session_handoff" / "schema" / "handoff.schema.json"
EXAMPLE = REPO_ROOT / "automation" / "session_handoff" / "examples" / "example_handoff.json"


def _load_module():
    spec = importlib.util.spec_from_file_location("close_session", HELPER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["close_session"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


CS = _load_module()


@pytest.fixture
def working_handoff(tmp_path: Path) -> Path:
    dst = tmp_path / "next_session.json"
    shutil.copy(EXAMPLE, dst)
    return dst


def test_validate_only_returns_zero_on_good(
    working_handoff: Path, capsys: pytest.CaptureFixture
):
    rc = CS.main(
        [
            "--validate-only",
            "--handoff-file",
            str(working_handoff),
            "--schema",
            str(SCHEMA),
        ]
    )
    assert rc == 0
    assert "Handoff is valid" in capsys.readouterr().out


def test_validate_only_rejects_malformed(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        CS.main(
            [
                "--validate-only",
                "--handoff-file",
                str(bad),
                "--schema",
                str(SCHEMA),
            ]
        )
    assert "validation failed" in str(excinfo.value)


def test_apply_updates_appends_completed(working_handoff: Path):
    rc = CS.main(
        [
            "--handoff-file",
            str(working_handoff),
            "--schema",
            str(SCHEMA),
            "--append-completed",
            "Wired up close_session helper",
            "--reason",
            "natural_checkpoint",
        ]
    )
    assert rc == 0
    data = json.loads(working_handoff.read_text())
    assert "Wired up close_session helper" in data["completed_items"]
    # An "updated" history event was appended.
    assert any(h["event"] == "updated" for h in data.get("history", []))


def test_apply_updates_rejects_bad_reason(working_handoff: Path):
    with pytest.raises(SystemExit) as excinfo:
        CS.main(
            [
                "--handoff-file",
                str(working_handoff),
                "--schema",
                str(SCHEMA),
                "--reason",
                "bogus_reason",
            ]
        )
    # argparse rejects choices with code 2 via SystemExit on stderr.
    assert excinfo.value.code == 2


def test_no_edits_means_no_history_growth(working_handoff: Path):
    """Re-running the helper with no edit flags must be idempotent."""
    before = json.loads(working_handoff.read_text())
    history_before = list(before.get("history", []))
    rc = CS.main(
        [
            "--handoff-file",
            str(working_handoff),
            "--schema",
            str(SCHEMA),
        ]
    )
    assert rc == 0
    after = json.loads(working_handoff.read_text())
    assert after.get("history", []) == history_before


def test_setting_ready_for_continue_false_persists(working_handoff: Path):
    rc = CS.main(
        [
            "--handoff-file",
            str(working_handoff),
            "--schema",
            str(SCHEMA),
            "--ready-for-continue",
            "false",
        ]
    )
    assert rc == 0
    data = json.loads(working_handoff.read_text())
    assert data["ready_for_continue"] is False


def test_handoff_outside_repo_rejected_for_commit(tmp_path: Path):
    """Plain validation against an out-of-repo path is fine, but the
    helper refuses to commit/push/dispatch a file that isn't under
    REPO_ROOT (no way to compute a stable repo-relative path)."""
    foreign = tmp_path / "elsewhere.json"
    foreign.write_text(EXAMPLE.read_text(), encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        CS.main(
            [
                "--handoff-file",
                str(foreign),
                "--schema",
                str(SCHEMA),
                "--commit",
            ]
        )
    assert "inside the repo" in str(excinfo.value)
