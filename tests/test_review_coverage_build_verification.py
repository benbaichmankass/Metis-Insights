"""The 2026-08-20 review-coverage keys must actually FAIL a bad payload.

A guard whose failure path is never exercised is indistinguishable from a guard
that always passes — this repo's "green is not evidence" rule. These three keys
exist because the previous generation of this guard was declared in SKILL.md and
enforced by nothing: `account_reachability` was named as mandatory on 2026-06-29
and absent from `_REQUIRED_COVERAGE_KEYS` until 2026-08-20, while the IB gateway
flapped across reviews unflagged.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _renderer():
    spec = importlib.util.spec_from_file_location(
        "render_system_report", REPO / "scripts/reports/render_system_report.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _validate(rc: dict) -> list[str]:
    return _renderer()._validate_review_coverage({"consolidated": {"review_coverage": rc}})


@pytest.mark.parametrize("key", [
    "strategy_promotion", "ml_training_health", "soak_status", "execution_capture",
    "backlog_drive", "account_reachability", "since_last_build_verification",
    "backlog_classes", "ml_output_actionability", "unexercised_fixes",
])
def test_every_declared_key_is_actually_enforced(key):
    """SKILL.md's list and the enforced tuple must not drift apart again."""
    assert key in _renderer()._REQUIRED_COVERAGE_KEYS
    violations = _validate({"flags_raised": []})
    assert any(f"review_coverage.{key} missing/empty" in v for v in violations)


def test_unwired_capability_must_be_escalated():
    """A capability that shipped and does not run has to be LOUD."""
    rc = {"since_last_build_verification": {
        "count_shipped": 1,
        "items": [{"name": "trainer_dataset_gc", "verdict": "UNWIRED"}]},
        "flags_raised": []}
    assert any("UNWIRED but not" in v for v in _validate(rc))
    # escalated -> that particular violation goes away
    rc["flags_raised"] = ["trainer_dataset_gc has no runner and the disk is at 93%"]
    assert not any("UNWIRED but not" in v for v in _validate(rc))


def test_partial_enumeration_is_caught():
    """count_shipped must match the list — a partial enumeration is the failure."""
    rc = {"since_last_build_verification": {
        "count_shipped": 4, "items": [{"name": "a", "verdict": "running"}]}}
    assert any("count_shipped=4" in v for v in _validate(rc))


def test_unverifiable_needs_a_reason():
    rc = {"since_last_build_verification": {
        "count_shipped": 1, "items": [{"name": "a", "verdict": "unverifiable"}]}}
    assert any("no reason" in v for v in _validate(rc))


def test_a_class_needs_more_than_one_member():
    """One row is an instance, not a class — the whole point of the key."""
    rc = {"backlog_classes": {"total_open_reviewed": 261, "classes": [
        {"class": "netting-divergence", "member_ids": ["BL-1"],
         "structural_fix": "prorate by journal total"}]}}
    assert any("one row is" in v for v in _validate(rc))


def test_a_class_without_a_structural_fix_is_refused():
    rc = {"backlog_classes": {"total_open_reviewed": 261, "classes": [
        {"class": "netting-divergence", "member_ids": ["BL-1", "BL-2"]}]}}
    assert any("structural_fix" in v for v in _validate(rc))


def test_whole_backlog_must_be_read_not_sampled():
    rc = {"backlog_classes": {"classes": []}}
    assert any("total_open_reviewed" in v for v in _validate(rc))


def test_unused_ml_output_must_be_flagged():
    """A training fleet nobody consumes is a finding, not a status line."""
    rc = {"ml_output_actionability": {
        "cycles_in_window": 149, "outputs_consumed_by": ["nothing"],
        "verdict": "producing_but_unused"}, "flags_raised": []}
    assert any("nothing about ML is in" in v for v in _validate(rc))
    rc["flags_raised"] = ["ML fleet: outputs produced but no consumer reads them"]
    assert not any("nothing about ML is in" in v for v in _validate(rc))


