"""The fleet sweep must be able to GRADE the rr_floor lever, not just run it.

The lever shipped in `scripts/backtest_trend.py` (2026-08-17) and was ported to
`scripts/backtest_pullback.py` (2026-08-18). `m20_fleet_exit_sweep.py` is the
ONLY thing that applies the Path A/B gate and the yearly walk-forward — and it
had no cell for the lever, so it was implemented, measurable and ungradeable.
An operator pre-approval to walk-forward it had nothing to run. Third instance
this session of "the thing exists and nothing calls it", after the IB
broker-PnL reader and `attach_ib_target`.

These tests pin the two properties that make the cells trustworthy:

1. **A cell that cannot fire is SKIPPED WITH A REASON, never silently dropped
   and never emitted anyway.** Emitting it would return exactly-zero deltas
   that read as `tie_no_improvement` — "we measured it and it made no
   difference" — when it was never measurable. That is the cosmetic-cell
   anti-pattern (`BL-20260730-DONCHIAN-COSMETIC-SHORT-CELLS`).
2. **The two ways of being inert stay distinguishable**, because they have
   different fixes: the RUN did not pass a cap, versus the FAMILY has no live
   TP clamp to mirror.
"""
from __future__ import annotations

import importlib.util
import os
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)


def _load():
    spec = importlib.util.spec_from_file_location(
        "_m20_fleet_rr", os.path.join(_REPO, "scripts/research/m20_fleet_exit_sweep.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_m20_fleet_rr"] = mod
    spec.loader.exec_module(mod)
    return mod


m20 = _load()
CFG = {"trail_mult": 5.0, "timeframe": "2h", "symbols": ["XRPUSDT"]}
LIVE_CAP = 0.099


def _cells(fam, cap):
    skipped: list = []
    cells = m20.cells_for(dict(CFG), fam, skipped=skipped, tp_cap_pct=cap)
    return ([t for t, lev, _ in cells if lev == "rr_floor"],
            [x for x in skipped if x.get("lever") == "rr_floor"])


# --- the lever is gradeable where it is implemented ----------------------- #

@pytest.mark.parametrize("fam", ["pullback", "donchian"])
def test_capped_families_emit_rr_floor_cells(fam):
    """POSITIVE CONTROL. Without this, every skip assertion below proves nothing."""
    emitted, skipped = _cells(fam, LIVE_CAP)
    assert emitted, f"{fam} must emit rr_floor cells under a live TP cap"
    assert skipped == []


def test_emitted_cells_carry_the_flag_the_harness_reads():
    cells = m20.cells_for(dict(CFG), "pullback", skipped=[], tp_cap_pct=LIVE_CAP)
    rr = [(t, a) for t, lev, a in cells if lev == "rr_floor"]
    assert rr
    for tag, argv in rr:
        assert argv[0] == "--rr-floor"
        float(argv[1])                      # a real number, not a tag fragment
        assert tag.startswith("rrfloor")


# --- inert is reported, never silently dropped ---------------------------- #

def test_no_tp_cap_skips_with_a_reason_rather_than_emitting():
    """A floor with no capped TP returns zero deltas that read as a measured
    no-op. It must not be asked at all, and the run must say it was not."""
    emitted, skipped = _cells("pullback", 0.0)
    assert emitted == []
    assert skipped, "an unasked cell must be recorded, not dropped"
    assert all("no_tp_cap_in_run" in x["reason"] for x in skipped)


def test_family_without_a_live_tp_clamp_skips_with_a_different_reason():
    """The two inert causes have different fixes, so they are different strings.

    `no_tp_cap_in_run` is fixed by passing --tp-cap-pct; `family_has_no_live_tp_cap`
    cannot be fixed at all — the lever does not apply to that family.
    """
    emitted, skipped = _cells("scalp", LIVE_CAP)
    assert emitted == []
    assert skipped
    assert all(x["reason"].startswith("family_has_no_live_tp_cap")
               for x in skipped)


def test_the_two_inert_reasons_are_not_the_same_string():
    a = m20.inert_rr_floor_reason("pullback", 0.0)
    b = m20.inert_rr_floor_reason("scalp", LIVE_CAP)
    assert a and b and a != b
    assert m20.inert_rr_floor_reason("pullback", LIVE_CAP) is None


def test_every_skipped_cell_names_the_cell_it_withheld():
    """A skip record with no cell id cannot be reconciled against the grid."""
    _, skipped = _cells("pullback", 0.0)
    assert all(x.get("cell") for x in skipped)


# --- the port did not disturb the existing grid --------------------------- #

def test_existing_cells_are_unchanged_by_the_addition():
    cells = m20.cells_for(dict(CFG), "pullback", skipped=[], tp_cap_pct=LIVE_CAP)
    levers = {lev for _, lev, _ in cells}
    assert {"stale_stop", "trail_geometry"} <= levers


# --- the sweep must READ the state back, not just emit the cells ---------- #

def _gate(cell, base):
    """The sweep's per-window gate reason (module-private name resolved once)."""
    for nm in ("gate_reason", "window_gate", "beats_detail"):
        fn = getattr(m20, nm, None)
        if fn is not None:
            try:
                return fn(cell, base)
            except TypeError:
                continue
    pytest.skip("gate-reason entry point not found under a known name")


BASE = {"net_total_r": 10.0, "max_drawdown_r": 5.0}


def test_an_inert_cell_is_not_graded_as_a_measured_no_op():
    """`unmeasurable_no_tp_cap` returns cn == bn and cd == bd, which without
    this branch falls into `tie_no_improvement` — "we measured it and it made
    no difference" — when it was never measured."""
    inert = {"net_total_r": 10.0, "max_drawdown_r": 5.0,
             "rr_floor_state": "unmeasurable_no_tp_cap"}
    g = _gate(inert, BASE)
    assert g["passed"] is False
    assert "lever_inert" in g["reason"]
    assert "tie_no_improvement" not in (g["reason"] or "")
    # Deltas are None, not 0.0 — we did not measure them.
    assert g["d_net_r"] is None and g["d_max_dd"] is None


def test_a_real_no_op_still_reads_as_tie_no_improvement():
    """NEGATIVE CONTROL. The branch above must not swallow a genuine flat
    result — an honest no-op is a measurement and keeps its own verdict."""
    flat = {"net_total_r": 10.0, "max_drawdown_r": 5.0,
            "rr_floor_state": "measurable"}
    g = _gate(flat, BASE)
    assert g["reason"] == "tie_no_improvement"
    assert g["d_net_r"] == 0.0


def test_a_cell_that_never_requested_the_lever_is_unaffected():
    """`off` needs no branch — it is graded on whatever it did change."""
    better = {"net_total_r": 12.0, "max_drawdown_r": 4.0, "rr_floor_state": "off"}
    g = _gate(better, BASE)
    assert g["passed"] is True
    assert g["d_net_r"] == 2.0


def test_a_cell_with_no_state_key_at_all_is_graded_normally():
    """Harnesses that predate the field must not all become `lever_inert`."""
    g = _gate({"net_total_r": 12.0, "max_drawdown_r": 4.0}, BASE)
    assert g["passed"] is True


def test_rr_floor_cell_is_withheld_when_the_harness_has_no_such_flag():
    """A cell must not be emitted for a harness that does not declare its flag.

    REGRESSION PIN for
    BL-20260820-SWEEP-EMITS-CELLS-FOR-FLAGS-THE-HARNESS-DOES-NOT-IMPLEMENT.

    `inert_rr_floor_reason` tested only whether the lever is SEMANTICALLY
    applicable — is the family in `LIVE_TP_CAPPED_FAMILIES`, so `rr_from_here`
    has a target to measure against. `squeeze` is correctly in that set (its live
    unit does clamp the TP), but `backtest_squeeze.py` declares no `--rr-floor`
    flag, so the sweep handed it an argument it does not accept.

    MEASURED on the M31 P5 rr_floor walk-forward, 2026-08-20: 3 of 57
    (leg x cell) rows returned `verdict: "error"` on `squeeze_breakout_4h`, one
    per rr_floor cell. `error` is a COLLAPSED state there — a genuine crash and a
    flag the harness never had are indistinguishable, and the second is not a
    failure at all. The honest grade is the `no_harness_lever` vocabulary the run
    plan already uses at the LEG level.
    """
    mod = m20
    cfg = {"symbols": ["BTCUSDT"], "timeframe": "4h", "tp_r": 50.0}

    skipped: list = []
    cells = mod.cells_for(cfg, "squeeze", skipped=skipped, tp_cap_pct=0.099,
                          harness="scripts/backtest_squeeze.py")
    tags = {c[0] for c in cells}
    assert not (tags & {"rrfloor0.5", "rrfloor0.75", "rrfloor1"}), (
        f"rr_floor cells emitted for a harness with no --rr-floor flag: {tags}")
    reasons = {s["reason"] for s in skipped if s.get("lever") == "rr_floor"}
    assert reasons and all("harness_has_no_rr_floor_flag" in r for r in reasons), (
        f"withheld, but not for the stated reason: {reasons}")

    # AND the control: a harness that DOES declare the flag still gets its cells.
    # Without this the test would pass against a change that withheld rr_floor
    # everywhere, which is the opposite defect.
    ok = mod.cells_for({"symbols": ["BTCUSDT"], "timeframe": "1h",
                        "tp_r": 50.0, "trail_mult": 5.0},
                       "donchian", skipped=[], tp_cap_pct=0.099,
                       harness="scripts/backtest_trend.py")
    assert {"rrfloor0.5", "rrfloor0.75", "rrfloor1"} <= {c[0] for c in ok}, (
        "the fix withheld rr_floor from a harness that DOES implement it")


def test_harness_implements_flag_never_collapses_unreadable_into_absent():
    """`None` (we could not read the source) must not read as `False`.

    Collapsing them would turn a filesystem problem into a fake coverage answer:
    every cell silently withheld for a harness we simply failed to open.
    """
    mod = m20
    assert mod.harness_implements_flag("scripts/backtest_trend.py", "--rr-floor") is True
    assert mod.harness_implements_flag("scripts/backtest_squeeze.py", "--rr-floor") is False
    assert mod.harness_implements_flag("scripts/does_not_exist.py", "--rr-floor") is None
    # and the gate distinguishes them in its reason string
    assert "harness_unreadable" in (
        mod.inert_rr_floor_reason("squeeze", 0.099, "scripts/does_not_exist.py") or "")
