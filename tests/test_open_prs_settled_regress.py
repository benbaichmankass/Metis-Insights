"""THE REGRESS, REPRODUCED BEFORE IT IS FIXED.

`docs/claude/work/OPEN-PRS.json` + `open_pr_record.grade_completeness` +
`handoff_check`'s `open_prs` gate together had a NON-TERMINATING loop, observed
live on 2026-09-02: PR #10775 merged (`d08cac48`) about ninety seconds after a
branch recorded a row for it.

  * every open PR must have a row            -> `unrecorded` FAIL
  * a row naming a PR no longer open is stale-> `stale_row` FAIL
  * the PR that MAINTAINS the rows is itself an open PR, so it needs a row
  * merging it makes that row stale seconds later

The loop is reproduced here as a SIMULATION rather than asserted as prose,
because the claim being made is about a fixed point ("no sequence of edits
reaches `recorded`"), and a fixed point is the kind of thing a test can
actually establish.

⚠️ THE TWO STRATEGIES DIFFER IN EXACTLY ONE RESPECT, deliberately. Both are
driven by the same `_round` driver against the same grader. The only difference
is WHERE the terminal state is written: `_prune_inside_the_merging_pr` edits the
record inside the commit that is about to merge (the old shape), while
`_reconcile_after_the_merge` writes it from outside (the new one). If the fix
were doing anything else — loosening the grader, exempting a PR — this pairing
would not isolate it.
"""
import sys
from pathlib import Path

import pytest

OPS = Path(__file__).resolve().parents[1] / "scripts" / "ops"
sys.path.insert(0, str(OPS))
import open_pr_record as opr  # noqa: E402

ROUNDS = 12


def _row(pr):
    return {"pr": pr, "operator_decision": {"verdict": "not_required",
                                            "text": "None required (Tier-1)."}}


class _World:
    """The smallest thing that can exhibit the regress: a live open-PR set, a
    record, and a manager that must open a PR to change the record."""

    def __init__(self):
        # One ordinary PR the manager does not own, so the record is never
        # trivially empty — an empty record would converge for the wrong reason.
        self.open_prs = {10746}
        self.record = {"open_prs": [_row(10746)], "settled_prs": []}
        self.next_pr = 10775

    def open_maintenance_pr(self):
        pr = self.next_pr
        self.next_pr += 1
        self.open_prs.add(pr)
        return pr

    def merge(self, pr):
        self.open_prs.discard(pr)

    def grade(self):
        return opr.grade_completeness(self.record, True, sorted(self.open_prs))["state"]


def _prune_inside_the_merging_pr(w, stale):
    """THE OLD SHAPE. The record edit rides in the PR being merged, so the PR
    doing the pruning must first record ITSELF."""
    pr = w.open_maintenance_pr()
    w.record["open_prs"] = [r for r in w.record["open_prs"]
                            if r["pr"] not in stale] + [_row(pr)]
    w.merge(pr)          # ...and the row just written is stale the moment it lands.


def _drive(w, strategy):
    """Grade AFTER each maintenance action, never before.

    ⚠️ The pre-action state is `recorded` and grading it would flatter both
    strategies equally — the regress is not "the record starts broken", it is
    "once maintenance begins the record can never RECOVER". So the sample taken
    is the state a successor would actually read at handoff: the one left
    behind by the merge that was supposed to fix things.
    """
    seen = []
    for _ in range(ROUNDS):
        stale = set(opr.grade_completeness(
            w.record, True, sorted(w.open_prs)).get("stale_rows") or [])
        strategy(w, stale)
        seen.append(w.grade())
    return seen


def test_the_old_shape_never_recovers_once_maintenance_begins():
    """A row written inside the merging commit is stale before it can be read.

    The starting state IS `recorded`; that is the point. Every attempt to keep
    it there leaves it `stale_row`, for as many rounds as anyone cares to run.
    """
    w = _World()
    assert w.grade() == "recorded", "premise: the record starts clean"
    seen = _drive(w, _prune_inside_the_merging_pr)
    assert set(seen) == {"stale_row"}, (
        "the regress did not reproduce; if this fails the premise is wrong, "
        f"not the fix. states={seen}")
    assert "recorded" not in seen


