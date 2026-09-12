#!/usr/bin/env python3
#
# wiring: imported by `scripts/ops/pr_queue_latency.py`, which is fired by
# `.github/workflows/pr-queue-watch.yml` (schedule + workflow_dispatch). It is
# deliberately NOT a CI guard and NOT a push-triggered check -- see
# "WHY THIS CANNOT BE A GUARD" below, which is the whole point of the module.
"""Does this branch head's commit message forbid its own CI from running?

THE FAILURE
-----------
GitHub skips EVERY workflow run for a push whose head commit message carries a
CI-skip directive. The directive is honoured ANYWHERE in the message, body
included -- not only on the subject line. So a commit that merely QUOTES the
directive while describing it suppresses its own checks.

MEASURED, with a control on one branch (2026-09-12, PR #11937,
`claude/mi279-u1-pregate-timeout`):

* head `cd43ff61` -- an ordinary merge commit, clean SUBJECT, directive quoted
  on line 9 of the BODY. `get_check_runs` returns `total_count: 0`, and
  `mergeable_state` reads `blocked`.
* its own ancestor `719efb9d` on the SAME branch -- same base, same workflow
  set, same permissions, no directive in the message -- ran its checks.

The only difference between the two heads is the token, which is what makes
this an observation rather than a reading of GitHub's documentation.

⚠️ WHY THIS CANNOT BE A GUARD, AND WHY THAT IS NOT A DETAIL
-----------------------------------------------------------
The obvious remedy -- a CI check that refuses the directive -- CANNOT WORK, and
the reason is the defect itself: when the directive is present NO workflow runs,
so a `pull_request`-triggered check never executes on the one commit it exists
to catch. The same disqualifies `armed-branch-push-watch.yml` and every other
`on: push` detector: the push is exactly what was skipped.

A SCHEDULED reader is the only surface that can see it, because its run is not
triggered by the offending push. That is why this module lives beside
`pr_queue_latency.py` and not in `scripts/ci/`.

(A local pre-push hook would also work and is not proposed: project hooks do not
load on Claude Code on the web -- 1,379 consecutive "Found 0 total hooks in
registry" lines, 2026-08-20 -- which is the runtime most of this repo's commits
are authored from.)

⚠️ IT PREVENTS NOTHING. It fires up to one scheduled period after the push. Its
value is that the stranded state currently reads IDENTICALLY to three benign
ones and is therefore found by hand, if at all.

THE STATE IT SEPARATES
----------------------
`ci_settle` already grades `no_checks` -- "the read SUCCEEDED and the head sha
carries zero check runs" -- and already reports `dirty` ahead of it, because a
merge conflict EXPLAINS the emptiness. That leaves `no_checks` on a NON-dirty PR
carrying at least two causes with OPPOSITE remedies:

* checks are queued, or attached late      -> wait
* the push was skipped by a directive      -> push a directive-free commit

Nothing distinguished them, and `blocked` -- which is what GitHub reports for a
required check that has not run -- is also what a genuinely pending PR reports.
CLAUDE.md documents exactly one cause for `total_count: 0` (a merge conflict)
and sends the reader to `mergeable_state`; on this failure that read says
`blocked`, not `dirty`, so the documented check leads to the wrong conclusion.

FOUR STATES, NEVER COLLAPSED
----------------------------
``absent``      the message was read and carries no directive.
``subject``     the directive is on the subject line -- almost always deliberate
                (a squash subject is generated from the PR title at merge time,
                so this is the ordinary shape on `main` and is NOT a finding by
                itself).
``body``        the directive appears ONLY below the subject line. This is the
                dangerous one: a subject is written on purpose, a body is where
                things get quoted.
``unreadable``  we could not read a message -- WE DID NOT LOOK. Never ``absent``.

Pure: no network, no git, no filesystem. The caller supplies the message.
"""
from __future__ import annotations

import argparse
import re
import sys
from typing import Any, Dict, Optional

ABSENT = "absent"
SUBJECT = "subject"
BODY = "body"
UNREADABLE = "unreadable"

STATES = (ABSENT, SUBJECT, BODY, UNREADABLE)

# GitHub's documented set. Kept as one source of truth so a caller cannot
# hand-roll a narrower pattern and report a clean `absent` for a real directive.
#
# ⚠️ The brackets are REQUIRED by GitHub and are what makes this safe to write
# down: prose about "skip ci" without them is inert, which is why this module
# can describe the defect without causing it.
_TOKENS = ("skip ci", "ci skip", "no ci", "skip actions", "actions skip")
_PATTERN = re.compile(
    r"\[\s*(" + "|".join(re.escape(t) for t in _TOKENS) + r")\s*\]",
    re.IGNORECASE,
)


