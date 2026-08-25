"""`is_policy_hold` — the BROAD declared-no-op predicate, and the namespace
invariant that makes a prefix test safe rather than sloppy.

Context (2026-08-25, operator decision on
``BL-20260825-DECLARED-POLICY-HOLDS-GRADE-AS-REFUSALS-IN-THE-DEAD-LEG-VOCABULARY``):
three call sites already used this broad rule and a fourth — ``dead_leg`` —
used a narrower one, because the broad rule lived in two nested closures that
nothing could import. The fix lifts it to module level. These tests pin the
three things that could make that fix wrong later.
"""
import pytest

from src.runtime.dead_leg import bucket_for
from src.runtime.execution_diagnostics import (
    EXPECTED_DISPATCH_SKIP_REASONS,
    is_expected_dispatch_skip,
    is_policy_hold,
)

# The three tokens this change is about, in the exact form the coordinator
# emits them (verified against src/core/coordinator.py, not invented here:
# f"intent_noop:{delta.reason}" at :1998/:2009,
# f"intent_noop:hold_to_bracket_{delta.action}_non_derivative" at :2120,
# f"reentry_suppressed_netting_guard:{delta.action}" at :1945).
DECLARED_POLICY_REASONS = [
    "intent_noop:flip_suppressed_hold_policy: desired buy opposes held sell",
    "intent_noop:hold_to_bracket_reduce_non_derivative",
    "reentry_suppressed_netting_guard:increase",
]

# Reasons that are GENUINE failures and must never be swallowed. The first is
# the load-bearing one: the coordinator names it deliberately OUTSIDE the
# `intent_noop:` namespace, with the comment "Failure -> a real error so the
# alert DOES fire". If a future edit moves it inside, this test fails.
GENUINE_FAILURE_REASONS = [
    "intent_close_flatten_failed:timeout",
    "exchange_client_unavailable_no_order_placed",
    "sizing_failed: RuntimeError: balance() returned None for ib_paper",
    "RiskBreach: INTRADAY_DRAWDOWN",
    "zero_balance",
]


@pytest.mark.parametrize("reason", DECLARED_POLICY_REASONS)
def test_declared_policy_reasons_are_holds(reason):
    assert is_policy_hold(reason) is True


@pytest.mark.parametrize("reason", DECLARED_POLICY_REASONS)
def test_declared_policy_reasons_bucket_as_policy_skipped(reason):
    """The behavioural half — the predicate is only useful via `bucket_for`."""
    assert bucket_for("rejected", reason) == "policy_skipped"


@pytest.mark.parametrize("reason", GENUINE_FAILURE_REASONS)
def test_genuine_failures_are_not_holds(reason):
    assert is_policy_hold(reason) is False
    assert bucket_for("rejected", reason) == "refused"


def test_intent_close_flatten_failed_is_not_inside_the_noop_namespace():
    """THE invariant the prefix rule rests on, asserted on the string itself.

    `intent_close_flatten_failed:` shares the `intent_` stem with
    `intent_noop:` and is a FAILURE. A prefix test is only safe because the
    namespace boundary is the colon-terminated `intent_noop:`, not `intent_`.
    """
    failure = "intent_close_flatten_failed:timeout"
    assert not failure.startswith("intent_noop:")
    assert is_policy_hold(failure) is False


def test_is_policy_hold_is_a_strict_superset_of_is_expected_dispatch_skip():
    """Both predicates survive, and their relationship is the point.

    Positive control first: the subset direction must actually hold for every
    declared token, or the "superset" claim is untested vacuity.
    """
    for token in EXPECTED_DISPATCH_SKIP_REASONS:
        assert is_expected_dispatch_skip(token) is True, token
        assert is_policy_hold(token) is True, token
    # ...and it is STRICT: at least one reason separates them. Without this,
    # a future edit collapsing the two into one predicate passes silently.
    strictly_broader = [
        r for r in DECLARED_POLICY_REASONS if not is_expected_dispatch_skip(r)
    ]
    assert strictly_broader == DECLARED_POLICY_REASONS, (
        "these three must NOT be in the narrow set — the narrow set is about "
        "an account being declared off, not about a decision to hold"
    )


def test_never_raises_on_junk():
    for junk in (None, 0, object(), b"bytes", ["list"]):
        assert is_policy_hold(junk) in (True, False)


def test_omitting_the_reason_preserves_pre_optin_behaviour():
    """`bucket_for(status)` with no reason must still grade `refused`.

    Callers that have not opted in are unaffected — the guarantee `bucket_for`'s
    own docstring makes, and the one `tests/test_dead_leg_caller_parity.py`
    exists to keep true across call sites.
    """
    assert bucket_for("rejected") == "refused"
    assert bucket_for("rejected", None) == "refused"
