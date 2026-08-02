"""Tests for scripts/ops/git_sync_preflight.sh — the destructive-git-sync guard
(BL-20260730-DESTRUCTIVE-GIT-SYNC-NO-GUARD).

The helper must REFUSE (exit 2) a sync to a ref that would discard commits the
current HEAD carries, and pass (exit 0) when the sync discards nothing or when
--allow-discard is given.
"""
from __future__ import annotations

import os
import shutil
import subprocess

import pytest

_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "scripts", "ops", "git_sync_preflight.sh",
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _run(cwd, *args):
    return subprocess.run(["bash", _SCRIPT, *args], cwd=cwd, capture_output=True, text=True)


def _repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "f").write_text("a\n")
    _git(r, "add", "f")
    _git(r, "commit", "-qm", "base")
    return r


def test_refuses_when_head_has_unmerged_commits(tmp_path):
    r = _repo(tmp_path)
    _git(r, "branch", "target")           # target == base
    (r / "f").write_text("b\n")
    _git(r, "commit", "-qam", "unmerged work")  # HEAD ahead of target
    res = _run(r, "target")
    assert res.returncode == 2, res.stderr
    assert "REFUSED" in res.stderr
    assert "unmerged work" in res.stderr


def test_allow_discard_passes(tmp_path):
    r = _repo(tmp_path)
    _git(r, "branch", "target")
    (r / "f").write_text("b\n")
    _git(r, "commit", "-qam", "unmerged work")
    res = _run(r, "target", "--allow-discard")
    assert res.returncode == 0, res.stderr
    assert "allow-discard" in res.stdout


def test_passes_when_nothing_to_discard(tmp_path):
    r = _repo(tmp_path)
    _git(r, "branch", "target")           # target == HEAD, nothing ahead
    res = _run(r, "target")
    assert res.returncode == 0, res.stderr
    assert "OK" in res.stdout


def test_unresolvable_ref_errors(tmp_path):
    r = _repo(tmp_path)
    res = _run(r, "origin/does-not-exist")
    assert res.returncode == 65, res.stdout + res.stderr
