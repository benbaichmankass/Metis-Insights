"""DELIBERATE CI BLOCK — this PR must not merge. Delete this file to unblock.

WHY THIS EXISTS
───────────────
PR #10789 was opened as a DRAFT and is explicitly not to be merged: it is a
Tier-2 change to a live trading system whose done-condition is a REAL TAP on
ClaudeBot, which no CI run can perform.

On 2026-09-02T11:34Z a force-push to this `claude/**` branch triggered
`.github/workflows/claude-pr-automerge.yml`, which
`markPullRequestReadyForReview` + `enablePullRequestAutoMerge(SQUASH)`. It
un-drafted the PR (verified: `draft` went `true` -> `false`). The intended
remedy — converting the PR back to draft, which also disables auto-merge —
could not be applied because the GitHub API returned
`API rate limit already exceeded for user ID 119055177` on every attempt.

A red required check is the one lever that does NOT need the API, and
auto-merge cannot fire while one is failing. So this is a HOLD, not a defect.

⚠️ THE ARMING WAS ACCIDENTAL AND IS WORTH RECORDING RATHER THAN JUST UNDOING.
That workflow filters on `paths` including the legacy shared file
`.github/pr-automerge-request`. This branch never touched it — but the push was
a REBASE onto a moved `main`, and GitHub computes a push's changed-file set as
the diff from the pre-push head to the new head. A rebase therefore drags every
path that changed on `main` in between into that set, so a branch can arm
auto-merge by rebasing, having requested nothing. Filed as a backlog row.

HOW TO UNBLOCK, once the PR is a draft again (or the reviewer intends to merge):
    git rm tests/test_zz_automerge_hold.py && git commit && git push
"""


def test_this_pr_is_held_and_must_not_auto_merge():
    raise AssertionError(
        "HOLD: PR #10789 must not merge — see this file's docstring. "
        "Auto-merge was armed accidentally by a rebase-triggered path filter "
        "while the API was rate-limited, and the done-condition (a real tap on "
        "ClaudeBot producing a work_decision_transit.jsonl row) is something no "
        "CI run can establish. Delete this file to unblock."
    )
