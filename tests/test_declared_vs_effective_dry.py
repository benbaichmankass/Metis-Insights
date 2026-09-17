"""MI-278 U39 — does an account's DECLARED liveness agree with what the executor did?

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
    "_u39", os.path.join(_HERE, "scripts", "research",
                         "declared_vs_effective_dry.py"))
U39 = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(U39)

SIZ = "REJECTED: dry_run_sizing_skip: risk_refused: sized_qty=0 with balance=200.10"
AMD = "REJECTED: account_mode_dry_run | tlt_pullback_1h signal"
DRY = "dry_run_no_order_placed"
CUN = "exchange_client_unavailable_no_order_placed"


def r(rid, created, reason, is_dry=None, status="rejected"):
    notes = {"reason": reason}
    if is_dry is not None:
        notes["is_dry"] = is_dry
    return {"id": rid, "account_id": "alpaca_live", "created_at": created,
            "entry_reason": reason, "status": status, "notes": json.dumps(notes),
            "account_class": "real_money"}


@pytest.fixture()
def rows():
    return [r(1, "2026-08-25T14:05:29", SIZ, is_dry=True),
            r(2, "2026-08-27T14:06:17", SIZ, is_dry=True),
            r(3, "2026-08-28T13:53:58", AMD, is_dry=True),
            r(4, "2026-09-01T16:05:36", DRY, is_dry=True),
            r(5, "2026-09-11T13:55:37", DRY, is_dry=True)]


def test_self_test_passes_in_full():
    """Catches: any control in the module's own suite regressing."""
    assert U39.self_test() == 0


# ------------------------------------------------------------------ is_dry


@pytest.mark.parametrize("row", [
    {"notes": json.dumps({"reason": DRY}), "entry_reason": DRY},
    {"notes": "not json", "entry_reason": DRY},
    {"notes": None, "entry_reason": DRY},
])
def test_absent_or_unreadable_is_dry_is_none_never_false(row):
    """Catches: an unrecorded is_dry reading as False, i.e. as a LIVE dispatch —
    which would turn 'we did not look' into 'the account traded'."""
    assert U39.observed_is_dry(row) is None


# ------------------------------------------------------------------- causes


def test_client_unavailable_is_not_an_intentional_dry_run():
    """Catches: folding exchange_client_unavailable into the dry bucket. The
    executor writes a distinct reason precisely to keep them apart
    (BL-20260707-MGCTREND-REASON-MISMATCH); folding it at the reading end undoes
    that."""
    assert U39.cause_of(r(1, "2026-09-01T00:00:00", CUN, is_dry=False)) \
        == "exchange_client_unavailable"


def test_every_cause_is_in_the_declared_vocabulary(rows):
    assert all(U39.cause_of(x) in U39.REFUSAL_CAUSES for x in rows)


# --------------------------------------------------------------------- eras


def test_current_era_is_the_newest_not_the_modal(rows):
    """Catches: reporting the modal cause as the standing condition — the exact
    reading that filed BL-20260909 two weeks after its cause stopped binding."""
    assert U39.current_era(rows)["cause"] == "dry_run_no_order_placed"


def test_a_recurring_cause_gets_its_own_era():
    """Catches: merging non-contiguous runs, which would erase a cause that came
    back."""
    got = U39.eras([r(1, "2026-09-01T00:00:00", DRY, is_dry=True),
                    r(2, "2026-09-02T00:00:00", SIZ, is_dry=True),
                    r(3, "2026-09-03T00:00:00", DRY, is_dry=True)])
    assert len(got) == 3


def test_no_rows_has_no_era_rather_than_an_empty_one():
    assert U39.current_era([]) is None


# ---------------------------------------------------------------- gate state


def test_declared_live_but_booked_dry_is_its_own_state(rows):
    """Catches: the finding being folded into agrees_dry, which would report a
    real-money gate disagreement as normal dry-run behaviour."""
    assert U39.gate_state(rows, declared_live=True) == "declared_live_books_dry"


def test_unknown_declared_state_is_ungradeable_not_dry(rows):
    """Catches: treating 'we could not establish the declared gate' as dry, which
    silently converts a missing input into a clean agreement."""
    assert U39.gate_state(rows, declared_live=None) == "ungradeable_no_is_dry"


