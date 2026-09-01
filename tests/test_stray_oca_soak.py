"""The stray-OCA sweep's decision must reach a durable, probe-able surface.

Before this, ``_sweep_stray_oca_groups`` returned a complete plan and its one
call site discarded it; the only output was a ``logger.warning``. These tests
pin the properties that make the resulting rows trustworthy — not merely that a
file gets written.
"""

from __future__ import annotations

import ast
import json
import pathlib

import pytest

from src.runtime import stray_oca_groups, stray_oca_soak


def _plan(**over):
    base = {
        "mode": "annotate", "global_mode": "annotate",
        "apply_scope": stray_oca_groups.SCOPE_NOT_APPLY,
        "account_id": "ib_paper", "read_state": "legs_read",
        "by_state": {}, "stray_groups": [], "preserved_groups": [],
        "ungrouped_seen": 0, "keep_group": "oca-protect-t4796", "acted": False,
    }
    base.update(over)
    return base


@pytest.fixture()
def soak(tmp_path, monkeypatch):
    monkeypatch.setattr(stray_oca_soak, "_log_path",
                        lambda: tmp_path / "stray_oca_soak.jsonl")
    return tmp_path / "stray_oca_soak.jsonl"


def _rows(path: pathlib.Path):
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


# ── the decision is never collapsed ────────────────────────────────────────

def test_a_read_failure_is_not_graded_as_no_strays():
    """The invariant the sweep defends one layer down, defended again here.

    An unreadable book and a clean book both present as an empty
    ``stray_groups``. Grading them alike would let "we could not look" render
    as "nothing is wrong" on the one surface a reviewer reads before arming a
    cancel.
    """
    unreadable = _plan(read_state="could_not_look")
    clean = _plan(read_state="legs_read")
    assert stray_oca_soak.decision_for(unreadable) == "could_not_look"
    assert stray_oca_soak.decision_for(clean) == "no_strays"
    assert stray_oca_soak.decision_for(unreadable) != stray_oca_soak.decision_for(clean)


def test_strays_grade_as_the_finding():
    assert stray_oca_soak.decision_for(
        _plan(stray_groups=["oca-protect-446"])) == "stray_unkeyed"


def test_every_declared_decision_is_reachable():
    """A vocabulary with an unreachable member is a lie about the states."""
    seen = {
        stray_oca_soak.decision_for(_plan(read_state="could_not_look")),
        stray_oca_soak.decision_for(_plan()),
        stray_oca_soak.decision_for(_plan(stray_groups=["g"])),
    }
    assert seen == set(stray_oca_soak.DECISIONS)


# ── the denominator ────────────────────────────────────────────────────────

def test_a_quiet_sweep_still_writes_its_row(soak):
    """Without the quiet row, "found nothing" and "never ran" are identical."""
    assert stray_oca_soak.record(_plan(), symbol="MGC") is not None
    rows = _rows(soak)
    assert len(rows) == 1 and rows[0]["decision"] == "no_strays"


def test_off_writes_nothing(soak):
    assert stray_oca_soak.record(
        _plan(global_mode="off", mode="off"), symbol="MGC") is None
    assert _rows(soak) == []


def test_absent_plan_writes_nothing(soak):
    assert stray_oca_soak.record(None, symbol="MGC") is None
    assert _rows(soak) == []


# ── the five leg states partition, checkably ───────────────────────────────

def test_all_five_leg_states_are_emitted_with_explicit_zeros(soak):
    """``by_state`` omits unseen states, so an absent key would be ambiguous."""
    stray_oca_soak.record(
        _plan(by_state={"stray_unkeyed": 2, "keep_target": 1}), symbol="MHG")
    row = _rows(soak)[0]
    assert set(row["legs_by_state"]) == {
        "keep_target", "sibling_keyed", "stray_unkeyed", "ungrouped",
        "not_protective"}
    assert row["legs_by_state"]["sibling_keyed"] == 0
    assert row["legs_seen"] == 3
    assert sum(row["legs_by_state"].values()) == row["legs_seen"]


