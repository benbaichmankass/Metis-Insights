"""Defined-risk options position sizing — premium / max-loss based.

Phase-1 of the Alpaca L3 options build (docs/research/alpaca-options-PHASE1-spec.md).

WHY THIS IS A SEPARATE PATH FROM ``RiskManager.position_size``
-------------------------------------------------------------
The equity/crypto/futures sizer (``src.units.accounts.risk``) sizes off the
**price distance** ``|entry - sl|`` — ``qty = (balance * risk_pct) / (risk_distance
* contract_value_usd)``. An options spread has no such stop distance: its risk is
the **premium paid (debit) or width-minus-credit (credit)**, realised as a known
**max loss per contract x 100 (the OCC multiplier)**. So options size off max-loss,
not stop-distance — a genuinely different formula that would not fit cleanly inside
``position_size``.

CONTRACT (a $150 cash account; see the research memo Section 0)
--------------------------------------------------------------
- Only **defined-risk** structures are sized here (long options + debit/credit
  verticals + calendars). Naked/undefined risk is out of scope (Alpaca offers no
  naked anyway — Levels 0-3 only).
- **Whole contracts**, minimum 1. A computed size below 1 contract is a per-trade
  **REFUSAL** (returns 0) — never silently bumped, mirroring the futures
  whole-contract rule in ``position_size`` (BL-20260611-001). The caller surfaces
  the refusal with a logged cause, exactly like the equity path.
- The **max-loss budget** is the operator's per-trade risk allowance in USD. On the
  $150 test account the risk knobs are deliberately loose (a single spread is a large
  fraction of the account) so trades actually fire; the floor that protects the
  account is the **account-level daily-loss cap**, which still applies downstream.

This module is **pure** (no I/O, no broker calls) and is unit-tested. It is not yet
wired into any executor — Phase-1 foundation only.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

# OCC standard: one US equity/ETF option contract controls 100 shares.
OPTION_MULTIPLIER = 100


@dataclass(frozen=True)
class OptionSizing:
    """Result of a defined-risk sizing decision.

    ``contracts`` == 0 means REFUSED (even one contract exceeds the budget, or
    a non-positive input); ``reason`` carries the machine-readable cause so the
    caller can journal it like a ``RiskManager`` refusal.
    """

    contracts: int
    max_loss_per_contract_usd: float
    total_max_loss_usd: float
    reason: Optional[str] = None

    @property
    def refused(self) -> bool:
        return self.contracts <= 0


def debit_max_loss_per_contract(net_debit: float, multiplier: int = OPTION_MULTIPLIER) -> float:
    """Max loss for one DEBIT structure (long option / debit vertical / calendar).

    For a debit structure the most you can lose is the premium you paid:
    ``net_debit * multiplier``. ``net_debit`` is the per-share debit (the price
    you'd see on the chain, e.g. 0.45), so a 0.45 debit = $45 max loss / contract.
    """
    return max(0.0, float(net_debit)) * multiplier


def credit_spread_max_loss_per_contract(
    width: float, net_credit: float, multiplier: int = OPTION_MULTIPLIER
) -> float:
    """Max loss for one CREDIT vertical / iron-condor leg-pair.

    ``(width - net_credit) * multiplier``. NOTE: credit structures require a
    >=$2,000 MARGIN account (FINRA 4210 / Alpaca) — out of scope for the $150
    cash pilot; this helper exists for the Phase-4 graduation only (research memo
    Section 0 / Section 9). ``width`` is the strike width (e.g. 1.0 for a $1-wide).
    """
    return max(0.0, float(width) - float(net_credit)) * multiplier


def size_defined_risk(
    *,
    max_loss_budget_usd: float,
    max_loss_per_contract_usd: float,
    max_contracts: Optional[int] = None,
) -> OptionSizing:
    """Size a defined-risk options position by max-loss budget.

    ``contracts = floor(budget / max_loss_per_contract)``, then:
      - **REFUSE (0)** when even a single contract's max loss exceeds the budget,
        or when any input is non-positive (an undefined/zero max-loss is never
        treated as "free" — that would be a fabricated position).
      - clamp to ``max_contracts`` when provided (e.g. a liquidity / open-interest
        cap the caller derived from the chain).

    Pure and total — never raises. Mirrors the whole-unit refuse-sub-1 semantics
    of ``RiskManager.position_size`` so the two sizers behave consistently.
    """
    budget = float(max_loss_budget_usd)
    per = float(max_loss_per_contract_usd)

    if budget <= 0:
        return OptionSizing(0, per, 0.0, reason="non_positive_budget")
    if per <= 0:
        # An undefined or zero per-contract max loss cannot be sized against a
        # finite budget — refuse rather than divide by zero / manufacture qty.
        return OptionSizing(0, per, 0.0, reason="non_positive_max_loss")

    raw = math.floor(budget / per)
    if raw < 1:
        # One contract already costs more than the whole budget.
        return OptionSizing(
            0, per, 0.0, reason="min_one_contract_exceeds_budget"
        )

    contracts = raw
    capped = False
    if max_contracts is not None and contracts > int(max_contracts):
        contracts = max(0, int(max_contracts))
        capped = True
        if contracts < 1:
            return OptionSizing(0, per, 0.0, reason="max_contracts_below_one")

    return OptionSizing(
        contracts=contracts,
        max_loss_per_contract_usd=per,
        total_max_loss_usd=contracts * per,
        reason="capped_by_max_contracts" if capped else None,
    )


def size_debit_structure(
    *,
    net_debit: float,
    max_loss_budget_usd: float,
    multiplier: int = OPTION_MULTIPLIER,
    max_contracts: Optional[int] = None,
) -> OptionSizing:
    """Convenience: size a DEBIT structure (the $150-pilot path) from its net debit.

    The feasible pilot structures (long option, debit vertical, calendar) are all
    debits, so this is the function the Phase-1 executor will call. Credit
    structures route through ``size_defined_risk`` with
    ``credit_spread_max_loss_per_contract`` once the account graduates to margin.
    """
    per = debit_max_loss_per_contract(net_debit, multiplier)
    return size_defined_risk(
        max_loss_budget_usd=max_loss_budget_usd,
        max_loss_per_contract_usd=per,
        max_contracts=max_contracts,
    )
