"""MI-278 U38 — is an `order_package_id` a fan-out key, and can a tail have truncated it?

The module ships an executable `--self-test`; this file is the pytest surface so
those controls run in CI on every PR rather than only when a session remembers
to invoke them. Every test names the defect it would catch, and each was
verified by PLANTING that defect and watching this file fail.
"""
import importlib.util
import os

import pytest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SPEC = importlib.util.spec_from_file_location(
    "_u38", os.path.join(_HERE, "scripts", "research",
                         "package_fanout_integrity.py"))
U38 = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(U38)

BASE = 1_700_000_000


def row(rid, pkg, acct, created):
    return {"id": rid, "order_package_id": pkg, "account_id": acct, "created_at": created}


@pytest.fixture()
def pull():
    return U38._dense_pull()


# --------------------------------------------------------------- the self-test


def test_self_test_passes_in_full():
    """Catches: any control in the module's own suite regressing."""
    assert U38.self_test() == 0


# ------------------------------------------------------------------- parse_ts


@pytest.mark.parametrize("value", [None, "", "not-a-date", True])
def test_unreadable_timestamp_is_none_never_zero(value):
    """Catches: coercing an unreadable stamp to 0.0, which puts the row at the
    Unix epoch and makes every package containing it look impossibly wide."""
    assert U38.parse_ts(value) is None


def test_epoch_seconds_and_millis_both_read():
    assert U38.parse_ts(1_700_000_000) == 1_700_000_000.0
    assert U38.parse_ts(1_700_000_000_000) == 1_700_000_000.0


# ---------------------------------------------------------------- fanout_state


def test_repeated_account_cannot_be_one_fanout():
    """Catches: grading a reused package id as a cross-account fan-out."""
    members = [row(1, "p", "bybit_1", BASE), row(2, "p", "bybit_1", BASE + 9_000)]
    assert U38.fanout_state(members) == "account_repeated"


def test_repeated_account_is_proven_without_any_timestamp():
    """Catches: testing simultaneity BEFORE the account repeat, which throws away
    a fact we hold — the repeat is provable with no clock at all."""
    members = [row(1, "p", "bybit_1", None), row(2, "p", "bybit_1", None)]
    assert U38.fanout_state(members) == "account_repeated"


def test_contiguous_ids_do_not_make_it_a_fanout():
    """Catches: using id contiguity as the test. 4 of the 16 reused ids measured
    on the live pull ARE contiguous, so contiguity misses a quarter of them."""
    members = [row(200, "p", "bybit_1", BASE), row(201, "p", "bybit_1", BASE + 9_000)]
    assert U38.fanout_state(members) == "account_repeated"


def test_noncontiguous_ids_do_not_disqualify_a_real_fanout():
    """Catches: the same error in the other direction."""
    members = [row(300, "p", "a1", BASE), row(305, "p", "a2", BASE + 1)]
    assert U38.fanout_state(members) == "simultaneous_fanout"


def test_wide_spread_with_distinct_accounts_is_its_own_state():
    """Catches: collapsing an ambiguous wide-spread package into either verdict."""
    members = [row(1, "p", "a1", BASE), row(2, "p", "a2", BASE + 40_000)]
    assert U38.fanout_state(members) == "distinct_accounts_wide_spread"


@pytest.mark.parametrize("acct", [None, "", "   "])
def test_missing_account_is_ungradeable_not_a_distinct_account(acct):
    """Catches: an empty account_id counting as a distinct account, which would
    grade a package a fan-out on evidence nobody has."""
    members = [row(1, "p", acct, BASE), row(2, "p", "a2", BASE)]
    assert U38.fanout_state(members) == "ungradeable_no_account"


# ----------------------------------------------------------------- the basis


def test_inverted_pull_is_not_monotone(pull):
    """Catches: skipping the monotonicity precondition, on which the whole reach
    bound rests."""
    inverted = [row(1, "p", "a1", BASE + 100), row(2, "q", "a1", BASE + 1)]
    assert U38.basis_state(inverted)[0] == "not_monotone"


def test_gapped_pull_is_not_dense():
    """Catches: skipping the density precondition — with a gap, a missing member
    could be an interior row rather than one below the edge."""
    gapped = [row(1, "p", "a1", BASE), row(5, "q", "a1", BASE + 1)]
    assert U38.basis_state(gapped)[0] == "not_dense"


def test_too_few_rows_is_ungradeable_not_monotone():
    """Catches: reporting a one-row pull as monotone, i.e. as a clean basis."""
    state, terms = U38.id_monotonicity([row(1, "p", "a1", BASE)])
    assert state == "ungradeable"
    assert terms["inversions"] is None


# ------------------------------------------------------------------ the bound


def test_absent_bound_is_none_never_zero():
    """Catches: a missing bound defaulting to 0.0, which declares every package
    beyond reach and reports a clean pull that was never measured."""
    only_reuse = U38.group_by_package(
        [row(1, "p", "a1", BASE), row(2, "p", "a1", BASE + 9_000)])
    assert U38.observed_fanout_bound(only_reuse) is None


def test_bound_is_the_widest_simultaneous_spread(pull):
    assert U38.observed_fanout_bound(U38.group_by_package(pull)) == 2.0


# ------------------------------------------------------------------ the reach


