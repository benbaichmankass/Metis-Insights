"""A round's evidence row must record the ORDERED leg set it trained over.

BL-20260815-EXIT-HEAD-VERDICT-DEPENDS-ON-LEG-ARGUMENT-ORDER. `--legs` order sets
the row order in `rows.jsonl`, which sets the tie-break in a STABLE sort over
`bars[0]["bar_t"]` (`train_exit_head.py:518`). On a 2h family every leg entering
on the same bar shares that timestamp, so the tie groups span every pooled leg
and the argument order moves fold membership.

Measured: the same 7 legs in two orders produced identical trade counts (2220),
identical rows (71199) and an identical 43x50 fold shape — yet 8 of 43 folds
differed, AUC moved up to 0.0331, and two legs lost a usable fold.

Recording the order does not fix it. It makes two rows that differ by it
DETECTABLE, which they were not: the order lived only in
`round_report.json::_round_meta`, no consumer compared it, and the committed
evidence row carried nothing. These tests are that consumer's contract.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "research"))
sys.path.insert(0, str(REPO / "scripts" / "ml"))

from build_exit_head_dataset import family_of  # noqa: E402


def _row_pooled_legs(leg: str, fam_name: str, ordered_legs: list[str]) -> list[str]:
    """The expression under test, lifted verbatim from m20_exit_head_round.py."""
    return ([leg] if family_of(leg) == leg
            else [x for x in ordered_legs if family_of(x) == fam_name])


def test_the_expression_is_the_one_the_emitter_uses() -> None:
    """Pin the helper to the emitter's source, so the two cannot drift.

    A hand-copied predicate that silently diverges from the producer is exactly
    how a guard starts passing against a fiction.
    """
    src = (REPO / "scripts" / "research" / "m20_exit_head_round.py").read_text()
    assert '"pooled_legs_ordered": (' in src, "emitter no longer stamps the field"
    assert "[leg] if _family_of(leg) == leg" in src
    assert 'else [x for x in meta["legs"] if _family_of(x) == fam_name]' in src


def test_order_is_PRESERVED_not_sorted() -> None:
    """The whole point. A sorted copy loses exactly the information recorded."""
    typed = ["eth_pullback_2h", "eth_pullback_prop_2h", "sol_pullback_2h",
             "xrp_pullback_2h", "ada_pullback_2h", "avax_pullback_2h",
             "htf_pullback_trend_2h"]
    got = _row_pooled_legs("sol_pullback_2h", "pullback", typed)
    assert got == typed, got
    assert got != sorted(typed), (
        "the stamp was sorted — it would then be identical for the two real "
        "orders that produced different fold membership, and could not "
        "distinguish them")


def test_the_two_REAL_orders_are_distinguishable_by_this_field() -> None:
    """The measured pair from relay #9406 — the case the field exists for."""
    recorded = ["eth_pullback_2h", "eth_pullback_prop_2h", "sol_pullback_2h",
                "xrp_pullback_2h", "ada_pullback_2h", "avax_pullback_2h",
                "htf_pullback_trend_2h"]
    arm = sorted(recorded)
    a = _row_pooled_legs("sol_pullback_2h", "pullback", recorded)
    b = _row_pooled_legs("sol_pullback_2h", "pullback", arm)
    assert set(a) == set(b), "same membership — that was never in doubt"
    assert a != b, (
        "these two rows must NOT compare equal: they are the orders that "
        "produced 8 differing folds and a 0.0331 AUC move")


def test_a_per_leg_row_records_ITSELF_not_null() -> None:
    """`per_leg` is immune, but immune is not the same as unrecorded.

    Null would conflate "this row cannot be affected" with "we did not look",
    which is the collapsed-state failure this repo has a guard for.
    """
    got = _row_pooled_legs("ict_scalp_sol_15m", "ict_scalp_sol_15m",
                           ["ict_scalp_sol_15m", "ict_scalp_xrp_15m"])
    assert got == ["ict_scalp_sol_15m"], got
    assert got is not None


def test_a_pooled_row_excludes_legs_from_OTHER_families() -> None:
    """A round can pass several families at once; only same-family legs share
    the sort, so only they belong in the stamp."""
    mixed = ["eth_pullback_2h", "trend_donchian_eth_4h", "sol_pullback_2h"]
    got = _row_pooled_legs("eth_pullback_2h", "pullback", mixed)
    assert "trend_donchian_eth_4h" not in got, got
    assert got == ["eth_pullback_2h", "sol_pullback_2h"], got