def scan(message: Optional[str]) -> Dict[str, Any]:
    """PURE. One commit message in, one graded row out.

    ``None`` is ``unreadable`` -- *we could not look* -- and is deliberately NOT
    folded into ``absent``. An empty string IS a real read of an empty message
    and grades ``absent``.
    """
    if message is None:
        return {
            "state": UNREADABLE,
            "token": None,
            "line": None,
            "why": ("the head commit message could not be read -- WE DID NOT "
                    "LOOK. This is not 'no directive is present'."),
        }
    lines = message.splitlines()
    subject = lines[0] if lines else ""
    m_subject = _PATTERN.search(subject)
    if m_subject is not None:
        return {
            "state": SUBJECT,
            "token": m_subject.group(0),
            "line": 1,
            "why": ("the SUBJECT line carries a CI-skip directive, so no "
                    "workflow ran for this push. On `main` this is the ordinary "
                    "shape of a squash merge whose PR title discusses the "
                    "directive, and is not by itself a finding; on an open PR's "
                    "head it means the checks were never allowed to run."),
        }
    for idx, line in enumerate(lines[1:], start=2):
        m = _PATTERN.search(line)
        if m is not None:
            return {
                "state": BODY,
                "token": m.group(0),
                "line": idx,
                "why": (f"a CI-skip directive appears on line {idx}, BELOW the "
                        f"subject. GitHub honours it anywhere in the message, so "
                        f"NO workflow ran for this push -- the PR will read "
                        f"`blocked` with zero checks and wait forever. The remedy "
                        f"is a new commit whose message does not contain it; an "
                        f"empty commit is not one (this repo forbids them) and "
                        f"re-running CI by hand cannot help, because there is no "
                        f"run to re-run."),
            }
    return {
        "state": ABSENT,
        "token": None,
        "line": None,
        "why": "the message was read and carries no CI-skip directive.",
    }


def explains_no_checks(scan_row: Dict[str, Any], check_state: Optional[str],
                       merge_verdict: Optional[str]) -> bool:
    """Does this directive EXPLAIN an observed emptiness of checks?

    ⚠️ Deliberately narrow, and the narrowness is the design. It answers ``True``
    only when checks were genuinely observed empty AND a conflict does not
    already explain that emptiness -- mirroring ``ci_settle``'s own rule that
    ``dirty`` is reported ahead of ``no_checks``. Reporting a directive as the
    cause of an emptiness a conflict already explains would swap one confident
    wrong answer for another.
    """
    if scan_row.get("state") not in (SUBJECT, BODY):
        return False
    if check_state != "no_checks":
        return False
    return merge_verdict != "conflicted"


def _self_test(quiet: bool = False) -> int:
    fails = []

    def ck(name: str, ok: bool) -> None:
        if not ok:
            fails.append(name)
        if not quiet:
            print(f"  {'PASS' if ok else 'FAIL'} — {name}")

    tok = "[" + "skip ci" + "]"
    alt = "[" + "CI Skip" + "]"

    if not quiet:
        print("ci-skip-directive self-test")

    # The planted positive the backlog row names in terms: a CLEAN subject with
    # the directive quoted in the body. A checker that reads only the subject
    # passes every naive test and is blind to the case that actually happened.
    planted = (
        "merge main: keep BOTH appends to the replay-pregate row\n"
        "\n"
        "The driver refused this merge and left its usual pure-OURS file,\n"
        f"which my row-set check caught: main added #11938's {tok} landing note\n"
        "and the branch added its own.\n"
    )
    row = scan(planted)
    ck("a directive in the BODY of a clean-subject message is FOUND", row["state"] == BODY)
    ck("...and the offending line number is reported, not just the fact",
       row["line"] == 4)
    ck("...and the token itself is named", row["token"] == tok)

    ck("a directive on the SUBJECT line grades `subject`, not `body`",
       scan(f"chore: land the receipt {tok}\n\nbody text\n")["state"] == SUBJECT)

    ck("an ordinary message grades `absent`",
       scan("fix: correct an off-by-one\n\nno directives here at all\n")["state"] == ABSENT)

    # The token is only live in brackets. This module, and the row it serves,
    # must be able to DISCUSS the defect without causing it.
    ck("bare prose naming the directive without brackets is NOT a match",
       scan("docs: explain why a skip ci token suppresses runs\n")["state"] == ABSENT)

    ck("matching is case-insensitive and tolerates inner spacing",
       scan(f"subject\n\n{alt}\n")["state"] == BODY
       and scan("subject\n\n[ no ci ]\n")["state"] == BODY)

    ck("every documented token is recognised",
       all(scan(f"s\n\n[{t}]\n")["state"] == BODY for t in _TOKENS))

    # The collapse this module exists to refuse.
    ck("None is `unreadable`, NEVER `absent`", scan(None)["state"] == UNREADABLE)
    ck("...and an EMPTY STRING is a real read, so it is `absent`",
       scan("")["state"] == ABSENT)

    # explains_no_checks: the narrowness is load-bearing.
    body_row = scan(planted)
    ck("a directive EXPLAINS an observed `no_checks` on a non-conflicted PR",
       explains_no_checks(body_row, "no_checks", "mergeable") is True)
    ck("...but NOT when a conflict already explains the emptiness",
       explains_no_checks(body_row, "no_checks", "conflicted") is False)
    ck("...and NOT when checks were never read (check_state None)",
       explains_no_checks(body_row, None, "mergeable") is False)
    ck("...and NOT when checks actually ran",
       explains_no_checks(body_row, "green", "mergeable") is False)
    ck("...and an `absent` scan explains nothing",
       explains_no_checks(scan("ok\n"), "no_checks", "mergeable") is False)

    if not quiet:
        print("ALL PASS" if not fails else f"{len(fails)} FAILURE(S): {fails}")
    return 0 if not fails else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--message", help="grade a message read from this string")
    ap.add_argument("--stdin", action="store_true",
                    help="grade a message read from stdin")
    args = ap.parse_args(argv)
    if args.self_test:
        return _self_test()
    msg: Optional[str]
    if args.stdin:
        msg = sys.stdin.read()
    elif args.message is not None:
        msg = args.message
    else:
        ap.error("pass --self-test, --message, or --stdin")
        return 2
    row = scan(msg)
    print(f"ci-skip-directive: state={row['state']} token={row['token']!r} "
          f"line={row['line']}")
    print(f"  {row['why']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
