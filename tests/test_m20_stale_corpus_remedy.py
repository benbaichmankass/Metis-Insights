"""A state that names a remedy must not name it for cells the remedy can't fix.

`stale_corpus_state` sorts every stale cell by whether the corpus already
answers it, and `no_live_parity_row` carries an instruction — *"nothing newer;
a re-run IS the remedy"*. That instruction is the useful part and it was wrong
for a third of the rows it covered.

A lever pinned at `GEOMETRY_CUTOVER_NEVER` has a harness that does not model the
live take-profit. Sweeping such a cell produces ANOTHER stale row: the remedy is
to fix the harness first. Measured on the committed matrix the day this test was
written, **38 of the 99** `no_live_parity_row` cells were `regime_flip_exit` —
the single largest block in the backlog, every one of them pointed at hours of
trainer time that could not have cleared it.

The distinction is derived from `cutover_for(lever)`, never a hardcoded lever
list, so it clears itself: deleting a lever's `LEVER_GEOMETRY_CUTOVER` entry —
which is how a harness fix is marked — returns its cells to `no_live_parity_row`
with no edit to the state logic. That self-clearing property is itself pinned
below, because a list someone has to remember to update is the failure mode this
replaces.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "m20_coverage_rollup", REPO / "scripts/research/m20_coverage_rollup.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["m20_coverage_rollup"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


RU = _load()
MATRIX = RU.load(REPO / "docs/research/exit-refinement-coverage.json")


def test_the_two_no_newer_evidence_states_are_distinct() -> None:
    assert RU.CORPUS_HARNESS_UNFIXED != RU.CORPUS_NO_ROW


def test_a_never_lever_is_not_labelled_re_runnable() -> None:
    """The whole finding, on the live matrix."""
    st = RU.stale_corpus_state(MATRIX)
    if not st.get("available"):
        import pytest
        pytest.skip(f"corpus unavailable: {st.get('why')}")
    never = {lv for lv in MATRIX["lever_columns"]
             if RU.cutover_for(lv) == RU.GEOMETRY_CUTOVER_NEVER}
    assert never, "no lever is pinned at NEVER — this test has no positive case"
    mislabelled = [r for r in st["rows"]
                   if r["lever"] in never and r["state"] == RU.CORPUS_NO_ROW]
    assert not mislabelled, (
        f"{len(mislabelled)} cell(s) in a NEVER lever are labelled re-runnable: "
        f"{sorted({(r['leg'], r['lever']) for r in mislabelled})[:5]}")


def test_the_unfixed_state_is_ONLY_used_for_never_levers() -> None:
    """The other half — a caveat on everything is a caveat on nothing."""
    st = RU.stale_corpus_state(MATRIX)
    if not st.get("available"):
        import pytest
        pytest.skip("corpus unavailable")
    for r in st["rows"]:
        if r["state"] == RU.CORPUS_HARNESS_UNFIXED:
            assert RU.cutover_for(r["lever"]) == RU.GEOMETRY_CUTOVER_NEVER, r


def test_every_stale_cell_still_lands_in_exactly_one_state() -> None:
    """Adding a state must not drop rows or double-count them."""
    st = RU.stale_corpus_state(MATRIX)
    if not st.get("available"):
        import pytest
        pytest.skip("corpus unavailable")
    assert sum(st["counts"].values()) == len(st["rows"])
    assert len(st["rows"]) == len(RU.evidence_vintage(MATRIX)["stale_cells"])


def test_fixing_a_harness_clears_the_caveat_with_no_edit_here() -> None:
    """Self-clearing: remove the cutover entry, the cells become re-runnable.

    Simulates the harness fix by dropping the lever's entry, which is exactly
    how `LEVER_GEOMETRY_CUTOVER` documents the fix being marked.
    """
    st = RU.stale_corpus_state(MATRIX)
    if not st.get("available"):
        import pytest
        pytest.skip("corpus unavailable")
    before = st["counts"].get(RU.CORPUS_HARNESS_UNFIXED, 0)
    assert before > 0, "no NEVER-lever cells today — nothing to clear"

    saved = dict(RU.LEVER_GEOMETRY_CUTOVER)
    try:
        for lv, v in list(RU.LEVER_GEOMETRY_CUTOVER.items()):
            if v == RU.GEOMETRY_CUTOVER_NEVER:
                del RU.LEVER_GEOMETRY_CUTOVER[lv]
        after = RU.stale_corpus_state(MATRIX)["counts"]
    finally:
        RU.LEVER_GEOMETRY_CUTOVER.clear()
        RU.LEVER_GEOMETRY_CUTOVER.update(saved)

    assert after.get(RU.CORPUS_HARNESS_UNFIXED, 0) == 0
    assert after.get(RU.CORPUS_NO_ROW, 0) >= before, (
        "the cells did not return to the re-runnable bucket")
    # And the mutation really was undone.
    assert RU.LEVER_GEOMETRY_CUTOVER == saved


def test_the_printed_line_says_NOT_the_remedy() -> None:
    """The count is only useful if the reader is told what to do instead.

    Asserted on the RENDERED output, not on the source: the sentence is built
    across several f-string literals, and grepping the file for the joined
    phrase tests the line breaks rather than what a reader sees.
    """
    import subprocess
    p = subprocess.run(
        [sys.executable, str(REPO / "scripts/research/m20_coverage_rollup.py"),
         "--stale-corpus-state"],
        capture_output=True, text=True, timeout=300, cwd=REPO)
    assert p.returncode == 0, p.stderr[-500:]
    out = " ".join(p.stdout.split())          # collapse the wrapping
    if RU.CORPUS_HARNESS_UNFIXED not in out:
        import pytest
        pytest.skip("no NEVER-lever stale cells to render today")
    assert "A RE-RUN IS **NOT** THE REMEDY" in out
    assert "Fix the harness, THEN sweep" in out
    assert "regime_flip_exit" in out, "the caveat does not name the lever"
