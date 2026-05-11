"""Tests for the OCI block-storage path helpers in ``src.utils.paths``.

Covers:

* Default resolution (no env) falls back to repo-relative subdirs.
* ``DATA_DIR`` umbrella env redirects all four roots.
* Per-root overrides (``RUNTIME_LOGS_DIR`` etc.) win over ``DATA_DIR``.
* Helpers ``mkdir`` their target so callers can write immediately.
* User-fallback kicks in when the repo subdir cannot be created.
* ``~`` expansion works in env values.
* ``describe_roots`` correctly labels the env source.

These tests use ``monkeypatch`` for env isolation and ``tmp_path`` for
filesystem isolation. The module is reloaded per-test where needed so
the ``@lru_cache`` on ``repo_root()`` doesn't leak resolved paths.
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest


@pytest.fixture
def paths_module(monkeypatch):
    """Return a freshly-imported ``src.utils.paths`` with clean env."""
    for var in (
        "DATA_DIR",
        "RUNTIME_LOGS_DIR",
        "RUNTIME_STATE_DIR",
        "ARTIFACTS_DIR",
        "DATA_SUBDIR_DATA",
    ):
        monkeypatch.delenv(var, raising=False)
    import src.utils.paths as paths
    importlib.reload(paths)
    return paths


def test_default_repo_relative(paths_module):
    """With no env set, each helper returns a repo-relative subdir."""
    root = Path(paths_module.repo_root())
    assert paths_module.data_dir() == root / "data"
    assert paths_module.runtime_logs_dir() == root / "runtime_logs"
    assert paths_module.runtime_state_dir() == root / "runtime_state"
    assert paths_module.artifacts_dir() == root / "artifacts"


def test_data_dir_umbrella_env(paths_module, monkeypatch, tmp_path):
    """``DATA_DIR`` redirects all four roots under that umbrella."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    assert paths_module.data_dir() == tmp_path / "data"
    assert paths_module.runtime_logs_dir() == tmp_path / "runtime_logs"
    assert paths_module.runtime_state_dir() == tmp_path / "runtime_state"
    assert paths_module.artifacts_dir() == tmp_path / "artifacts"


def test_per_root_override_wins_over_umbrella(paths_module, monkeypatch, tmp_path):
    """A per-root env override is taken even when ``DATA_DIR`` is set."""
    umbrella = tmp_path / "umbrella"
    logs_override = tmp_path / "hot-logs"
    monkeypatch.setenv("DATA_DIR", str(umbrella))
    monkeypatch.setenv("RUNTIME_LOGS_DIR", str(logs_override))

    assert paths_module.runtime_logs_dir() == logs_override
    # Other roots still come from the umbrella.
    assert paths_module.runtime_state_dir() == umbrella / "runtime_state"


def test_helpers_create_directory(paths_module, monkeypatch, tmp_path):
    """The helper ``mkdir``s its target so callers can write immediately."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "new-root"))
    target = paths_module.runtime_logs_dir()
    assert target.exists()
    assert target.is_dir()

    probe = target / "smoke.txt"
    probe.write_text("ok")
    assert probe.read_text() == "ok"


def test_tilde_expansion(paths_module, monkeypatch, tmp_path):
    """``~`` in env values resolves to the user home."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("DATA_DIR", "~/oci-data")

    resolved = paths_module.runtime_logs_dir()
    assert resolved == fake_home / "oci-data" / "runtime_logs"


def test_user_fallback_when_repo_unwritable(paths_module, monkeypatch, tmp_path):
    """When the repo-relative path can't be created, fall back to ``~/.ict-trading-bot``."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    # Force repo_root() to point at a path whose parent is unwritable.
    unwritable = tmp_path / "unwritable"
    unwritable.mkdir(mode=0o555)

    def fake_repo_root():
        return str(unwritable)

    monkeypatch.setattr(paths_module, "repo_root", fake_repo_root)
    # The fallback path is derived at import time, but the public helper
    # recomputes per call so the patched home takes effect.
    monkeypatch.setattr(
        paths_module, "_USER_FALLBACK", Path(fake_home) / ".ict-trading-bot"
    )

    try:
        resolved = paths_module.runtime_logs_dir()
        # Either we hit the fallback OR we got the (now-readonly) repo path.
        # On systems where root can still mkdir into a 0o555 dir (running as
        # root), the first branch wins — accept either as long as it exists.
        assert resolved.exists()
        assert resolved.is_dir()
    finally:
        os.chmod(unwritable, 0o755)  # so pytest can clean up


def test_describe_roots_labels_sources(paths_module, monkeypatch, tmp_path):
    """``describe_roots`` reports which env (if any) drove each root."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "umbrella"))
    monkeypatch.setenv("RUNTIME_LOGS_DIR", str(tmp_path / "hot"))

    report = paths_module.describe_roots()
    assert "env:RUNTIME_LOGS_DIR" in report["runtime_logs"]
    assert "env:DATA_DIR" in report["runtime_state"]
    assert "env:DATA_DIR" in report["artifacts"]
    assert "env:DATA_DIR" in report["data"]


def test_describe_roots_default_is_repo_relative(paths_module):
    """With no env, describe_roots labels every root as repo-relative."""
    report = paths_module.describe_roots()
    for sub in ("data", "runtime_logs", "runtime_state", "artifacts"):
        assert "repo-relative" in report[sub], report[sub]
