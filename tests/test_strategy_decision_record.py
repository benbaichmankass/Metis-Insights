"""Tests for the strategy-decision-record guard (C4, operating-layer Phase F).

The guard compares the DECISION RECORD (``config/strategy_changelog.json``)
against the GATE it is a record of (``config/strategies.yaml``).

⚠️ **The most valuable test here is the POSITIVE CONTROL against the REAL repo
files.** The guard currently reports zero divergences, and a quiet guard is only
evidence if the probe can be shown to find a positive — *"a search returning
nothing is not proof of absence; a negative needs a denominator"* (RULE ONE).
``test_positive_control_*`` types the real ``squeeze_breakout_4h`` demotion
entry on an in-memory COPY of the real changelog and asserts the guard catches
the real, live divergence. Without it, "0 divergences" would be indistinguishable
from a guard that stopped matching.
"""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_GUARD = _ROOT / "scripts" / "ci" / "check_strategy_decision_record.py"


def _load_guard():
    spec = importlib.util.spec_from_file_location("_sdr_guard", _GUARD)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


g = _load_guard()


# ---------------------------------------------------------------------------
# The guard's own self-test must pass — a registered-and-never-executed control
# is the written-and-never-read shape this guard family exists to catch.
# ---------------------------------------------------------------------------

def test_self_test_passes():
    assert g._self_test() == 0


# ---------------------------------------------------------------------------
# The five grades are never collapsed.
# ---------------------------------------------------------------------------

def _rows(strategies, changelog):
    return {r["strategy"]: r for r in g.grade(strategies, changelog)}


def test_agrees():
    rows = _rows({"a": {"enabled": True, "execution": "live"}},
                 {"a": [{"date": "2026-01-01", g.VERDICT_FIELD: "live"}]})
    assert rows["a"]["grade"] == g.GRADE_AGREES


def test_diverges():
    rows = _rows({"a": {"enabled": True, "execution": "live"}},
                 {"a": [{"date": "2026-01-01", g.VERDICT_FIELD: "shadow"}]})
    assert rows["a"]["grade"] == g.GRADE_DIVERGES


def test_unrecorded_is_not_agreement():
    """Entries exist, none typed. `we did not look` must not read as `agrees`.

    This is the whole defect: folding it into `agrees` would report a fleet as
    reconciled that nobody has compared.
    """
    rows = _rows({"a": {"enabled": True, "execution": "live"}},
                 {"a": [{"date": "2026-01-01", "ref": "r", "summary": "s"}]})
    assert rows["a"]["grade"] == g.GRADE_UNRECORDED
    assert rows["a"]["grade"] != g.GRADE_AGREES


def test_no_record_is_distinct_from_unrecorded():
    """No entry at all vs entries that are silent on the gate.

    Different remedies — write a first entry vs type the next one — so they are
    different states.
    """
    rows = _rows({"a": {"enabled": True, "execution": "live"}}, {})
    assert rows["a"]["grade"] == g.GRADE_NO_RECORD


def test_not_in_config_is_reported_never_failed():
    """A record of a RETIRED leg is correct history, not drift."""
    rows = _rows({}, {"retired": [{"date": "2026-01-01"}]})
    assert rows["retired"]["grade"] == g.GRADE_NOT_IN_CONFIG


# ---------------------------------------------------------------------------
# Gate derivation
# ---------------------------------------------------------------------------

def test_disabled_wins_over_execution_live():
    """A disabled leg does not run; reporting it `live` describes a gate that
    is not acting."""
    assert g.gate_verdict({"enabled": False, "execution": "live"}) == g.VERDICT_DISABLED


def test_execution_default_is_permissive_and_delegated():
    """Absent `execution` resolves `live` — via the CANONICAL normaliser.

    The value is pinned here so a change to the permissive default cannot pass
    silently, but the guard must never carry its own copy of the rule.
    """
    assert g.gate_verdict({"enabled": True}) == g.VERDICT_LIVE
    assert g.gate_verdict({"enabled": True, "execution": "SHADOW "}) == g.VERDICT_SHADOW


def test_unknown_execution_value_falls_back_permissive_not_to_shadow():
    assert g.gate_verdict({"enabled": True, "execution": "typo"}) == g.VERDICT_LIVE


# ---------------------------------------------------------------------------
# `no_execution_change` is not omission and not agreement.
# ---------------------------------------------------------------------------

def test_no_execution_change_never_asserts_a_gate_state():
    rows = _rows({"a": {"enabled": True, "execution": "live"}},
                 {"a": [{"date": "2026-01-01",
                         g.VERDICT_FIELD: g.VERDICT_NO_CHANGE}]})
    assert rows["a"]["grade"] == g.GRADE_UNRECORDED


def test_no_execution_change_does_not_mask_an_earlier_gate_verdict():
    rows = _rows({"a": {"enabled": True, "execution": "shadow"}},
                 {"a": [{"date": "2026-01-01", g.VERDICT_FIELD: "shadow"},
                        {"date": "2026-06-01",
                         g.VERDICT_FIELD: g.VERDICT_NO_CHANGE}]})
    assert rows["a"]["grade"] == g.GRADE_AGREES
    assert rows["a"]["recorded_date"] == "2026-01-01"


# ---------------------------------------------------------------------------
# Ordering: file order is a convention, not an invariant.
# ---------------------------------------------------------------------------

def test_latest_is_by_date_not_by_position():
    rows = _rows({"a": {"enabled": True, "execution": "live"}},
                 {"a": [{"date": "2026-05-01", g.VERDICT_FIELD: "live"},
                        {"date": "2026-02-01", g.VERDICT_FIELD: "shadow"}]})
    assert rows["a"]["grade"] == g.GRADE_AGREES


