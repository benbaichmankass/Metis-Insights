#!/usr/bin/env python3
"""Pick the concurrency group a system-action run waits in (OA-17, 2026-10-04).

WHY THIS EXISTS. system-actions.yml used ONE workflow-level concurrency group
("system-actions") for every issue-triggered run. GitHub keeps only ONE pending
run per group: a newer request silently CANCELS the older pending one, its
issue stays open with no comment, and nobody is told. Measured 2026-10-04:
#16251 (breakout_1 executor-disable-timer, an OA-03 de-risk step) and #16263
(tradeify_1 executor-enable-timer) were both dropped that way.

THE RULE.
  * A READ-ONLY action gets a group of its own (``system-actions-ro-<run_id>``):
    it can neither displace nor be displaced by anything, and two reads never
    race on a VM resource.
  * Every other action stays in the shared ``system-actions`` group, so two
    mutations on the VM still never run at once. That lane can still drop a
    pending run (GitHub's one-pending limit), which is why
    scripts/ops/system_action_cancel_guard.py exists: it comments
    "CANCELLED, NOT RUN" on the issue and alerts. It never re-queues -- that
    would run the older request after the newer one that displaced it.

READ_ONLY is an explicit allowlist, never a pattern: an action not named here is
treated as mutating (fail toward serialisation). Adding one is a reviewed edit;
each entry below is documented read-only in docs/claude/system-actions.md.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

SHARED_GROUP = "system-actions"

READ_ONLY = frozenset({
    "status-check",
    "list-listening-ports",
    "gateway-logs",
    "gateway-forensics",
    "pull-latest-logs",
    "inspect-insights",
    "net-r-regrade",
    "verify-account-mode",
    "get-env",
})

_ACTION_LINE = re.compile(r"^\s*action\s*:\s*(.*)$", re.IGNORECASE)


def action_from_issue_body(body: str) -> str:
    """The first ``action:`` line, whitespace removed -- the same parse as the
    workflow's 'Resolve action + reason' step (grep -i -m1 ... | tr -d space)."""
    for line in (body or "").splitlines():
        m = _ACTION_LINE.match(line)
        if m:
            return re.sub(r"\s+", "", m.group(1))
    return ""


def concurrency_group(action: str, run_id: str) -> str:
    if action in READ_ONLY:
        return f"{SHARED_GROUP}-ro-{run_id}"
    return SHARED_GROUP


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--event", required=True)
    ap.add_argument("--run-id", required=True)
    args = ap.parse_args(argv)
    if args.event == "workflow_dispatch":
        action = (os.environ.get("DISPATCH_ACTION") or "").strip()
    else:
        action = action_from_issue_body(os.environ.get("ISSUE_BODY") or "")
    group = concurrency_group(action, args.run_id)
    print(f"action={action}")
    print(f"group={group}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
