"""Pinned operator-facing rejection messages for ``sized_qty <= 0``.

Pre-fix, ``Coordinator.multi_account_execute`` hardcoded
``below_min_balance: balance=X < min_balance_usd=Y`` for every
zero-qty outcome from RiskManager — the operator saw "balance=186.87
< 50.0" and couldn't tell the comparison was a lie.

The arbitrary minimum-balance floor was removed 2026-06-24:
``min_balance_usd`` is gone, and the only balance gate left is physics
— a non-positive ``gate_balance`` (you can't risk a fraction of zero).
A small positive balance no longer refuses on a floor; it sizes off
the risk budget and is then subject to the margin/buying-power cap and
the exchange min-lot size. Real refusal causes:

  1. ``zero_balance``  — the account has no funds to size against
     (non-positive ``gate_balance``). This is the only balance gate.
  2. ``zero_exchange_capacity`` — the spot-margin ``available_usd``
     cap fired because Bybit V5 returned ``availableToBorrow=0`` for
     the order's spending side. Canonical case: USDT-only wallet
     shorting BTC on bybit_2 (2026-05-08 trade-875 incident).
     Pinned by ``test_short_zero_capacity_when_borrow_line_zero`` in
     test_s047_t3_spot_margin_routing.py — that contract is the
     correct refusal; the bug was the misleading message.
  3. ``risk_refused`` — generic catch-all (daily-loss budget,
     liquidation buffer, max_borrow). Surfaces all the inputs the
     operator needs to reproduce.
"""
from __future__ import annotations

from src.core.coordinator import _explain_zero_sized_qty


