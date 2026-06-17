"""S-052 — min_balance_usd gate evaluates total account equity, not free quote balance.

Spot/spot-margin accounts pass free USDT as ``balance_usd`` for sizer
collateral math (S-049). The min_balance_usd gate ("is this account
big enough to bother sizing into?") needs the operator's *total*
account equity, not free USDT — otherwise an account holding $120
total but only $40 free USDT is incorrectly refused as too small.

These tests pin:
  1. The new ``total_account_usd`` parameter overrides the gate.
  2. When unset (default), behaviour is bit-identical to pre-S-052.
  3. The gate uses ``total_account_usd`` even when free balance is high
     (catches the inverse — a wallet with $1000 free but rest of the
     account drained somehow still passes the gate, which is correct).
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


def _rm(min_balance_usd: float = 50.0) -> RiskManager:
    return RiskManager({
        "min_balance_usd": min_balance_usd,
        "risk_pct": 0.01,
        "min_qty": 0.001,
        "qty_precision": 3,
        "daily_usd": 1_000.0,
        "max_dd_pct": 0.05,
        "pos_size": 5_000.0,
    })


# ---------------------------------------------------------------------------
# 1. Total-equity override unblocks an account whose free balance is below
#    the gate but whose total equity is above (the wallet-with-locked-funds
#    case the S-052 bug report describes).
# ---------------------------------------------------------------------------


def test_total_account_usd_above_gate_passes_even_when_free_balance_below():
    rm = _rm(min_balance_usd=50.0)
    # Free USDT $40 < $50 gate, but total equity $120 > $50 → gate passes.
    # Tight stop so the small (but gate-clearing) balance still sizes at least
    # the platform min-lot: post-BL-20260617-SIZEFLOOR a sub-min size is a
    # refusal, so the gate-pass must be shown with a tradeable size (a wide
    # stop would refuse on the floor and mask whether the GATE itself passed).
    qty = rm.position_size(
        _pkg(entry=70_000.0, sl=69_900.0, tp=70_300.0),
        balance_usd=40.0,
        total_account_usd=120.0,
    )
    assert qty > 0, "gate should pass when total equity clears min_balance_usd"


# ---------------------------------------------------------------------------
# 2. Backward compat — when total_account_usd is None, gate uses balance_usd
#    exactly as before. Pre-S-052 callers get bit-identical behaviour.
# ---------------------------------------------------------------------------


def test_no_total_falls_back_to_balance_usd_passes():
    rm = _rm(min_balance_usd=50.0)
    qty = rm.position_size(_pkg(), balance_usd=200.0)
    assert qty > 0


def test_no_total_falls_back_to_balance_usd_refuses():
    rm = _rm(min_balance_usd=50.0)
    qty = rm.position_size(_pkg(), balance_usd=40.0)
    assert qty == 0.0


# ---------------------------------------------------------------------------
# 3. Total below threshold refuses, even if free balance somehow looks big.
#    (Defensive: catches a regression where someone wires balance and total
#    in the wrong order.)
# ---------------------------------------------------------------------------


def test_total_below_gate_refuses_even_if_balance_high():
    rm = _rm(min_balance_usd=50.0)
    qty = rm.position_size(
        _pkg(),
        balance_usd=1_000.0,
        total_account_usd=30.0,
    )
    assert qty == 0.0, (
        "gate should fire on total equity, not free balance; "
        "$30 total under $50 floor must refuse regardless of balance"
    )


# ---------------------------------------------------------------------------
# 4. total=0.0 is a real value (empty account) — gate must fire, NOT fall
#    back to balance. None vs 0.0 distinction is load-bearing.
# ---------------------------------------------------------------------------


def test_total_account_zero_is_explicit_refusal():
    rm = _rm(min_balance_usd=50.0)
    qty = rm.position_size(
        _pkg(),
        balance_usd=200.0,
        total_account_usd=0.0,
    )
    assert qty == 0.0


# ---------------------------------------------------------------------------
# 5. Test orders bypass the gate regardless (smoke-test contract,
#    pre-existing behaviour preserved).
# ---------------------------------------------------------------------------


def test_test_order_bypasses_gate_with_or_without_total():
    rm = _rm(min_balance_usd=50.0)
    smoke = _pkg(meta={"is_test": True, "test_qty": 0.0001})
    # Free balance below gate; total below gate; smoke order still sizes.
    assert rm.position_size(smoke, balance_usd=10.0, total_account_usd=10.0) == 0.0001
    # And without total at all.
    assert rm.position_size(smoke, balance_usd=10.0) == 0.0001
