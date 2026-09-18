"""MI-278 U37 — was the order size a RISK decision or a CEILING, and can we tell?

The module ships an executable `--self-test`; this file is the pytest surface so
those controls run in CI. Every test names the defect it would catch, and each
was verified by PLANTING that defect.
"""
import importlib.util
import json
import os

import pytest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SPEC = importlib.util.spec_from_file_location(
    "_u37", os.path.join(_HERE, "scripts", "research",
                         "margin_ceiling_visibility.py"))
U37 = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(U37)


def row(rid, size, cap, status="rejected", leg="ict_scalp_avax_5m",
        acct="bybit_1", detail=None, kind="venue_available", stamp=True,
        extra_notes=None):
    nt = dict(extra_notes or {})
    if stamp:
        nt["margin_basis"] = {"kind": kind, "basis_usd": 1000.0, "leverage": 3,
                              "buffer": 0.9, "max_qty_by_margin": cap,
                              "detail": detail}
    return {"id": rid, "position_size": size, "status": status,
            "strategy_name": leg, "account_id": acct,
            "notes": json.dumps(nt) if nt else None}


def test_module_self_test_passes():
    assert U37._self_test() == 0


# --------------------------------------------------------------------------
# The band — and the trap PB-20260822's wording sets
# --------------------------------------------------------------------------

def test_a_quantised_size_grades_at_ceiling_while_exact_equality_misses_it():
    """PB-20260822 says `position_size` equals `max_qty_by_margin` "to FULL
    FLOAT PRECISION". It does not — the shipped size is quantised to the venue
    step — so an `==` probe returns ZERO across the whole journal and a reader
    concludes the effect stopped.

    Plant: `if ps == cap:` instead of the ratio band.
    """
    band, ratio = U37.ceiling_band(row(1, 33141.1000, 33141.1001))
    assert band == "at_ceiling"
    assert ratio < 1.0
    assert U37.ceiling_band(row(2, 33141.1001, 33141.1001))[0] == "at_ceiling"


@pytest.mark.parametrize("size,cap,want", [
    (1.0, 1.0, "at_ceiling"), (0.9995, 1.0, "at_ceiling"),
    (0.995, 1.0, "near_ceiling"), (0.7, 1.0, "part_of_ceiling"),
    (0.2, 1.0, "well_below"),
])
def test_bands(size, cap, want):
    assert U37.ceiling_band(row(3, size, cap))[0] == want


def test_a_zero_size_is_its_own_band():
    """The sizer refusing is not a conservatively-sized order. Pooling them
    would let 154 refusals read as prudence. Plant: drop the `ps == 0` test."""
    assert U37.ceiling_band(row(4, 0.0, 1.0))[0] == "zero_size"


@pytest.mark.parametrize("kw", [
    {"size": 1.0, "cap": 0.0}, {"size": "x", "cap": 1.0},
    {"size": 1.0, "cap": None},
])
def test_ungradeable_is_we_could_not_look(kw):
    assert U37.ceiling_band(row(5, **kw))[0] == "ungradeable"


def test_no_stamp_is_ungradeable_not_a_band():
    assert U37.ceiling_band(row(6, 1.0, 1.0, stamp=False))[0] == "ungradeable"


# --------------------------------------------------------------------------
# Visibility — the finding
# --------------------------------------------------------------------------

def test_a_placed_row_can_never_carry_the_ceiling_record():
    """`margin_basis` is stamped only by `execute.log_rejection_to_journal`, a
    refusal-only path, while PB-20260822's criterion is about orders that
    PLACE. Plant: collapse `absent_on_placed` into `absent_on_refused`."""
    assert U37.visibility_state(
        row(10, 1.0, 2.0, status="closed", stamp=False)) == "absent_on_placed"
    assert U37.visibility_state(
        row(11, 1.0, 2.0, status="open", stamp=False)) == "absent_on_placed"