def test_undated_entry_cannot_become_the_latest():
    """An undated entry sorts last so it can never mask a real, dated verdict."""
    rows = _rows({"a": {"enabled": True, "execution": "live"}},
                 {"a": [{"date": "2026-05-01", g.VERDICT_FIELD: "live"},
                        {g.VERDICT_FIELD: "shadow"}]})
    assert rows["a"]["recorded_date"] == "2026-05-01"


# ---------------------------------------------------------------------------
# Malformed values fail. The cheapest way to satisfy the guard must not be to
# write something it cannot read.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["SHADOW", "Live", "off", "", None, 1, True])
def test_malformed_verdict_is_a_finding(value):
    assert len(g.malformed_verdicts({"a": [{"date": "d",
                                            g.VERDICT_FIELD: value}]})) == 1


@pytest.mark.parametrize("value", list(g.VALID_VERDICTS))
def test_valid_verdicts_are_not_flagged(value):
    assert g.malformed_verdicts({"a": [{"date": "d", g.VERDICT_FIELD: value}]}) == []


def test_absent_field_is_not_malformed():
    """Legacy entries are UNGRADEABLE, never invalid — they predate the field."""
    assert g.malformed_verdicts({"a": [{"date": "d", "summary": "s"}]}) == []


# ---------------------------------------------------------------------------
# Coverage is a reported denominator, not a decoration.
# ---------------------------------------------------------------------------

def test_coverage_counts_ungradeable_separately_from_agreed():
    strategies = {"a": {"enabled": True, "execution": "live"},
                  "b": {"enabled": True, "execution": "live"}}
    changelog = {"a": [{"date": "d", g.VERDICT_FIELD: "live"}],
                 "b": [{"date": "d", "summary": "s"}]}
    cov = g.coverage(g.grade(strategies, changelog), changelog)
    assert cov["gradeable"] == 1
    assert cov["config_strategies"] == 2
    assert cov["typed_entries"] == 1
    assert cov["untyped_entries"] == 1


# ---------------------------------------------------------------------------
# THE POSITIVE CONTROL — against the REAL repo files.
# ---------------------------------------------------------------------------

def test_real_repo_files_are_readable():
    """If this fails the guard exits 2 (could-not-check), never 0."""
    assert g.load_strategies()
    assert g.load_changelog()


def test_real_repo_has_no_malformed_verdicts():
    assert g.malformed_verdicts(g.load_changelog()) == []


def test_positive_control_guard_catches_the_real_squeeze_divergence():
    """Type ONLY the real 2026-06-01 demotion entry and the guard must fire.

    ``config/strategies.yaml`` documents `squeeze_breakout_4h` as RE-PROMOTED
    shadow -> live on 2026-06-23 (operator pre-approved, PERF-20260601-005,
    paper-only `bybit_1` routing); the changelog's newest entry is the
    2026-06-01 demotion and stops there. So the record is STALE and the gate is
    authorized — the divergence is a missing record, not an unapproved leg.

    This test is what makes the guard's silence meaningful today.
    """
    strategies = g.load_strategies()
    changelog = copy.deepcopy(g.load_changelog())
    entries = changelog.get("squeeze_breakout_4h")
    assert entries, "squeeze_breakout_4h must have changelog entries"

    demotion = [e for e in entries if e.get("date") == "2026-06-01"]
    assert demotion, "the 2026-06-01 demotion entry must still be present"
    demotion[0][g.VERDICT_FIELD] = g.VERDICT_SHADOW

    row = {r["strategy"]: r for r in g.grade(strategies, changelog)}["squeeze_breakout_4h"]
    assert row["grade"] == g.GRADE_DIVERGES
    assert row["gate"] == g.VERDICT_LIVE
    assert row["recorded"] == g.VERDICT_SHADOW


def test_divergence_message_routes_the_remedy_to_the_record_not_the_gate():
    """A guard that only says "these disagree" points the next session at the
    gate — and flipping a live execution gate on inference is Tier-3 and is
    exactly what the backlog row forbids in both directions."""
    strategies = {"a": {"enabled": True, "execution": "live"}}
    changelog = {"a": [{"date": "2026-01-01", g.VERDICT_FIELD: "shadow"}]}
    rows = g.grade(strategies, changelog)
    text = g.render(rows, g.coverage(rows, changelog), False)
    assert "config/strategies.yaml IS AUTHORITATIVE" in text
    assert "DO NOT flip the execution gate" in text
    assert "Tier-3" in text


def test_guard_passes_today_and_reports_its_zero_coverage():
    """Zero coverage must PASS and must be SAID.

    Failing on zero coverage would red every PR on day one, which is how a
    guard gets disabled instead of fixed (`check_pr_queue_watch.py`'s
    `never_ran` precedent).
    """
    strategies = g.load_strategies()
    changelog = g.load_changelog()
    rows = g.grade(strategies, changelog)
    assert [r for r in rows if r["grade"] == g.GRADE_DIVERGES] == []
    text = g.render(rows, g.coverage(rows, changelog), False)
    assert "UNGRADEABLE, not agreed" in text


def test_could_not_check_exits_2_not_1(tmp_path):
    """`we could not look` is neither a pass nor a finding."""
    missing = tmp_path / "nope.json"
    with pytest.raises(g.CouldNotCheck):
        g.load_changelog(missing)
    with pytest.raises(g.CouldNotCheck):
        g.load_strategies(tmp_path / "nope.yaml")
