"""sync-vm-secrets.yml: every declared secret name is actually delivered.

The workflow iterates REQUIRED_SECRETS / OPTIONAL_SECRETS with `${!name}`
indirect lookups, so a name in the list with no `NAME: ${{ secrets.NAME }}`
line in the step's env block reads as EMPTY — for an optional secret that is
silently "absent, skipped", never an error. This pins the lists and the env
blocks together. Static: parses YAML, runs nothing.
"""
from __future__ import annotations

from pathlib import Path

import yaml

WF = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "sync-vm-secrets.yml"


def _job():
    return yaml.safe_load(WF.read_text())["jobs"]["sync-secrets"]


def _step(name_prefix: str) -> dict:
    steps = [s for s in _job()["steps"] if str(s.get("name", "")).startswith(name_prefix)]
    assert len(steps) == 1, name_prefix
    return steps[0]


def _names(key: str) -> list:
    return _job()["env"][key].split()


def test_every_optional_secret_is_in_the_report_and_sync_env_blocks():
    for step in ("Report OPTIONAL secrets presence", "Sync secrets to VM"):
        env = _step(step).get("env") or {}
        missing = [n for n in _names("OPTIONAL_SECRETS") if n not in env]
        assert not missing, f"{step!r} env block lacks {missing}"


def test_every_required_secret_is_in_the_verify_and_sync_env_blocks():
    for step in ("Verify REQUIRED secrets are set", "Sync secrets to VM"):
        env = _step(step).get("env") or {}
        missing = [n for n in _names("REQUIRED_SECRETS") if n not in env]
        assert not missing, f"{step!r} env block lacks {missing}"


def test_env_lines_map_each_name_to_its_own_secret():
    for step in ("Report OPTIONAL secrets presence", "Sync secrets to VM"):
        for name, val in (_step(step).get("env") or {}).items():
            if str(val).startswith("${{ secrets."):
                assert val == "${{ secrets.%s }}" % name, (step, name, val)


def test_breakout_dx_credentials_are_optional_not_required():
    assert {"BREAKOUT_DX_USERNAME", "BREAKOUT_DX_PASSWORD"} <= set(_names("OPTIONAL_SECRETS"))
    assert not {"BREAKOUT_DX_USERNAME", "BREAKOUT_DX_PASSWORD"} & set(_names("REQUIRED_SECRETS"))
