"""Tests for the enforced trainer heavy-job queue (src/utils/trainer_heavy_lock).

BL-20260717-TRAINER-QUEUE-ENFORCE: the `ml train`/`build-dataset` CLI must
acquire the shared lock so a BARE invocation can't bypass the queue — but ONLY
on the trainer VM, and never blocking CI / dev / live.
"""
import os
import subprocess
import sys

import pytest

from src.utils import trainer_heavy_lock as T

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def clean_env(monkeypatch):
    for k in ("TRAINER_HEAVY_LOCK_HELD", "TRAINER_HEAVY_LOCK_DISABLED",
              "TRAINER_HEAVY_LOCK_FORCE", "TRAINER_HEAVY_LOCK_FILE",
              "TRAINER_HEAVY_LOCK_WAIT_S"):
        monkeypatch.delenv(k, raising=False)
    yield monkeypatch


def test_no_op_off_trainer(clean_env):
    # No role marker in the test env → not the trainer → pure no-op.
    assert T.on_trainer_vm() is False
    assert T.acquire_heavy_lock("x") is None


def test_no_op_when_parent_holds(clean_env):
    clean_env.setenv("TRAINER_HEAVY_LOCK_FORCE", "1")   # pretend on trainer
    clean_env.setenv("TRAINER_HEAVY_LOCK_HELD", "1")    # wrapper already holds it
    assert T.acquire_heavy_lock("x") is None            # re-entrant skip


def test_no_op_when_disabled(clean_env):
    clean_env.setenv("TRAINER_HEAVY_LOCK_FORCE", "1")
    clean_env.setenv("TRAINER_HEAVY_LOCK_DISABLED", "1")
    assert T.acquire_heavy_lock("x") is None


def test_acquire_writes_holder_and_sets_held(clean_env, tmp_path):
    lock = tmp_path / ".heavy.lock"
    clean_env.setenv("TRAINER_HEAVY_LOCK_FORCE", "1")
    clean_env.setenv("TRAINER_HEAVY_LOCK_FILE", str(lock))
    fh = T.acquire_heavy_lock("job-A")
    try:
        assert fh is not None
        assert os.environ.get("TRAINER_HEAVY_LOCK_HELD") == "1"
        holder = T.read_holder()
        assert holder and holder["label"] == "job-A" and holder["pid"] == os.getpid()
        # HELD now set → a second acquire in-process is a re-entrant skip.
        assert T.acquire_heavy_lock("job-A2") is None
    finally:
        fh.close()


def test_contended_bare_invocation_times_out(clean_env, tmp_path):
    """A held lock + a separate bare invocation → clean timeout, exit 75."""
    lock = tmp_path / ".heavy.lock"
    clean_env.setenv("TRAINER_HEAVY_LOCK_FORCE", "1")
    clean_env.setenv("TRAINER_HEAVY_LOCK_FILE", str(lock))
    holder_fh = T.acquire_heavy_lock("holder")
    assert holder_fh is not None
    try:
        code = (
            "import os,sys; sys.path.insert(0, sys.argv[1]);"
            "os.environ.update(TRAINER_HEAVY_LOCK_FORCE='1',"
            " TRAINER_HEAVY_LOCK_FILE=sys.argv[2], TRAINER_HEAVY_LOCK_WAIT_S='1');"
            "os.environ.pop('TRAINER_HEAVY_LOCK_HELD', None);"
            "from src.utils import trainer_heavy_lock as T;"
            "T.acquire_heavy_lock('contender')"
        )
        r = subprocess.run(
            [sys.executable, "-c", code, _REPO, str(lock)],
            capture_output=True, text=True,
        )
        assert r.returncode == 75, r.stderr
        assert "heavy_lock_timeout" in r.stderr
    finally:
        holder_fh.close()


def test_cli_imports_real_helper():
    """ml.cli wires the real helper (not the defensive empty fallback)."""
    from ml.cli import _HEAVY_COMMANDS
    assert _HEAVY_COMMANDS == frozenset({"train", "build-dataset"})
