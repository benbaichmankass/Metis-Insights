"""A lever whose harness never learned the live TP cannot pass a DATE test.

`LEVER_GEOMETRY_CUTOVER` maps a lever to "the date THIS lever's harness started
modelling the live TP". For `regime_flip_exit` that harness
(`m20_flip_replay_sweep.py`) has not started: it calls `base_args` positionally,
so `tp_cap_pct` defaults to 0.0 and `base_args` appends neither `--tp-cap-pct`
nor `--tp-r` for a capped family.

Falling back to the default DATE therefore asserted that column became
live-parity on 2026-08-10, and any cell dated later scored `post_cutover`.
Measured: 42 of the 43 `regime_flip_exit` negatives are capped-family legs, and
6 of them were scoring clean on that date test. `NEVER` is the honest value.

Removing the entry is what marks the harness fixed — not editing a date.

**UPDATE 2026-08-16: `regime_flip_exit` no longer holds the sentinel, and that
is the documented success path, not a regression.** The harness now passes
`tp_cap_pct` (default 0.099) and stamps `tp_geometry`; a live-parity sweep
landed (42 legs, 30 fail / 12 `INERT_NEVER_FLIPPED` / 0 PASS, relay #9536) and
its 41 matching matrix cells carry both the measurement and an explicit
`tp_geometry: live_parity`. So the entry was removed, exactly as this file's
last line prescribes.

The lever-specific assertion below therefore had to go: pinning a test to
"lever X is currently at NEVER" makes it a function of production state, and it
fails on the correct change while reporting nothing about the mechanism. What
this file tests now is the MECHANISM — that the sentinel is honoured, branched
on explicitly rather than by string ordering, and that a lever placed at NEVER
behaves as designed — which keeps testing whether or not any lever holds it
today.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ROLLUP = REPO / "scripts" / "research" / "m20_coverage_rollup.py"

_spec = importlib.util.spec_from_file_location("_rollup", ROLLUP)
rollup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rollup)


def test_a_lever_placed_at_never_resolves_to_the_sentinel() -> None:
    """The mechanism, exercised — not read off whichever lever holds it today.

    Replaces `test_regime_flip_exit_is_marked_never_not_given_a_date`, which
    asserted a production fact that was CORRECTLY changed on 2026-08-16 (see
    the module docstring). A lever must still be placeable at NEVER and resolve
    there, whether or not one currently is.
    """
    saved = dict(rollup.LEVER_GEOMETRY_CUTOVER)
    try:
        rollup.LEVER_GEOMETRY_CUTOVER["_probe_lever"] = rollup.GEOMETRY_CUTOVER_NEVER
        assert rollup.cutover_for("_probe_lever") == rollup.GEOMETRY_CUTOVER_NEVER
    finally:
        rollup.LEVER_GEOMETRY_CUTOVER.clear()
        rollup.LEVER_GEOMETRY_CUTOVER.update(saved)
    assert rollup.LEVER_GEOMETRY_CUTOVER == saved


def test_the_flip_harness_that_earned_the_sentinel_now_passes_the_cap() -> None:
    """Why the entry could be removed. Asserted on the harness, not the map.

    The sentinel's whole justification was that `m20_flip_replay_sweep.py`
    called `base_args` positionally. If that regresses, the entry has to come
    back — so the condition is pinned here rather than left as prose.
    """
    src = (REPO / "scripts/research/m20_flip_replay_sweep.py").read_text()
    assert "base_args(name, cfg, fam, data, resample, a.tp_cap_pct)" in src, (
        "the flip harness is back to a positional base_args call — restore the "
        "regime_flip_exit GEOMETRY_CUTOVER_NEVER entry")
    assert '"--tp-cap-pct", type=float, default=0.099' in src


def test_the_sentinel_is_not_silently_comparable_as_a_date() -> None:
    """The trap this sentinel had to avoid.

    `max(dates) < "NEVER"` is True for every ISO date by string order, so a
    caller that forgot to branch would still mark cells stale — the right answer
    for the wrong reason, which breaks the day the sentinel string changes.
    The comparison must be explicit, so assert the branch exists in source.
    """
    src = ROLLUP.read_text()
    assert "cut == GEOMETRY_CUTOVER_NEVER" in src, (
        "the NEVER case is no longer branched on explicitly; it would be "
        "relying on string ordering"
    )


def test_a_lever_with_a_working_harness_still_gets_its_date() -> None:
    assert rollup.cutover_for("exit_head_ml") == "2026-08-14"
    assert rollup.cutover_for("stale_stop") == rollup.GEOMETRY_CUTOVER
    assert rollup.cutover_for("_unknown_lever") == rollup.GEOMETRY_CUTOVER


def test_never_cells_are_counted_as_geometry_undeclared_too() -> None:
    """We know the PRODUCER was wrong; we still never looked at the cell.

    Folding NEVER cells out of `geometry_undeclared` would let "its harness
    could not have got it right" read as "we checked this cell".
    """
    src = ROLLUP.read_text()
    i = src.index("cut == GEOMETRY_CUTOVER_NEVER")
    branch = src[i:i + 500]
    assert 'out["geometry_undeclared"] += 1' in branch, (
        "NEVER cells no longer count toward geometry_undeclared"
    )
    assert "stale = True" in branch
