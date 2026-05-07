"""S-047 T2 — RiskManager spot-margin sizing kernel.

Pins the contract for ``RiskManager.position_size(pkg, balance_usd,
*, market_type="spot")`` on spot-margin accounts:

  - Long sizes from USDT collateral; no borrow when notional ≤ collateral.
  - Short sizes from USDT collateral; BTC borrow is implied by qty.
  - ``max_borrow_btc`` is a sizing CAP (clipped, not refused).
  - ``borrow_fee_apr_pct`` SCALES qty when the implied daily fee
    exceeds the remaining daily-loss budget (does not refuse).
  - ``liquidation_buffer_pct`` REFUSES (returns 0.0, same shape as
    the existing ``min_balance_usd`` refusal — a risk-manager rule,
    not a new gate) when the SL distance violates the buffer.
  - The existing daily-loss-budget rule still wins on conflict — an
    exhausted daily budget refuses regardless of spot-margin params.
  - The ``min_qty`` floor and ``min_balance_usd`` refusal remain
    unchanged on the spot-margin path.

Plus a non-spot-margin regression: ``market_type`` defaulting to
``"spot"`` produces bit-identical sizing to the pre-T2 contract for
the three S-026 G2 / G3 invariants (risk-proportional sizing, no
max-position clamp, floor rounding, smoke-test bypass).

Compliance with `docs/claude/workplan.md` § "Live / dry-run rule":
all refusals here are existing risk-manager refusals (zero-qty
returns), not new pre-flight gates. The dispatcher's
``live | dry_run`` switch remains the only canonical execution gate.
"""
from __future__ import annotations

import sys
import types
from unittest import mock

import pytest

# pipeline.py needs matplotlib at import time via signal_notifications.
if "matplotlib" not in sys.modules:
    _mpl_stub = types.ModuleType("matplotlib")
    _mpl_stub.pyplot = mock.MagicMock()
    sys.modules["matplotlib"] = _mpl_stub
    sys.modules["matplotlib.pyplot"] = mock.MagicMock()

from src.core.coordinator import OrderPackage
from src.units.accounts.risk import (
    DEFAULT_BORROW_FEE_APR_PCT,
    DEFAULT_LIQUIDATION_BUFFER_PCT,
    DEFAULT_MAX_BORROW_BTC,
    RiskManager,
)


def _pkg(
    *,
    direction: str = "long",
    entry: float = 50_000.0,
    sl: float = 49_500.0,
    tp: float = 51_000.0,
    meta: dict | None = None,
) -> OrderPackage:
    return OrderPackage(
        strategy="vwap",
        symbol="BTCUSDT",
        direction=direction,
        entry=entry,
        sl=sl,
        tp=tp,
        confidence=0.7,
        meta=dict(meta) if meta else {},
    )


# ---------------------------------------------------------------------------
# Spot-margin path — the eight cases from S-047 § 6
# ---------------------------------------------------------------------------


