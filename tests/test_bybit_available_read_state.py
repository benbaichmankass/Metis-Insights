"""The Bybit available-margin read must say WHICH branch it took.

BL-20260701-BYBIT-AVAILABLE-FIELD (filed 2026-08-13). The read used to return
a bare ``Optional[float]`` and log nothing on either non-venue branch, so three
materially different states arrived at the sizer as one value:

  * the account-level ``totalAvailableBalance``  — broker truth
  * the per-coin ``availableToWithdraw``         — a SUBSTITUTE (deprecated for
                                                   UNIFIED accounts 2025-01-09;
                                                   withdrawal eligibility, not
                                                   new-order margin)
  * nothing readable                             — we COULD NOT LOOK

That collapse is why establishing what had happened on bybit_2 during the
2026-08 110007 rejections required four diag pulls and a proof by contradiction
(BL-20260813-ICTSCALP-BTC-BYBIT2-BALANCE-REJECTS) — and even then the two
non-venue branches stayed undecidable from outside.

The load-bearing test in this file is
``test_sizer_facing_return_is_byte_for_byte_unchanged``: the split is
observability only. If it ever fails, a read-path change has silently become an
order-path change.
"""
from __future__ import annotations

import logging

import pytest

from src.units.accounts.execute import (
    AVAILABLE_STATE_DEPRECATED,
    AVAILABLE_STATE_UNAVAILABLE,
    AVAILABLE_STATE_VENUE,
    _fetch_linear_available_balance,
    read_linear_available_balance,
)


class _Client:
    """Minimal stand-in for the Bybit client's one method used here."""

    def __init__(self, account: dict | None = None, raises: Exception | None = None):
        self._account = account
        self._raises = raises

    def get_wallet_balance(self, accountType: str):  # noqa: N803  (venue's spelling)
        assert accountType == "UNIFIED"
        if self._raises is not None:
            raise self._raises
        return {"result": {"list": [self._account]}} if self._account is not None else {}


def _venue(total: str = "264.71", coin_avail: str = "999.99") -> _Client:
    return _Client({
        "totalAvailableBalance": total,
        "coin": [{"coin": "USDT", "availableToWithdraw": coin_avail}],
    })


def _deprecated_only(coin_avail: str = "274.91") -> _Client:
    # The shape that matters: the account-level field present but EMPTY, which
    # is what Bybit returns for account types that do not populate it. An empty
    # string is not zero and must not be read as one.
    return _Client({
        "totalAvailableBalance": "",
        "coin": [{"coin": "USDT", "availableToWithdraw": coin_avail}],
    })


# ── the three states are reachable and distinct ────────────────────────────

def test_venue_field_is_broker_truth():
    value, state, detail = read_linear_available_balance(_venue("264.71"))
    assert (value, state) == (pytest.approx(264.71), AVAILABLE_STATE_VENUE)
    assert detail is None


def test_venue_field_wins_over_the_deprecated_one():
    """Preference order is the whole point of the 2026-07-01 change."""
    value, state, _ = read_linear_available_balance(_venue("264.71", "999.99"))
    assert value == pytest.approx(264.71)
    assert state == AVAILABLE_STATE_VENUE


@pytest.mark.parametrize("absent", ["", "null", None])
def test_absent_account_field_falls_back_and_SAYS_SO(absent):
    client = _Client({
        "totalAvailableBalance": absent,
        "coin": [{"coin": "USDT", "availableToWithdraw": "274.91"}],
    })
    value, state, detail = read_linear_available_balance(client)
    assert value == pytest.approx(274.91)
    assert state == AVAILABLE_STATE_DEPRECATED
    assert detail and "deprecated" in detail


def test_nothing_readable_is_unavailable_not_zero():
    """'We could not look' and 'the account is flat' are opposite statements."""
    value, state, detail = read_linear_available_balance(_Client({"coin": []}))
    assert value is None, "must be None — a fabricated 0.0 would read as 'no margin'"
    assert state == AVAILABLE_STATE_UNAVAILABLE
    assert detail


def test_raising_client_is_unavailable_and_names_the_exception():
    value, state, detail = read_linear_available_balance(
        _Client(raises=RuntimeError("connection reset"))
    )
    assert (value, state) == (None, AVAILABLE_STATE_UNAVAILABLE)
    assert "RuntimeError" in detail and "connection reset" in detail


def test_the_three_states_are_mutually_exclusive_strings():
    states = {AVAILABLE_STATE_VENUE, AVAILABLE_STATE_DEPRECATED, AVAILABLE_STATE_UNAVAILABLE}
    assert len(states) == 3, "two states collapsing to one string defeats the contract"


# ── the split changed NOTHING the sizer sees ───────────────────────────────

@pytest.mark.parametrize(
    "client, expected",
    [
        (_venue("264.71"), 264.71),          # venue figure
        (_deprecated_only("274.91"), 274.91),  # deprecated substitute
        (_Client({"coin": []}), None),         # could not look
        (_Client(raises=ValueError("boom")), None),
    ],
)
def test_sizer_facing_return_is_byte_for_byte_unchanged(client, expected):
    """THE NEGATIVE CONTROL.

    ``_fetch_linear_available_balance`` is what the coordinator calls and what
    therefore reaches ``risk.py::position_size``. Making the read three-state is
    observability; it must not move a single sized quantity. Acting on
    ``read_state`` is an order-path change and is gated separately.
    """
    got = _fetch_linear_available_balance(client)
    if expected is None:
        assert got is None
    else:
        assert got == pytest.approx(expected)


def test_negative_venue_figure_is_floored_at_zero_as_before():
    value, state, _ = read_linear_available_balance(_venue("-5.0"))
    assert value == 0.0 and state == AVAILABLE_STATE_VENUE


# ── the branch is no longer silent ─────────────────────────────────────────

def test_deprecated_substitution_warns(caplog):
    with caplog.at_level(logging.WARNING):
        _fetch_linear_available_balance(_deprecated_only())
    assert any(
        "SUBSTITUTED" in r.message or "SUBSTITUTED" in r.getMessage()
        for r in caplog.records
    ), "a substitute wearing the label of broker truth must not be silent"


def test_could_not_look_warns(caplog):
    with caplog.at_level(logging.WARNING):
        _fetch_linear_available_balance(_Client(raises=RuntimeError("x")))
    assert any("could NOT read" in r.getMessage() for r in caplog.records)


def test_venue_branch_does_not_warn(caplog):
    """Broker truth is the normal path — warning on it would be alarm fatigue,
    which this repo treats as its own P1 bug."""
    with caplog.at_level(logging.WARNING):
        _fetch_linear_available_balance(_venue())
    assert not [r for r in caplog.records if "available_balance" in r.getMessage()]


# ── the contract is registered, so the guard actually enforces it ──────────

def test_contract_is_registered_with_collapsed_state_guard():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts/ci/check_collapsed_states.py"
    spec = importlib.util.spec_from_file_location("_ccs", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]

    entry = [c for c in mod.CONTRACTS if c["name"] == "bybit_available.read_state"]
    assert entry, "registering the contract is how a three-state field becomes enforced"
    assert set(entry[0]["states"]) == {
        AVAILABLE_STATE_VENUE,
        AVAILABLE_STATE_DEPRECATED,
        AVAILABLE_STATE_UNAVAILABLE,
    }, "the registry must name the SAME three strings the producer emits"
