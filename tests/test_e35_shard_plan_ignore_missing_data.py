"""`plan_legs(ignore_missing_data=...)` — the flag that made the e35 sweep runnable.

WHY THIS EXISTS (`BL-20260824-E35-SHARD-PLANNER-CANNOT-PLAN-ON-A-FRESH-CHECKOUT`).
`e35-bracket-sweep.yml` had **never run**. Its planner resolves legs through
`e35_bracket_geometry_sweep.plan_legs`, which requires each leg's CSV to be ON
DISK — but leg CSVs are gitignored (`.gitignore: data/*.csv`) and, on that
workflow, the CSV is fetched by the very per-leg job the planner is supposed to
schedule. So on a fresh CI checkout every leg resolved `data=None`, the matrix
expanded to zero jobs, and the planner (correctly) refused:

    shard-plan: 0 job(s); 55 not scheduled (data_missing=43, out_of_scope_family=12)

The refusal was RIGHT — an empty matrix is a green run that tested nothing. The
defect was planning on a precondition the plan itself creates.

TWO PROPERTIES ARE PINNED HERE, and the second is the one that matters most:

1. **Default OFF is byte-for-byte the old behaviour.** The sweep never sets the
   flag, so its own planning is untouched.
2. **THE FLAG DROPS EXACTLY ONE GATE — the data-presence check — AND NOTHING
   ELSE.** The family filter, the symbol/timeframe resolution, and (deliberately)
   the absence of an `enabled` check all still apply. If the flag also widened
   scope, the shard plan would schedule legs the sweep would refuse, and nothing
   would catch it. The arithmetic below is the check: flag-ON must move exactly
   the `data_missing` legs into `runnable` and leave `out_of_scope_family`
   refused, with the two populations reconciling to the same total.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "research"))

import e35_bracket_geometry_sweep as sweep  # noqa: E402


@pytest.fixture(scope="module")
def empty_dir(tmp_path_factory):
    """A directory with no leg CSVs — what a fresh CI checkout looks like."""
    return tmp_path_factory.mktemp("no_candles")


@pytest.fixture(scope="module")
def off(empty_dir):
    return sweep.plan_legs(empty_dir, None, 0.099)


@pytest.fixture(scope="module")
def on(empty_dir):
    return sweep.plan_legs(empty_dir, None, 0.099, ignore_missing_data=True)


def test_default_off_still_schedules_nothing_without_data(off):
    """The pre-fix behaviour, unchanged: the sweep's own planning is untouched."""
    runnable, skipped = off
    assert runnable == []
    assert skipped, "expected the legs to be recorded as skipped, not vanish"
    assert all("data_missing" in s["reason"] or "out_of_scope_family" in s["reason"]
               for s in skipped)


def test_flag_on_schedules_the_data_missing_legs(on):
    runnable, _ = on
    assert runnable, "the flag must make a fresh checkout plannable"
    assert all(r["data_pending"] for r in runnable)


def test_the_flag_drops_the_data_gate_AND_NOTHING_ELSE(off, on):
    """The load-bearing check, done by arithmetic rather than by reading.

    Flag-ON must convert exactly the `data_missing` skips into runnable legs.
    Every other refusal must survive untouched, and the two populations must
    reconcile to the same total — a flag that also widened scope would show up
    here as a total that grew.
    """
    off_runnable, off_skipped = off
    on_runnable, on_skipped = on

    off_missing = [s for s in off_skipped if s["reason"].startswith("data_missing")]
    off_other = [s for s in off_skipped if not s["reason"].startswith("data_missing")]

    # The newly-scheduled legs are EXACTLY the ones that were data_missing.
    assert {r["leg"] for r in on_runnable} == {s["leg"] for s in off_missing}
    # Every non-data refusal survives, unchanged.
    assert {s["leg"] for s in on_skipped} == {s["leg"] for s in off_other}
    # And the totals reconcile — no leg appeared from nowhere.
    assert (len(off_runnable) + len(off_skipped)
            == len(on_runnable) + len(on_skipped))


def test_family_filter_still_applies_under_the_flag(on):
    """`out_of_scope_family` legs stay refused — the flag is not a bypass."""
    runnable, skipped = on
    assert all(r["family"] in ("donchian", "pullback", "squeeze") for r in runnable)
    assert skipped, "the out-of-scope families must still be refused"
    assert all(s["reason"].startswith("out_of_scope_family") for s in skipped)


def test_a_data_pending_leg_carries_no_fabricated_base(on):
    """`base_args` needs the resolved data path, so a base without it is a fiction.

    `None` says "not built"; a half-built base from defaults would be a value a
    reader could mistake for the leg's real geometry.
    """
    runnable, _ = on
    assert all(r["base"] is None for r in runnable)
    assert all(r["base_geometry"] is None for r in runnable)
    assert all(r["data"] is None for r in runnable)


def test_scope_fields_are_present_so_the_matrix_can_be_built(on):
    """The shard planner reads only these; they must survive a pending row."""
    runnable, _ = on
    for r in runnable:
        assert r["leg"] and r["symbol"] and r["tf"] and r["family"]