class TestSpotMarginSizing:
    """RiskManager.position_size on `market_type="spot-margin"`."""

    def test_spot_long_no_borrow(self):
        """Long where notional fits inside USDT collateral — no borrow,
        liquidation buffer is skipped (no leverage), qty matches the
        standard risk-pct math.

        risk_pct=0.01, distance=$1000, balance=$10_000 →
        risk_usd=$100, qty = 100/1000 = 0.1 BTC.
        notional = 0.1 × $50_000 = $5_000 ≤ $10_000 collateral
        → no borrow → liquidation buffer skipped.
        """
        rm = RiskManager({
            "risk_pct": 0.01,
            "min_balance_usd": 50,
            "daily_usd": 1_000_000_000,  # disable daily-loss interference
        })
        pkg = _pkg(direction="long", entry=50_000, sl=49_000)
        qty = rm.position_size(
            pkg, balance_usd=10_000, market_type="spot-margin",
        )
        assert qty == pytest.approx(0.1, rel=1e-3)
        # Sanity: notional fits inside collateral — no borrow needed.
        assert qty * pkg.entry <= 10_000

    def test_spot_short_with_btc_borrow(self):
        """Short borrows BTC; sizing is off USDT collateral.

        risk_pct=0.01, distance=$1000, balance=$10_000 →
        qty = 0.1 BTC borrowed, sold at $50_000 entry.
        liquidation_distance ≈ collateral/qty = $10_000/0.1 = $100_000.
        SL distance $1000 ≪ (1-0.30)·$100_000 = $70_000 ✅ buffer OK.
        """
        rm = RiskManager({
            "risk_pct": 0.01,
            "min_balance_usd": 50,
            "daily_usd": 1_000_000_000,
        })
        pkg = _pkg(direction="short", entry=50_000, sl=51_000)
        qty = rm.position_size(
            pkg, balance_usd=10_000, market_type="spot-margin",
        )
        assert qty == pytest.approx(0.1, rel=1e-3)

    def test_liquidation_buffer_violation_returns_zero(self):
        """When the SL sits inside the configured liquidation buffer,
        sizer refuses with 0.0 — same shape as min_balance_usd.

        risk_pct=1.0, balance=$200, distance=$10_000 → qty = 0.02 BTC.
        notional = $1000 > collateral $200 → has borrow → check buffer.
        liquidation_distance = $200/0.02 = $10_000.
        SL distance $10_000 ≥ (1-0.30)·$10_000 = $7000 → violates → 0.0.
        """
        rm = RiskManager({
            "risk_pct": 1.0,
            "min_balance_usd": 50,
            "daily_usd": 1_000_000_000,
            "liquidation_buffer_pct": 30.0,  # explicit per § 7
        })
        pkg = _pkg(direction="long", entry=50_000, sl=40_000)
        qty = rm.position_size(
            pkg, balance_usd=200, market_type="spot-margin",
        )
        assert qty == 0.0

    def test_borrow_fee_budget_scales_qty(self):
        """When the daily borrow-fee accrual at the proposed qty
        exceeds the remaining daily-loss budget, qty SCALES down to
        fit (does NOT refuse outright — same shape as the existing
        daily-loss-budget gate's scaling branch).

        Setup: distance=$25 / risk_pct=0.01 / balance=$10_000 →
        raw qty = 4 BTC; SL-loss = 4·$25 = $100 ≤ $100 budget → passes
        the daily-loss gate. apr=36.5% → 0.1%/day. Fee at qty=4 is
        4·$50_000·0.001 = $200 > $100 budget → scale to fit:
        qty = 100 / ($50_000 · 0.001) = 2 BTC. Floor 2.000.
        """
        rm = RiskManager({
            "risk_pct": 0.01,
            "min_balance_usd": 50,
            "daily_usd": 100,
            "borrow_fee_apr_pct": 36.5,  # exactly 0.1%/day
            "liquidation_buffer_pct": 0.0,  # isolation
            "max_borrow_btc": 100.0,       # disable cap for isolation
        })
        pkg = _pkg(direction="short", entry=50_000, sl=49_975)
        qty = rm.position_size(
            pkg, balance_usd=10_000, market_type="spot-margin",
        )
        assert qty == pytest.approx(2.0, rel=1e-3)
        assert qty > 0  # scaled, not refused

    def test_daily_loss_budget_wins_on_conflict(self):
        """Existing daily-loss-budget rule still refuses (returns 0.0)
        when the budget is exhausted, regardless of spot-margin path.

        Pins: daily-loss-budget runs BEFORE the spot-margin block, so
        ``loss_budget_remaining ≤ 0`` short-circuits to 0.0.
        """
        rm = RiskManager({
            "risk_pct": 0.01,
            "min_balance_usd": 50,
            "daily_usd": 100,
        })
        rm.daily_pnl = -150.0  # already past the cap
        pkg = _pkg(direction="long", entry=50_000, sl=49_500)
        qty = rm.position_size(
            pkg, balance_usd=10_000, market_type="spot-margin",
        )
        assert qty == 0.0

    def test_min_qty_floor_respected(self):
        """When the risk-based qty rounds below ``min_qty``, the
        sizer floors UP to ``min_qty`` (existing behavior preserved
        on the spot-margin path).

        risk_pct=0.0001, balance=$200 → risk_usd=$0.02; distance=$500
        → raw qty = 0.00004 → floor to 0.000 → max(0.001, 0.000) = 0.001.
        """
        rm = RiskManager({
            "risk_pct": 0.0001,
            "min_balance_usd": 50,
            "daily_usd": 1_000_000_000,
            "liquidation_buffer_pct": 0.0,
        })
        pkg = _pkg(direction="long", entry=50_000, sl=49_500)
        qty = rm.position_size(
            pkg, balance_usd=200, market_type="spot-margin",
        )
        assert qty == pytest.approx(0.001)

    def test_max_borrow_btc_caps_qty(self):
        """When risk-based qty exceeds ``max_borrow_btc``, qty is
        clipped to the cap (sizing cap, not refusal).

        risk_pct=1.0, balance=$100_000, distance=$500 → raw qty =
        $1000/$500 = 200 BTC. ``max_borrow_btc=0.05`` → cap to 0.05.
        """
        rm = RiskManager({
            "risk_pct": 1.0,
            "min_balance_usd": 50,
            "daily_usd": 1_000_000_000,
            "max_borrow_btc": 0.05,
            "liquidation_buffer_pct": 0.0,
        })
        pkg = _pkg(direction="short", entry=50_000, sl=50_500)
        qty = rm.position_size(
            pkg, balance_usd=100_000, market_type="spot-margin",
        )
        assert qty == pytest.approx(0.05)

    def test_below_min_balance_returns_zero(self):
        """Existing ``min_balance_usd`` refusal unchanged on the
        spot-margin path. balance < min → 0.0; balance ≥ min → > 0.0.
        """
        rm = RiskManager({"risk_pct": 0.01, "min_balance_usd": 50})
        pkg = _pkg(direction="long", entry=50_000, sl=49_500)
        assert rm.position_size(
            pkg, balance_usd=49.99, market_type="spot-margin",
        ) == 0.0
        assert rm.position_size(
            pkg, balance_usd=10.0, market_type="spot-margin",
        ) == 0.0
        assert rm.position_size(
            pkg, balance_usd=0.0, market_type="spot-margin",
        ) == 0.0
        # At/above min — sizes.
        assert rm.position_size(
            pkg, balance_usd=50.0, market_type="spot-margin",
        ) > 0