def test_unusable_basis_refuses_every_reach_verdict():
    """Catches: computing a reach bound on a pull that violates its own
    preconditions — worse than no bound, because it reads as an answer."""
    gapped_but_bounded = [row(1, "pkg-f", "a1", BASE), row(2, "pkg-f", "a2", BASE + 1),
                          row(9, "pkg-late", "a1", BASE + 900)]
    assert U38.observed_fanout_bound(U38.group_by_package(gapped_but_bounded)) == 1.0
    states = {p["reach_state"] for p in U38.census(gapped_but_bounded)["per_package"].values()}
    assert states == {"ungradeable_reach"}


def test_reuse_class_is_unbounded_not_safe(pull):
    """Catches: pooling the reuse class into beyond_observed_reach. Its spread is
    limited only by the window, so it can straddle the edge from far above it."""
    assert U38.census(pull)["per_package"]["pkg-reuse"]["reach_state"] == "unbounded_class"


def test_single_row_packages_are_graded_for_reach(pull):
    """Catches: exempting singles — a truncated fan-out's remnant IS a single row,
    so exempting them hides exactly the case the backlog row is about."""
    assert U38.census(pull)["per_package"]["pkg-solo1"]["reach_state"] in U38.REACH_STATES


def test_edge_package_is_at_risk_and_a_distant_one_is_not(pull):
    per = U38.census(pull)["per_package"]
    assert per["pkg-fan"]["reach_state"] == "within_observed_reach"
    assert per["pkg-solo3"]["reach_state"] == "beyond_observed_reach"


# ------------------------------------------------------------------ the census


def test_every_state_is_reported_present_or_not(pull):
    """Catches: reporting only the states that occurred, so a zero cannot be told
    from a state the code never emits."""
    c = U38.census(pull)
    assert set(c["fanout_states"]) == set(U38.FANOUT_STATES)
    assert set(c["reach_states"]) == set(U38.REACH_STATES)


def test_report_prints_every_state_including_the_zeroes(pull):
    """Catches: dropping zero rows from the RENDER while the dict still has them."""
    text = "\n".join(U38.report(U38.census(pull)))
    for state in U38.FANOUT_STATES + U38.REACH_STATES:
        assert state in text


def test_counts_sum_to_their_own_denominators(pull):
    c = U38.census(pull)
    assert sum(c["reach_states"].values()) == c["population"]["packages"]
    assert sum(c["fanout_states"].values()) == c["population"]["multi_row_packages"]


def test_rows_without_a_package_are_counted_not_dropped(pull):
    """Catches: silently discarding ungroupable rows, so the population shrinks
    with no record of by how much."""
    c = U38.census(pull + [row(110, None, "a1", BASE + 99)])
    assert c["population"]["rows_without_package_or_id"] == 1


def test_report_states_its_denominators(pull):
    text = "\n".join(U38.report(U38.census(pull)))
    assert "denominator 3)" in text and "denominator 6 packages" in text


def test_report_warns_when_the_basis_is_unusable():
    """Catches: an unusable basis rendering identically to a clean one."""
    inverted = [row(1, "p", "a1", BASE + 100), row(2, "q", "a1", BASE + 1)]
    text = "\n".join(U38.report(U38.census(inverted)))
    assert "REACH BOUND IS NOT COMPUTABLE" in text
    assert "NOT 'nothing is truncated'" in text


# ------------------------------------------------------------- the cross-check


def test_no_overlap_is_not_rendered_as_no_effect():
    """Catches: a positive-control failure reading as a clean zero — the exact
    shape this repo files as unprovenanced diagnostic output."""
    cc = {"state": "no_overlap_cannot_verify",
          "positive_control": {"unit_packages": 40, "recognised_in_pull": 0},
          "reuse_packages_in_pull": 16}
    text = "\n".join(U38.cross_check_report(cc))
    assert "NOT 'no effect'" in text and "0 of 40" in text


def test_no_reuse_in_cells_is_its_own_outcome():
    cc = {"state": "no_reuse_in_cells",
          "positive_control": {"unit_packages": 40, "recognised_in_pull": 40},
          "reuse_units_per_cell": {"treated_pre": 0}, "reuse_packages_in_pull": 16}
    text = "\n".join(U38.cross_check_report(cc))
    assert "No reuse package falls inside" in text
    assert "could not look" not in text


def test_graded_prints_both_bands_their_widths_and_no_recommendation():
    """Catches: printing only the excluded band (hiding what the choice cost),
    dropping the width (which is what the reuse class actually moves), or letting
    the instrument recommend an exclusion it has no standing to recommend."""
    cells = {c: {"denominator": 1, "disagreement": 1}
             for c in ("treated_pre", "treated_post", "control_pre", "control_post")}
    cells0 = {c: {"denominator": 1, "disagreement": 0} for c in cells}
    cc = {"state": "graded",
          "positive_control": {"unit_packages": 168, "recognised_in_pull": 168},
          "reuse_packages_in_pull": 16,
          "reuse_units_per_cell": {"control_post": 10},
          "band_with_reuse": {"did_low": 0.038, "did_high": 0.296,
                              "sign_survives": True, "cells": cells},
          "band_without_reuse": {"did_low": 0.061, "did_high": 0.237,
                                 "sign_survives": True, "cells": cells0}}
    text = "\n".join(U38.cross_check_report(cc))
    assert "3.8..29.6" in text and "6.1..23.7" in text
    assert "width 25.8 pp" in text and "width 17.6 pp" in text
    assert "NO EXCLUSION IS PROPOSED" in text
    assert "RECOMMENDATION" not in text


def test_cross_check_states_are_declared():
    assert U38.CROSSCHECK_STATES == ("graded", "no_overlap_cannot_verify",
                                     "no_reuse_in_cells")
