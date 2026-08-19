"""lever-wiring-guard — can every shipped exit lever be run, graded, and seen?

The LEVER analogue of provenance-consumer-guard. Four findings on 2026-08-18
were one shape — a capability existing while the thing meant to consume it did
not know — and nothing asserted the relationship.

The load-bearing test here is the NEGATIVE control: a guard that cannot fail is
decoration. Everything else only means something if that one holds.
"""
from __future__ import annotations

import importlib.util
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)


def _load():
    spec = importlib.util.spec_from_file_location(
        "_lw_guard", os.path.join(_REPO, "scripts/ci/check_lever_wiring.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_lw_guard"] = mod
    spec.loader.exec_module(mod)
    return mod


lw = _load()


def _rows():
    return {r["lever"]: r for r in lw.assess()}


def test_the_guard_can_fail():
    """NEGATIVE CONTROL. Plants the exact defect the file exists for — a lever
    with no cell in the sweep that grades it — and requires a catch."""
    lw.LEVERS["_planted"] = {"sweep_lever": "no_such_lever",
                             "probe_family": "donchian"}
    try:
        planted = _rows()["_planted"]
    finally:
        lw.LEVERS.pop("_planted", None)
    assert any("GRADEABLE" in f for f in planted["failures"])


def test_the_repo_currently_passes():
    assert lw.main([]) == 0


def test_stale_stop_is_fully_wired():
    r = _rows()["stale_stop"]
    assert r["failures"] == []
    assert r["gradeable"]


def test_the_shared_extraction_is_reflected():
    """Both families must be seen — the whole point of src/runtime/exit_levers.py.

    A source-only detector answers 1 here, which is how the extraction nearly
    reported a real-money family as having lost two mechanisms.
    """
    assert len(_rows()["stale_stop"]["visible_in_units"]) >= 2
    assert len(_rows()["giveback_stop"]["visible_in_units"]) >= 2


def test_rr_floor_is_gradeable_but_recorded_as_not_runnable():
    """The gap the guard found on its first run — and it must stay VISIBLE.

    Recorded as a printed exemption rather than by dropping rr_floor from
    LEVERS: a lever absent from that table is invisible to the guard, which is
    the failure mode being guarded against.
    """
    r = _rows()["rr_floor"]
    assert r["gradeable"], "rr_floor must still have sweep cells"
    assert r["visible_in_units"] == [], "no live unit implements rr_floor"
    assert "exempt" in str(r.get("runnable", ""))
    assert "BL-20260818-RR-FLOOR-IS-BACKTEST-ONLY" in str(r.get("runnable", ""))


def test_every_exemption_states_a_reason():
    """An exemption with no reason is a silent hole."""
    for lever, spec in lw.LEVERS.items():
        for key in ("gradeable_exempt", "runnable_exempt"):
            if key in spec:
                assert spec[key].strip(), f"{lever}.{key} is empty"


def test_exit_head_is_exempt_from_grading_not_failing_it():
    r = _rows()["exit_head"]
    assert "exempt" in str(r["gradeable"])
    assert r["failures"] == []


def test_self_test_passes():
    assert lw._self_test() == 0
