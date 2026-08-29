"""T+1 settlement basis: the arithmetic, the states, and the documented limits."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone

import pytest

from src.runtime.cash_settlement import (
    STATES,
    _parse_closed_at,
    may_apply,
    recent_sales,
    record_observation,
    resolve_for_account,
    settlement_mode,
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


# --------------------------------------------------------------------------
# The I/O half: readers, the gate, and the orchestrator's failure states.
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("2026-08-28T14:00:00Z", date(2026, 8, 28)),
        ("2026-08-28T14:00:00+00:00", date(2026, 8, 28)),
        ("1788012000000", date(2026, 8, 29)),  # epoch MS, the reconciler's form
        ("", None),
        (None, None),
        ("not-a-date", None),
    ],
)
def test_closed_at_parses_both_forms_and_refuses_the_rest(raw, expected):
    """The epoch-ms form is the one that produced the '0 closed trades' bug."""
    assert _parse_closed_at(raw) == expected


def _make_db(tmp_path, rows):
    path = tmp_path / "j.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE trades (account_id TEXT, status TEXT, exit_price REAL,"
        " position_size REAL, closed_at TEXT)"
    )
    conn.executemany("INSERT INTO trades VALUES (?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return str(path)


def test_recent_sales_returns_None_on_an_unreadable_db_not_empty(tmp_path):
    """`None` and `[]` mean opposite things and must not be conflated."""
    assert recent_sales("alpaca_live", db_path=str(tmp_path / "nope.db")) is None


def test_recent_sales_reads_proceeds(tmp_path):
    db = _make_db(
        tmp_path,
        [("alpaca_live", "closed", 10.0, 5.0, "2026-08-31T12:00:00Z"),
         ("other_acct", "closed", 99.0, 9.0, "2026-08-31T12:00:00Z")],
    )
    got = recent_sales("alpaca_live", now=NOW, db_path=db)
    assert got == [(date(2026, 8, 31), 50.0)], "scoped to the account, price x qty"


def test_orchestrator_reports_journal_unreadable_when_a_row_cannot_be_dated(tmp_path):
    """An undateable row must NOT be treated as old enough to have settled."""

    class _Client:
        def account_status(self):
            return {"capacity": {"cash": 1000.0, "buying_power": 1000.0}}

        def trading_days(self, start, end):
            return [date(2026, 8, 31), date(2026, 9, 1)]

    db = _make_db(
        tmp_path, [("alpaca_live", "closed", 10.0, 5.0, "garbage-timestamp")]
    )
    import src.runtime.cash_settlement as cs

    orig = cs.recent_sales
    cs.recent_sales = lambda acct, **kw: orig(acct, db_path=db, **{k: v for k, v in kw.items() if k != "db_path"})
    try:
        got = resolve_for_account("alpaca_live", _Client(), now=NOW)
    finally:
        cs.recent_sales = orig
    assert got.state == "journal_unreadable"


def test_orchestrator_survives_a_broker_that_raises():
    class _Broken:
        def account_status(self):
            raise RuntimeError("gateway down")

    got = resolve_for_account("alpaca_live", _Broken(), now=NOW)
    assert got.state in STATES
    assert got.basis_usd is None, "no venue figure -> nothing to spend against"


def test_apply_binds_only_an_allowlisted_account(monkeypatch):
    monkeypatch.setenv("ALPACA_CASH_SETTLEMENT_MODE", "apply")
    monkeypatch.setenv("ALPACA_CASH_SETTLEMENT_ACCOUNTS", "")
    assert may_apply("alpaca_live") is False, "empty allowlist means NONE, not ALL"
    monkeypatch.setenv("ALPACA_CASH_SETTLEMENT_ACCOUNTS", "alpaca_live")
    assert may_apply("alpaca_live") is True
    assert may_apply("alpaca_paper") is False
    assert may_apply(None) is False


@pytest.mark.parametrize("raw, expected", [("off", "off"), ("apply", "apply"),
                                           ("APPLY", "apply"), ("typo", "annotate"),
                                           ("", "annotate")])
def test_mode_falls_back_to_annotate_never_to_off_or_apply(monkeypatch, raw, expected):
    monkeypatch.setenv("ALPACA_CASH_SETTLEMENT_MODE", raw)
    assert settlement_mode() == expected


def test_soak_row_distinguishes_held_back_from_applied(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPACA_CASH_SETTLEMENT_MODE", "apply")
    monkeypatch.setenv("ALPACA_CASH_SETTLEMENT_ACCOUNTS", "")  # not allowlisted
    import src.runtime.cash_settlement as cs

    monkeypatch.setattr(cs, "runtime_logs_dir", lambda: tmp_path, raising=False)
    monkeypatch.setattr(
        "src.utils.paths.runtime_logs_dir", lambda: tmp_path, raising=False
    )
    basis = settled_basis(
        venue_cash=1000.0, venue_buying_power=1000.0, unsettled_usd=300.0
    )
    record_observation(
        account_id="alpaca_live", basis=basis, available_usd=1000.0, applied=False
    )
    rows = [
        json.loads(line)
        for line in (tmp_path / "cash_settlement_soak.jsonl").read_text().splitlines()
    ]
    assert len(rows) == 1
    assert rows[0]["applied"] is False
    assert rows[0]["apply_scope"] == "not_allowlisted"
    assert rows[0]["global_mode"] == "apply", "what was ASKED, beside what happened"
    assert rows[0]["would_have_reduced_usd"] == 300.0
