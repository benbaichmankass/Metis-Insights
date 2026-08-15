"""The mover rate the research doc quotes must be the one the data computes.

`docs/research/m20-fold-dispersion-2026-08-15.md` states the fold-dispersion
headline in prose. `scripts/research/m20_dispersion_rate.py` derives it from
`docs/research/m20-fold-dispersion-arms-consolidated.jsonl`. If those two drift,
the document becomes a claim nothing re-derives — which is exactly the defect
the consolidated record was built to fix, and which had ALREADY happened once:
the doc's `per_leg` denominator read 2 where the data says 3, because a leg
screened in `unanimity2` was never counted, and that error invalidated a power
calculation built on top of it.

⚠️ THE RATE DEPENDS ON A DEDUP RULE, and the two rules differ by 7.4 points for
`family_pooled` (any-screen 33.3 % vs every-screen 25.9 %). So a test that
asserted "the doc says 33.3 %" without pinning WHICH rule produced it would pass
against a document that had quietly switched rules. Both are pinned.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
# Kept on ONE line — `test_pytest_run_filter.py` scans tests/ for a docs/ path
# joined onto the repo root, and a wrapped join truncates to the directory.
DOC = REPO / "docs" / "research" / "m20-fold-dispersion-2026-08-15.md"
ARMS = REPO / "docs" / "research" / "m20-fold-dispersion-arms-consolidated.jsonl"
SCRIPT = REPO / "scripts" / "research" / "m20_dispersion_rate.py"


def _rates() -> dict:
    spec = importlib.util.spec_from_file_location("m20_dispersion_rate", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["m20_dispersion_rate"] = mod
    spec.loader.exec_module(mod)
    import json
    rows = [json.loads(x) for x in ARMS.read_text().splitlines() if x.strip()]
    return mod.rates(rows)


# --------------------------------------------------------------------------
# Positive controls first — a negative needs a denominator.
# --------------------------------------------------------------------------

def test_the_record_is_non_trivial() -> None:
    r = _rates()
    assert r["rows"] >= 200, (
        f"the consolidated arms record has only {r['rows']} rows; every "
        "assertion below would be over a population too small to mean anything")
    assert r["distinct_legs"] >= 25, r["distinct_legs"]


def test_the_two_dedup_rules_actually_DIFFER() -> None:
    """If they ever coincide, this module's whole premise needs re-deriving.

    The reason both rules are printed and pinned is that they disagree. A change
    that made them identical would silently make half of this file vacuous.
    """
    r = _rates()
    a = r["by_rule"]["any_screen"]["total"]["movers"]
    e = r["by_rule"]["every_screen"]["total"]["movers"]
    assert a != e, (
        "the any-screen and every-screen rules now agree, so the distinction "
        "this module exists to pin has collapsed — re-read the record before "
        "deleting anything")


# --------------------------------------------------------------------------
# The rule.
# --------------------------------------------------------------------------

def test_the_doc_quotes_the_ANY_SCREEN_rate_the_data_computes() -> None:
    r = _rates()
    fam = r["by_rule"]["any_screen"]["by_block_unit"]["family_pooled"]
    tot = r["by_rule"]["any_screen"]["total"]
    doc = DOC.read_text()

    assert f"{fam['movers']}/{fam['legs']}" in doc, (
        f"the doc no longer quotes the family_pooled any-screen rate "
        f"{fam['movers']}/{fam['legs']} that the data computes — one of the two "
        "moved and they must not drift")
    assert f"{tot['movers']} of {tot['legs']}" in doc or \
           f"{tot['movers']}/{tot['legs']}" in doc, (
        f"the doc no longer quotes the all-leg any-screen rate "
        f"{tot['movers']}/{tot['legs']}")


def test_the_doc_quotes_the_EVERY_SCREEN_rate_too() -> None:
    """Pinned separately, because quoting only one rule hides the choice."""
    r = _rates()
    fam = r["by_rule"]["every_screen"]["by_block_unit"]["family_pooled"]
    doc = DOC.read_text()
    assert f"{fam['movers']}/{fam['legs']}" in doc, (
        f"the doc does not quote the stricter every-screen family_pooled rate "
        f"{fam['movers']}/{fam['legs']}. Reporting only the any-screen figure "
        "silently picks a side of a 7.4-point choice")


def test_the_disagreeing_legs_are_NAMED_in_the_doc() -> None:
    """The second-order finding must survive an edit that tidies the prose.

    That 'does this leg move' is itself unstable is the reason neither rate is
    authoritative. A doc that dropped the two leg names would still quote
    correct numbers while losing why they are uncertain.
    """
    # SCOPED TO THE SECTION, not the whole document. The first version searched
    # the entire file, and every one of these leg names appears in a dozen other
    # tables — so it passed even with the disagreement section gutted. Proven by
    # planting: replacing the leg name on the disagreement line did NOT fail the
    # test. A guard that cannot fail is not a guard, and this file already says
    # so about the rules it pins; it should not exempt itself.
    r = _rates()
    doc = DOC.read_text()
    anchor = "mover verdict itself"
    lowered = doc.lower()
    assert anchor in lowered, (
        "the section discussing cross-screen mover disagreement is gone from "
        "the doc, so its second-order caveat has been lost entirely")
    i = lowered.index(anchor)
    section = doc[i:i + 1200]   # the disagreement block, not the whole file
    missing = [leg for leg in r["legs_whose_mover_verdict_disagrees"]
               if leg not in section]
    assert not missing, (
        f"legs whose mover verdict disagrees across screens are not named in "
        f"the disagreement section: {missing}. The rate is one draw of a "
        "statistic with its own dispersion, and that is only legible if the "
        "cases are named where the caveat is made")


def test_single_arm_legs_are_EXCLUDED_not_counted_as_non_movers() -> None:
    """A leg measured at one offset cannot move; counting it deflates the rate."""
    r = _rates()
    assert r["screen_leg_pairs_excluded_single_arm"] > 0, (
        "no single-arm pairs were excluded — either the record changed shape or "
        "the exclusion stopped happening, and the second would silently deflate "
        "every rate this script prints")
    assert r["distinct_legs"] < r["rows"], "sanity: legs cannot exceed rows"
