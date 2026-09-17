"""Controls for MI-278 U50 — the payment axis on `fanout_denominator_survey`.

Two things here are worth more than the rest and both are about NOT overclaiming.

**The probe is widened, and a named control keeps it that way.** A narrow probe on
the `order_package_id` COLUMN grades `scripts/ops/dead_leg_audit.py` a false
declaration — it declares `states_package_count` and never names that column — while
the file plainly counts packages by querying the `order_packages` TABLE. The
declaration is right and the probe was wrong. A guard shipped on the narrow probe
would report a correct surface as lying, which is how a guard gets disabled rather
than fixed.

**The positive is not called `paid`.** A whole-file token match is necessary and not
sufficient: the token can sit in a comment, in an unrelated query, or beside the rate
rather than in it. An earlier draft named that state `paid_in_place` and graded 6
surfaces with it, contradicting the module's own docstring. These tests pin the
asymmetry so the name cannot drift back.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
for _p in (str(_ROOT), str(_ROOT / "scripts" / "research")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import fanout_denominator_survey as S  # noqa: E402

ROOT = _ROOT


# ---------------------------------------------------------------- the probe

def test_the_narrow_probe_would_produce_a_false_declaration():
    """The measurement that justifies widening it — asserted, not asserted-about."""
    text = (ROOT / "scripts/ops/dead_leg_audit.py").read_text(errors="replace")
    assert "order_package_id" not in text, "control is vacuous if the column appears"
    assert "order_packages" in text, "the file must really count packages"
    assert S.DECLARED["scripts/ops/dead_leg_audit.py"]["states_package_count"] is True
    # The widened probe sees it; a narrow one would not.
    assert S.package_notion("scripts/ops/dead_leg_audit.py", ROOT) is True


def test_no_declaration_is_contradicted_by_the_code():
    assert S.declaration_contradictions(S.find_candidates(ROOT), ROOT) == []


def test_an_unreadable_file_is_none_never_false():
    """*We could not look* must never render as the sound negative."""
    assert S.package_notion("scripts/research/definitely_not_a_file_xyz.py", ROOT) is None
    assert S.payment_state("scripts/research/definitely_not_a_file_xyz.py",
                           ROOT) == S.PAYMENT_UNREADABLE


# ------------------------------------------------------------ the asymmetry

def test_the_positive_state_is_never_named_paid():
    assert S.NOTION_UNVERIFIED == "package_notion_present_rate_unverified"
    assert "paid" not in S.NOTION_UNVERIFIED
    for name in dir(S):
        assert name != "PAID_IN_PLACE", "the overclaiming state must not come back"


def test_the_four_payment_states_are_distinct():
    states = {S.NOTION_UNVERIFIED, S.REDERIVED_ELSEWHERE,
              S.NO_PACKAGE_NOTION, S.PAYMENT_UNREADABLE}
    assert len(states) == 4


def test_declaration_contradictions_runs_in_one_direction_only():
    """It can refute a package claim. It must never CONFIRM one."""
    for c in S.declaration_contradictions(S.find_candidates(ROOT), ROOT):
        assert c["package_notion"] is False


# ------------------------------------------------- the two unchanged surfaces

@pytest.mark.parametrize("surface,companion", [
    ("scripts/research/bleed_attribution_2026_09_11.py",
     "scripts/research/bleed_record_package_denominator.py"),
    ("scripts/research/e35_break_attribution.py",
     "scripts/research/e35_binomial_package_denominator.py"),
])
def test_a_rederived_surface_is_itself_unchanged(surface, companion):
    """The finding: the companion exists and the producing surface does not know."""
    assert S.package_notion(surface, ROOT) is False, (
        "if this fails the surface was fixed — update the registry, do not widen this")
    assert S.DECLARED[surface]["rederived_by"] == companion
    assert (ROOT / companion).exists(), "the companion must actually be on disk"
    assert S.payment_state(surface, ROOT) == S.REDERIVED_ELSEWHERE


@pytest.mark.parametrize("surface", [
    "scripts/research/bleed_attribution_2026_09_11.py",
    "scripts/research/e35_break_attribution.py",
])
def test_a_rederived_surface_is_still_counted_as_exposed(surface):
    """`rederived_elsewhere` must not quietly remove a surface from the debt."""
    g = S.grade_registry(S.find_candidates(ROOT))
    assert surface in g["price_path_not_deduped"]


def test_a_surface_with_neither_grades_the_sound_negative():
    assert S.payment_state("scripts/research/m20_exit_analysis.py",
                           ROOT) == S.NO_PACKAGE_NOTION


def test_rederived_requires_a_declared_companion_not_just_a_missing_notion():
    """Without the registry pointer the same file must grade `no_package_notion`."""
    surface = "scripts/research/bleed_attribution_2026_09_11.py"
    saved = dict(S.DECLARED[surface])
    try:
        S.DECLARED[surface] = {k: v for k, v in saved.items() if k != "rederived_by"}
        assert S.payment_state(surface, ROOT) == S.NO_PACKAGE_NOTION
    finally:
        S.DECLARED[surface] = saved


# ------------------------------------------------------------------- totals

def test_the_exposed_population_partitions_exactly():
    g = S.grade_registry(S.find_candidates(ROOT))
    exposed = g["price_path_not_deduped"]
    counts = {}
    for p in exposed:
        counts[S.payment_state(p, ROOT)] = counts.get(S.payment_state(p, ROOT), 0) + 1
    assert sum(counts.values()) == len(exposed), "every exposed surface gets exactly one state"
    assert counts.get(S.REDERIVED_ELSEWHERE) == 2
    assert counts.get(S.NO_PACKAGE_NOTION, 0) >= 1


def test_self_test_passes():
    assert S.self_test() == 0
