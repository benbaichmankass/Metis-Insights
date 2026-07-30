#!/usr/bin/env python3
"""Fail-closed guard for git-history questions on a shallow clone.

WHY THIS EXISTS (BL-20260730-SHALLOW-CLONE-DEFEATS-HISTORY-RULE)
----------------------------------------------------------------
`CLAUDE.md` § "Every session" and `docs/CLAUDE-RULES-CANONICAL.md` require, for any
Tier-2/3 file you are about to change:

    also read its recent history (`git log -p <file>`) so you don't undo a
    load-bearing, operator-approved decision.

On a **shallow clone** that check does not fail — it returns a *plausible but wrong*
answer. Every file reads as having a single commit, dated whenever the clone's
truncation point happens to be, with no warning of any kind. A session then concludes
"this file has no meaningful history" and proceeds, which is precisely the state the
rule exists to prevent.

This is the repo's own recurring bug class — an answer that is true relative to a scope
that is silently wrong — applied to the tooling that is supposed to prevent it. It was
found on 2026-07-30 when `git log -S` on every config file dead-ended at one whole-file
commit three days old; the session clone was 57 commits deep. Deepening to 2718 commits
immediately overturned a research conclusion: `trend_donchian`'s six live regime cells
turned out to predate the exit head that now drives their exits by 2-6 weeks
(`docs/research/regime-debt-matrix-corrected-cost-2026-07-30.md` §A6).

THE CONTRACT
------------
Answer a history question, or refuse — never answer it wrongly. `require_full_history()`
raises on a shallow clone; `file_history()` refuses rather than returning a truncated
log. Callers that genuinely tolerate truncation must pass `allow_shallow=True`, which
makes the tolerance explicit at the call site instead of implicit everywhere.

Stdlib-only and side-effect-free on import, so it is safe to call from a hook, a CI step,
or a session script.

Usage:
  python scripts/ops/git_history_check.py                       # exit 0 full / 1 shallow
  python scripts/ops/git_history_check.py --file config/strategies.yaml -n 5
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The remedy, in one place so the hook, the CLI and the exception text cannot drift.
DEEPEN_HINT = (
    "git fetch --deepen=2000 origin   # or: git fetch --unshallow (larger)"
)


class ShallowCloneError(RuntimeError):
    """Raised when a history question is asked of a clone that cannot answer it."""


def _git(args: list[str], cwd: str | None = None) -> tuple[int, str]:
    proc = subprocess.run(
        ["git", *args], cwd=cwd or REPO,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return proc.returncode, proc.stdout


def is_shallow(cwd: str | None = None) -> bool:
    """True when this clone's history is truncated.

    Prefers `git rev-parse --is-shallow-repository` (authoritative, and correct for
    worktrees / alternate git-dir layouts where `.git` is a file, not a directory).
    Falls back to probing for the `shallow` marker only if that plumbing is
    unavailable — an ancient git predating the flag.
    """
    rc, out = _git(["rev-parse", "--is-shallow-repository"], cwd)
    if rc == 0:
        return out.strip() == "true"
    rc, out = _git(["rev-parse", "--git-dir"], cwd)
    if rc != 0:
        # Not a git repo at all. That is not a *shallow* clone, and claiming it is
        # would send a caller chasing a fetch that cannot help.
        return False
    git_dir = out.strip()
    if not os.path.isabs(git_dir):
        git_dir = os.path.join(cwd or REPO, git_dir)
    return os.path.exists(os.path.join(git_dir, "shallow"))


def history_depth(cwd: str | None = None) -> int | None:
    """Reachable commit count on HEAD, or None if it cannot be determined."""
    rc, out = _git(["rev-list", "--count", "HEAD"], cwd)
    if rc != 0:
        return None
    try:
        return int(out.strip())
    except ValueError:
        return None


def earliest_commit_date(cwd: str | None = None) -> str | None:
    """Date of the oldest reachable commit — i.e. where history is truncated."""
    rc, out = _git(["log", "--reverse", "--format=%ad", "--date=short"], cwd)
    if rc != 0 or not out.strip():
        return None
    return out.strip().splitlines()[0]


# Below this reachable-commit count, a clone is so truncated that essentially every
# file reads as having ~one commit — the pathological case that produced the incident.
# Above it the clone is still truncated (anything past the cut-off is invisible) but a
# per-file log is usually informative. The distinction matters because a warning that
# describes a depth-1 clone while looking at a depth-2700 one is itself an overclaim,
# and an overclaiming guard gets ignored — which is how alarms become background noise.
SEVERELY_SHALLOW_COMMITS = 500


def shallow_warning(cwd: str | None = None) -> str | None:
    """A ready-to-print warning, or None when the clone is fine.

    Severity is scaled to the actual depth: the message must not describe a
    catastrophically-truncated clone when the clone is merely bounded.
    """
    if not is_shallow(cwd):
        return None
    depth = history_depth(cwd)
    earliest = earliest_commit_date(cwd)
    where = (f"{depth} commits" if depth is not None else "depth unknown")
    if earliest:
        where += f", oldest {earliest}"
    severe = depth is None or depth < SEVERELY_SHALLOW_COMMITS

    if severe:
        risk = (
            "  `git log -p <file>` WILL RETURN A PLAUSIBLE BUT WRONG ANSWER here:\n"
            "  files read as having ~one commit, with no error of any kind.\n")
    else:
        risk = (
            "  A per-file `git log` is informative at this depth, but anything before\n"
            "  the cut-off is still INVISIBLE and returns no error — so 'no history\n"
            "  found' remains indistinguishable from 'truncated away'. Treat an\n"
            "  apparently-empty result for an older decision as UNKNOWN, not absent.\n")

    return (
        f"SHALLOW CLONE — git history is TRUNCATED ({where}).\n"
        + risk
        + "  CLAUDE.md requires reading a Tier-2/3 file's history before changing it\n"
        "  so you don't undo a load-bearing, operator-approved decision.\n"
        f"  Fix: {DEEPEN_HINT}\n"
        "  Ref: BL-20260730-SHALLOW-CLONE-DEFEATS-HISTORY-RULE"
    )


def require_full_history(cwd: str | None = None) -> None:
    """Raise ShallowCloneError when the clone cannot answer a history question."""
    warning = shallow_warning(cwd)
    if warning:
        raise ShallowCloneError(warning)


def file_history(path: str, n: int = 10, cwd: str | None = None,
                 allow_shallow: bool = False, patch: bool = False) -> str:
    """`git log` for one path — refusing rather than truncating.

    `allow_shallow=True` opts into a possibly-truncated answer; it exists so the
    tolerance is visible at the call site rather than assumed everywhere.
    """
    if not allow_shallow:
        require_full_history(cwd)
    args = ["log", f"-{int(n)}", "--format=%h %ad %s", "--date=short"]
    if patch:
        args.append("-p")
    rc, out = _git([*args, "--", path], cwd)
    if rc != 0:
        raise RuntimeError(f"git log failed for {path!r}: {out.strip()[-300:]}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--file", help="show this path's history (refuses if shallow)")
    ap.add_argument("-n", type=int, default=10)
    ap.add_argument("--patch", action="store_true")
    ap.add_argument("--allow-shallow", action="store_true",
                    help="opt into a possibly-truncated answer")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    warning = shallow_warning()
    if warning and not args.quiet:
        print(warning, file=sys.stderr)

    if args.file:
        try:
            print(file_history(args.file, args.n,
                               allow_shallow=args.allow_shallow, patch=args.patch))
        except ShallowCloneError:
            # Already printed above; the non-zero exit IS the refusal.
            return 1
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        return 0

    if warning:
        return 1
    if not args.quiet:
        depth = history_depth()
        print(f"OK — full history ({depth} commits reachable).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
