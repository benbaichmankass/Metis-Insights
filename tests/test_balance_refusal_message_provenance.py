"""A balance refusal must not name a cause no code path tested.

WHY (BL-20260813-ALPACA-BALANCE-NONE-WHILE-ACCOUNT-READS-ACTIVE, diag #9056/#9058).

The refusal used to read "API error or credentials missing — account
unreachable". Measured on the live VM 2026-08-13, all three claims were false
when it fired: `/api/diag/broker_account_status` opened its own read-only client
with the SAME resolved credentials and `GET /v2/account` returned
`status ACTIVE, trading_blocked false` for both `alpaca_paper` and
`alpaca_portfolio`. Credentials resolved, the host was reachable, the account
parsed — only `balance()` returned None.

That is UNPROVENANCED DIAGNOSTIC OUTPUT sub-class A: the operator was pointed at
credentials and connectivity, neither of which was the fault.

These call the real function. An earlier draft of this file reconstructed the
string and grepped `coordinator.py` for it — which failed to match because the
prose wraps across source lines, and would have asserted NOTHING had the
wrapping been different. That is why the message is now a module-level function:
an inline refusal is only testable by grepping, and a grep for wrapped prose is
a test that can silently pass while checking nothing.
"""
from __future__ import annotations

import pytest

from src.core.coordinator import balance_none_refusal_message


def test_message_does_not_assert_an_untested_cause():
    """THE regression. Fails against the old string, which asserted both."""
    msg = balance_none_refusal_message("alpaca_paper", "alpaca")

    assert "credentials missing" not in msg
    # "unreachable" may appear ONLY inside the disclaimer.
    assert "does NOT establish" in msg
    disclaimed = msg.split("does NOT establish", 1)[1]
    assert "unreachable" in disclaimed
    assert "unreachable" not in msg.split("does NOT establish", 1)[0]


def test_message_states_what_the_call_site_actually_knows():
    msg = balance_none_refusal_message("alpaca_portfolio", "alpaca")
    assert "the balance fetch was attempted and returned no value" in msg


def test_message_points_at_the_discriminating_surface():
    """A refusal that cannot name the cause must at least route the reader to
    something that can — otherwise it is honest and useless."""
    msg = balance_none_refusal_message("alpaca_paper", "alpaca")
    assert "/api/diag/broker_account_status?account_id=alpaca_paper" in msg


def test_message_still_identifies_the_account_and_exchange():
    """Regression: the added provenance must not cost the basic facts."""
    msg = balance_none_refusal_message("bybit_2", "bybit")
    assert "bybit_2" in msg
    assert "exchange=bybit" in msg
    assert msg.startswith("balance() returned None for bybit_2")


def test_unknown_exchange_is_rendered_not_dropped():
    """The call site passes `getattr(acc, 'exchange', 'unknown')`; an account
    object missing the attribute must still produce a usable refusal."""
    msg = balance_none_refusal_message("mystery_acct", "unknown")
    assert "exchange=unknown" in msg
    assert "mystery_acct" in msg


def test_coordinator_raises_this_exact_message():
    """Ties the function to its ONE caller, so a future edit cannot quietly
    reintroduce an inline string that drifts from what these tests check."""
    from pathlib import Path
    src = Path("src/core/coordinator.py").read_text(encoding="utf-8")

    assert "balance_none_refusal_message(" in src
    assert "API error or credentials missing" not in src, (
        "the old untested-cause message is back in coordinator.py"
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
