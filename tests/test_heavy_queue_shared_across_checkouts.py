"""A heavy job run from a WORKTREE must join the same queue as the main clone.

`BL-20260815-WORKTREE-RUN-TAKES-A-PRIVATE-HEAVY-LOCK`.

`trainer_heavy_lock._lock_file()` resolves `<repo_root>/runtime_logs/trainer/
.heavy.lock` from `parents[2]` of the RUNNING module. That is correct for the
canonical clone and a **trap** for any other checkout: run the same code from a
git worktree and the "shared" mutex becomes a PRIVATE file. The job prints
`{"status": "heavy_lock_acquired"}` — which is TRUE — and serializes against
nothing.

MEASURED on the trainer (trainer-diag #9497, 2026-08-15) while the 5m dispersion
screen was mid-arm from `/tmp/m20_screen_wt`:

  * `/tmp/m20_screen_wt/runtime_logs/trainer/.heavy.lock` — **HELD**
  * `/home/ubuntu/ict-trading-bot/runtime_logs/trainer/.heavy.lock` — **FREE**
  * a probe launched from the canonical clone **acquired immediately, rc=0**

So a training cycle or drift-retrain was free to start straight into the 6 GB box
beside a running screen — the exact contention the queue exists to prevent, and
introduced BY the worktree fix that solved a different problem (the lock-free
15-min code reset). Two correct-looking fixes, and their interaction was the bug.

⚠️ THE LOCK MESSAGE IS NOT EVIDENCE THE QUEUE WAS JOINED. That is what made this
survive review: every log line said the right thing. The only check that
distinguishes them is whether a SECOND process, resolving its own repo root
differently, is actually blocked — which is what this module tests, with real
processes and a real flock rather than by asserting on a path string.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HELPER = REPO / "scripts" / "ml" / "_heavy_queue.py"


def _load():
    spec = importlib.util.spec_from_file_location("_heavy_queue_t", HELPER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_heavy_queue_t"] = mod
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------
# Positive control first — a negative needs a denominator.
# --------------------------------------------------------------------------

def test_the_helper_exposes_the_resolution_at_all() -> None:
    mod = _load()
    assert hasattr(mod, "canonical_lock_file"), (
        "the shared-lock resolution is gone; a worktree run would silently take "
        "a private lock again")
    assert hasattr(mod, "take_heavy_queue")


def test_a_non_canonical_checkout_is_pointed_at_the_canonical_lock(tmp_path) -> None:
    mod = _load()
    canon = tmp_path / "canonical"
    (canon / "runtime_logs" / "trainer").mkdir(parents=True)
    mod._CANONICAL_TRAINER_CLONE = canon
    mod._REPO = tmp_path / "some_worktree"

    got = mod.canonical_lock_file()
    assert got == canon / "runtime_logs" / "trainer" / ".heavy.lock", got


def test_the_canonical_checkout_gets_NO_override(tmp_path) -> None:
    """Returning a path here would be harmless but dishonest — and it would mask
    a future change to the default by pinning it from two places."""
    mod = _load()
    canon = tmp_path / "canonical"
    (canon / "runtime_logs" / "trainer").mkdir(parents=True)
    mod._CANONICAL_TRAINER_CLONE = canon
    mod._REPO = canon
    assert mod.canonical_lock_file() is None


def test_a_machine_with_no_canonical_clone_is_left_alone(tmp_path) -> None:
    """Off-trainer (CI, dev, sandbox) there is no clone to point at.

    Pointing at a path that does not exist would move the lock somewhere nothing
    else looks — the same defect one directory over.
    """
    mod = _load()
    mod._CANONICAL_TRAINER_CLONE = tmp_path / "definitely-not-here"
    mod._REPO = tmp_path / "some_worktree"
    assert mod.canonical_lock_file() is None


# --------------------------------------------------------------------------
# The property that was silently false: two checkouts, one queue.
# --------------------------------------------------------------------------

_CHILD = """
import sys, os
sys.path.insert(0, {helper_dir!r})
import _heavy_queue as q
from pathlib import Path
q._CANONICAL_TRAINER_CLONE = Path({canon!r})
q._REPO = Path({repo!r})
h = q.take_heavy_queue("child")
print("ACQUIRED" if h is not None else "SKIPPED")
"""


def _run_child(canon: Path, repo: Path, env_extra: dict) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update(env_extra)
    # Force the lock live off-trainer, and never inherit a parent's HELD flag
    # (that would make the child skip and the test pass vacuously).
    env["TRAINER_HEAVY_LOCK_FORCE"] = "1"
    env.pop("TRAINER_HEAVY_LOCK_HELD", None)
    env.pop("TRAINER_HEAVY_LOCK_FILE", None)
    code = _CHILD.format(helper_dir=str(HELPER.parent), canon=str(canon), repo=str(repo))
    return subprocess.run([sys.executable, "-c", textwrap.dedent(code)],
                          capture_output=True, text=True, env=env, timeout=120)


def test_two_DIFFERENT_checkouts_actually_serialize(tmp_path) -> None:
    """The real assertion, with real processes and a real flock.

    A path-string comparison would have passed against the broken version too —
    the broken version resolved a perfectly valid path, just not a shared one.
    """
    canon = tmp_path / "canonical"
    (canon / "runtime_logs" / "trainer").mkdir(parents=True)
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    lock = canon / "runtime_logs" / "trainer" / ".heavy.lock"

    # Hold the CANONICAL lock in this process, the way a training cycle would.
    import fcntl
    holder = open(lock, "w")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        # A child running from the WORKTREE must be blocked by it.
        p = _run_child(canon, worktree, {"TRAINER_HEAVY_LOCK_WAIT_S": "2"})
        assert p.returncode == 75, (
            "a worktree run was NOT blocked by a lock held on the canonical "
            f"clone — it is taking a private lock again. rc={p.returncode} "
            f"stdout={p.stdout!r} stderr={p.stderr[-300:]!r}")
        assert "ACQUIRED" not in p.stdout, p.stdout
    finally:
        holder.close()

    # Control: with the lock released, the same child MUST acquire — otherwise
    # the assertion above would pass for the wrong reason (e.g. the child
    # erroring out) and this module would be worthless.
    p2 = _run_child(canon, worktree, {"TRAINER_HEAVY_LOCK_WAIT_S": "2"})
    assert p2.returncode == 0 and "ACQUIRED" in p2.stdout, (
        f"the control failed: a worktree run cannot acquire even when the "
        f"canonical lock is FREE. rc={p2.returncode} stdout={p2.stdout!r} "
        f"stderr={p2.stderr[-300:]!r}")
