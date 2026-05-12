"""Margin ceiling in RiskManager.position_size.

The sizer uses the live available-margin figure (available_usd) supplied
by the coordinator from the exchange to cap qty. No arbitrary buffer —
just the exact available margin multiplied by the account leverage.
When available_usd is None (spot, dry-run, or fetch failed) the ceiling
is skipped entirely.
"""
from __future__ import annotations

import math

from src.core.coordinator import OrderPackage
from src.units.accounts.risk import RiskManager


def _pkg(entry: float = 80000.0, sl: float = 79900.0, tp: float = 80200.0) -> OrderPackage:
    return OrderPackage(
        symbol="BTCUSDT",
        side="buy",
        entry=entry,
        sl=sl,
        tp=tp,
        meta={"strategy_name": "test", "strategy_risk_pct": 1.0},
    )


def test_margin_ceiling_scales_to_available_usd() -> None:
    """When available_usd is provided, qty is capped to fit within it.

    Reproduces the 2026-05-12 bybit_2 scenario: $158 wallet at 3x
    leverage but only $50 truly available (rest locked in existing
    positions). Risk-based sizing would produce a notional that needs
    more margin. Expected: scale down to available_usd * leverage / entry.
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
    qty = rm.position_size(pkg, balance_usd=158.0, available_usd=50.0)

    expected_max_raw = (50.0 * 3) / 80888.0
    expected_qty = math.floor(expected_max_raw * 1000) / 1000

    assert qty == expected_qty, (
        f"expected margin-capped qty={expected_qty}, got {qty}"
    )


def test_margin_ceiling_refuses_when_below_min_qty() -> None:
    """When available margin cannot support even min_qty, return 0.0."""
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
    # 0.1 USDT * 3 / 80000 = 0.00000375 → below min_qty 0.001
    qty = rm.position_size(pkg, balance_usd=158.0, available_usd=0.1)
    assert qty == 0.0, f"expected refusal (0.0), got {qty}"


def test_margin_ceiling_skipped_when_available_usd_is_none() -> None:
    """When available_usd is None, margin ceiling is skipped entirely.

    Applies to spot accounts, dry-run, and fetch failures — risk_pct
    is the only gate in those cases.
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
    qty = rm.position_size(pkg, balance_usd=10000.0, available_usd=None)
    assert qty > 0.0, "risk_pct-sized qty must not be refused with ample balance"


def test_margin_ceiling_no_op_when_risk_qty_already_fits() -> None:
    """When risk-based qty already fits, ceiling is not hit."""
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
    qty = rm.position_size(pkg, balance_usd=10000.0, available_usd=10000.0)
    max_by_margin = (10000.0 * 3) / 80000.0
    assert qty <= max_by_margin, "qty must never exceed the margin ceiling"
    assert qty > 0.0, "must not refuse with ample margin"