def test_refused_and_placed_absences_are_different_states():
    assert U37.visibility_state(
        row(12, 1.0, 2.0, status="rejected", stamp=False)) == "absent_on_refused"


def test_an_unrecognised_status_is_its_own_state():
    assert U37.visibility_state(
        {"id": 13, "status": "weird", "notes": None}) == "absent_status_unknown"


def test_every_visibility_state_is_declared():
    for r in (row(14, 1.0, 2.0), row(15, 1.0, 2.0, status="closed", stamp=False),
              row(16, 1.0, 2.0, stamp=False), {"id": 17, "status": "?"}):
        assert U37.visibility_state(r) in U37.VISIBILITY_STATES


# --------------------------------------------------------------------------
# The report, and its positive control
# --------------------------------------------------------------------------

def _rows():
    return [row(20, 33141.1, 33141.1001), row(21, 27758.8, 27758.8013),
            row(22, 0.0, 5.0), row(23, 0.6, 1.0),
            row(24, 1.0, 2.0, status="closed", stamp=False,
                extra_notes={"pnl_source": "local_compute"}),
            row(25, 1.0, 2.0, status="open", stamp=False,
                extra_notes={"_truncated": True})]


def test_the_positive_control_holds():
    """The absence of the stamp on placed rows only means something if those
    rows HAVE notes. Plant: remove `placed_rows_with_any_notes`."""
    v = U37.report(_rows())
    assert v["placed_rows"] == 2
    assert v["placed_rows_with_any_notes"] == 2
    assert v["placed_rows_with_a_ceiling_record"] == 0


def test_truncation_is_counted_as_a_separate_hazard():
    """A dict in `notes` is what `dump_capped` sheds first, so even a stamp on
    the placement path would be dropped on a full row. Different from the write
    site, and fixing one leaves the other."""
    assert U37.report(_rows())["placed_rows_truncated"] == 1


def test_bands_partition_the_recorded_rows():
    v = U37.report(_rows())
    assert sum(v["bands"].values()) == v["rows_with_a_ceiling_record"] == 4


def test_at_ceiling_rows_are_attributed():
    v = U37.report(_rows())
    assert v["at_ceiling_by_strategy"] == {"ict_scalp_avax_5m": 2}
    assert v["at_ceiling_share"] == pytest.approx(0.5)


def test_an_empty_population_reports_share_none_never_zero():
    """Plant: `else 0.0`."""
    v = U37.report([])
    assert v["at_ceiling_share"] is None
    assert "0 journal row(s) read" in U37.render(v)


# --------------------------------------------------------------------------
# The opposite failure, reported even when empty
# --------------------------------------------------------------------------

def test_a_size_above_its_own_cap_is_surfaced_loudly():
    """The clamp not binding is the opposite and more serious failure.
    Plant: `if False:` on the `ratio > 1.0` test."""
    v = U37.report([row(30, 3.0, 1.0)])
    assert len(v["clamp_did_not_bind"]) == 1
    assert "🚨" in U37.render(v)


def test_an_empty_clamp_list_says_the_zero_was_measured():
    v = U37.report(_rows())
    assert v["clamp_did_not_bind"] == []
    assert "measured zero" in U37.render(v)


# --------------------------------------------------------------------------
# Basis provenance
# --------------------------------------------------------------------------

def test_an_estimated_basis_is_flagged():
    """A cap computed from an estimated basis is an estimated ceiling."""
    assert U37.basis_provenance(
        row(40, 1.0, 2.0, kind="equity_minus_pledged",
            detail="equity 1 less pledged margin 0 (ESTIMATED: notional / 3x)")
    ) == "estimated"


def test_a_direct_basis_reads_direct_and_no_stamp_reads_none():
    assert U37.basis_provenance(row(41, 1.0, 2.0)) == "direct"
    assert U37.basis_provenance(row(42, 1.0, 2.0, stamp=False)) is None


def test_render_states_the_code_paths_are_tier_2():
    assert "Tier-2" in U37.render(U37.report(_rows()))
