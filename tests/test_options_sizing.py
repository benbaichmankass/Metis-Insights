"""Tests for defined-risk options sizing (Phase-1, premium/max-loss based).

Covers the $150-cash-account contract: whole contracts, refuse-sub-1, the
budget arithmetic, max_contracts clamp, and the debit/credit max-loss helpers.
"""
from __future__ import annotations

from src.units.accounts.options_sizing import (
    OPTION_MULTIPLIER,
    credit_spread_max_loss_per_contract,
    debit_max_loss_per_contract,
    size_debit_structure,
    size_defined_risk,
)


def test_debit_max_loss_is_premium_times_100():
    # 0.45 debit on a contract = $45 max loss.
    assert debit_max_loss_per_contract(0.45) == 45.0
    assert debit_max_loss_per_contract(0.45, multiplier=100) == 45.0


def test_credit_spread_max_loss_is_width_minus_credit_times_100():
    # $1-wide credit spread collecting 0.40 -> max loss (1.00-0.40)*100 = $60.
    assert credit_spread_max_loss_per_contract(1.0, 0.40) == 60.0


def test_single_contract_fits_budget():
    # XLF-style: $45 debit, $150 budget -> floor(150/45) = 3 contracts.
    r = size_debit_structure(net_debit=0.45, max_loss_budget_usd=150.0)
    assert r.contracts == 3
    assert r.max_loss_per_contract_usd == 45.0
    assert r.total_max_loss_usd == 135.0
    assert not r.refused
    assert r.reason is None


def test_exactly_one_contract():
    # Budget == per-contract cost -> exactly 1.
    r = size_debit_structure(net_debit=0.50, max_loss_budget_usd=50.0)
    assert r.contracts == 1
    assert r.total_max_loss_usd == 50.0
    assert not r.refused


def test_refuse_when_one_contract_exceeds_budget():
    # A $0.70 debit = $70/contract but only $50 budget -> REFUSE (0).
    r = size_debit_structure(net_debit=0.70, max_loss_budget_usd=50.0)
    assert r.contracts == 0
    assert r.refused
    assert r.reason == "min_one_contract_exceeds_budget"


def test_never_bumps_up_a_sub_one_size():
    # Mirrors the futures whole-contract refuse-sub-1 rule: no rounding up.
    r = size_debit_structure(net_debit=1.20, max_loss_budget_usd=100.0)
    assert r.contracts == 0
    assert r.refused


def test_max_contracts_clamp():
    # 3 would fit on budget, but a liquidity cap of 1 clamps it.
    r = size_debit_structure(
        net_debit=0.45, max_loss_budget_usd=150.0, max_contracts=1
    )
    assert r.contracts == 1
    assert r.total_max_loss_usd == 45.0
    assert r.reason == "capped_by_max_contracts"


def test_max_contracts_zero_refuses():
    r = size_debit_structure(
        net_debit=0.45, max_loss_budget_usd=150.0, max_contracts=0
    )
    assert r.contracts == 0
    assert r.refused
    assert r.reason == "max_contracts_below_one"


def test_non_positive_budget_refuses():
    r = size_debit_structure(net_debit=0.45, max_loss_budget_usd=0.0)
    assert r.refused
    assert r.reason == "non_positive_budget"


def test_zero_max_loss_is_refused_not_infinite():
    # A zero/undefined per-contract max loss must never manufacture qty.
    r = size_defined_risk(
        max_loss_budget_usd=150.0, max_loss_per_contract_usd=0.0
    )
    assert r.refused
    assert r.reason == "non_positive_max_loss"


def test_multiplier_constant():
    assert OPTION_MULTIPLIER == 100


def test_credit_path_through_generic_sizer():
    # Phase-4 (margin) path: a $1-wide credit spread, $60 max loss, $150 budget.
    per = credit_spread_max_loss_per_contract(1.0, 0.40)
    r = size_defined_risk(max_loss_budget_usd=150.0, max_loss_per_contract_usd=per)
    assert r.contracts == 2
    assert r.total_max_loss_usd == 120.0