def test_a_clean_payload_raises_none_of_these():
    """The controls must distinguish — a check that always fails is useless."""
    rc = {
        "strategy_promotion": "all HOLD, evidence attached",
        "ml_training_health": "cycles ran nightly",
        "soak_status": "accruing",
        "execution_capture": {"anomalies": []},
        "backlog_drive": {"summary": "x" * 100,
                          "health": {"drained": ["BL-1"], "deferred": []}},
        "account_reachability": {"bybit_2": "up"},
        "since_last_build_verification": {
            "count_shipped": 2,
            "items": [{"name": "a", "verdict": "running"},
                      {"name": "b", "verdict": "wired_not_yet_exercised"}]},
        "backlog_classes": {"total_open_reviewed": 261, "classes": [
            {"class": "netted-duplicate-pnl", "member_ids": ["BL-1", "BL-2"],
             "structural_fix": "prorate by journal-total share, mark FABRICATED"}]},
        "ml_output_actionability": {
            "cycles_in_window": 149,
            "outputs_consumed_by": ["intents._decision_vol_regime"],
            "verdict": "actionable"},
        # A clean payload must carry an `exercised` row WITH evidence — the only
        # state that legitimately drains this key. `still_unexercised` would be
        # honest but not clean: it is required to reach flags_raised[].
        "unexercised_fixes": [{
            "fix": "#10174 IB transmit", "deployed_sha": "abc1234",
            "verdict": "exercised",
            "evidence": "trade 4931 exited at the attached LMT, exit_reason=target_fill"}],
        "flags_raised": [],
    }
    assert _validate(rc) == []


# ── unexercised_fixes (2026-08-24, operator-directed) ────────────────────────
#
# "A deployed fix and a working fix look identical" is the whole point, so the
# guard has to refuse the two ways a review could assert success without
# showing it: claiming `exercised` with no evidence, and reporting a fix that
# is still unproven without making it loud.


def _rc(rows, flags=None):
    """A minimal block carrying only what these tests are about.

    Deliberately NOT a fully-populated coverage object: the sibling validators
    have their own shape requirements, and filling every key with a placeholder
    trips them instead of the one under test. Every assertion below filters to
    `unexercised_fixes` violations, so the other keys' "missing/empty" noise is
    irrelevant here and is covered by the parametrized test above.
    """
    return {"unexercised_fixes": rows, "flags_raised": flags or []}


def test_exercised_without_evidence_is_refused():
    """The claim that settles this key cannot be made by assertion alone."""
    v = _validate(_rc([{"fix": "#10174 IB transmit", "verdict": "exercised"}]))
    assert any("claims 'exercised' with no evidence" in x for x in v)


def test_exercised_with_evidence_passes():
    """The falsifier: the same row WITH evidence must be clean."""
    v = _validate(_rc([{
        "fix": "#10174 IB transmit", "verdict": "exercised",
        "evidence": "trade 4931 MGC exited at the attached LMT 4393.00, exit_reason=target_fill",
    }]))
    assert not [x for x in v if "unexercised_fixes" in x], v


@pytest.mark.parametrize("verdict", ["still_unexercised", "regressed", "unverifiable"])
def test_unproven_fix_must_reach_flags(verdict):
    """A fix we cannot show working is a standing risk, not a status line."""
    v = _validate(_rc([{"fix": "#10174 IB transmit", "verdict": verdict}]))
    assert any("unexercised_fixes" in x and "flags_raised" in x for x in v)
    # ...and is clean once it IS flagged.
    v2 = _validate(_rc([{"fix": "#10174 IB transmit", "verdict": verdict}],
                       flags=["#10174 IB transmit fix still unexercised"]))
    assert not [x for x in v2 if "unexercised_fixes" in x], v2


def test_unknown_verdict_is_refused():
    """An unrecognised verdict must not pass as a fourth valid state."""
    v = _validate(_rc([{"fix": "x", "verdict": "probably_fine"}]))
    assert any("not one of" in x for x in v)
