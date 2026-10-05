#!/usr/bin/env python3
"""Decide whether a `push` to the M20 sweep sentinel should run the sweep.

PI-20261003-ILZMCTFQ-0001. `m20-exit-lever-sweep.yml` fires on a push to
`claude/**` touching `.github/exit-lever-sweep-request`, and its corpus job
commits to the TRIGGERING branch. A branch that merely merges `main` picks up
the sentinel change from main, fires the sweep, and receives a bot commit
rewriting `docs/research/m20-sweep-corpus.jsonl`. A bot-authored head commit
leaves every CI workflow at `action_required`, stalling the PR (go-live PR
#14673, run 37138828225; #15603).

The sentinel arrived by merge exactly when the branch's copy is byte-identical
to the default branch's. A lane that edits it on its own branch differs from
main, so that remains the intended trigger. Content (blob id) is compared, not
the diff range, so a merge commit cannot smuggle an inherited edit through.

Only `push` is gated; `workflow_dispatch` always proceeds.
Usage: m20_sweep_sentinel_gate.py <event_name> <head_blob> <default_blob>
(blob = `git rev-parse <ref>:<path>`; empty string = path absent).
Prints `proceed=true|false` and a reason line on stderr.
"""
from __future__ import annotations

import sys


def decide(event: str, head_blob: str, default_blob: str) -> tuple[bool, str]:
    if event != "push":
        return True, f"event={event}: not a push, always proceeds"
    if not head_blob:
        return False, "sentinel absent on the pushed branch"
    if not default_blob:
        return True, "sentinel absent on default branch: this branch introduced it"
    if head_blob == default_blob:
        return False, ("sentinel identical to the default branch's: it arrived "
                       "by merge, not by an edit on this branch")
    return True, "sentinel differs from the default branch: edited on this branch"


def main(argv: list[str]) -> int:
    event, head_blob, default_blob = (list(argv[1:4]) + ["", "", ""])[:3]
    ok, why = decide(event, head_blob, default_blob)
    print(f"::notice::m20 sweep sentinel gate: {why}", file=sys.stderr)
    print(f"proceed={'true' if ok else 'false'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
