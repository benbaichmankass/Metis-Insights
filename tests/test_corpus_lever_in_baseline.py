"""A cell row says whether its own lever was already in the measured baseline.

`BL-20260817-A-SHIPPED-LEVER-RE-SWEPT-AGAINST-ITSELF-READS-AS-A-MEASURED-NO-OP`.

Once a lever is DECLARED on a leg the sweep's baseline already runs it, so a
re-sweep returns `d_net_r == 0.0` on both windows under `tie_no_improvement`
with `wf_ran: false`. That is arithmetically correct and completely illegible:
it is byte-identical to a lever that WAS measured and does nothing. On the
committed corpus 10 rows sit in that state while **192** other rows carry the
SAME verdict string with the lever genuinely absent — one string, two opposite
meanings.

⚠️ WHAT THIS FILE MOSTLY EXISTS TO PIN IS THE ORDERING, NOT THE HAPPY PATH.
`dropped` is consulted BEFORE `present`, because "declared" is not "in the
measured baseline": the lever-OFF arm REMOVES a declared lever, and then the
baseline genuinely excludes it and the delta is a REAL measurement. **41 of the
1373 corpus rows are in exactly that state**, and in all 41 the dropped lever is
the row's own. The naive two-field predicate (`lever in present`) mislabels
those 41 genuine measurements as structurally meaningless — including
`gld_pullback_1d/shipped_trail_decay_5.06_10_2` at `d_net_r_IS +19.1782`.

That is the MIRROR of the defect the field exists to fix. The bug reads an
artifact as a measurement; the naive fix reads 41 measurements as artifacts. A
future edit that "simplifies" the helper to two fields would reintroduce it
while every happy-path test still passed, which is why `test_dropped_is_checked_
before_present` and `test_the_shipped_predicate_is_not_the_naive_one` are
separate assertions rather than one.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORPUS = REPO / "docs" / "research" / "m20-sweep-corpus.jsonl"

# Measured 2026-08-17 over the committed corpus. Pinned so a silent change to
# the predicate shows up as a diff in a number, not as prose nobody re-derives.
EXPECTED_PARTITION = {
    "lever_in_baseline": 61,
    "lever_absent_from_baseline": 471,
    "unknown": 841,
}
EXPECTED_TOTAL = 1373
# Rows where the row's own lever was DROPPED — the population the naive
# predicate gets wrong, and the reason `dropped` is consulted first.
EXPECTED_OWN_LEVER_DROPPED = 41


def _extract():
    spec = importlib.util.spec_from_file_location(
        "_corpus_extract_probe", REPO / "scripts" / "research" / "m20_corpus_extract.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_corpus_extract_probe"] = mod
    spec.loader.exec_module(mod)
    return mod


def _cells():
    return [r for r in (json.loads(x) for x in CORPUS.read_text().splitlines() if x.strip())
            if r.get("kind") == "cell"]


# ---------------------------------------------------------------------------
# 1. THE ORDERING. This is the load-bearing case.

def test_dropped_is_checked_before_present():
    """A DECLARED lever that was DROPPED is ABSENT from the measured baseline.

    If this ever reads `lever_in_baseline`, the predicate has collapsed back to
    two fields and 41 real measurements are being suppressed as artifacts.
    """
    f = _extract().lever_in_baseline
    assert f("trail_decay", ["trail_decay"], ["trail_decay"]) == \
        "lever_absent_from_baseline"


def test_a_different_lever_being_dropped_does_not_clear_my_own():
    """Only the ROW'S OWN lever matters — a sibling's removal is irrelevant."""
    f = _extract().lever_in_baseline
    assert f("trail_decay", ["trail_decay"], ["stale_stop"]) == "lever_in_baseline"


def test_the_shipped_predicate_is_not_the_naive_one():
    """Measured on the real corpus: the two disagree on exactly the 41 rows.

    Stated as a COUNT rather than a boolean so a partial regression (someone
    special-cases one lever) cannot pass.
    """
    f = _extract().lever_in_baseline
    cells = _cells()

    def naive(r):
        pres = r.get("declared_levers_present")
        if not isinstance(pres, list):
            return "unknown"
        return ("lever_in_baseline" if r.get("lever") in pres
                else "lever_absent_from_baseline")

    disagree = [r for r in cells
                if f(r.get("lever"), r.get("declared_levers_present"),
                     r.get("declared_levers_dropped")) != naive(r)]
    assert len(disagree) == EXPECTED_OWN_LEVER_DROPPED, (
        f"expected the naive predicate to differ on {EXPECTED_OWN_LEVER_DROPPED} "
        f"rows, got {len(disagree)}")
    # Every disagreement must be the dropped case, in the safe direction.
    for r in disagree:
        assert r.get("lever") in (r.get("declared_levers_dropped") or [])
        assert f(r.get("lever"), r.get("declared_levers_present"),
                 r.get("declared_levers_dropped")) == "lever_absent_from_baseline"


# ---------------------------------------------------------------------------
# 2. "WE DID NOT LOOK" IS NOT "ABSENT".

def test_absent_declared_set_is_unknown_not_absent():
    """A pre-field row is unknowable. Grading it `absent` would assert that the
    baseline definitely excluded the lever, which was never measured."""
    f = _extract().lever_in_baseline
    assert f("trail_decay", None, None) == "unknown"
    assert f("trail_decay", "not-a-list", None) == "unknown"


def test_an_empty_declared_list_is_absent_not_unknown():
    """An EMPTY list is a real answer — we looked, the leg declares nothing."""
    f = _extract().lever_in_baseline
    assert f("trail_decay", [], None) == "lever_absent_from_baseline"


# ---------------------------------------------------------------------------
# 3. THE MEASURED PARTITION, so a silent behaviour change is a failing number.

def test_partition_over_the_committed_corpus():
    f = _extract().lever_in_baseline
    cells = _cells()
    assert len(cells) == EXPECTED_TOTAL, (
        "the corpus grew or shrank — re-measure the partition rather than "
        "loosening this assertion")
    got = Counter(f(r.get("lever"), r.get("declared_levers_present"),
                    r.get("declared_levers_dropped")) for r in cells)
    assert dict(got) == EXPECTED_PARTITION


def test_the_ten_zero_delta_rows_are_all_genuinely_self_baselined():
    """The finding's own population, re-checked through the shipped predicate.

    All 10 exactly-zero rows must grade `lever_in_baseline` — if any graded
    `absent`, the finding would have been counting a real measurement.
    """
    f = _extract().lever_in_baseline
    zero = [r for r in _cells()
            if r.get("d_net_r_IS") == 0.0 and r.get("d_net_r_OOS") == 0.0
            and r.get("lever") in (r.get("declared_levers_present") or [])]
    assert len(zero) == 10
    assert all(f(r.get("lever"), r.get("declared_levers_present"),
                 r.get("declared_levers_dropped")) == "lever_in_baseline"
               for r in zero)


def test_every_state_is_reachable_from_the_real_corpus():
    """A three-state field with a state no real row reaches is two states with
    extra prose. All three must actually occur."""
    f = _extract().lever_in_baseline
    got = {f(r.get("lever"), r.get("declared_levers_present"),
             r.get("declared_levers_dropped")) for r in _cells()}
    assert got == set(EXPECTED_PARTITION), f"unreachable state(s): {set(EXPECTED_PARTITION) - got}"