def _reconcile_after_the_merge(w, stale):
    """THE NEW SHAPE. The maintenance PR still exists and still merges — that is
    NOT what changed. What changed is WHO writes the terminal state and WHEN:
    the row is MOVED to `settled_prs[]` by a reconciler running on `push: main`,
    after the merge, from outside the commit.

    Note the row is moved, never dropped: `settled` grows by exactly what
    `open_prs` loses, which the test below asserts.
    """
    pr = w.open_maintenance_pr()
    w.record["open_prs"] = w.record["open_prs"] + [_row(pr)]
    w.merge(pr)
    # ...and now the reconciler runs, which the merging commit could not.
    moved = [r for r in w.record["open_prs"] if r["pr"] not in w.open_prs]
    w.record["open_prs"] = [r for r in w.record["open_prs"] if r["pr"] in w.open_prs]
    w.record["settled_prs"] = w.record["settled_prs"] + moved


def test_the_new_shape_recovers_every_round():
    """Same driver, same grader, same maintenance PRs — only the writer moved."""
    w = _World()
    seen = _drive(w, _reconcile_after_the_merge)
    assert set(seen) == {"recorded"}, seen


def test_the_move_destroys_nothing():
    """The regress could always have been 'fixed' by deleting rows. It was not.

    Every PR the record ever knew about is still in it afterwards — which is the
    property the old prune violated, and the one #10746's conditional Tier-2
    approval depends on.
    """
    w = _World()
    _drive(w, _reconcile_after_the_merge)
    known = {r["pr"] for r in w.record["open_prs"]} | {
        r["pr"] for r in w.record["settled_prs"]}
    assert known == {10746} | {10775 + i for i in range(ROUNDS)}
    assert len(w.record["settled_prs"]) == ROUNDS


def test_settled_rows_are_never_graded_against_the_live_open_list():
    """The load-bearing asymmetry, asserted directly rather than via the driver.

    A settled row names a PR that is SUPPOSED to be closed. If completeness saw
    `settled_prs[]` the whole decision history would grade stale by construction
    — which is exactly the pressure that used to get it deleted.
    """
    doc = {"open_prs": [_row(1)],
           "settled_prs": [{"pr": 99, "terminal": "merged", "merge_sha": "a"}]}
    assert opr.grade_completeness(doc, True, [1])["state"] == "recorded"
    # ...and the guard against a future refactor quietly folding them together:
    assert {r["pr"] for r in opr.rows(doc)} == {1}
    assert {r["pr"] for r in opr.settled_rows(doc)} == {99}


# --------------------------------------------------------------------------- #
# The TYPED automation exclusion — the residual the reconciler's own landing
# PR creates, closed on the record's own terms.
#
# ⚠️ This is NOT the exemption that stays rejected. "The PR currently merging"
# is byte-indistinguishable from a PR nobody recorded. An `automation/` landing
# PR is distinguishable: it carries no owner session, no intent and no operator
# decision — none of the three things the record exists to hold.
#
# ⚠️ THE PREDICATE DOES NOT READ THE AUTHOR. Measured 2026-09-02 over the whole
# visible population, the author field is inverted on BOTH sides:
#
#   #10785  automation/work-decision-commit-33618689721-1  benbaichmankass HUMAN
#   #10781  automation/due-list-33616772895-1              benbaichmankass HUMAN
#   #10398  automation/econ-calendar-33232352515-1         benbaichmankass HUMAN
#   #10783  claude/openprs-settled-reconciler              github-actions[bot]
#
# The landing PRs are PAT-authored (`BRANCH_PROTECTION_TOKEN`), so a bot-author
# requirement would have excused NOTHING — failing on exactly the PRs it exists
# for — while a relayed sub-session PR IS bot-authored and would have been
# wrongly excused. `automation/` is therefore a RESERVED NAMESPACE, and the hole
# that creates is asserted below rather than left implicit.
# --------------------------------------------------------------------------- #
AUTO_HUMAN = {"number": 10398,
              "user": {"login": "benbaichmankass"},
              "head": {"ref": "automation/econ-calendar-33232352515-1"}}
