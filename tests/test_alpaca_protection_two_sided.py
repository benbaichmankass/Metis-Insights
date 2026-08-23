"""Alpaca protection must be graded per SIDE — BL-20260816-COVERAGE-IS-ONE-SIDED.

`_check_broker_naked_equity_positions` decided on `has_protective_orders`,
which returns True on the FIRST leg matching `"stop" in otype or otype ==
"limit"` — one membership test over both sides. So a stop-only book answered
"protected", the sweep skipped it, and a position that can only stop out or run
was invisible. Measured on IB the same day (both `ib_paper` positions
stop-covered, ZERO limit orders account-wide); Alpaca carried the identical
grading with 13 live positions behind it.

The classifier is exercised through a fake transport so no broker is needed.
"""
from __future__ import annotations

import pytest

from src.units.accounts.alpaca_client import AlpacaClient


class _FakeClient(AlpacaClient):
    """Bypass __init__ so no credentials/HTTP are involved."""
    def __init__(self, legs):
        self._legs = legs

    def _open_orders_for_symbol(self, symbol):  # type: ignore[override]
        return self._legs


@pytest.mark.parametrize("legs,expect", [
    ([{"type": "stop"}],                       {"stop": True,  "target": False}),
    ([{"type": "limit"}],                      {"stop": False, "target": True}),
    ([{"type": "stop"}, {"type": "limit"}],    {"stop": True,  "target": True}),
    ([],                                       {"stop": False, "target": False}),
    ([{"type": "trailing_stop"}],              {"stop": True,  "target": False}),
    ([{"order_type": "stop"}],                 {"stop": True,  "target": False}),
])
def test_sides_are_graded_separately(legs, expect):
    st = _FakeClient(legs).protection_state("SPY")
    assert st is not None
    assert {"stop": st["stop"], "target": st["target"]} == expect


def test_stop_limit_is_a_STOP_not_a_target():
    """The precedence trap, and the one that would be worse than the bug.

    Alpaca's type string for a stop-limit is `"stop_limit"`, which CONTAINS
    "limit". A naive limit-first test files it as a take-profit — MANUFACTURING
    target coverage that does not exist, which is strictly worse than the
    one-sided grading being fixed: the old bug hid a real gap, this would
    invent a fill. Mirrors `IBClient._protective_leg_side`'s ordering.
    """
    st = _FakeClient([{"type": "stop_limit"}]).protection_state("SPY")
    # Assert the keys this test is ABOUT, not the whole dict. `protection_state`
    # gained additive `stop_prices` / `target_prices` on 2026-08-23
    # (BL-20260820-PROTECTION-COVERAGE-IS-PRICE-BLIND criterion 5), and an
    # exact-equality assertion breaks on every additive field — which makes a
    # correct, purely-additive change look like a regression.
    assert st["stop"] is True
    assert st["target"] is False
    assert st["legs"] == 1


def test_a_stop_only_book_is_not_protected_on_the_target_side():
    """The whole finding in one assertion."""
    st = _FakeClient([{"type": "stop"}]).protection_state("QQQ")
    assert st["stop"] is True and st["target"] is False


def test_read_failure_is_None_not_an_empty_book():
    """`None` must never collapse into "no legs" — that would re-arm blind."""
    assert _FakeClient(None).protection_state("SPY") is None


def test_has_protective_orders_still_answers_its_OWN_question():
    """The combined test survives there deliberately, and that is correct.

    `has_protective_orders` answers "does ANY protective leg rest?", for which
    a stop-only book truthfully answers True. Its docstring forbids
    naked-detection use and routes callers to `protection_state`. Widening this
    assertion to forbid the combined test anywhere would force a change to a
    call site that is not wrong — the same carve-out the IB test makes.
    """
    assert _FakeClient([{"type": "stop"}]).has_protective_orders("SPY") is True


def test_the_sweep_consumes_protection_state_not_the_boolean():
    """Guard against reintroduction at the DECISION site, which is the bug."""
    import inspect
    from src.runtime import order_monitor
    src = inspect.getsource(order_monitor._check_broker_naked_equity_positions)
    assert "protection_state(" in src, (
        "the equity naked sweep must grade sides via protection_state"
    )
    assert "has_protective_orders(" not in src, (
        "the any-leg boolean must not be the sweep's decision input — a "
        "stop-only book answers True and the target gap goes invisible"
    )
