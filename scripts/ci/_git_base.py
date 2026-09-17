"""Resolve a `--base` ref to the FORK POINT, and read a file there honestly.

TWO DEFECTS THIS EXISTS TO STOP, both measured in this repo on 2026-09-12.

1. **READING AT THE TIP.** A guard asks *"did THIS DIFF change X?"* and answers
   it by comparing the branch against `origin/main` **as it is now**. That is
   only the diff's doing if the comparison is against the branch's ANCESTOR.
   Read at the tip it also differs when the base moved AHEAD — the normal state
   of every working branch — so the guard blames the author for the base's
   changes.

   ⚠️ **THIS PARAGRAPH USED TO FREEZE A COUNT, AND EVERY TERM OF IT WENT STALE
   IN A DAY.** It read: *"MEASURED across the repo: of 10 scripts that take
   `--base` and read file content there, 7 read the tip and 3 resolve the merge
   base."* Re-measured 2026-09-13 the population is **12** and the split is
   **7 resolving, 5 reading the tip** — nothing regressed; four fixes landed and
   the sentence could not know. The old figure also came from a probe that
   required a literal `git show`, so it missed every caller that DELEGATES its
   read to `read_at` below — including the ones most recently fixed to do
   exactly that. Re-derive it rather than retyping it:

       python3 scripts/ops/base_resolution_census.py

   ⚠️ **AND THAT CENSUS COUNTS A PATTERN, NOT DEFECTS.** Reading at the tip
   contaminates a CONTENT or COUNT comparison outright — measured, an honest
   one-row append reported *"421 base line(s) lost"* against 0 at the fork
   point. It is **benign** for the `if rid in base_ids: continue` id
   set-difference shape: measured 2026-09-13 on
   `check_claim_basis.check_new_rows`, the fork point and the tip returned
   identical findings, because ids the base GAINS are ids the branch does not
   have. It breaks that shape in one narrow case — when the base REMOVES a row
   the branch still carries, a tip read grades that row as NEW (measured: 2
   findings against 1). Ask what is being compared before calling a tip read a
   bug.

2. **AN UNREADABLE BASE READING AS AN EMPTY ONE.** `git show <ref>:<path>`
   exits non-zero for *"the path is not in that ref"* (a genuinely new file —
   nothing to regress from) AND for *"that ref does not exist"* (we could not
   look). Collapsing them makes a guard go **silent** precisely when its input
   is broken, which is the direction nobody notices.

⚠️ **THE FALLBACK IS TO THE TIP, NEVER TO SKIPPING.** An unresolvable merge base
returns the ref unchanged, i.e. today's behaviour, so this can only ever remove
a false FAILURE — never introduce a false pass.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

MERGE_BASE, TIP_UNRESOLVABLE = "merge_base", "tip_unresolvable"
#: Three states for a base read, never collapsed.
READ, ABSENT_AT_BASE, UNREADABLE = "read", "absent_at_base", "unreadable"


def _git(repo: Path | None, *args: str) -> tuple[int, str]:
    try:
        out = subprocess.run(["git", *args], cwd=repo, capture_output=True,
                             text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return out.returncode, out.stdout


def resolve_base(ref: str, *, repo: Path | None = None,
                 head: str = "HEAD") -> tuple[str, str]:
    """`(ref_to_read, state)` — the fork point where one can be computed."""
    if not ref:
        return ref, TIP_UNRESOLVABLE
    rc, out = _git(repo, "merge-base", head, ref)
    sha = out.strip()
    if rc != 0 or not sha:
        return ref, TIP_UNRESOLVABLE
    return sha, MERGE_BASE


def ref_exists(ref: str, *, repo: Path | None = None) -> bool:
    rc, _ = _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    return rc == 0


def read_at(ref: str, rel: str, *, repo: Path | None = None) -> tuple[str, str | None]:
    """`(state, text)`.

    ⚠️ The ref is checked SEPARATELY from the path, because `git show` reports
    both failures the same way and they mean opposite things: a missing path is
    a new file (skip it), a missing ref is *we could not look* (say so).
    """
    if not ref_exists(ref, repo=repo):
        return UNREADABLE, None
    rc, out = _git(repo, "show", f"{ref}:{rel}")
    if rc != 0:
        return ABSENT_AT_BASE, None
    return READ, out
