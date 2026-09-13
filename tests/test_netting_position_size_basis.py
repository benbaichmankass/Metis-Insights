"""MI-278 U33 — is `position_size` observed, or assigned by netting attribution?

The module ships an executable `--self-test`; this file is the pytest surface so
those controls run in CI on every PR rather than only when a session remembers
to invoke them. Every test names the defect it would catch, and each was
verified by PLANTING that defect and watching this file fail.
"""
import importlib.util
import json
import os

import pytest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SPEC = importlib.util.spec_from_file_location(
    "_u33", os.path.join(_HERE, "scripts", "research",
                         "netting_position_size_basis.py"))
U33 = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(U33)

STAMP = {"netting_attribution_basis": "leg_gone", "netting_attributed_qty": 52.83}


def row(rid, notes, exit_reason="sl_cross", size=1.0, acct="bybit_1", **kw):
    d = {"id": rid, "account_id": acct, "symbol": "ETHUSDT",
         "strategy_name": "ict_scalp_eth_15m", "exit_reason": exit_reason,
         "status": "closed", "position_size": size, "pnl": 1.0,
         "notes": None if notes is None else json.dumps(notes)}
    d.update(kw)
    return d


def sk(tid, qty, mode="apply"):
    return {"trade_id": tid, "attributed_qty": qty, "mode": mode}


def test_module_self_test_passes():
    assert U33._self_test() == 0


# --------------------------------------------------------------------------
# The four basis states — and the one that must never collapse
# --------------------------------------------------------------------------

@pytest.mark.parametrize("notes,expected", [
    (STAMP, "assigned_by_attribution"),
    ({"netting_attribution_basis": "fifo"}, "assigned_by_attribution"),
    ({"netting_attributed_qty": 1.0}, "assigned_by_attribution"),
    ({"confidence": 0.7}, "observed"),
    ({"confidence": 0.7, "_truncated": True}, "evidence_may_be_shed"),
    (None, "notes_unreadable"),
])
def test_qty_basis_states(notes, expected):
    assert U33.qty_basis(row(1, notes)) == expected


def test_a_truncated_row_is_never_graded_observed():
    """`dump_capped` sheds unprotected keys and NEITHER attribution key is in
    `json_notes._DEFAULT_PROTECTED`, so a truncated unstamped row is *we could
    not look*. Plant: `if False:` on the `_truncated` branch."""
    assert U33.qty_basis(row(2, {"c": 1, "_truncated": True})) \
        == "evidence_may_be_shed"
    assert U33.visibility(row(2, {"c": 1, "_truncated": True})) == "cannot_grade"


def test_either_stamp_key_alone_is_enough():
    """`dump_capped` sheds ONE key at a time, so a row can retain one and lose
    the other. Plant: `all(...)` instead of `any(...)`."""
    assert U33.qty_basis(row(3, {"netting_attributed_qty": 1.0})) \
        == "assigned_by_attribution"


@pytest.mark.parametrize("raw", ["{not json", "[1,2,3]", '"a string"'])
def test_non_object_notes_are_unreadable_not_empty(raw):
    assert U33.qty_basis({"id": 4, "notes": raw}) == "notes_unreadable"


# --------------------------------------------------------------------------
# Visibility — the population the prior work's filter cannot see
# --------------------------------------------------------------------------

def test_a_stamped_row_with_an_ordinary_exit_reason_is_invisible():
    """MI-277 annex A6, MI-278 U9 and U16 all scope this contamination by
    `exit_reason == 'netting_attributed'`. Plant: always return
    `visible_via_exit_reason`."""
    assert U33.visibility(row(5, STAMP, exit_reason="sl_cross")) \
        == "invisible_to_exit_reason_filter"
    assert U33.visibility(row(6, STAMP, exit_reason="netting_attributed")) \
        == "visible_via_exit_reason"


def test_cannot_grade_is_not_folded_into_not_attributed():
    """Plant: drop the `observed` test and return `not_attributed` for all."""
    assert U33.visibility(row(7, {"c": 1, "_truncated": True})) == "cannot_grade"
    assert U33.visibility(row(8, {"c": 1})) == "not_attributed"


def test_every_state_is_declared():
    for n in (STAMP, {"c": 1}, {"c": 1, "_truncated": True}, None):
        assert U33.qty_basis(row(9, n)) in U33.QTY_BASIS_STATES
        assert U33.visibility(row(9, n)) in U33.VISIBILITY_STATES


# --------------------------------------------------------------------------
# The quantity, and why every share is a lower bound without the soak
# --------------------------------------------------------------------------

def test_an_unreadable_or_absent_slice_is_none_never_zero():
    """Plant: `return 0.0` in the except branch of `last_take`."""
    assert U33.last_take(row(10, {"netting_attributed_qty": "x"})) is None
    assert U33.last_take(row(11, {"c": 1})) is None
    assert U33.last_take(row(12, STAMP)) == 52.83


def test_removed_share_is_none_not_zero_when_ungradeable():
    """A share of zero is a real reading — nothing was removed — and must not
    stand in for *we do not know*. Plant: `else 0.0`."""
    assert U33.removed_share(row(13, {"c": 1}, size=1.0)) is None
    assert U33.removed_share(row(14, {"netting_attributed_qty": 0.0}, size=0.0)) \
        is None
    assert U33.removed_share(row(15, {"netting_attributed_qty": 3.0}, size=1.0)) \
        == pytest.approx(0.75)


# --------------------------------------------------------------------------
# The soak — the second, independent source
# --------------------------------------------------------------------------

