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
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ROLLUP = REPO / "scripts" / "research" / "m20_coverage_rollup.py"

_spec = importlib.util.spec_from_file_location("_rollup", ROLLUP)
rollup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rollup)


def test_regime_flip_exit_is_marked_never_not_given_a_date() -> None:
    assert rollup.cutover_for("regime_flip_exit") == rollup.GEOMETRY_CUTOVER_NEVER, (
        "regime_flip_exit is back on a date cutover; its harness still calls "
        "base_args positionally, so no date can clear its cells"
    )


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
