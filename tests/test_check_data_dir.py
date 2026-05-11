"""Smoke tests for scripts/check_data_dir.sh.

The preflight script gates `ExecStartPre=` on every trader service that
binds to the OCI block-storage mount, so a regression that lets it
return 0 on a broken mount would mask exactly the failure the
externalization is meant to surface. These tests pin the contract:

  * exit 0 + creates subdirs on a healthy target
  * exit 1 on a missing target
  * exit 0 with mountpoint warning when target is a regular dir
  * exit-code-driven, not stdout-grep — so coloured output / locale
    differences on the live VM don't break the contract
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_data_dir.sh"
SUBDIRS = ("data", "runtime_logs", "runtime_state", "artifacts")


def _run(target: str, env: dict | None = None) -> subprocess.CompletedProcess:
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    return subprocess.run(
        ["bash", str(SCRIPT), target],
        capture_output=True,
        text=True,
        env=full_env,
        check=False,
    )


def test_script_is_executable_and_has_shebang():
    assert SCRIPT.exists(), f"missing: {SCRIPT}"
    assert os.access(SCRIPT, os.X_OK), f"not executable: {SCRIPT}"
    first = SCRIPT.read_text().splitlines()[0]
    assert first.startswith("#!"), f"missing shebang: {first!r}"


def test_healthy_target_passes_and_creates_subdirs(tmp_path):
    result = _run(str(tmp_path))
    assert result.returncode == 0, result.stderr or result.stdout
    for sub in SUBDIRS:
        assert (tmp_path / sub).is_dir(), f"missing subdir {sub}"


def test_missing_target_fails(tmp_path):
    missing = tmp_path / "does-not-exist"
    result = _run(str(missing))
    assert result.returncode == 1, (result.stdout, result.stderr)


def test_existing_subdirs_are_idempotent(tmp_path):
    for sub in SUBDIRS:
        (tmp_path / sub).mkdir()
    # Drop a sentinel into one of the existing subdirs; a re-run must
    # not touch it. The preflight is read-only on existing trees.
    sentinel = tmp_path / "runtime_logs" / "do-not-delete.txt"
    sentinel.write_text("preserved")

    result = _run(str(tmp_path))
    assert result.returncode == 0, result.stdout
    assert sentinel.read_text() == "preserved"


def test_explicit_arg_wins_over_env(tmp_path, monkeypatch):
    """Positional arg should override $DATA_DIR (per script convention)."""
    bogus = tmp_path / "from-env"
    monkeypatch.setenv("DATA_DIR", str(bogus))

    # bogus does not exist; the explicit arg (tmp_path) does. If the
    # script used $DATA_DIR despite the explicit arg, it would fail
    # on the missing-path check.
    result = _run(str(tmp_path))
    assert result.returncode == 0, result.stdout


def test_uses_data_dir_env_when_no_arg(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env={**os.environ, "DATA_DIR": str(tmp_path)},
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for sub in SUBDIRS:
        assert (tmp_path / sub).is_dir()


def test_mountpoint_warning_is_not_fatal(tmp_path):
    """A non-mountpoint regular dir should pass with a warning, not fail."""
    # tmp_path is a regular dir; the script's mountpoint check should
    # warn but still return 0.
    result = _run(str(tmp_path))
    assert result.returncode == 0
    assert "not a mountpoint" in result.stdout
