"""Trainer heavy-job queue for the RESEARCH entrypoints — one module owns this.

`BL-20260815-RESEARCH-TRAINERS-BYPASS-THE-HEAVY-JOB-QUEUE`.
`docs/claude/trainer-resource-protocol.md` § Rule 1 is binding — every
memory-heavy job on the 6 GB trainer takes ONE shared blocking lock — and it had
**two** enforcement halves, neither of which reached this family:

  * the shell timer wrappers (`run_training_cycle.sh`, `trainer_run.sh`, …) take
    the flock explicitly;
  * `python -m ml train|build-dataset` is locked at the CLI entrypoint by
    `src/utils/trainer_heavy_lock.py`, wired in `ml/cli.py`.

A research script shelling out to another research script is neither, so the
protocol read as *enforced* while an entire family of ~5 GB jobs was exempt —
worse than a protocol known to be voluntary.

**WHY THE ENTRYPOINTS AND NOT THE CALLERS.** `train_exit_head.py` alone has five
in-repo callers (`m20_exit_head_round`, `m20_exit_head_denominator`,
`m20_fleet_exit_sweep`, `m21_entry_head_round`, `export_exit_head`) plus every
ad-hoc `trainer-vm-diag` relay that invokes it directly — and the direct
invocation is how most relays run it. Locking callers leaves that path open and
has to be re-done for the next caller; locking the entrypoint covers all of them
at once, and is the shape `ml/cli.py` already uses.

**NO DEADLOCK against a caller that also locks.** `acquire_heavy_lock` sets
`TRAINER_HEAVY_LOCK_HELD=1` in `os.environ` on acquisition and returns None
early when it is already set; the callers' subprocess helpers pass no `env=`, so
the child inherits the flag and skips. **Verified before shipping** — read out of
`src/utils/trainer_heavy_lock.py` (lines 162-163, 201) and out of
`m20_exit_head_round.py::sh`, which calls `subprocess.run` with no `env=` — not
assumed, because getting it wrong hangs a training run for the full hour of
`TRAINER_HEAVY_LOCK_WAIT_S` and looks exactly like a slow job.

**INERT EVERYWHERE BUT THE TRAINER.** `acquire_heavy_lock` gates on the trainer
role marker, so CI, dev boxes, the live VM and the web sandbox are a pure no-op.

**FAIL-OPEN.** Any *infrastructure* problem — including this module failing to
import the helper at all — proceeds WITHOUT locking. Training is a required
capability and must never be blocked by a bug in the lock plumbing. Only a
*clean* queue timeout refuses, exiting 75 (EX_TEMPFAIL), which is the queue
doing its job.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import IO, Optional

_REPO = Path(__file__).resolve().parents[2]

# The trainer's ONE canonical clone. `CLAUDE.md` pins it: "VM clone dirs stay
# /home/ubuntu/ict-trading-bot + /opt/ict-trading-bot — a GitHub repo rename does
# NOT move the on-disk clone." Every timer wrapper runs from here, so this is
# where the SHARED lock lives.
_CANONICAL_TRAINER_CLONE = Path("/home/ubuntu/ict-trading-bot")
_LOCK_REL = Path("runtime_logs") / "trainer" / ".heavy.lock"


def canonical_lock_file() -> Optional[Path]:
    """The SHARED lock path, or None when this checkout is already canonical.

    ⚠️ THE LOCK PATH IS CHECKOUT-RELATIVE, AND THAT IS A TRAP FOR A WORKTREE.
    `trainer_heavy_lock._lock_file()` resolves `<repo_root>/runtime_logs/trainer/
    .heavy.lock` from `parents[2]` of the RUNNING module. Run the same code from
    a git worktree and the "shared" mutex silently becomes a PRIVATE file — the
    job prints `{"status": "heavy_lock_acquired"}`, which is true, and serializes
    against nothing.

    MEASURED, not theorised (trainer-diag #9497, 2026-08-15, while the 5m screen
    was mid-arm from `/tmp/m20_screen_wt`): the worktree lock read **HELD**, the
    shared lock at the canonical clone read **FREE**, and a probe launched from
    the canonical clone **acquired immediately (rc=0)** — i.e. a training cycle
    or drift-retrain was free to start straight into the box beside it. That is
    the exact contention the queue exists to prevent, and it was introduced BY
    the worktree fix that solved a different problem (the 15-min code reset).

    So a non-canonical checkout points at the canonical clone's lock explicitly.
    Returns None when we are already canonical (no override needed) or when the
    canonical clone is absent (a dev box / CI — the caller then keeps the
    default, which is correct off-trainer since the lock is inert there anyway).
    """
    try:
        if _REPO == _CANONICAL_TRAINER_CLONE:
            return None
        target = _CANONICAL_TRAINER_CLONE / _LOCK_REL
        # Require the canonical clone to actually exist. Pointing at a path on a
        # machine that has no such clone would move the lock somewhere nothing
        # else looks — the same defect one directory over.
        return target if _CANONICAL_TRAINER_CLONE.is_dir() else None
    except OSError:
        return None


def take_heavy_queue(label: str) -> Optional[IO]:
    """Acquire the shared trainer heavy-job lock; return the handle or None.

    KEEP THE RETURN VALUE BOUND for the process lifetime — the flock releases
    when the fd closes, so letting it be garbage-collected silently unlocks
    while the job keeps running.
    """
    # Join the SHARED queue even when running from a worktree — see
    # canonical_lock_file(). An explicit caller-set override always wins, so a
    # test that pins its own lock file is unaffected.
    import os
    if not os.environ.get("TRAINER_HEAVY_LOCK_FILE"):
        shared = canonical_lock_file()
        if shared is not None:
            os.environ["TRAINER_HEAVY_LOCK_FILE"] = str(shared)
    # Resolve the repo root ourselves rather than relying on CWD. The callers
    # happen to `cd` to the repo root today, but the 5m screen already runs from
    # a git WORKTREE at a different path, and a lock that quietly stops being
    # taken because someone changed directory is the exact silent-regression
    # shape this row exists to close.
    if str(_REPO) not in sys.path:
        sys.path.insert(0, str(_REPO))
    try:
        from src.utils.trainer_heavy_lock import acquire_heavy_lock
    except ImportError:
        return None  # fail-open — see the module docstring
    return acquire_heavy_lock(label)
