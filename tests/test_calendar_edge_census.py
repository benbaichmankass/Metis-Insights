"""Tests for the MI-218 calendar-time-to-edge census.

The invariants here are the ones that decide whether the census's NUMBER can be
believed, so each pins a specific way the probe could have lied:

* a probe that cannot look must say so, never report ``0``;
* the positive control must GATE the count, not decorate it;
* the widened verdict predicate must actually see what check D cannot, proven
  against a synthetic control rather than asserted;
* the prose classifier must keep ``edge`` / ``mechanics`` / ``both`` /
  ``unclassified`` apart.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ops import calendar_edge_census as cec  # noqa: E402


# ---------------------------------------------------------------------------
# "We could not look" is not "we looked and found nothing".
# ---------------------------------------------------------------------------

def test_absent_detector_yields_none_not_zero(monkeypatch):
    """The whole census refuses a number when check D is unavailable.

    Reporting ``0`` here would manufacture exactly the clean result the census
    exists to distrust — and it is the reachable state today, because check D
    is still landing in #11529.
    """
    monkeypatch.setattr(cec, "DETECTOR_AVAILABLE", False)
    census = cec.Census(detector_state="absent")
    cec.run_code_passes(census)
    assert census.pass_a is None
    assert census.pass_b is None
    assert census.pass_a != 0 and census.pass_b != 0


def test_report_refuses_to_print_a_count_without_the_control(capsys):
    """A count is not printed unless the M7 instance was rediscovered."""
    census = cec.Census(detector_state="available", pass_a=[], pass_b=[])
    census.control_found = False
    rc = cec.report(census)
    out = capsys.readouterr().out
    assert rc == 2, "a missing control must be a non-zero exit, not a footnote"
    assert "CONTROL NOT ESTABLISHED" in out
    assert "PASS A" not in out, "no count may be printed once the control failed"


def test_report_prints_counts_once_the_control_holds(capsys):
    census = cec.Census(detector_state="available", pass_a=[], pass_b=[])
    census.control_found = True
    assert cec.report(census) == 0
    out = capsys.readouterr().out
    assert "PASS A" in out and "PASS B" in out


# ---------------------------------------------------------------------------
# The widened verdict predicate — the third hole, pinned by control.
# ---------------------------------------------------------------------------

ATTR_TARGET = """
def gate(x):
    d = Obj()
    n = x.n_closed
    if n < MIN_CLOSED_FOR_ACTION:
        d.action = "hold"
    return d
"""

NAME_TARGET = """
def gate(x):
    n = x.n_closed
    if n < MIN_CLOSED_FOR_ACTION:
        verdict = "hold"
    return verdict
"""

RETURN_TARGET = """
def gate(x):
    n = x.n_closed
    if n < MIN_CLOSED_FOR_ACTION:
        return "hold"
    return "kill"
"""

SUBSCRIPT_TARGET = """
def gate(x):
    out = {}
    n = x.n_closed
    if n < MIN_CLOSED_FOR_ACTION:
        out["verdict"] = "hold"
    return out
"""


def _assigns(src: str) -> bool:
    import ast

    tree = ast.parse(src)
    fn = tree.body[0]
    for node in ast.walk(fn):
        if isinstance(node, ast.If):
            return cec._widened_assigns_verdict(node.body)
    raise AssertionError("no if-branch in fixture")


@pytest.mark.parametrize(
    "src", [ATTR_TARGET, NAME_TARGET, RETURN_TARGET, SUBSCRIPT_TARGET]
)
def test_widened_predicate_sees_every_verdict_shape(src):
    assert _assigns(src) is True


def test_widened_predicate_does_not_fire_on_an_unrelated_assignment():
    src = """
def gate(x):
    n = x.n_closed
    if n < MIN_CLOSED_FOR_ACTION:
        total = n + 1
    return total
"""
    assert _assigns(src) is False


@pytest.mark.skipif(
    not cec.DETECTOR_AVAILABLE, reason="check D (#11529) not landed in this tree"
)
def test_check_d_misses_the_name_target_that_the_census_catches():
    """The measured basis for Pass B existing at all.

    check D's shipped ``_assigns_verdict`` matches only an ``ast.Attribute``
    target, so an identical accrual-gated branch is FOUND with ``d.action =``
    and MISSED with ``verdict =``. If this ever starts passing on the name
    target, check D has been widened and Pass B's rationale must be revisited.
    """
    import scripts.check_soak_doctrine as csd

    assert csd.find_accrual_gated_verdicts(ATTR_TARGET, ("gate",)), (
        "control: check D must see the attribute-target branch"
    )
    assert csd.find_accrual_gated_verdicts(NAME_TARGET, ("gate",)) == []


# ---------------------------------------------------------------------------
# The prose classifier keeps its four states apart.
# ---------------------------------------------------------------------------

def test_prose_classifier_states_are_not_collapsed():
    edge = cec._classify_text("s", "r", "f", "at least 20 graded rows before the expectancy is trusted")
    mech = cec._classify_text("s", "r", "f", "a soak row observed after the order reached the venue")
    both = cec._classify_text("s", "r", "f", "soak until the PnL is measured and the order is placed")
    none = cec._classify_text("s", "r", "f", "wait for the operator to reply")
    assert edge is not None and edge.klass == "edge"
    assert mech is not None and mech.klass == "mechanics"
    assert both is not None and both.klass == "both"
    assert none is not None and none.klass == "unclassified"


def test_prose_classifier_ignores_text_with_no_accrual_requirement():
    """No accrual language means no candidate — the classifier is not a
    general edge-detector, and must not return a hit it cannot justify."""
    assert cec._classify_text("s", "r", "f", "the leg's expectancy is negative") is None


def test_positive_control_target_is_the_known_m7_instance():
    """Pinned so the control cannot be quietly retargeted at something easier."""
    assert cec.POSITIVE_CONTROL == ("scripts/ml/strategy_review_packet.py", "decide")


def test_census_is_read_only():
    """Tier-1: the census must not write. Pinned so a later edit cannot make it
    a mutation path without this test failing."""
    src = (ROOT / "scripts" / "ops" / "calendar_edge_census.py").read_text()
    for banned in ("write_text(", "open(", "os.remove", "subprocess", "shutil"):
        assert banned not in src, f"census must stay read-only; found {banned!r}"
