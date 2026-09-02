"""Tests for the manager handoff-readiness check.

The load-bearing property is that `ready` is UNOBTAINABLE without a live
observation. Every test that asserts a `not_ready` is paired with one asserting
the same check can also pass, so none of them is a constant.
"""
import sys
from pathlib import Path

import pytest

OPS = Path(__file__).resolve().parents[1] / "scripts" / "ops"
sys.path.insert(0, str(OPS))
import handoff_check as hc  # noqa: E402
import manager_lease  # noqa: E402
import open_pr_record as opr  # noqa: E402
import session_registry as sr  # noqa: E402

REG = {"sessions": [{"session_id": "session_01AAAAAAAAAAAAAAAAAAAA"}]}
LIVE = [{"id": "session_01AAAAAAAAAAAAAAAAAAAA"}]
STRANGER = [{"id": "session_01ZZZZZZZZZZZZZZZZZZZZ"}]


def test_self_test_passes():
    assert hc.main(["--self-test"]) == 0


# --------------------------------------------------------------------------- #
# grade() — the three states, never collapsed
# --------------------------------------------------------------------------- #
def test_all_pass_is_ready():
    assert hc.grade([{"state": hc.PASS}, {"state": hc.PASS}]) == "ready"


def test_any_fail_is_not_ready():
    assert hc.grade([{"state": hc.PASS}, {"state": hc.FAIL}]) == "not_ready"


def test_any_unknown_without_a_fail_is_unknown_never_ready():
    assert hc.grade([{"state": hc.PASS}, {"state": hc.UNKNOWN}]) == "unknown"


def test_fail_dominates_unknown():
    """A known blocker is a definite not-ready; reporting it as `unknown` would
    understate a certainty."""
    assert hc.grade([{"state": hc.UNKNOWN}, {"state": hc.FAIL}]) == "not_ready"


def test_exit_codes_distinguish_all_three():
    assert hc._EXIT["ready"] == 0
    assert hc._EXIT["not_ready"] != 0 and hc._EXIT["unknown"] != 0
    assert hc._EXIT["not_ready"] != hc._EXIT["unknown"], \
        "a caller must be able to tell a blocker from 'we could not look'"


# --------------------------------------------------------------------------- #
# the individual checks
# --------------------------------------------------------------------------- #
def test_live_registry_fails_on_an_unregistered_session_and_passes_when_clean():
    assert hc.check_live_registry(REG, True, LIVE + STRANGER, None)["state"] == hc.FAIL
    assert hc.check_live_registry(REG, True, LIVE, None)["state"] == hc.PASS


def test_ready_is_unobtainable_without_a_live_observation():
    """THE enforcement mechanism. There is deliberately no flag that asserts the
    registry is fine — asserting it is what failed twice."""
    assert hc.check_live_registry(REG, True, None, None)["state"] == hc.UNKNOWN


def test_checklist_owner_check_fires_and_is_quiet_in_the_other_direction():
    bad = {"items": [{"id": "MI-1", "state": "in_flight",
                      "owner": "session_01ZZZZZZZZZZZZZZZZZZZZ"}]}
    good = {"items": [{"id": "MI-1", "state": "in_flight",
                       "owner": "session_01AAAAAAAAAAAAAAAAAAAA"}]}
    st = sr.DEFAULT_ENFORCED_STATES
    assert hc.check_checklist_owners(REG, True, bad, True, st)["state"] == hc.FAIL
    assert hc.check_checklist_owners(REG, True, good, True, st)["state"] == hc.PASS


def _mine(holder="S1"):
    return {"state": "held", "holder": holder,
            "heartbeat_at": manager_lease._iso(manager_lease._now())}


def test_lease_must_be_held_by_the_outgoing_manager():
    assert hc.check_lease(_mine(), True, "S1")["state"] == hc.PASS
    assert hc.check_lease(_mine(), True, "S2")["state"] == hc.FAIL


def test_an_already_released_lease_fails():
    """You cannot hand over what you no longer hold."""
    assert hc.check_lease({"state": "released", "holder": None}, True, "S1")["state"] == hc.FAIL


@pytest.mark.parametrize("lease,readable,me", [(None, False, "S1"), (_mine(), True, None)])
def test_lease_unknowns_are_never_a_pass(lease, readable, me):
    assert hc.check_lease(lease, readable, me)["state"] == hc.UNKNOWN


