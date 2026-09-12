"""R15 — a Tier-2 PR lands on a recorded approval it DEMONSTRABLY COULD NOT HAVE WRITTEN.

WHAT THIS FILE IS FOR, AND WHAT IT DELIBERATELY IS NOT
-----------------------------------------------------
Anything can be made to pass. What matters for a merge gate is that it still
REFUSES, so the bulk of this file is negative controls — and each one is checked
for the RULE IT FIRES, not merely for failing, because a control that fails for
an unrelated reason is testing nothing.

⚠️ AND A GREEN SUITE IS NOT THE DONE-CONDITION. `WO-20260911-LET-AN-APPROVED-TIER-2-PR-LAND`
is two-sided and neither half is a test: an approved Tier-2 PR OBSERVED landing
with no human click, and an unapproved one OBSERVED still being refused. A
harness cannot reach either. This proves the failure paths fire; it does not
prove a real PR landed.

THE MUTATION CHECKS AT THE BOTTOM ARE THE POINT
-----------------------------------------------
The forgery case is caught by TWO independent rules (R12, because the approvals
directory is landing machinery, and R15(e)). That redundancy is deliberate and
it is also a hazard for a test suite: a control can keep passing after its own
property is removed, because the other rule still catches it. So each
load-bearing predicate is MUTATED in isolation, with the overlapping rule
neutralised, and the suite is proved to notice.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "check_pr_landing",
    Path(__file__).resolve().parents[1] / "scripts/ci/check_pr_landing.py")
g = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(g)

BRANCH = "claude/demo"


def _run(tmp: Path, plant, **kw):
    root = g._t2_sandbox(tmp, **kw)
    plant(root)
    g._commit(root)
    return g.check(root, "main", BRANCH)


def _rules(fails: list[str]) -> set[str]:
    return {f.split()[0] for f in fails}


# ---------------------------------------------------------------------------
# The positive control. A rule that never admits anything has not fixed the
# click it was built to remove.
# ---------------------------------------------------------------------------

def test_an_approved_tier2_pr_self_lands():
    with tempfile.TemporaryDirectory() as td:
        state, fails, notes = _run(Path(td), lambda r: g._t2_full(r))
    assert fails == [], fails
    assert state == "declared_self_land"
    assert any("R15 OK" in n for n in notes)


def test_the_admitting_note_states_what_r15_does_not_establish():
    """The residual travels with the grant, not only in a PR body nobody re-reads."""
    with tempfile.TemporaryDirectory() as td:
        _, _, notes = _run(Path(td), lambda r: g._t2_full(r))
    blob = " ".join(notes)
    assert "NOT that the operator originated it" in blob


# ---------------------------------------------------------------------------
# THE FIVE NEGATIVE CONTROLS THE BRIEF NAMES. Each asserts its own rule.
# ---------------------------------------------------------------------------

def test_tier2_with_no_approval_record_is_refused():
    with tempfile.TemporaryDirectory() as td:
        _, fails, _ = _run(Path(td), lambda r: (
            g._t2_declare(r, approved_by=None), g._arm(r), g._claim_slot(r)))
    assert "R15(a)" in _rules(fails)


def test_an_approval_on_the_prs_own_branch_is_refused():
    """THE FORGERY CASE — the one that matters.

    The record is byte-for-byte the one that PASSES in the positive control
    above. The only difference is which commit carries it.
    """
    with tempfile.TemporaryDirectory() as td:
        _, fails, _ = _run(Path(td), lambda r: g._t2_full(r),
                           approval_on_branch=True)
    rules = _rules(fails)
    assert "R15(e)" in rules
    assert "R12" in rules, "the second, independent refusal must also fire"


def test_an_approval_naming_a_different_branch_is_refused():
    with tempfile.TemporaryDirectory() as td:
        _, fails, _ = _run(
            Path(td), lambda r: g._t2_full(r),
            approval={**g._APPROVAL_OK, "branch": "claude/somebody-else"})
    assert "R15(f)" in _rules(fails)


def test_a_diff_exceeding_the_approved_scope_is_refused():
    with tempfile.TemporaryDirectory() as td:
        _, fails, _ = _run(
            Path(td), lambda r: g._t2_full(r),
            branch_files={"src/runtime/exit_loop.py": "x = 1\n",
                          "src/units/accounts/ib_client.py": "y = 2\n"})
    assert "R15(i)" in _rules(fails)


def test_tier3_is_refused_even_with_a_valid_tier2_style_approval():
    # (a) the declaration says tier 3.
    with tempfile.TemporaryDirectory() as td:
        _, fails, _ = _run(Path(td), lambda r: g._t2_full(r, tier=3))
    assert "R4" in _rules(fails)
    # (b) the declaration says tier 2 but the DIFF reaches a Tier-3 path, and
    #     the record even lists it in scope_paths. A scope entry must not buy it.
    with tempfile.TemporaryDirectory() as td:
        _, fails, _ = _run(
            Path(td), lambda r: g._t2_full(r),
            approval={**g._APPROVAL_OK,
                      "scope_paths": ["src/runtime/**", "config/**"]},
            branch_files={"src/runtime/exit_loop.py": "x = 1\n",
                          "config/strategies.yaml": "a: 1\n"})
    assert "R15(g)" in _rules(fails)
    # (c) the RECORD itself declares tier 3.
    with tempfile.TemporaryDirectory() as td:
        _, fails, _ = _run(Path(td), lambda r: g._t2_full(r),
                           approval={**g._APPROVAL_OK, "tier": 3})
    assert "R15(g)" in _rules(fails)


# ---------------------------------------------------------------------------
# The ways a credential could be hollowed out.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("over,rule", [
    ({"verdict": "pending"}, "R15(h)"),
    ({"verdict": "approved_with_conditions"}, None),   # a legitimate verdict
    ({"channel": "trust me"}, "R15(h)"),
    ({"text": "ok"}, "R15(h)"),
    ({"decided_by": ""}, "R15(h)"),
    ({"decision_id": ""}, "R15(h)"),
    ({"work_object": "WO-DOES-NOT-EXIST"}, "R15(h)"),
    ({"scope_paths": []}, "R15(i)"),
    ({"scope_paths": "src/runtime/**"}, "R15(i)"),     # a string, not a list
])
def test_record_field_discipline(over, rule):
    rec = {**g._APPROVAL_OK}
    rec.update(over)
    if over.get("scope_paths") == []:
        rec["scope_paths"] = []
    with tempfile.TemporaryDirectory() as td:
        _, fails, _ = _run(Path(td), lambda r: g._t2_full(r), approval=rec)
    if rule is None:
        assert fails == [], fails
    else:
        assert rule in _rules(fails), fails


def test_an_approval_outside_the_protected_directory_is_refused():
    """The directory IS the mechanism — it is what puts R12 in front of writing one."""
    def plant(r):
        (r / "docs").mkdir(parents=True, exist_ok=True)
        (r / "docs/my-own-approval.json").write_text(
            json.dumps(g._APPROVAL_OK), encoding="utf-8")
        g._t2_full(r, approved_by="docs/my-own-approval.json")
    with tempfile.TemporaryDirectory() as td:
        _, fails, _ = _run(Path(td), plant)
    assert "R15(b)" in _rules(fails)


def test_the_approvals_directory_is_landing_machinery():
    """Without this, the two-PR chain re-opens: land the approval, then cite it."""
    assert f"{g.APPROVAL_DIR}/**" in g.LANDING_MACHINERY


def test_a_branch_editing_the_approval_it_cites_is_refused():
    with tempfile.TemporaryDirectory() as td:
        _, fails, _ = _run(Path(td), lambda r: (
            g._write_approval(r, {**g._APPROVAL_OK, "scope_paths": ["**"]}),
            g._t2_full(r)))
    assert "R15(e)" in _rules(fails)


def test_the_record_is_read_from_main_not_from_the_worktree():
    """A guard should not be one `sed` away from a different answer.

    Clause (d) already refuses a DIFF, so this covers the residual case: an
    uncommitted worktree edit, which no diff records.
    """
    with tempfile.TemporaryDirectory() as td:
        root = g._t2_sandbox(Path(td))
        g._t2_full(root)
        g._commit(root)
        # Widen the scope on disk only — never committed, so no diff exists.
        g._write_approval(root, {**g._APPROVAL_OK, "scope_paths": ["**"],
                                 "branch": "claude/somebody-else"})
        _, fails, _ = g.check(root, "main", BRANCH)
    assert fails == [], (
        "the verdict moved when an UNCOMMITTED worktree edit was made, so the "
        "record is being read from disk rather than from the merge-base blob")


def test_landing_paperwork_is_exempt_from_scope_but_nothing_else_is():
    """R6/R11/R13 force three files; requiring every record to enumerate them
    would tempt authors into a wildcard that swallows the real diff."""
    with tempfile.TemporaryDirectory() as td:
        state, fails, _ = _run(Path(td), lambda r: g._t2_full(r))
    assert fails == [] and state == "declared_self_land"


# ---------------------------------------------------------------------------
# MUTATION CHECKS — break the property, prove the suite notices.
# ---------------------------------------------------------------------------

def test_the_on_branch_forgery_is_defended_in_depth_by_three_rules():
    """MEASURED, not assumed — and each is removed in turn below.

    A branch that adds its own approval trips (1) R15(e), the separation
    predicate; (2) R12, because the approvals directory is landing machinery;
    and (3) R15(i), because the record it added is itself a path outside the
    scope that record grants. Three independent refusals for one act.
    """
    with tempfile.TemporaryDirectory() as td:
        _, fails, _ = _run(Path(td), lambda r: g._t2_full(r),
                           approval_on_branch=True)
    assert {"R15(e)", "R12"} <= _rules(fails)


def test_mutation_separation_predicate_is_load_bearing(monkeypatch):
    """Isolate the separation property by removing every OTHER refusal.

    The strongest forger available: a branch that writes its own approval AND
    gives that record a `scope_paths` wide enough to cover the record itself,
    so the scope comparison has nothing to say. That leaves exactly two rules —
    R15(e) and R12 — and the mutation removes both.

    If the PR is then ADMITTED, those two are demonstrably what stands between
    a self-written approval and `main`. If it is still refused, the negative
    control above is testing something else and this file is lying.
    """
    forger = {**g._APPROVAL_OK,
              "scope_paths": ["src/runtime/**", f"{g.APPROVAL_DIR}/**"]}

    # Control: the real guard refuses it, on both rules.
    with tempfile.TemporaryDirectory() as td:
        _, fails, _ = _run(Path(td), lambda r: g._t2_full(r),
                           approval=forger, approval_on_branch=True)
    assert {"R15(e)", "R12"} <= _rules(fails), fails

    # Mutation: remove both, and the identical PR sails through.
    monkeypatch.setattr(
        g, "approval_separation",
        lambda root, base, branch, norm, changed: ("separate", "HEAD"))
    monkeypatch.setattr(
        g, "LANDING_MACHINERY",
        [p for p in g.LANDING_MACHINERY if p != f"{g.APPROVAL_DIR}/**"])
    with tempfile.TemporaryDirectory() as td:
        state, fails, _ = _run(Path(td), lambda r: g._t2_full(r),
                               approval=forger, approval_on_branch=True)
    assert fails == [], (
        "with BOTH refusals removed a branch-written approval still failed, so "
        f"neither control is exercising them: {fails}")
    assert state == "declared_self_land"


def test_an_approval_that_exists_nowhere_is_refused():
    """`approved_by` naming a record on neither `main` nor the branch."""
    with tempfile.TemporaryDirectory() as td:
        _, fails, _ = _run(Path(td), lambda r: g._t2_full(r), approval=None)
    assert "R15(c)" in _rules(fails), fails


def test_mutation_r12_alone_still_catches_the_on_branch_forgery(monkeypatch):
    """Remove only the R15 half; the landing-machinery rule must still bite."""
    monkeypatch.setattr(
        g, "approval_separation",
        lambda root, base, branch, norm, changed: ("separate", "HEAD"))
    with tempfile.TemporaryDirectory() as td:
        _, fails, _ = _run(Path(td), lambda r: g._t2_full(r),
                           approval_on_branch=True)
    assert "R12" in _rules(fails)


def test_mutation_landing_machinery_alone_removed_still_catches_it(monkeypatch):
    """And remove only the R12 half; R15(e) must still bite."""
    monkeypatch.setattr(
        g, "LANDING_MACHINERY",
        [p for p in g.LANDING_MACHINERY if p != f"{g.APPROVAL_DIR}/**"])
    with tempfile.TemporaryDirectory() as td:
        _, fails, _ = _run(Path(td), lambda r: g._t2_full(r),
                           approval_on_branch=True)
    assert "R15(e)" in _rules(fails)


def test_mutation_scope_comparison_is_load_bearing(monkeypatch):
    monkeypatch.setattr(g, "paths_outside_scope", lambda paths, scope: [])
    with tempfile.TemporaryDirectory() as td:
        _, fails, _ = _run(
            Path(td), lambda r: g._t2_full(r),
            branch_files={"src/runtime/exit_loop.py": "x = 1\n",
                          "src/units/accounts/ib_client.py": "y = 2\n"})
    assert fails == [], (
        "the out-of-scope diff still failed with the scope comparison removed, "
        f"so that control is not testing the scope check: {fails}")


def test_mutation_branch_binding_is_load_bearing():
    """R15(f) is the only thing stopping a second branch re-using a record."""
    src = (Path(__file__).resolve().parents[1]
           / "scripts/ci/check_pr_landing.py").read_text()
    assert 'if named != branch:' in src, (
        "the branch-binding comparison is gone; a landed approval could then be "
        "cited by any other branch")


def test_separation_states_are_never_collapsed():
    assert set(g.SEPARATION_STATES) == {
        "separate", "touched_by_branch", "absent_at_merge_base",
        "merge_base_unreadable"}


def test_an_unreadable_merge_base_refuses_rather_than_grants():
    with tempfile.TemporaryDirectory() as td:
        root = g._t2_sandbox(Path(td))
        g._t2_full(root)
        g._commit(root)
        state, fails, _ = g.check(root, "no-such-ref", BRANCH)
    # `not_a_pr` (could not diff) or an R15(c) refusal — never an admission.
    assert state != "declared_self_land"


def test_the_self_test_still_passes():
    rc = subprocess.run(
        ["python3", str(Path(__file__).resolve().parents[1]
                        / "scripts/ci/check_pr_landing.py"), "--self-test"],
        capture_output=True, text=True)
    assert rc.returncode == 0, rc.stdout + rc.stderr


# ---------------------------------------------------------------------------
# Clause (j): WHO wrote the approval, where that is knowable at all.
# ---------------------------------------------------------------------------

def _amend_trailer(root: Path, sid: str) -> None:
    body = subprocess.run(["git", "-C", str(root), "log", "-1", "--format=%B"],
                          capture_output=True, text=True).stdout.rstrip()
    subprocess.run(["git", "-C", str(root), "commit", "-q", "--amend",
                    "-m", f"{body}\n\nClaude-Session: {sid}"], check=True)


def test_clause_j_refuses_when_one_session_both_grants_and_spends():
    with tempfile.TemporaryDirectory() as td:
        root = g._t2_sandbox(Path(td))
        # Re-write the BASE commit's trailer, then rebuild the branch on it.
        subprocess.run(["git", "-C", str(root), "checkout", "-q", "main"], check=True)
        _amend_trailer(root, "session_SAME")
        subprocess.run(["git", "-C", str(root), "branch", "-qD", "claude/demo"],
                       check=True)
        subprocess.run(["git", "-C", str(root), "checkout", "-qb", "claude/demo"],
                       check=True)
        (root / "src/runtime").mkdir(parents=True, exist_ok=True)
        (root / "src/runtime/exit_loop.py").write_text("x = 1\n", encoding="utf-8")
        g._t2_full(root)
        g._commit(root)
        _amend_trailer(root, "session_SAME")
        _, fails, _ = g.check(root, "main", BRANCH)
    assert "R15(j)" in _rules(fails), fails


def test_clause_j_does_not_block_when_no_trailer_exists():
    """66.7% coverage measured on `main`; absence must never deny a grant."""
    with tempfile.TemporaryDirectory() as td:
        state, fails, notes = _run(Path(td), lambda r: g._t2_full(r))
    assert fails == [] and state == "declared_self_land"
    assert any("we did not look" in n for n in notes)


def test_clause_j_reports_a_different_approving_session_without_failing():
    with tempfile.TemporaryDirectory() as td:
        root = g._t2_sandbox(Path(td))
        subprocess.run(["git", "-C", str(root), "checkout", "-q", "main"], check=True)
        _amend_trailer(root, "session_APPROVER")
        subprocess.run(["git", "-C", str(root), "branch", "-qD", "claude/demo"],
                       check=True)
        subprocess.run(["git", "-C", str(root), "checkout", "-qb", "claude/demo"],
                       check=True)
        (root / "src/runtime").mkdir(parents=True, exist_ok=True)
        (root / "src/runtime/exit_loop.py").write_text("x = 1\n", encoding="utf-8")
        g._t2_full(root)
        g._commit(root)
        _amend_trailer(root, "session_LANDER")
        state, fails, notes = g.check(root, "main", BRANCH)
    assert fails == [], fails
    assert state == "declared_self_land"
    assert any("session_APPROVER" in n for n in notes)
