"""The prop safety line must never print a Python repr where a number belongs.

WHY THIS EXISTS
---------------
Observed live 2026-08-23. The operator typed ``bal 4871 4871`` into the prop bot
and got back::

    ✅ account status recorded [breakout_1] · to daily-loss $None · to DD-floor $171.0

``distance_to_daily_loss_usd`` is legitimately ``None`` — ``realized_today`` and
``day_start_balance`` are null on every ``prop_account_status`` row, so the
distance genuinely cannot be computed and refusing to state one is CORRECT.
Rendering that refusal as ``$None`` is not: it wears a dollar sign, so it reads
as a value, on the one message whose whole job is to say how much cushion is
left before an account-killer.

This is the operator-facing instance of the collapsed-state family: *"we did not
measure this"* and *"the distance is zero"* must not look alike, and ``$None``
is worse than either because it looks like neither.

WHAT IS ASSERTED
----------------
Every state the field can take, including the two that are easy to skip: a
genuine **zero** (which must stay a number, not become "not measured") and a
**negative** (which means the line is already crossed — the most important thing
this message can say).
"""
from __future__ import annotations

import pytest

from src.prop.telegram_report_handler import _cushion


def test_a_missing_distance_says_so_in_words_and_carries_no_dollar_sign():
    out = _cushion(None)
    assert out == "not measured"
    assert "$" not in out, "a non-measurement must not wear a currency sign"
    assert "None" not in out, "the live defect, verbatim"


def test_a_real_distance_is_money_formatted_not_str_dumped():
    assert _cushion(171.0) == "$171.00", "'$171.0' was also sloppy for money"
    assert _cushion(149.4858) == "$149.49"
    assert _cushion(1234.5) == "$1,234.50"


def test_zero_is_a_MEASUREMENT_and_must_not_read_as_missing():
    """The distinction the whole row is about, in its sharpest form.

    A cushion of exactly zero means the account is ON the line — that is a
    measured fact and one of the most urgent states possible. Collapsing it into
    "not measured" would be the same defect pointing the other way.
    """
    assert _cushion(0) == "$0.00"
    assert _cushion(0.0) == "$0.00"
    assert _cushion(0) != _cushion(None)


def test_a_negative_distance_says_BREACHED_rather_than_hiding_behind_a_minus():
    """A crossed line must not render as '$-25.50'.

    That is the same failure as '$None' one state over: the most important thing
    the message can carry, in the shape a reader skims past.
    """
    assert _cushion(-25.5) == "BREACHED by $25.50"
    assert _cushion(-0.01) == "BREACHED by $0.01"
    assert "-$" not in _cushion(-25.5)


def test_a_non_numeric_that_is_not_None_is_named_not_dollar_signed():
    out = _cushion("abc")
    assert out.startswith("unreadable")
    assert not out.startswith("$")


def test_nan_and_inf_are_NOT_MEASURED_rather_than_dollar_nan():
    """NaN/inf are genuine floats, so they slip past a try/except on float().

    They render as "$nan" / "$inf" if unhandled — a currency sign glued to a
    computation failure, which is the "$None" defect in a third costume.
    """
    assert _cushion(float("nan")) == "not measured"
    assert _cushion(float("inf")) == "not measured"
    assert _cushion(float("-inf")) == "not measured"


@pytest.mark.parametrize("bad", [None, "abc", float("nan"), float("inf")])
def test_no_input_can_produce_a_bare_dollar_repr(bad):
    """The invariant, stated once over the failure inputs.

    Whatever comes out, it is never a currency sign glued to a Python repr.
    """
    out = _cushion(bad)
    assert not out.startswith("$"), f"a failure state must not wear a currency sign: {out!r}"
