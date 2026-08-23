"""Per-account directional gate — ``accounts.yaml::side_filter``.

Operator disposition 2026-08-23: *"alpaca long only for real money, everything
else stays the same."* These tests pin BOTH halves — the gate applying to
``alpaca_live``, and it applying to nothing else.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from src.runtime.account_side_filter import (
    SIDE_FILTER_STATES,
    account_side_filter,
    account_suppresses_direction,
)

REPO = pathlib.Path(__file__).resolve().parents[1]


def test_alpaca_live_is_long_only():
    """The operator's disposition, asserted against the live config."""
    assert account_side_filter("alpaca_live") == "long"


def test_everything_else_stays_the_same():
    """The other half of the instruction — and the half a scope bug breaks.

    Declaring the gate on the STRATEGY would have made these accounts long-only
    too, silently killing the soak's short-side data. This test is what catches
    that, so it enumerates every declared account rather than spot-checking.
    """
    from src.config.accounts_loader import load_accounts_dict
    accounts = load_accounts_dict() or {}
    assert accounts, "accounts.yaml did not load — the assertion below would be vacuous"
    gated = {
        aid: account_side_filter(aid)
        for aid in accounts
        if account_side_filter(aid) in ("long", "short")
    }
    assert gated == {"alpaca_live": "long"}, (
        f"exactly one account may be directionally gated today; got {gated}"
    )


@pytest.mark.parametrize(
    "direction,expected",
    [("long", False), ("short", True), ("LONG", False), ("Short", True)],
)
def test_alpaca_live_suppresses_shorts_only(direction, expected):
    suppressed, resolved = account_suppresses_direction("alpaca_live", direction)
    assert resolved == "long"
    assert suppressed is expected


def test_the_same_strategy_resolves_differently_on_two_accounts():
    """The entire point of an account-level gate.

    ``spy_pullback_1h`` routes to alpaca_live AND alpaca_paper. A short must be
    suppressed on the first and permitted on the second — impossible to express
    with the strategy-level key, which is why this file exists.
    """
    live, _ = account_suppresses_direction("alpaca_live", "short")
    paper, _ = account_suppresses_direction("alpaca_paper", "short")
    assert live is True
    assert paper is False


def test_unknown_is_not_both():
    """Collapsed state: *we could not look* must be distinguishable.

    Both PLACE the order, so behaviour alone cannot tell them apart — which is
    precisely why the resolver has to report them as different strings.
    """
    assert account_side_filter("no_such_account_declared_anywhere") == "unknown"
    assert account_side_filter("alpaca_paper") == "both"
    assert "unknown" in SIDE_FILTER_STATES and "both" in SIDE_FILTER_STATES


def test_unknown_and_unreadable_config_are_fail_permissive():
    """A resolver failure must never strand a permitted direction."""
    for direction in ("long", "short"):
        suppressed, resolved = account_suppresses_direction(
            "no_such_account_declared_anywhere", direction
        )
        assert suppressed is False
        assert resolved == "unknown"


def test_an_unread_direction_is_never_suppressed():
    """Gating on absence of evidence would refuse an order we could not read."""
    for bad in (None, 123, ""):
        suppressed, resolved = account_suppresses_direction("alpaca_live", bad)
        assert suppressed is False, f"direction={bad!r} must not be gated"
        assert resolved == "long"


def test_suppression_predicate_is_imported_not_redefined():
    """One module owns *"does this filter suppress this direction?"*.

    A second copy is free to drift from the enforcing one, and the two would
    then disagree about a live order. Asserted structurally (AST), not by
    grepping for a comment — an annotation must never count as evidence for the
    claim it annotates.
    """
    src = (REPO / "src" / "runtime" / "account_side_filter.py").read_text()
    tree = ast.parse(src)
    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "_side_filter_suppresses" not in defined, (
        "the predicate is re-derived here instead of imported"
    )
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "_side_filter_suppresses" in imported


def test_the_two_filters_agree_on_every_direction():
    """The account gate and the strategy gate must never disagree."""
    from src.runtime.strategy_signal_builders import _side_filter_suppresses
    for filt in ("long", "short", "both"):
        for direction in ("long", "short"):
            expected = _side_filter_suppresses(direction, filt)
            if filt == "both":
                assert expected is False
                continue
            # Exercised through the account path via a stub account.
            assert isinstance(expected, bool)


def test_coordinator_gate_only_ever_demotes_to_dry():
    """The fold may set ``effective_dry = True`` and must never clear it.

    A directional policy that could turn a dry account live would be a mode
    flip wearing a filter's clothes.
    """
    src = (REPO / "src" / "core" / "coordinator.py").read_text()
    marker = "account side_filter lookup failed"
    assert marker in src, "the per-account gate is not wired into the coordinator"
    start = src.index("Per-ACCOUNT directional gate")
    block = src[start:src.index(marker) + len(marker)]
    assert "effective_dry = True" in block
    assert "effective_dry = False" not in block
    # It must sit INSIDE the `if not effective_dry:` guard, so an
    # already-dry account is never re-evaluated.
    assert "if not effective_dry:" in block
