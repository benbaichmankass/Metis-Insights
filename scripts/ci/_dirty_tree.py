"""Say so when a guard's verdict is about a DIFFERENT tree than the one you edited.

THE DEFECT, MEASURED ON ITS AUTHOR
----------------------------------
Every `--base` guard here grades a COMMIT RANGE. Run one with uncommitted work in
the tree and it reads the committed version, prints a confident verdict, and says
nothing about the file you just changed. The remedy for that shipped on
2026-09-13 into `scripts/ci/run_guards.py` -- and only there.

A session chasing one CI failure does not type the 15-minute orchestrator. It
types the single guard the failure named, because that is four minutes. So the
notice reached the run nobody makes and missed the run everybody makes:
`BL-20260917-THE-DIRTY-TREE-NOTICE-LIVES-ONLY-IN-RUN-GUARDS-SO-ALL-18-DIRECTLY-INVOCABLE-DIFF-SCOPED-GUARDS-STILL-GRADE-THE-WRONG-TREE-SILENTLY`
records FOUR instances in one day, all by an author who had read the instruction
to commit first.

⚠️ IT IS A NOTICE AND NOTHING ELSE. No stashing, no committing, no grading the
worktree instead, and **no change to any exit code**. The guards' committed-state
reading is what CI does; changing it would make the local run disagree with CI,
which is worse than the trap. A dirty tree is not a guard failure -- it is a
verdict about a different tree than the one you are looking at.

⚠️ AND IT IS SILENT ON A CLEAN TREE, deliberately. A notice that fires every run
is walked past, which this repo calls its own P1.

THREE STATES, NEVER COLLAPSED
-----------------------------
``clean``           git answered and the tree matches HEAD. Silent.
``dirty``           git answered and named paths. The notice prints.
``could_not_look``  git did not answer -- not a repo, git missing, a failed
                    call. **NOT silence**, because "we could not establish
                    whether your tree is clean" is exactly the collapse this
                    module exists to stop. It prints a one-liner and, like every
                    other state, changes no exit code.
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import List, Optional, TextIO, Tuple

CLEAN = "clean"
DIRTY = "dirty"
COULD_NOT_LOOK = "could_not_look"


def uncommitted(repo: Optional[str] = None) -> Tuple[str, List[str]]:
    """``(state, paths)``. ``paths`` is empty unless the state is ``dirty``.

    Untracked files are INCLUDED: a guard that reads `git show <base>:<path>`
    cannot see a file you have created either, and that is the same trap.
    """
    cmd = ["git"]
    if repo:
        cmd += ["-C", repo]
    cmd += ["status", "--porcelain"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except OSError:
        return COULD_NOT_LOOK, []
    if proc.returncode != 0:
        return COULD_NOT_LOOK, []
    paths = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        # porcelain v1: XY<space>path, and a rename carries "old -> new".
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.append(path.strip('"'))
    return (DIRTY, paths) if paths else (CLEAN, [])


def notice_lines(state: str, paths: List[str], *, limit: int = 12) -> List[str]:
    """The notice, as lines. PURE, so the wording is testable without a repo."""
    if state == CLEAN:
        return []
    if state == COULD_NOT_LOOK:
        return ["⚠️  could not read the working tree, so whether this verdict "
                "matches your edits is UNKNOWN — not confirmed clean."]
    out = [f"⚠️  UNCOMMITTED WORK ({len(paths)} path(s)) — this guard graded a "
           f"COMMIT RANGE, so it did NOT read your working tree:"]
    for p in paths[:limit]:
        out.append(f"      - {p}")
    if len(paths) > limit:
        out.append(f"      … and {len(paths) - limit} more")
    out.append("    Commit them and re-run. The verdict above is about the "
               "COMMITTED tree; it is not a clean bill for your change.")
    return out


def warn(repo: Optional[str] = None, *, stream: Optional[TextIO] = None) -> str:
    """Print the notice if there is one. Returns the state; never exits.

    ⚠️ Writes to **stderr**, so it can never contaminate a guard's stdout --
    several of these are parsed by their callers.

    ⚠️ Set ``DIRTY_TREE_NOTICE=0`` to silence it. That exists for a caller that
    already prints its own (``run_guards.py``), NOT as a way to make the
    inconvenience go away, and it is deliberately not the default in either
    direction: an unset variable leaves the notice ON.
    """
    if os.environ.get("DIRTY_TREE_NOTICE", "").strip() in ("0", "false", "no", "off"):
        return "suppressed"
    state, paths = uncommitted(repo)
    lines = notice_lines(state, paths)
    if lines:
        print("\n".join(lines), file=stream if stream is not None else sys.stderr)
    return state
