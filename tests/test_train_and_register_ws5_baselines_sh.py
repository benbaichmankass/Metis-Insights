"""Smoke tests for `scripts/ops/train_and_register_ws5_baselines.sh`.

Same shape as `tests/test_run_training_cycle_sh.py` — covers the
bash-level guardrails (env error, invalid TARGET_STAGE, bash -n).
The happy path requires a real venv + ml.cli + a populated
DATASETS_ROOT, which we exercise against the trainer VM during
operator acceptance rather than in CI.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "ops" / "train_and_register_ws5_baselines.sh"


def _run(env: dict[str, str]) -> subprocess.CompletedProcess:
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
    def test_invalid_target_stage_exits_2(self, tmp_path: Path):
        env = {
            "REPO_ROOT": str(tmp_path),
            "LOG_PATH": str(tmp_path / "kickoff.jsonl"),
            "TARGET_STAGE": "not_a_real_stage",
        }
        r = _run(env)
        assert r.returncode == 2
        ev = _last_event(r.stdout)
        assert ev["status"] == "env_error"
        assert "TARGET_STAGE" in ev["detail"]
        assert "not_a_real_stage" in ev["detail"]

    def test_missing_repo_exits_2(self, tmp_path: Path):
        # Valid TARGET_STAGE but REPO_ROOT isn't a git repo.
        env = {
            "REPO_ROOT": str(tmp_path),
            "LOG_PATH": str(tmp_path / "kickoff.jsonl"),
            "TARGET_STAGE": "shadow",
        }
        r = _run(env)
        assert r.returncode == 2
        ev = _last_event(r.stdout)
        assert ev["status"] == "env_error"
        assert "not a git repo" in ev["detail"]

    def test_jsonl_persists_to_log_path(self, tmp_path: Path):
        log = tmp_path / "kickoff.jsonl"
        env = {
            "REPO_ROOT": str(tmp_path),
            "LOG_PATH": str(log),
            "TARGET_STAGE": "shadow",
        }
        _run(env)
        # env_error event is also persisted to disk.
        assert log.is_file()
        lines = [line for line in log.read_text().splitlines() if line.strip()]
        assert len(lines) == 1
        assert json.loads(lines[0])["status"] == "env_error"


class TestStageLadder:
    """Sanity-check the canonical stage list embedded in the script
    matches the registry's promotion ladder. If the ladder grows or
    re-orders, this test makes the script's coupling visible."""

    def test_canonical_ladder_in_script(self):
        contents = _SCRIPT.read_text()
        # The script defines LADDER as a bash array — assert the
        # documented stages appear in order. Loose match (substring)
        # so we don't trip on incidental whitespace.
        canonical = [
            "research_only",
            "candidate",
            "backtest_approved",
            "shadow",
            "advisory",
            "limited_live",
            "live_approved",
        ]
        # Find the LADDER=( ... ) declaration.
        marker = contents.find("LADDER=(")
        assert marker != -1, "LADDER= declaration missing from script"
        end = contents.find(")", marker)
        assert end != -1
        body = contents[marker:end]
        last_pos = 0
        for stage in canonical:
            pos = body.find(stage, last_pos)
            assert pos != -1, f"stage {stage!r} missing or out of order in LADDER"
            last_pos = pos + len(stage)


def test_bash_syntax_valid():
    """`bash -n` should pass on the script."""
    r = subprocess.run(
        ["/bin/bash", "-n", str(_SCRIPT)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
