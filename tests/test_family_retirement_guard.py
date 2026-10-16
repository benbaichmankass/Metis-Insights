"""family-retirement-guard: a FAMILY cannot be retired on config FAILs alone (operator 2026-10-16)."""
from pathlib import Path

from scripts.research import family_retirement_guard as g

REPO = Path(__file__).resolve().parents[1]


def test_self_test():
    assert g._self_test() == 0


def test_main_tree_has_no_unbacked_structured_retirement():
    assert [p for p in g.check(REPO, None) if p.startswith(("R1", "R2"))] == []


def test_policy_is_loadable_and_provisional():
    pol = g.load_policy(REPO)
    assert pol["provisional"] is True and pol["minimums"]["outside_sources"] >= 1
    assert pol["retiring_verdicts"] == ["C"]                       # A (tested wrong) and B never retire
    assert len(pol["test_design_checks"]) == 6 and len(pol["not_tested_dimensions"]) == 9