AUTO_BOT = {"number": 10785,
            "user": {"login": "github-actions[bot]"},
            "head": {"ref": "automation/work-decision-commit-33618689721-1"}}
CLAUDE_BOT = {"number": 10783,
              "user": {"login": "github-actions[bot]"},
              "head": {"ref": "claude/openprs-settled-reconciler"}}


@pytest.mark.parametrize("entry,who", [(AUTO_HUMAN, "human"), (AUTO_BOT, "bot")])
def test_an_automation_branch_is_excused_regardless_of_author(entry, who):
    """Both authors, so a later reader cannot mistake the current behaviour for
    an accident of which account happened to open the PR."""
    assert opr.is_automation_landing_pr(entry) is True


def test_a_claude_pr_on_a_claude_branch_still_needs_a_row():
    """⚠️ THE FIXTURE THAT PROVES THE PREDICATE DOES NOT KEY ON AUTHOR.

    #10783 — this very change — is bot-authored, because `pr-opener.yml` relayed
    it. It is still required to have a row. If the predicate ever starts reading
    the author, this test is what fails.
    """
    assert opr.is_automation_landing_pr(CLAUDE_BOT) is False
    doc = {"open_prs": [_row(1)]}
    v = opr.grade_completeness(doc, True, [1, 10783], automation_excluded=[])
    assert v["state"] == "unrecorded"
    assert v["unrecorded"] == [10783]


def test_the_prefix_is_anchored_not_a_substring():
    """`claude/automation-notes` is session work, not a landing PR."""
    assert opr.is_automation_landing_pr(
        {"number": 5, "head": {"ref": "claude/automation-notes"}}) is False
    assert opr.is_automation_landing_pr(
        {"number": 6, "head": {"ref": "feature/automation/x"}}) is False


def test_automation_is_a_reserved_namespace_and_that_is_the_residual():
    """⚠️ THE NAMED HOLE, asserted so it cannot be forgotten.

    The prefix now carries the whole weight and it is a CONVENTION, not
    evidence. A session that opens a PR from an `automation/`-prefixed branch is
    SILENTLY excused and its operator decision goes unrecorded with nothing
    complaining. Session work belongs on `claude/**`; nothing here enforces it.
    """
    session_work_on_reserved_prefix = {
        "number": 999, "user": {"login": "benbaichmankass"},
        "head": {"ref": "automation/my-real-work"}}
    assert opr.is_automation_landing_pr(session_work_on_reserved_prefix) is True
    doc = {"open_prs": [_row(1)]}
    v = opr.grade_completeness(
        doc, True, [1, 999],
        automation_excluded=opr.automation_landing_prs(
            [session_work_on_reserved_prefix]))
    assert v["state"] == "recorded"   # ...and nothing complains. That is the hole.


def test_the_exclusion_removes_a_false_failure_but_not_a_real_one():
    doc = {"open_prs": [_row(1)]}
    assert opr.grade_completeness(
        doc, True, [1, 10398], automation_excluded=[10398])["state"] == "recorded"
    assert opr.grade_completeness(doc, True, [1, 10398])["state"] == "unrecorded"


def test_an_excused_pr_that_has_a_row_is_not_turned_into_a_stale_row():
    """#10398 carries a hand-written row today. Subtracting the excused set from
    the OBSERVATION would make that row read stale — swapping one false failure
    for another."""
    doc = {"open_prs": [_row(1), _row(10398)]}
    v = opr.grade_completeness(doc, True, [1, 10398], automation_excluded=[10398])
    assert v["state"] == "recorded"
    assert v["stale_rows"] == []


def test_a_bare_number_observation_excludes_nothing():
    """The predicate needs head-ref fields. When it cannot be evaluated the
    caller must exclude NOTHING — fail-closed."""
    assert opr.automation_landing_prs([10398, 10783]) is None
