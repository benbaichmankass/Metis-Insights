"""The balance gate evaluates total account equity, not free quote balance.

Spot/spot-margin accounts pass free USDT as ``balance_usd`` for sizer
collateral math (S-049). The balance gate needs the operator's *total*
account equity, not free USDT.

The arbitrary minimum-balance floor (``min_balance_usd``) was removed
2026-06-24 (operator directive): size is a pure function of available
balance+margin and risk-per-trade. The only floor left is physics — a
non-positive ``gate_balance`` sizes to 0 (you can't risk a fraction of
zero; this also guards the positive-balance requirement downstream).

These tests pin what remains of the original S-052 intent — that
``total_account_usd``, when supplied, is the gate basis (and the
margin-cap basis), not free ``balance_usd``:
  1. The ``total_account_usd`` parameter is the basis the gate/margin
     math uses; a healthy total equity sizes even when free USDT is low.
  2. A non-positive ``total_account_usd`` refuses (the only balance
     gate), even when free ``balance_usd`` looks high — None vs 0.0 is
     load-bearing (0.0 is a real empty-account reading, not "unset").
  3. Smoke-test orders bypass the gate regardless.
"""
from __future__ import annotations

from src.core.coordinator import OrderPackage
from src.units.accounts.risk import RiskManager


def _pkg(**overrides):
    base = {
        "strategy": "vwap",
        "symbol": "BTCUSDT",
        "direction": "long",
        "entry": 70_000.0,
        "sl": 68_600.0,
        "tp": 71_400.0,
        "confidence": 0.6,
        "meta": {},
    }
    base.update(overrides)
    return OrderPackage(**base)


def _rm() -> RiskManager:
    # ``min_balance_usd`` left in as harmless config input — it is ignored
    # since the floor was removed 2026-06-24.
    return RiskManager({
        "min_balance_usd": 50.0,
        "risk_pct": 0.01,
        "min_qty": 0.001,
        "qty_precision": 3,
        "daily_usd": 1_000.0,
        "max_dd_pct": 0.05,
        "pos_size": 5_000.0,
    })


# ---------------------------------------------------------------------------
# 1. Total equity is the gate/margin basis. A healthy total equity sizes
#    even when free USDT is modest (the wallet-with-locked-funds case the
#    original S-052 bug report described). There is no $50 floor anymore —
#    the basis just has to clear physics + the margin cap for the min lot.
# ---------------------------------------------------------------------------


def test_total_account_usd_is_the_basis_when_free_balance_low():
    rm = _rm()
    # Tight SL ($70 risk-distance) so the risk-based size is large enough to hit
    # the margin cap — that's where the basis matters. (With a wide SL the
    # risk-based size is sub-min-lot and is refused outright since the
    # bump-to-min-lot was removed — #3910 Item 3 — so the basis never comes into
    # play; this scenario keeps the size above the min lot to isolate the
    # margin-basis question the S-052 fix was about.)
    pkg = _pkg(entry=70_000.0, sl=69_930.0)
    # Free USDT $300 but total equity $5000: the margin cap uses the $5000
    # basis, so the position isn't clamped down to the tiny free-balance ceiling.
    qty_total = rm.position_size(pkg, balance_usd=300.0, total_account_usd=5_000.0)
    qty_free = rm.position_size(pkg, balance_usd=300.0)  # free-balance basis
    assert qty_total > 0, "total equity (not free balance) should back the margin cap"
    assert qty_total > qty_free, (
        "total-equity basis must allow a larger position than the free-balance "
        "basis (the S-052 locked-funds intent)"
    )


# ---------------------------------------------------------------------------
# 2. Backward compat — when total_account_usd is None, the gate/margin math
#    uses balance_usd. A healthy free balance sizes.
# ---------------------------------------------------------------------------


def test_no_total_falls_back_to_balance_usd_passes():
    rm = _rm()
    qty = rm.position_size(_pkg(), balance_usd=5_000.0)
    assert qty > 0


# ---------------------------------------------------------------------------
# 3. The only balance gate: a non-positive total equity refuses, even if
#    free balance somehow looks big (catches a regression where someone
#    wires balance and total in the wrong order). None vs 0.0 is load-bearing
#    — 0.0 is a real empty-account reading and must fire the gate.
# ---------------------------------------------------------------------------


def test_zero_total_refuses_even_if_balance_high():
    rm = _rm()
    qty = rm.position_size(
        _pkg(),
        balance_usd=1_000.0,
        total_account_usd=0.0,
    )
    assert qty == 0.0, (
        "the balance gate fires on a non-positive total equity, not on free "
        "balance; $0 total must refuse regardless of free balance"
    )


def test_negative_total_refuses():
    rm = _rm()
    qty = rm.position_size(
        _pkg(),
        balance_usd=1_000.0,
        total_account_usd=-5.0,
    )
    assert qty == 0.0


def test_zero_balance_no_total_refuses():
    """No total supplied → the gate uses balance_usd; a non-positive free
    balance refuses."""
    rm = _rm()
    qty = rm.position_size(_pkg(), balance_usd=0.0)
    assert qty == 0.0


# ---------------------------------------------------------------------------
# 4. Test orders bypass the gate regardless (smoke-test contract,
#    pre-existing behaviour preserved).
# ---------------------------------------------------------------------------


def test_test_order_bypasses_gate_with_or_without_total():
    rm = _rm()
    smoke = _pkg(meta={"is_test": True, "test_qty": 0.0001})
    # Even a zero balance/total: smoke order still sizes.
    assert rm.position_size(smoke, balance_usd=0.0, total_account_usd=0.0) == 0.0001
    # And without total at all.
    assert rm.position_size(smoke, balance_usd=0.0) == 0.0001