def test_without_a_soak_the_report_says_it_did_not_look():
    """Plant: return `neither` when `applied is None`, or `{}`/`0` in the
    report — either reads as 'the soak agrees there is nothing'."""
    v = U33.report([row(20, STAMP)])
    assert v["corroboration"] is None
    assert v["invisible_total_two_source"] is None
    assert v["rows_whose_stamp_was_SHED"] is None
    assert U33.corroboration(row(20, STAMP), None) == "soak_not_supplied"
    assert "NO SOAK SUPPLIED" in U33.render(v)


def test_a_shed_stamp_is_rescued_by_the_soak_alone():
    """THE FINDING. The soak is written before the DB is touched and is not
    subject to the notes cap, so it sees rows the journal has lost.

    Plant: fold `soak_only` into `stamp_only`.
    """
    r = row(21, {"c": 1, "_truncated": True}, exit_reason="sl", size=0.06)
    v = U33.report([r], soak_rows=[sk(21, 12.52)])
    assert v["corroboration"] == {"soak_only": 1}
    assert [x["id"] for x in v["rows_whose_stamp_was_SHED"]] == [21]
    assert v["rows_whose_stamp_was_SHED"][0]["removed_share"] == \
        pytest.approx(12.52 / 12.58)
    assert "STAMP SHED" in U33.render(v)


def test_the_two_invisible_populations_stay_separable():
    """They have different evidence: one is stamped and mislabelled, the other
    has no journal trace at all. Plant: merge the two lists."""
    stamped = row(22, STAMP, exit_reason="sl_cross")
    sheded = row(23, {"c": 1, "_truncated": True}, exit_reason="sl")
    v = U33.report([stamped, sheded], soak_rows=[sk(23, 5.0)])
    assert len(v["invisible_rows"]) == 1
    assert len(v["rows_whose_stamp_was_SHED"]) == 1
    assert v["invisible_total_two_source"] == 2


def test_an_annotate_soak_row_mutated_nothing_and_is_not_counted():
    """Counting it would manufacture contamination that never happened.
    Plant: drop the mode test in `soak_applied`."""
    r = row(24, {"c": 1, "_truncated": True})
    v = U33.report([r], soak_rows=[sk(24, 9.0, mode="annotate")])
    assert v["corroboration"] == {"neither": 1}
    assert v["rows_whose_stamp_was_SHED"] == []


def test_the_soak_sums_passes_and_an_unreadable_slice_voids_the_sum():
    """The journal stamp is OVERWRITTEN each pass, so only the soak can give a
    cumulative. Plant: swallow the unreadable slice instead of voiding."""
    a = U33.soak_applied([sk(30, 1.0), sk(30, 2.0), sk(30, 4.0)])
    assert a[30]["passes"] == 3
    assert a[30]["cumulative_qty"] == pytest.approx(7.0)
    b = U33.soak_applied([sk(31, 1.0), sk(31, "x")])
    assert b[31]["passes"] == 2
    assert b[31]["cumulative_qty"] is None


def test_a_soak_row_with_no_trade_id_is_dropped_never_keyed_on_none():
    assert U33.soak_applied([{"attributed_qty": 1.0, "mode": "apply"}]) == {}


def test_stamp_only_is_a_short_tail_not_a_contradiction():
    """The soak read is capped, so a stamped row it does not reach is a fact
    about the READ, not a disagreement between sources."""
    v = U33.report([row(32, STAMP, exit_reason="netting_attributed")],
                   soak_rows=[])
    assert v["corroboration"] == {"stamp_only": 1}
    assert v["invisible_total_two_source"] == 0


def test_every_corroboration_state_is_declared():
    cases = [
        ([row(40, STAMP)], None),
        ([row(41, STAMP, exit_reason="netting_attributed")], [sk(41, 1.0)]),
        ([row(42, STAMP)], []),
        ([row(43, {"c": 1, "_truncated": True})], [sk(43, 1.0)]),
        ([row(44, {"c": 1})], []),
    ]
    for rows, soak in cases:
        v = U33.report(rows, soak_rows=soak)
        for state in (v["corroboration"] or {}):
            assert state in U33.CORROBORATION_STATES


# --------------------------------------------------------------------------
# Scope, provenance, and the empty case
# --------------------------------------------------------------------------

def test_a_non_netting_venue_is_counted_and_not_graded():
    """Attribution runs on Bybit only, so grading an Alpaca row `observed` is a
    pass it never had the opportunity to fail. Plant: grade everything."""
    v = U33.report([row(50, STAMP, acct="alpaca_live"), row(51, {"c": 1})])
    assert v["rows_graded"] == 1
    assert v["rows_off_venue_not_graded"] == 1


def test_a_measured_pnl_on_an_assigned_quantity_is_surfaced():
    """`src.runtime.provenance` grades the PRICE and has no quantity axis —
    `position_size`, `quantity` and `size` appear zero times in it. PnL is
    price x quantity. Plant: `if False` on the disagreement test."""
    r = row(52, dict(STAMP, exit_price_source="exchange"), exit_reason="sl_cross")
    if U33.pnl_provenance(r) == "provenance_unavailable":
        pytest.skip("canonical provenance module not importable here")
    v = U33.report([r])
    assert len(v["measured_pnl_on_an_assigned_quantity"]) == 1
    assert "has no quantity axis" in U33.render(v)


def test_an_empty_population_leads_with_its_denominator():
    v = U33.report([])
    assert v["rows_graded"] == 0
    assert v["invisible_rows"] == []
    assert "graded=0" in U33.render(v)


def test_render_never_crashes_on_either_path():
    assert U33.render(U33.report([row(60, STAMP)]))
    assert U33.render(U33.report([row(61, STAMP)], soak_rows=[sk(61, 1.0)]))
