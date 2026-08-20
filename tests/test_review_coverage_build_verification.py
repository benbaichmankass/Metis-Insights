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
    "backlog_classes", "ml_output_actionability",
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
        "flags_raised": [],
    }
    assert _validate(rc) == []
