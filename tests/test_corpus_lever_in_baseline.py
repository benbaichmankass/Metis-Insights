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
#
# RE-MEASURED 2026-08-17 17:3xZ when the corpus grew 1373 -> 1376 cells: three
# stranded `squeeze_breakout_4h`/`vol_trail` rows were landed off the unmerged
# `claude/m20-sweep-corpus` branch. This assertion did its job — it failed on the
# count, its message said "re-measure the partition rather than loosening this
# assertion", and that is what happened: every constant below was recomputed from
# the corpus, not adjusted until it passed.
#
# All three new rows grade `lever_absent_from_baseline`, so the +3 lands entirely
# in that bucket and the other two are unchanged. That is the CORRECT grade and it
# cross-checks independently: `squeeze_breakout_4h` does not declare `vol_trail`
# (no leg does — `vol_trail` is declared by zero of 55 legs in
# `config/strategies.yaml`), so the lever is genuinely outside the measured
# baseline and these are real measurements rather than self-baselined no-ops.
# `EXPECTED_OWN_LEVER_DROPPED` (41) and the ten zero-delta rows are both unmoved,
# which is the check that the corpus grew without disturbing the finding's
# own population.
#
# RE-MEASURED 2026-09-29 when the corpus grew 1376 -> 1379: three
# `spy_pullback_1h`/`vol_trail` rows (vt_hot90_t2.5, vt_hot80_t2.5,
# vt_cold10_t2.5; run_id 2026-09-28T14:16:04.794618Z) landed via
# `scripts/research/m20_corpus_union.py`, resolving a coverage-matrix
# misclassification (the cell read `passed_unshipped` off a leg-level
# "passing_cells" summary that only meant the weaker `candidate`/IS-OOS
# stage; the per-cell verdict was `wf_fail` on all three independent
# measurements of it, corrected to `honest_negative`). All three rows'
# `declared_levers_present`/`declared_levers_dropped` are `[]` (vol_trail is
# undeclared on every live leg), so they grade `lever_absent_from_baseline`
# -- the +3 lands entirely in that bucket (474 -> 477); `lever_in_baseline`
# (61), `unknown` (841) and `EXPECTED_OWN_LEVER_DROPPED` (41) are all
# unmoved. NOTE: this correction and the 2026-09-29 `ada_pullback_2h`
# correction (1376 -> 1377, PR #14053, still unmerged at the time this was
# written) both grow the SAME baseline independently -- whichever merges
# second needs a rebase and a re-measured total, not a naive sum of the two
# deltas, since a merge can itself deduplicate or reorder rows.
#
# RE-MEASURED 2026-09-29 (same day, a later lane pass) when the corpus grew
# 1379 -> 1382: three `qqq_trend_long_1d`/`vol_trail` rows (vt_hot90_t2,
# vt_hot80_t2, vt_cold10_t2; run_id 2026-08-29T10:47:02.683729Z) landed via
# `scripts/research/m20_corpus_union.py`, resolving a SECOND coverage-matrix
# misclassification: the cell read `passed_unshipped` citing "walk-forward
# 6/6" and "awaiting Tier-3 apply", but the fold-level detail on the one run
# that actually clears MIN_OOS_TRADES=25 (base_oos=40) shows all 6 of 6 folds
# `inert` (d_net_r=0.0 every year) -- wf_wins_effective reads "0/6", the
# opposite of what the summary implied. Status intentionally left
# `passed_unshipped` (matching the existing `gdx_pullback_1d`/`vol_trail`
# precedent in the same file: a cell that mechanically clears the gate at an
# inert cadence stays `passed_unshipped` with the caveat inline, not
# `honest_negative`, since the gate formula genuinely passed). These three
# rows' `declared_levers_present` is `["trail_decay"]` (qqq_trend_long_1d
# does declare trail_decay) but never `"vol_trail"`, so `vol_trail` -- the
# row's own lever -- is still absent from what's declared and all three
# grade `lever_absent_from_baseline` (477 -> 480); `lever_in_baseline` (61),
# `unknown` (841) and `EXPECTED_OWN_LEVER_DROPPED` (41) are all unmoved.
#
# RE-MEASURED 2026-10-02 (lane M20-EXITS-4) when the corpus grew 1382 -> 1413: the
# `m20-exit-lever-sweep` run 37037846659 (run_ids 2026-10-02T17:03:41 / 17:03:50 /
# 17:05:01; legs iaum_pullback_1d, ief_pullback_1d, trend_donchian_xrp_4h, live
# parity tp_cap_pct 0.099) merged 48 cell rows, 17 of which replaced same-key rows
# already in the corpus, so the net is +31. MEASURED by re-running the shipped
# predicate over the committed corpus: lever_in_baseline 61 -> 65 (+4),
# lever_absent_from_baseline 480 -> 507 (+27), unknown 841 unmoved. The exactly-
# zero-delta self-baselined population goes 10 -> 11 and every one still grades
# `lever_in_baseline`.
# 2026-10-04 (RESEARCH-RUN, RQ-20261004-753/654 XRP trail4 confirmation sweeps): +2 cell rows,
# MEASURED by re-running the shipped predicate over the committed corpus:
# lever_absent_from_baseline 507 -> 509 (+2); in_baseline 65 and unknown 841 unmoved; total 1413 -> 1415.
EXPECTED_PARTITION = {
    "lever_in_baseline": 65,
    "lever_absent_from_baseline": 584,
    "unknown": 841,
}
EXPECTED_TOTAL = 1490
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

    All 11 exactly-zero rows must grade `lever_in_baseline` — if any graded
    `absent`, the finding would have been counting a real measurement.
    """
    f = _extract().lever_in_baseline
    zero = [r for r in _cells()
            if r.get("d_net_r_IS") == 0.0 and r.get("d_net_r_OOS") == 0.0
            and r.get("lever") in (r.get("declared_levers_present") or [])]
    assert len(zero) == 11
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