class TestExplainZeroSizedQty:
    def test_zero_balance_fires_when_total_equity_non_positive(self):
        """The only balance gate left: a non-positive ``gate_balance``
        (here total equity of $0) yields the ``zero_balance`` token.
        There is no arbitrary floor — a small positive balance no
        longer refuses here."""
        msg = _explain_zero_sized_qty(
            balance=0.0,
            available_usd=None,
            total_account_usd=0.0,
            risk_manager=None,
            direction="long",
            market_type="spot",
        )
        assert msg.startswith("zero_balance:")
        assert "gate_balance=0.00" in msg

    def test_zero_balance_uses_total_account_when_balance_inflated(self):
        """Mirror the gate in risk.py — ``total_account_usd`` is the
        canonical input when present (the post-S-052 contract).
        ``balance`` may be free USDT (which inflates after a borrow-
        and-sell short); the gate must use net equity. A non-positive
        net equity refuses with ``zero_balance`` even though free USDT
        looks healthy.
        """
        msg = _explain_zero_sized_qty(
            balance=600.0,            # inflated free USDT after short fill
            available_usd=None,
            total_account_usd=0.0,    # actual net equity is zero
            risk_manager=None,
            direction="short",
            market_type="spot-margin",
        )
        assert msg.startswith("zero_balance:")
        assert "gate_balance=0.00" in msg

    def test_small_positive_balance_no_longer_refuses_on_floor(self):
        """Post-2026-06-24: a balance BELOW the old $50 floor (here $40)
        is NOT a balance refusal — there is no floor. With plenty of
        exchange capacity it falls through to ``risk_refused`` (i.e. the
        balance gate did not fire), never the removed
        ``below_min_balance`` token.
        """
        msg = _explain_zero_sized_qty(
            balance=40.0,
            available_usd=10_000.0,
            total_account_usd=40.0,
            risk_manager=None,
            direction="long",
            market_type="spot-margin",
        )
        assert "below_min_balance" not in msg
        assert "zero_balance" not in msg
        assert msg.startswith("risk_refused:")

    def test_zero_exchange_capacity_for_short_on_usdt_only_wallet(self):
        """The headline case the operator hit on 2026-05-08: bybit_2
        had $186.87 USDT but Bybit's wallet API returned
        ``availableToBorrow=0`` for BTC, so the spot-margin SHORT
        path's S-054 cap collapsed to 0.

        PR 5 (2026-05-10): the ``zero_exchange_capacity`` token was
        removed alongside the spot-margin code paths in coordinator.py.
        The current contract falls through to ``risk_refused:`` with
        all inputs for operator triage.
        """
        msg = _explain_zero_sized_qty(
            balance=186.87,
            available_usd=0.0,
            total_account_usd=186.87,
            risk_manager=None,
            direction="short",
            market_type="spot-margin",
        )
        assert msg.startswith("risk_refused:")
        assert "direction=short" in msg
        assert "available_usd=0.00" in msg
        assert "balance=186.87" in msg
        # The misleading legacy template must NOT fire here.
        assert "below_min_balance" not in msg

    def test_zero_exchange_capacity_for_long_names_usdt_side(self):
        """Symmetric: a long with zero available_usd falls through to
        risk_refused (PR 5, 2026-05-10 removed zero_exchange_capacity).
        """
        msg = _explain_zero_sized_qty(
            balance=100.0,
            available_usd=0.0,
            total_account_usd=100.0,
            risk_manager=None,
            direction="long",
            market_type="spot-margin",
        )
        assert msg.startswith("risk_refused:")
        assert "direction=long" in msg
        assert "available_usd=0.00" in msg

    def test_zero_balance_takes_priority_over_capacity(self):
        """Order-of-evaluation contract: when BOTH net equity is
        non-positive AND available_usd is 0, name the balance gate
        first. That's the more fundamental cause and dictates the
        operator's action.
        """
        msg = _explain_zero_sized_qty(
            balance=0.0,
            available_usd=0.0,
            total_account_usd=0.0,
            risk_manager=None,
            direction="short",
            market_type="spot-margin",
        )
        assert msg.startswith("zero_balance:")

    def test_risk_refused_when_no_obvious_cause(self):
        """Daily-loss budget exhaustion / liquidation-buffer refusal
        / max_borrow cap don't match the balance branch. Fall through
        to a structured ``risk_refused`` reason that surfaces every
        input so the operator can reproduce.
        """
        msg = _explain_zero_sized_qty(
            balance=200.0,
            available_usd=10_000.0,   # plenty of capacity
            total_account_usd=200.0,
            risk_manager=None,
            direction="long",
            market_type="spot-margin",
        )
        assert msg.startswith("risk_refused:")
        assert "balance=200.00" in msg
        assert "available_usd=10000.00" in msg
        assert "total_account_usd=200.00" in msg
        assert "direction=long" in msg
        assert "market_type=spot-margin" in msg
        # The hint must guide the operator to the residual rules.
        assert "daily-loss" in msg or "liquidation" in msg
        # The removed floor must not reappear in the message.
        assert "min_balance_usd" not in msg

    def test_risk_refused_handles_none_available_usd(self):
        """Non-spot-margin paths leave ``available_usd=None``. The
        explainer must tolerate that and still synthesise a reason.
        """
        msg = _explain_zero_sized_qty(
            balance=200.0,
            available_usd=None,
            total_account_usd=200.0,
            risk_manager=None,
            direction="short",
            market_type="linear",
        )
        assert msg.startswith("risk_refused:")
        assert "available_usd=n/a" in msg

    def test_zero_exchange_capacity_only_fires_on_spot_margin(self):
        """A derivatives account with available_usd=0 is implausible
        (the S-049/S-053 caps don't run on linear/inverse), but the
        explainer must not misclassify it as the spot-margin
        zero-capacity case. Falls through to risk_refused.
        """
        msg = _explain_zero_sized_qty(
            balance=200.0,
            available_usd=0.0,
            total_account_usd=200.0,
            risk_manager=None,
            direction="short",
            market_type="linear",
        )
        assert "zero_exchange_capacity" not in msg
        assert msg.startswith("risk_refused:")

    def test_pinning_case_186_87_short_btc(self):
        """Pin the literal trade 875 / 876 inputs from 2026-05-08.
        Regression guard against any future change reintroducing the
        ``balance=186.87 < 50.0`` lie.

        PR 5 (2026-05-10): the ``zero_exchange_capacity`` token was
        removed alongside the spot-margin code paths in coordinator.py
        (see docstring at coordinator.py:2193). Current contract:
        ``risk_refused:`` with all inputs surfaced for triage.
        """
        msg = _explain_zero_sized_qty(
            balance=186.87,
            available_usd=0.0,
            total_account_usd=186.87,
            risk_manager=None,
            direction="short",
            market_type="spot-margin",
        )
        # NEVER again say the balance is below the floor when it isn't.
        assert "below_min_balance" not in msg
        assert "186.87 < 50" not in msg
        # Current contract: risk_refused with all operator inputs.
        assert msg.startswith("risk_refused:")
        assert "spot-margin" in msg
        assert "short" in msg