# ---------------------------------------------------------------------------
# Non-spot-margin regression — bit-identical to pre-T2 sizing
# ---------------------------------------------------------------------------


class TestNonSpotMarginRegression:
    """Default ``market_type="spot"`` keeps the S-026 G2 / G3 contract
    bit-identical for the three core invariants."""

    def test_default_market_type_unchanged(self):
        """No ``market_type`` kwarg → existing risk-pct math applies.

        S-026 G2 invariant 1 (risk-proportional sizing): two balances
        → two qtys, each = balance · risk_pct / risk_distance.
        """
        rm = RiskManager({"risk_pct": 0.01, "min_balance_usd": 50})
        pkg = _pkg(direction="long", entry=50_000, sl=49_500)
        qty_big = rm.position_size(pkg, balance_usd=10_000)
        qty_small = rm.position_size(pkg, balance_usd=1_000)
        assert qty_big == pytest.approx(0.2, rel=1e-3)
        assert qty_small == pytest.approx(0.02, rel=1e-3)

    def test_explicit_market_type_spot_unchanged(self):
        """``market_type="spot"`` does not trigger spot-margin kernel.

        S-026 G2 invariant 2 (no max-position clamp): a tiny
        ``max_borrow_btc`` must NOT cap a non-spot-margin qty.
        """
        rm = RiskManager({
            "risk_pct": 0.01,
            "min_balance_usd": 50,
            "max_borrow_btc": 0.001,  # would cap on spot-margin
        })
        pkg = _pkg(direction="long", entry=50_000, sl=49_500)
        qty = rm.position_size(pkg, balance_usd=10_000, market_type="spot")
        # 0.2 BTC, NOT clipped to 0.001.
        assert qty == pytest.approx(0.2, rel=1e-3)

    def test_floor_rounding_invariant(self):
        """S-026 G3 invariant: floor (not banker's) rounding so realised
        risk never exceeds the cap by one step.

        risk_pct=0.01, balance=$10_000, distance=$499 → raw qty =
        100/499 = 0.20040... → floor to 0.200 (qty_precision=3).
        """
        rm = RiskManager({"risk_pct": 0.01, "min_balance_usd": 50})
        pkg = _pkg(direction="long", entry=50_000, sl=49_501)
        qty = rm.position_size(pkg, balance_usd=10_000)
        assert qty == pytest.approx(0.200)

    def test_smoke_test_bypass_invariant(self):
        """``meta.is_test=True`` bypasses sizing on BOTH paths.

        Pin both the default (non-spot-margin) and the explicit
        spot-margin path so future refactors keep the smoke-test
        contract.
        """
        rm = RiskManager({"risk_pct": 0.01, "min_balance_usd": 50})
        pkg = _pkg(
            direction="long",
            meta={"is_test": True, "test_qty": 0.0001},
        )
        # Smoke test bypasses min_balance_usd on both paths — the
        # whole point is to exercise the live plumbing without sizing
        # real risk into the account.
        assert rm.position_size(pkg, balance_usd=0.0) == pytest.approx(0.0001)
        assert rm.position_size(
            pkg, balance_usd=0.0, market_type="spot-margin",
        ) == pytest.approx(0.0001)


# ---------------------------------------------------------------------------
# Sanity — defaults are imported and equal to the module constants
# (T1 already pins the storage contract; this re-pins it through the
# sizer's lens so a regression that drifts the defaults is caught here).
# ---------------------------------------------------------------------------


class TestDefaultsStillMatchT1Contract:
    def test_defaults_match_t1(self):
        rm = RiskManager({})
        assert rm.max_borrow_btc == DEFAULT_MAX_BORROW_BTC
        assert rm.borrow_fee_apr_pct == DEFAULT_BORROW_FEE_APR_PCT
        assert rm.liquidation_buffer_pct == DEFAULT_LIQUIDATION_BUFFER_PCT
