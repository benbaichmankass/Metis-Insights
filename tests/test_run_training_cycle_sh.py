"""Smoke tests for `scripts/ops/run_training_cycle.sh` (S-AI-WS9 FU).

The script's primary contract is its exit codes + JSONL event
stream. Full end-to-end testing requires a real repo + venv + the
ml.cli surface, which we exercise via the unit tests on those
modules — this file covers the bash-level env-error guardrail.
The happy path is exercised against a real VM during operator
acceptance.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "ops" / "run_training_cycle.sh"


def _run(env: dict[str, str]) -> subprocess.CompletedProcess:
    """Run the script with a controlled env."""
    full_env = {**os.environ, **env}
    return subprocess.run(
        ["/bin/bash", str(_SCRIPT)],
        env=full_env, capture_output=True, text=True, timeout=30,
    )


def _last_event(stdout: str) -> dict:
    """Pluck the last well-formed JSON event from stdout."""
    last = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            last = json.loads(line)
        except Exception:
            continue
    return last or {}


class TestEnvErrors:
    def test_missing_repo_exits_2(self, tmp_path: Path):
        # Point REPO_ROOT at a non-git directory.
        env = {
            "REPO_ROOT": str(tmp_path),
            "TRAINING_LOG_PATH": str(tmp_path / "log.jsonl"),
        }
        r = _run(env)
        assert r.returncode == 2
        ev = _last_event(r.stdout)
        assert ev["status"] == "env_error"
        assert "not a git repo" in ev["detail"]

    def test_jsonl_written_to_log_path(self, tmp_path: Path):
        log = tmp_path / "log.jsonl"
        env = {
            "REPO_ROOT": str(tmp_path),  # also triggers env_error
            "TRAINING_LOG_PATH": str(log),
        }
        _run(env)
        # The env_error event is also persisted to disk.
        assert log.is_file()
        lines = [line for line in log.read_text().splitlines() if line.strip()]
        assert len(lines) == 1
        assert json.loads(lines[0])["status"] == "env_error"


def test_bash_syntax_valid():
    """`bash -n` should pass on the script."""
    r = subprocess.run(
        ["/bin/bash", "-n", str(_SCRIPT)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