# ── the request / effect distinction ───────────────────────────────────────

def test_a_held_back_row_cannot_read_as_an_applied_one(soak):
    """`apply` requested, allowlist withheld: the row must say all three."""
    stray_oca_soak.record(_plan(
        mode="annotate", global_mode="apply",
        apply_scope=stray_oca_groups.SCOPE_NOT_ALLOWLISTED,
        stray_groups=["oca-protect-446"], acted=False), symbol="MGC")
    row = _rows(soak)[0]
    assert row["global_mode"] == "apply"      # what was asked for
    assert row["mode"] == "annotate"          # what governed
    assert row["apply_scope"] == "not_allowlisted"   # why they differ
    assert row["acted"] is False              # the effect


def test_the_applied_readback_is_absent_not_zeroed_when_nothing_acted(soak):
    """A zeroed verification would assert a read-back nobody performed."""
    stray_oca_soak.record(_plan(stray_groups=["g"]), symbol="MGC")
    row = _rows(soak)[0]
    for k in ("cancel_calls_made", "verify_state", "still_resting"):
        assert k not in row


def test_an_applied_row_carries_the_cancel_readback(soak):
    stray_oca_soak.record(_plan(
        mode="apply", global_mode="apply",
        apply_scope=stray_oca_groups.SCOPE_ALLOWLISTED,
        stray_groups=["oca-protect-446"], acted=True, cancelled=2,
        verify_state="confirmed_gone", still_resting=[],
        confirmed_gone=["446a", "446b"]), symbol="MGC")
    row = _rows(soak)[0]
    assert row["acted"] is True
    assert row["cancel_calls_made"] == 2          # CALLS, never an outcome
    assert row["verify_state"] == "confirmed_gone"
    assert row["still_resting"] == []
    assert row["cancelled_groups"] == ["oca-protect-446"]


def test_the_probe_predicate_matches_an_applied_stray_row(soak):
    """The exact pair the OPEN-ITEMS probe requires: decision AND acted."""
    stray_oca_soak.record(_plan(
        mode="apply", global_mode="apply",
        apply_scope=stray_oca_groups.SCOPE_ALLOWLISTED,
        stray_groups=["oca-protect-446"], acted=True, cancelled=1,
        verify_state="confirmed_gone", still_resting=[]), symbol="MGC")
    row = _rows(soak)[0]
    assert row["decision"] == "stray_unkeyed" and row["acted"] is True


# ── it must never break a protective arm ───────────────────────────────────

def test_a_write_failure_is_swallowed(monkeypatch):
    monkeypatch.setattr(stray_oca_soak, "_log_path",
                        lambda: (_ for _ in ()).throw(OSError("disk")))
    assert stray_oca_soak.record(_plan(), symbol="MGC") is None


def test_the_soak_does_not_re_resolve_the_mode():
    """A second env read is a second source of truth, free to drift from the
    one that actually governed the cancel."""
    src = pathlib.Path("src/runtime/stray_oca_soak.py").read_text()
    assert "PROTECTION_STRAY_GROUP_MODE" not in src.split('"""', 2)[2], (
        "the writer re-reads the gate outside its docstring; it must record "
        "the mode the sweep decided, not resolve its own")


def test_the_call_site_passes_the_returned_plan_and_does_not_discard_it():
    """Structural: the defect was a discarded return value, so assert the
    binding exists rather than trusting the diff."""
    tree = ast.parse(pathlib.Path("src/units/accounts/ib_client.py").read_text())
    bound = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        call = node.value
        if (isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "_sweep_stray_oca_groups"):
            bound = True
    assert bound, "_sweep_stray_oca_groups' return value is discarded again"


def test_the_allowlist_entry_ships_with_the_writer():
    """#8778: a soak that cannot be read is worse than no soak."""
    diag = pathlib.Path("src/web/api/routers/diag.py").read_text()
    assert '"stray_oca_soak"' in diag
    assert "stray_oca_soak.jsonl" in diag
