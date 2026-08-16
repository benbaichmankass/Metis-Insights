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


def _pin_never(lever: str):
    """Context-manager-ish helper: pin a lever at NEVER, yielding the restore."""
    saved = dict(RU.LEVER_GEOMETRY_CUTOVER)
    RU.LEVER_GEOMETRY_CUTOVER[lever] = RU.GEOMETRY_CUTOVER_NEVER

    def restore():
        RU.LEVER_GEOMETRY_CUTOVER.clear()
        RU.LEVER_GEOMETRY_CUTOVER.update(saved)
    return restore


def test_a_never_lever_is_not_labelled_re_runnable() -> None:
    """The finding itself — exercised, not observed.

    This used to assert that some lever was pinned at NEVER on the live config
    and then check those cells. That made the test a function of production
    state, and on 2026-08-16 it did what such a test always eventually does:
    `regime_flip_exit`'s entry was legitimately REMOVED (harness fixed, sweep
    landed) and the test failed on a correct change, with a message about a
    missing positive case rather than about the logic.

    So the NEVER lever is now INJECTED. The behaviour under test is the state
    machine, not today's `LEVER_GEOMETRY_CUTOVER` contents, and this keeps
    testing after every future harness fix.
    """
    st = RU.stale_corpus_state(MATRIX)
    if not st.get("available"):
        import pytest
        pytest.skip(f"corpus unavailable: {st.get('why')}")
    # Pick a lever that actually HAS stale cells, so the injection has bite.
    from collections import Counter
    counts = Counter(r["lever"] for r in st["rows"])
    assert counts, "no stale cells at all — nothing to classify"
    lever = counts.most_common(1)[0][0]

    restore = _pin_never(lever)
    try:
        after = RU.stale_corpus_state(MATRIX)
        mislabelled = [r for r in after["rows"]
                       if r["lever"] == lever and r["state"] == RU.CORPUS_NO_ROW]
        assert not mislabelled, (
            f"{len(mislabelled)} cell(s) in the pinned-NEVER lever `{lever}` "
            f"are still labelled re-runnable")
        assert after["counts"].get(RU.CORPUS_HARNESS_UNFIXED, 0) >= 1, (
            f"pinning `{lever}` at NEVER produced no harness-unfixed cells")
    finally:
        restore()
    assert RU.stale_corpus_state(MATRIX)["counts"] == st["counts"], (
        "the injection leaked — later tests would run against a mutated map")


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
    from collections import Counter
    counts = Counter(r["lever"] for r in st["rows"])
    assert counts, "no stale cells at all — nothing to clear"
    # Same injection as above: pin a lever, confirm it is held stale, then
    # remove the entry (which is how a harness fix is marked) and confirm the
    # cells return on their own. Injected rather than read off the live config
    # for the reason recorded on `test_a_never_lever_is_not_labelled_re_runnable`.
    lever = counts.most_common(1)[0][0]
    saved = dict(RU.LEVER_GEOMETRY_CUTOVER)
    try:
        RU.LEVER_GEOMETRY_CUTOVER[lever] = RU.GEOMETRY_CUTOVER_NEVER
        held = RU.stale_corpus_state(MATRIX)["counts"]
        before = held.get(RU.CORPUS_HARNESS_UNFIXED, 0)
        assert before > 0, f"pinning `{lever}` held nothing stale"

        del RU.LEVER_GEOMETRY_CUTOVER[lever]
        after = RU.stale_corpus_state(MATRIX)["counts"]
    finally:
        RU.LEVER_GEOMETRY_CUTOVER.clear()
        RU.LEVER_GEOMETRY_CUTOVER.update(saved)

    assert after.get(RU.CORPUS_HARNESS_UNFIXED, 0) == 0, (
        "removing the entry left cells held as harness-unfixed")
    # RESTORED EXACTLY, not "returned to the re-runnable bucket". The earlier
    # assertion demanded the cells land in `no_live_parity_row`, which is true
    # only for a lever that HAS a producer; the most-stale lever today is
    # `exit_ladder`, which correctly returns to
    # `no_sweep_driver_emits_this_column` instead. Conservation is the real
    # self-clearing property and it holds for either kind.
    assert after == st["counts"], (
        f"the state distribution did not return to its starting shape: "
        f"{after} vs {st['counts']}")
    assert before > 0  # the injection did something, so the restore proves something
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


# ------------------------------------- …and the one with no producer at all

def test_the_declared_producer_set_matches_what_cells_for_ACTUALLY_emits() -> None:
    """Declared, not introspected — so it must be checked against reality.

    Regexing `cells_for`'s source for lever literals would be a probe adjacent
    to the question (sub-class A). This CALLS it over every live strategy and
    compares the levers it really emits, so the declared set cannot drift into
    a comforting fiction.
    """
    import yaml
    sys.path.insert(0, str(REPO / "scripts" / "research"))
    import m20_fleet_exit_sweep as S

    cfgs = (yaml.safe_load((REPO / "config/strategies.yaml").read_text())
            or {}).get("strategies") or {}
    emitted = set()
    for name, cfg in cfgs.items():
        if not isinstance(cfg, dict):
            continue
        fam = S.classify(name)
        if fam is None:
            continue
        for _tag, lever, _extra in S.cells_for(cfg, fam):
            if lever:
                emitted.add(lever)
    assert emitted, "cells_for emitted nothing — the probe found no positive"
    assert emitted == set(RU.COLUMNS_WITH_A_SWEEP_PRODUCER), (
        f"declared {sorted(RU.COLUMNS_WITH_A_SWEEP_PRODUCER)} but cells_for "
        f"emits {sorted(emitted)}")


def test_every_lever_column_is_accounted_for() -> None:
    """No column may fall between the two sets — that is how one goes unnoticed."""
    cols = set(MATRIX["lever_columns"])
    covered = (set(RU.COLUMNS_WITH_A_SWEEP_PRODUCER)
               | set(RU._COLUMNS_WITH_THEIR_OWN_DRIVER))
    orphans = cols - covered
    assert orphans == {"exit_ladder"}, (
        f"the set of driverless columns changed: {sorted(orphans)}. If a "
        f"producer was built, move that column into one of the two sets; if a "
        f"new column was added, classify it.")


def test_a_column_with_its_own_driver_is_not_called_driverless() -> None:
    """regime_flip_exit HAS a driver; it is broken, which is a different state."""
    st = RU.stale_corpus_state(MATRIX)
    if not st.get("available"):
        import pytest
        pytest.skip("corpus unavailable")
    for r in st["rows"]:
        if r["state"] == RU.CORPUS_NO_PRODUCER:
            assert r["lever"] not in RU._COLUMNS_WITH_THEIR_OWN_DRIVER, r
            assert r["lever"] not in RU.COLUMNS_WITH_A_SWEEP_PRODUCER, r


def test_the_three_no_newer_evidence_states_partition_cleanly() -> None:
    st = RU.stale_corpus_state(MATRIX)
    if not st.get("available"):
        import pytest
        pytest.skip("corpus unavailable")
    trio = {RU.CORPUS_NO_ROW, RU.CORPUS_HARNESS_UNFIXED, RU.CORPUS_NO_PRODUCER}
    assert len(trio) == 3, "two of the three states collapsed to one string"
    assert sum(st["counts"].values()) == len(st["rows"])
