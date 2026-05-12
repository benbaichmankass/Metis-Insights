"""Margin pre-flight cap in RiskManager.position_size.

2026-05-12: risk-based sizing produced qty whose required initial
margin exceeded available wallet balance × leverage → Bybit returned
ErrCode 110007 ("ab not enough for new order"). Per the Prime
Directive (docs/CLAUDE-RULES-CANONICAL.md), the RiskManager now
catches this BEFORE the exchange call so the operator gets a
per-trade refusal Telegram with a verbatim reason instead of an
exchange-side error that the (now-deleted) breaker would have used
to flip the account to dry_run.
"""
from __future__ import annotations

import math

from src.core.coordinator import OrderPackage
from src.units.accounts.risk import RiskManager, _MARGIN_SAFETY_BUFFER


def _pkg(entry: float = 80000.0, sl: float = 79900.0, tp: float = 80200.0) -> OrderPackage:
    return OrderPackage(
        symbol="BTCUSDT",
        side="buy",
        entry=entry,
        sl=sl,
        tp=tp,
        meta={"strategy_name": "test", "strategy_risk_pct": 1.0},
    )


def test_margin_cap_scales_qty_to_fit_available_margin() -> None:
    """The 2026-05-12 bybit_2 scenario: $158 wallet, 3x leverage,
    entry $80,888, stop $80,815. Risk-based qty would produce a
    notional whose required IM exceeds the wallet. Expected: cap to
    (balance × leverage × buffer) / entry, floor-rounded.
    """
    rm = RiskManager({
        "risk_pct": 0.005,
        "min_balance_usd": 50,
        "leverage": 3,
        "qty_precision": 3,
        "min_qty": 0.001,
        "daily_usd": 1000,
        "pos_size": 50000,
    })
    pkg = _pkg(entry=80888.0, sl=80815.0)
    qty = rm.position_size(pkg, balance_usd=158.0)

    expected_cap_raw = (158.0 * 3 * _MARGIN_SAFETY_BUFFER) / 80888.0
    expected_qty = math.floor(expected_cap_raw * 1000) / 1000

    assert qty == expected_qty, (
        f"expected margin-capped qty={expected_qty}, got {qty}; "
        f"cap_raw={expected_cap_raw}"
    )
    assert qty < 0.008, "must cap below the risk-based qty that produced 110007"
    assert qty >= 0.005, "cap should preserve a tradeable qty"


def test_margin_cap_refuses_when_below_min_qty() -> None:
    """Account too small to open even the min_qty at the configured
    leverage → refuse (return 0.0). The executor sees a per-trade
    refusal via the standard refusal wire instead of dispatching a
    guaranteed-to-fail order.
    """
    rm = RiskManager({
        "risk_pct": 0.01,
        "min_balance_usd": 1,
        "leverage": 3,
        "qty_precision": 3,
        "min_qty": 0.001,
        "daily_usd": 1000,
        "pos_size": 50000,
    })
    pkg = _pkg(entry=80000.0, sl=79900.0)
    qty = rm.position_size(pkg, balance_usd=20.0)
    assert qty == 0.0, f"expected refusal (0.0), got {qty}"


def test_margin_cap_no_op_when_risk_qty_within_margin() -> None:
    """Margin pre-flight is a NO-OP when the risk-based qty already
    fits — output equals the pre-cap qty.
    """
    rm = RiskManager({
        "risk_pct": 0.0001,
        "min_balance_usd": 1,
        "leverage": 3,
        "qty_precision": 3,
        "min_qty": 0.001,
        "daily_usd": 1000,
        "pos_size": 50000,
    })
    pkg = _pkg(entry=80000.0, sl=79900.0)
    qty = rm.position_size(pkg, balance_usd=10000.0)
    assert qty > 0.0, "qty must not be refused with ample margin"
    max_qty_by_margin = (10000.0 * 3 * _MARGIN_SAFETY_BUFFER) / 80000.0
    assert qty <= max_qty_by_margin, (
        "even when not at the cap, qty must never exceed the margin ceiling"
    )


def test_margin_cap_spot_account_treats_leverage_as_1x() -> None:
    """Cash spot accounts (leverage=0 or unset) cap qty at
    (balance × buffer) / entry — no leverage multiplier.
    """
    rm = RiskManager({
        "risk_pct": 0.01,
        "min_balance_usd": 1,
        "qty_precision": 3,
        "min_qty": 0.001,
        "daily_usd": 1000,
        "pos_size": 50000,
    })
    pkg = _pkg(entry=80000.0, sl=79900.0)
    qty = rm.position_size(pkg, balance_usd=158.0, market_type="spot")
    # 158 * 1 * 0.9 / 80000 = 0.001777 → floors to 0.001 == min_qty
    expected = math.floor((158.0 * 1 * _MARGIN_SAFETY_BUFFER / 80000.0) * 1000) / 1000
    assert qty == expected, f"spot 1x cap should yield {expected}, got {qty}"
    assert qty == 0.001, "spot cap on $158 wallet should land exactly at min_qty"
