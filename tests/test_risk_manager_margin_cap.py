"""Margin pre-flight cap in RiskManager.position_size.

2026-05-12: risk-based sizing produced qty whose required initial
margin exceeded available wallet balance × leverage → Bybit returned
ErrCode 110007 ("ab not enough for new order"). Per the Prime
Directive (docs/CLAUDE-RULES-CANONICAL.md), the RiskManager now
catches this BEFORE the exchange call so the operator gets a
per-trade refusal Telegram with a verbatim reason instead of an
exchange-side error that the (now-deleted) breaker would have used
to flip the account to dry_run.

Two ceiling modes (both carry _MARGIN_SAFETY_BUFFER since
BL-20260701-BYBIT-AVAILABLE-FIELD — the live path used to size to 100%
of the exchange figure, leaving no room for fees / IM rounding):
  live figure  — available_usd × leverage × _MARGIN_SAFETY_BUFFER
                 (account-level totalAvailableBalance from Bybit UNIFIED,
                  or Alpaca/OANDA broker buying power)
  buffer       — balance × leverage × _MARGIN_SAFETY_BUFFER (fallback)
"""
from __future__ import annotations

import math

from src.core.coordinator import OrderPackage
from src.units.accounts.risk import RiskManager, _MARGIN_SAFETY_BUFFER


def _pkg(entry: float = 80000.0, sl: float = 79900.0, tp: float = 80200.0) -> OrderPackage:
    return OrderPackage(
        strategy="test",
        symbol="BTCUSDT",
        direction="long",
        entry=entry,
        sl=sl,
        tp=tp,
        meta={"strategy_name": "test", "strategy_risk_pct": 1.0},
    )


# ---------------------------------------------------------------------------
# Buffer path (available_usd=None — spot, dry-run, fetch failure)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Live-figure path (available_usd supplied by coordinator from exchange)
# ---------------------------------------------------------------------------

def test_live_figure_caps_to_available_usd() -> None:
    """When available_usd is provided, the ceiling is based on the
    live exchange figure, not balance × buffer.

    Scenario: $300 balance but only $50 truly free (rest locked in
    open positions). Buffer path would allow: 300 × 3 × 0.9 / 80888 ≈ 0.010.
    Live-figure path should cap to: 50 × 3 × 0.9 / 80888 ≈ 0.001.
    """
    rm = RiskManager({
        "risk_pct": 0.02,
        "min_balance_usd": 50,
        "leverage": 3,
        "qty_precision": 3,
        "min_qty": 0.001,
        "daily_usd": 1000,
        "pos_size": 50000,
    })
    pkg = _pkg(entry=80888.0, sl=80815.0)
    qty_live = rm.position_size(pkg, balance_usd=300.0, available_usd=50.0)
    qty_buffer = rm.position_size(pkg, balance_usd=300.0, available_usd=None)

    expected_live = math.floor((50.0 * 3 * _MARGIN_SAFETY_BUFFER / 80888.0) * 1000) / 1000
    assert qty_live == expected_live, f"live-figure cap: expected {expected_live}, got {qty_live}"
    assert qty_live < qty_buffer, "live figure (less free margin) must cap lower than buffer"


def test_live_figure_refuses_when_below_min_qty() -> None:
    """When available_usd is too small to support even min_qty, return 0.0."""
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
    # 0.1 * 3 / 80000 = 0.00000375 → below min_qty 0.001
    qty = rm.position_size(pkg, balance_usd=500.0, available_usd=0.1)
    assert qty == 0.0, f"expected refusal (0.0) when available_usd too small, got {qty}"


def test_live_figure_no_op_when_risk_qty_already_fits() -> None:
    """When risk-based qty fits within the live figure, ceiling is not hit."""
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
    max_by_margin = (10000.0 * 3 * _MARGIN_SAFETY_BUFFER) / 80000.0
    assert qty <= max_by_margin, "qty must never exceed the live-figure ceiling"
    assert qty > 0.0, "must not refuse with ample available margin"


def test_none_available_usd_falls_back_to_buffer() -> None:
    """available_usd=None must fall back to the buffer path, not skip
    the ceiling entirely. Ensures fetch failures still protect the account.
    """
    rm = RiskManager({
        "risk_pct": 0.10,
        "min_balance_usd": 1,
        "leverage": 3,
        "qty_precision": 3,
        "min_qty": 0.001,
        "daily_usd": 100000,
        "pos_size": 500000,
    })
    pkg = _pkg(entry=80000.0, sl=79000.0)
    qty_none = rm.position_size(pkg, balance_usd=158.0, available_usd=None)
    expected_buffer_cap = math.floor((158.0 * 3 * _MARGIN_SAFETY_BUFFER / 80000.0) * 1000) / 1000
    assert qty_none <= expected_buffer_cap, (
        f"None path must apply buffer ceiling {expected_buffer_cap}, got {qty_none}"
    )
    assert qty_none > 0.0, "buffer ceiling should still allow a trade on $158"