def test_no_rows_is_not_an_agreement():
    """Catches: an account nobody routed to reading as a healthy live account."""
    assert U39.gate_state([], declared_live=True) == "no_rows_observed"


def test_rows_without_is_dry_cannot_grade_agreement():
    assert U39.gate_state([r(1, "2026-09-01T00:00:00", DRY)], declared_live=True) \
        == "ungradeable_no_is_dry"


# --------------------------------------------------------------- attribution


def test_both_gates_permissive_and_dry_is_unattributed(rows):
    """Catches: naming a cause the evidence does not support. unattributed_dry is
    the honest terminal state, not a fallback."""
    assert U39.dry_attribution(rows, account_mode_live=True,
                               strategy_execution_live=True) == "unattributed_dry"


@pytest.mark.parametrize("mode,exec_,want", [
    (False, True, "account_mode"),
    (True, False, "strategy_shadow"),
    (None, True, "ungradeable"),
    (True, None, "ungradeable"),
])
def test_attribution_names_the_gate_that_explains_it(rows, mode, exec_, want):
    assert U39.dry_attribution(rows, account_mode_live=mode,
                               strategy_execution_live=exec_) == want


def test_attribution_is_scoped_to_the_current_era(rows):
    """Catches: attributing an OLD era with TODAY's gate flags. On the real
    account the mode was dry_run until 2026-08-27 and live after, so a
    window-wide attribution answers about rows a declared gate fully explains."""
    mixed = [r(1, "2026-08-20T00:00:00", CUN, is_dry=True),
             r(2, "2026-09-01T00:00:00", DRY, is_dry=True),
             r(3, "2026-09-11T00:00:00", DRY, is_dry=True)]
    got = U39.assess(mixed, account_id="x", declared_live=True,
                     account_mode_live=True, strategy_execution_live=True)
    assert got["dry_attribution_scope"] == "current_era"
    assert got["dry_attribution"] == "unattributed_dry"
    assert got["dry_attribution_window_wide"] == "client_unavailable"
    assert got["dry_attribution"] != got["dry_attribution_window_wide"]


# --------------------------------------------------------------- tradeability


def test_one_cause_is_structurally_unable_and_several_is_not(rows):
    """Catches: calling a mixed-cause account structurally unable, which is a
    tuning question rather than a standing condition."""
    assert U39.tradeability([rows[3], rows[4]]) == "structurally_unable"
    assert U39.tradeability(rows) == "refusing_mixed_causes"


def test_any_placement_means_traded(rows):
    placed = rows + [r(9, "2026-09-12T00:00:00", "filled", is_dry=False,
                       status="closed")]
    assert U39.tradeability(placed) == "traded"


def test_no_rows_is_not_structurally_unable():
    assert U39.tradeability([]) == "no_rows_observed"


# -------------------------------------------------------------------- balance


def test_an_unquoted_balance_is_none_never_zero():
    """Catches: a refusal that quoted no balance reading as a zero balance, which
    would invent the very condition the backlog row is about."""
    assert U39.balance_in(r(1, "2026-09-01T00:00:00", DRY)) is None
    assert U39.balance_in(r(1, "2026-09-01T00:00:00", SIZ)) == 200.10


# --------------------------------------------------------------------- report


def test_report_states_population_scope_and_both_warnings(rows):
    """Catches: dropping the loud real-money warning, hiding the attribution
    scope, or omitting the population."""
    res = U39.assess(rows, account_id="alpaca_live", declared_live=True,
                     account_mode_live=True, strategy_execution_live=True)
    text = "\n".join(U39.report(res))
    assert "5 journal row(s)" in text
    assert "DECLARED GATE SAYS LIVE AND THE EXECUTOR BOOKED DRY" in text
    assert "NOT 'no cause exists'" in text
    assert "over the CURRENT ERA ONLY" in text
    assert "Window-wide, for comparison only" in text
    assert "denominator 5" in text


def test_empty_account_does_not_read_as_quiet():
    res = U39.assess([], account_id="x", declared_live=True,
                     account_mode_live=True, strategy_execution_live=True)
    text = "\n".join(U39.report(res))
    assert "no era EXISTS" in text and "not a quiet account" in text


def test_vocabularies_are_declared():
    assert "unattributed_dry" in U39.DRY_ATTRIBUTION
    assert "declared_live_books_dry" in U39.GATE_STATES
    assert "structurally_unable" in U39.TRADEABILITY
