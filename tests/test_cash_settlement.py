"""T+1 settlement basis: the arithmetic, the states, and the documented limits."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from src.runtime.cash_settlement import (
    STATES,
    SettlementBasis,
    UnsettledTotal,
    conservative_settlement_date,
    settled_basis,
    settlement_date,
    unsettled_from_sales,
)

NOW = datetime(2026, 8, 31, 15, 0, tzinfo=timezone.utc)  # a Monday


# --------------------------------------------------------------------------
# The core claim: min() is correct under BOTH unknowns, in all four combos.
# This is why the gate can ship before Alpaca's cash-account semantics are
# established -- see the module docstring.
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "alpaca_nets, we_saw_the_sale, expected",
    [
        # cash 1000, a 300 sale is unsettled -> settled truth is 700.
        (True, True, 700.0),    # both terms agree
        (False, True, 700.0),   # our subtraction supplies the correction
        (True, False, 700.0),   # the venue term binds when our journal missed it
        (False, False, 1000.0),  # NEITHER can see it -- nothing could
    ],
)
def test_min_is_correct_under_every_combination(alpaca_nets, we_saw_the_sale, expected):
    cash = 1000.0
    bp = 700.0 if alpaca_nets else 1000.0
    unsettled = 300.0 if we_saw_the_sale else 0.0
    got = settled_basis(
        venue_cash=cash, venue_buying_power=bp, unsettled_usd=unsettled
    )
    assert got.basis_usd == expected
    assert got.is_measured


def test_the_defect_this_prevents_sizing_against_cash_directly():
    """Fault injection: pin what the OLD basis would have allowed.

    AlpacaClient.buying_power resolves regt -> buying_power -> `cash`. If both
    preferred keys are absent the caller sizes against `cash`, which may include
    unsettled proceeds. This asserts the gap is real and material, so a future
    change that reintroduces the `cash` fallback fails loudly here.
    """
    cash_only_basis = 1000.0  # what falling through to `cash` would hand the sizer
    gated = settled_basis(
        venue_cash=1000.0, venue_buying_power=1000.0, unsettled_usd=300.0
    )
    assert gated.basis_usd == 700.0
    assert cash_only_basis - gated.basis_usd == 300.0, (
        "the whole unsettled amount would have been spendable"
    )


# --------------------------------------------------------------------------
# Settlement dates come from the VENUE calendar, and absence is not a guess.
# --------------------------------------------------------------------------
def test_settlement_is_the_next_trading_day_not_the_next_calendar_day():
    # Fri 2026-08-28 sale; Mon 08-31 is the next trading day.
    cal = [date(2026, 8, 28), date(2026, 8, 31), date(2026, 9, 1)]
    assert settlement_date(date(2026, 8, 28), cal) == date(2026, 8, 31)


def test_a_holiday_does_not_credit_funds_early():
    """The reason we do not count with market_hours.py.

    Mon 2026-08-31 is absent from the calendar (a holiday). A naive
    'next calendar day' rule would settle a Friday sale on Monday and let the
    account spend money it does not have.
    """
    cal = [date(2026, 8, 28), date(2026, 9, 1)]  # Monday missing
    settles = settlement_date(date(2026, 8, 28), cal)
    assert settles == date(2026, 9, 1)
    assert settles != date(2026, 8, 31), "would have credited the funds a day early"


@pytest.mark.parametrize(
    "cal", [None, [], [date(2026, 8, 27)]]  # absent, empty, does not reach past
)
def test_no_calendar_returns_none_rather_than_guessing(cal):
    assert settlement_date(date(2026, 8, 28), cal) is None


# --------------------------------------------------------------------------
# The conservative fallback: late by construction for the ordinary cases,
# and its DOCUMENTED limit is asserted so nobody deletes the caveat.
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "sale_day, true_settle",
    [
        (date(2026, 8, 31), date(2026, 9, 1)),   # Mon -> Tue
        (date(2026, 8, 28), date(2026, 8, 31)),  # Fri -> Mon
        (date(2026, 8, 27), date(2026, 8, 31)),  # Thu + Fri holiday -> Mon
    ],
)
def test_conservative_rule_is_never_earlier_than_the_truth(sale_day, true_settle):
    assert conservative_settlement_date(sale_day) >= true_settle


def test_the_conservative_rule_HAS_a_documented_gap_and_it_is_real():
    """A two-day closure can outrun the 4-calendar-day rule.

    The module docstring says so. This asserts the limit exists rather than
    letting a future reader assume the fallback is universally safe -- if the
    rule is ever widened, this test should be updated deliberately, not
    silently.
    """
    sale = date(2026, 8, 26)               # Wednesday
    true_settle = date(2026, 8, 31)        # Thu+Fri closed -> Monday
    assert conservative_settlement_date(sale) < true_settle


# --------------------------------------------------------------------------
# unsettled_from_sales
# --------------------------------------------------------------------------
def test_unsettled_sums_only_what_has_not_settled():
    cal = [date(2026, 8, 27), date(2026, 8, 28), date(2026, 8, 31), date(2026, 9, 1)]
    sales = [
        (date(2026, 8, 27), 100.0),  # settles 08-28 -> settled by Mon
        (date(2026, 8, 31), 250.0),  # settles 09-01 -> still unsettled on Mon
    ]
    got = unsettled_from_sales(sales, now=NOW, trading_days=cal)
    assert got.total_usd == 250.0
    assert got.used_calendar is True
    assert got.is_complete


def test_missing_calendar_flags_itself_and_holds_longer():
    got = unsettled_from_sales(
        [(date(2026, 8, 28), 250.0)], now=NOW, trading_days=None
    )
    assert got.used_calendar is False
    assert got.total_usd == 250.0, "conservative rule still holds a Friday sale on Monday"


@pytest.mark.parametrize("bad", [None, "abc", float("nan"), float("inf")])
def test_an_unparseable_row_is_COUNTED_not_silently_dropped(bad):
    """Dropping it would shrink the unsettled total and WIDEN the basis.

    That is the permissive direction, so an ungradeable row must be visible to
    the caller rather than absorbed.
    """
    cal = [date(2026, 8, 31), date(2026, 9, 1)]
    got = unsettled_from_sales(
        [(date(2026, 8, 31), bad), (date(2026, 8, 31), 250.0)],
        now=NOW,
        trading_days=cal,
    )
    assert got.ungradeable == 1
    assert not got.is_complete
    assert got.total_usd == 250.0, "the readable row still counts; the total is a LOWER BOUND"


@pytest.mark.parametrize("nil", [0.0, -50.0])
def test_a_nonpositive_sale_is_skipped_WITHOUT_poisoning_the_total(nil):
    """Distinct from unparseable: nothing settles from it, and we know that."""
    cal = [date(2026, 8, 31), date(2026, 9, 1)]
    got = unsettled_from_sales([(date(2026, 8, 31), nil)], now=NOW, trading_days=cal)
    assert got.total_usd == 0.0
    assert got.is_complete, "a zero sale is readable, not ungradeable"


def test_an_incomplete_total_must_be_passed_as_unknown_not_as_the_partial_sum():
    """The contract between the two halves, asserted rather than assumed."""
    cal = [date(2026, 8, 31), date(2026, 9, 1)]
    got = unsettled_from_sales(
        [(date(2026, 8, 31), "corrupt"), (date(2026, 8, 31), 250.0)],
        now=NOW,
        trading_days=cal,
    )
    # Correct caller behaviour on an incomplete total:
    honest = settled_basis(
        venue_cash=1000.0,
        venue_buying_power=1000.0,
        unsettled_usd=got.total_usd if got.is_complete else None,
    )
    assert honest.state == "journal_unreadable"
    # The tempting shortcut -- spending against the partial sum -- would hand
    # the sizer 750 while an unknown amount is still unsettled.
    wrong = settled_basis(
        venue_cash=1000.0, venue_buying_power=1000.0, unsettled_usd=got.total_usd
    )
    assert wrong.basis_usd == 750.0
    assert wrong.state == "measured", "and it would falsely claim to be measured"


# --------------------------------------------------------------------------
# States are never collapsed.
# --------------------------------------------------------------------------
def test_venue_unreadable_is_not_a_number():
    got = settled_basis(venue_cash=None, venue_buying_power=None, unsettled_usd=0.0)
    assert got.state == "venue_unreadable"
    assert got.basis_usd is None, "None means 'could not look', never 0.0"


def test_journal_unreadable_is_distinct_from_nothing_unsettled():
    unreadable = settled_basis(
        venue_cash=1000.0, venue_buying_power=1000.0, unsettled_usd=None
    )
    nothing_unsettled = settled_basis(
        venue_cash=1000.0, venue_buying_power=1000.0, unsettled_usd=0.0
    )
    assert unreadable.state == "journal_unreadable"
    assert nothing_unsettled.state == "measured"
    # Same number, DIFFERENT epistemic state -- which is the whole point.
    assert unreadable.basis_usd == nothing_unsettled.basis_usd == 1000.0
    assert unreadable.state != nothing_unsettled.state


def test_no_calendar_downgrades_the_state_but_still_answers():
    got = settled_basis(
        venue_cash=1000.0,
        venue_buying_power=1000.0,
        unsettled_usd=300.0,
        used_calendar=False,
    )
    assert got.state == "estimated_no_calendar"
    assert got.basis_usd == 700.0
    assert not got.is_measured


def test_every_declared_state_is_reachable():
    """A declared state no producer emits is decoration."""
    seen = {
        settled_basis(venue_cash=None, venue_buying_power=None, unsettled_usd=0.0).state,
        settled_basis(venue_cash=1.0, venue_buying_power=1.0, unsettled_usd=None).state,
        settled_basis(venue_cash=1.0, venue_buying_power=1.0, unsettled_usd=0.0).state,
        settled_basis(
            venue_cash=1.0, venue_buying_power=1.0, unsettled_usd=0.0, used_calendar=False
        ).state,
    }
    assert seen == set(STATES)


def test_basis_clamps_at_zero_rather_than_going_negative():
    got = settled_basis(
        venue_cash=100.0, venue_buying_power=100.0, unsettled_usd=500.0
    )
    assert got.basis_usd == 0.0
    assert got.unsettled_usd == 500.0, "the raw evidence is still reported"