def test_pending_spawns_block_a_handoff():
    pend = {"sessions": [{"state": "spawn_pending", "registry_key": "k",
                          "session_id": None}]}
    assert hc.check_pending_spawns(pend, True)["state"] == hc.FAIL
    assert hc.check_pending_spawns(REG, True)["state"] == hc.PASS


def test_an_unresolvable_base_ref_is_unknown_not_a_pass():
    v = hc.check_manager_state_pushed("refs/nope/definitely-not-a-ref")
    assert v["state"] == hc.UNKNOWN


# --------------------------------------------------------------------------- #
# end to end against the real repo
# --------------------------------------------------------------------------- #
def test_run_over_the_live_repo_never_returns_ready_without_an_observation():
    """The one property that must hold whatever state the repo is in."""
    res = hc.run(observation=None, manager_session_id=None)
    assert res["readiness"] in {"not_ready", "unknown"}
    assert {c["check"] for c in res["checks"]} == {
        "live_registry", "checklist_owners", "lease",
        "manager_state_pushed", "pending_spawns", "open_prs", "pr_decisions",
        # MI-57: `settled_prs` grades the DURABLE half of the record — a PR that
        # never reached `main` with no reason recorded. Pinning the inventory is
        # the point of this assertion, so a new check belongs here explicitly
        # rather than being waved through by a loosened comparison.
        "settled_prs"}


# --------------------------------------------------------------------------- #
# the open-PR half (MI-43 scope extension)
# --------------------------------------------------------------------------- #
REC = {"open_prs": [{"pr": 1, "operator_decision": {
    "verdict": "approved_with_conditions", "condition": "bybit_1 demo only",
    "text": "APPROVED -- bybit_1 ONLY"}}]}


def test_an_open_pr_with_no_row_fails_and_a_complete_record_passes():
    assert hc.check_open_prs_recorded(REC, True, [1, 2])["state"] == hc.FAIL
    assert hc.check_open_prs_recorded(REC, True, [1])["state"] == hc.PASS


def test_a_row_for_a_closed_pr_is_the_staleness_signal():
    """No wall-clock threshold: the record's own _doc says it goes stale the
    moment a PR merges, so a row for a PR no longer open IS that, observed."""
    assert hc.check_open_prs_recorded(REC, True, [])["state"] == hc.FAIL


def test_open_pr_completeness_without_a_live_list_is_unknown():
    assert hc.check_open_prs_recorded(REC, True, None)["state"] == hc.UNKNOWN


def test_a_declared_condition_that_is_not_recorded_fails():
    """THE dangerous case. A successor knowing 'approved' but not the condition
    could merge a demo-only Tier-2 approval onto a real-money account."""
    bad = {"open_prs": [{"pr": 1, "operator_decision": {
        "verdict": "approved_with_conditions", "text": "approved"}}]}
    assert hc.check_pr_decisions(bad, True)["state"] == hc.FAIL
    assert hc.check_pr_decisions(REC, True)["state"] == hc.PASS


def test_an_unconditional_approval_is_not_forced_to_invent_a_condition():
    """Failing plain `approved` would push authors to invent a condition to
    satisfy the guard, which is worse than the gap it closes."""
    ok = {"open_prs": [{"pr": 1, "operator_decision": {
        "verdict": "approved", "text": "approved, no conditions"}}]}
    assert hc.check_pr_decisions(ok, True)["state"] == hc.PASS


def test_free_text_decisions_are_unknown_never_a_pass():
    """A condition dropped from prose is not mechanically detectable; matching
    English for it would be diagnostic-provenance sub-class A."""
    prose = {"open_prs": [{"pr": 1, "operator_decision": "approved, bybit_1 only"}]}
    assert hc.check_pr_decisions(prose, True)["state"] == hc.UNKNOWN


def test_the_live_pr_record_is_fully_typed_and_consistent():
    """Runs against the REAL file."""
    doc, ok = opr.read_record()
    assert ok, "OPEN-PRS.json does not parse"
    assert opr.grade_decisions(doc, ok)["state"] == "graded"


def test_the_pr_record_is_covered_by_the_unpushed_state_check():
    """An operator CONDITION that exists only in a worktree is exactly as lost
    to a successor as an unpushed registry row."""
    assert "docs/claude/work/OPEN-PRS.json" in hc.MANAGER_STATE_PATHS
