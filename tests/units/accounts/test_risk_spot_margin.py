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

        Pass ``available_usd=2_000`` (S-049) so the new
        notional-vs-available cap does NOT fire (notional $1000 ≤
        $2000 available) — that lets the buffer rule (rule 4) actually
        get exercised on this scenario instead of being short-circuited
        by the cap. The buffer check is the contract this test pins.
        """
        rm = RiskManager({
            "risk_pct": 1.0,
            "min_balance_usd": 50,
            "daily_usd": 1_000_000_000,
            "liquidation_buffer_pct": 30.0,  # explicit per § 7
        })
        pkg = _pkg(direction="long", entry=50_000, sl=40_000)
        qty = rm.position_size(
            pkg, balance_usd=200,
            market_type="spot-margin",
            available_usd=2_000,
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

        S-053: pass an explicit oversized ``available_usd`` so the
        new SHORT-side notional cap (rule 3) doesn't fire — this
        test isolates rule 2 (borrow-fee scaling).
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
            available_usd=1_000_000_000.0,
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


# ---------------------------------------------------------------------------
# S-049 — ``available_usd`` notional-vs-available cap.
#
# Diagnoses + closes the live recurrence of Bybit ErrCode 170131
# ("Insufficient balance") on ``bybit_2``: with ``isLeverage=1``
# already in the request post-T3, the matching engine still validates
# ``cost <= availableBalance`` before consulting borrow capacity, and
# the pre-S-049 sizer used ``walletBalance - locked`` (free cash) —
# zero fee headroom and no borrow capacity. The S-049 kernel cap
# ``qty * entry <= available_usd`` clips qty so notional fits inside
# the live exchange-side ``availableBalance`` (collateral + USDT
# borrow capacity, less the caller's fee buffer).
# ---------------------------------------------------------------------------


class TestAvailableUsdCap:
    """``available_usd`` is the live exchange-side ``availableBalance``
    in USDT terms (collateral + USDT borrow capacity, less the
    caller's fee headroom buffer). Distinct from ``balance_usd``
    (collateral), which still drives the liquidation-distance math.
    """

    def test_long_clipped_when_notional_exceeds_available(self):
        """Bug-recurrence scenario: free USDT $177, no borrow line,
        narrow risk distance produces a qty whose notional exceeds
        the buffered available cash.

        risk_pct=0.01, balance=$177, distance=$50 → raw qty = 1.77/50
        = 0.0354 BTC → notional = 0.0354 * $50_000 = $1770. Without
        the cap, Bybit returns 170131. With ``available_usd=$176.12``
        (= 177 * 0.995, no borrow), the kernel clips qty to
        floor($176.12 / $50_000) = floor(0.00352) = 0.003
        (qty_precision=3).
        """
        rm = RiskManager({
            "risk_pct": 0.01,
            "min_balance_usd": 50,
            "daily_usd": 1_000_000_000,
        })
        pkg = _pkg(direction="long", entry=50_000, sl=49_950)
        qty = rm.position_size(
            pkg, balance_usd=177.0,
            market_type="spot-margin",
            available_usd=177.0 * 0.995,
        )
        assert qty == pytest.approx(0.003, rel=1e-3)
        # Notional now fits inside the buffered available USDT.
        assert qty * pkg.entry <= 177.0 * 0.995 + 1e-9

    def test_long_uses_borrow_capacity_when_provided(self):
        """``available_usd`` includes USDT borrow capacity → sizer can
        place a notional that exceeds free cash.

        Free $177 + borrow $400 → buffered $574.115. risk-pct math
        produces 0.0354 BTC ($1770 notional); cap clips to
        floor($574.115 / $50_000) = 0.011 BTC. Without the borrow
        line (T2 contract), the cap would have hit at $176.12 →
        0.003 BTC. So the borrow line is materially used.
        """
        rm = RiskManager({
            "risk_pct": 0.01,
            "min_balance_usd": 50,
            "daily_usd": 1_000_000_000,
        })
        pkg = _pkg(direction="long", entry=50_000, sl=49_950)
        qty = rm.position_size(
            pkg, balance_usd=177.0,
            market_type="spot-margin",
            available_usd=(177.0 + 400.0) * 0.995,
        )
        assert qty == pytest.approx(0.011, rel=1e-3)

    def test_default_available_usd_falls_back_to_balance(self):
        """Pre-S-049 callers (no ``available_usd``) get the same qty
        as a caller passing ``available_usd == balance_usd``.
        Backward-compat invariant for T2's sizing contract.
        """
        rm = RiskManager({
            "risk_pct": 0.01,
            "min_balance_usd": 50,
            "daily_usd": 1_000_000_000,
        })
        pkg = _pkg(direction="long", entry=50_000, sl=49_950)
        qty_default = rm.position_size(
            pkg, balance_usd=10_000.0, market_type="spot-margin",
        )
        qty_explicit_eq = rm.position_size(
            pkg, balance_usd=10_000.0,
            market_type="spot-margin",
            available_usd=10_000.0,
        )
        assert qty_default == qty_explicit_eq

    def test_short_clipped_by_base_side_available(self):
        """S-053: shorts ALSO clip against ``available_usd``. The
        caller now passes a base-side primitive (free_base_usd +
        base_borrow_capacity, post-fee-buffer) for shorts, so the
        cap stops Bybit from rejecting with 170131 once an open
        spot-margin short has consumed part of the BTC borrow line.

        risk_pct=0.01, balance=$10_000, distance=$1000 → raw qty
        = 100/1000 = 0.1 BTC ($5_000 notional). With
        ``available_usd=2_500`` (e.g. $500 free BTC + $2_000
        borrow capacity, less buffer), the cap clips qty to
        floor($2_500 / $50_000) = 0.05 BTC.
        """
        rm = RiskManager({
            "risk_pct": 0.01,
            "min_balance_usd": 50,
            "max_borrow_btc": 1.0,
            "daily_usd": 1_000_000_000,
        })
        pkg = _pkg(direction="short", entry=50_000, sl=51_000)
        qty = rm.position_size(
            pkg, balance_usd=10_000.0,
            market_type="spot-margin",
            available_usd=2_500.0,
        )
        assert qty == pytest.approx(0.05, rel=1e-3)
        assert qty * pkg.entry <= 2_500.0 + 1e-9

    def test_short_uncapped_when_available_covers_notional(self):
        """When the base-side availability comfortably exceeds the
        risk-sized notional, the cap doesn't fire — sizing falls
        through to the standard risk_pct math.
        """
        rm = RiskManager({
            "risk_pct": 0.01,
            "min_balance_usd": 50,
            "max_borrow_btc": 1.0,
            "daily_usd": 1_000_000_000,
        })
        pkg = _pkg(direction="short", entry=50_000, sl=51_000)
        qty = rm.position_size(
            pkg, balance_usd=10_000.0,
            market_type="spot-margin",
            available_usd=1_000_000.0,
        )
        assert qty == pytest.approx(0.1, rel=1e-3)

    def test_below_min_qty_after_clip_returns_zero(self):
        """Clipped qty below ``min_qty`` floor refuses the trade
        (returns 0.0, same shape as the existing ``min_balance_usd``
        refusal — risk-manager rule, not new gate).
        """
        rm = RiskManager({
            "risk_pct": 0.01,
            "min_balance_usd": 5.0,
            "min_qty": 0.001,
            "daily_usd": 1_000_000_000,
        })
        pkg = _pkg(direction="long", entry=50_000, sl=49_950)
        qty = rm.position_size(
            pkg, balance_usd=10.0,
            market_type="spot-margin",
            available_usd=10.0 * 0.995,
        )
        assert qty == 0.0

    def test_non_spot_margin_ignores_available_usd(self):
        """Non-spot-margin path is bit-identical to pre-S-049: passing
        ``available_usd`` has no effect because the cap lives inside
        ``_apply_spot_margin_rules`` (which only fires on spot-margin).
        """
        rm = RiskManager({
            "risk_pct": 0.01,
            "min_balance_usd": 50,
            "daily_usd": 1_000_000_000,
        })
        pkg = _pkg(direction="long", entry=50_000, sl=49_500)
        qty_with = rm.position_size(
            pkg, balance_usd=10_000.0,
            market_type="spot",
            available_usd=1.0,  # would clip on spot-margin
        )
        qty_without = rm.position_size(
            pkg, balance_usd=10_000.0, market_type="spot",
        )
        assert qty_with == qty_without
        # Sanity: unclipped qty is the standard 0.2 BTC for $1000 risk
        # over a $500 distance.
        assert qty_with == pytest.approx(0.2, rel=1e-3)


# ---------------------------------------------------------------------------
# S-053 — post-restart-stable-sizing contract.
#
# Before the fix: when an open spot-margin SHORT had already credited
# its borrowed-coin sale proceeds to free USDT, the coordinator passed
# the inflated free-USDT figure as ``balance_usd`` and the next short
# sized ~6× too big — Bybit rejected with 170131. The ratio observed
# in the field on 2026-05-08 was 0.058 / 0.009 = 6.4× across two
# consecutive ticks on the same wallet.
#
# These tests pin the new contract directly at the kernel level so a
# future refactor of the coordinator/override doesn't regress it:
#
#   1. ``balance_usd`` represents wallet *net equity*. When the same
#      net equity is fed twice (the only thing that should be stable
#      across an open borrow position), qty is unchanged.
#   2. The new SHORT-side ``available_usd`` cap clips qty when the
#      live BTC borrow line shrinks — no Bybit 170131.
# ---------------------------------------------------------------------------


class TestPostRestartStableSizing:
    """S-053 — successive shorts on the same net-equity wallet must
    produce qtys whose only variance is risk-distance, NOT a 6× jump
    caused by Bybit crediting borrow proceeds to the operator's free
    USDT line.
    """

    def test_short_qty_stable_across_open_short(self):
        """Pre-S-053 reproducer: identical wallet net equity →
        identical qty on consecutive ticks, even when the operator's
        free USDT has been inflated by an open short's sale proceeds.
        Field measurement on 2026-05-08: pre-fix the second tick
        sized 6.4× the first because the caller passed inflated
        free USDT as ``balance_usd``.

        The kernel's contract: sizing math is a pure function of
        ``balance_usd``. The fix lives one layer up — the coordinator
        now passes ``total_account_usd`` (Bybit ``totalEquity``), which
        is borrow-state-invariant, instead of free USDT. This test
        pins that as long as the caller passes the same net equity
        twice, the kernel returns the same qty.
        """
        rm = RiskManager({
            "risk_pct": 0.01,
            "min_balance_usd": 50,
            "max_borrow_btc": 1.0,
            "daily_usd": 1_000_000_000,
        })
        pkg = _pkg(
            direction="short",
            entry=79_850.0, sl=79_922.74,    # risk_distance ≈ 72.74
        )
        # available_usd large enough that the SHORT-side cap doesn't
        # fire on either tick — isolates the test to the
        # net-equity-stability contract.
        large_available = 1_000_000.0
        qty_1 = rm.position_size(
            pkg, balance_usd=194.0,
            market_type="spot-margin",
            available_usd=large_available,
            total_account_usd=194.0,
        )
        qty_2 = rm.position_size(
            pkg, balance_usd=194.0,
            market_type="spot-margin",
            available_usd=large_available,
            total_account_usd=194.0,
        )
        assert qty_1 == qty_2
        # Sanity: with risk_pct=0.01, 194 × 0.01 / 72.74 ≈ 0.0267 →
        # floor(qty_precision=3) = 0.026.
        assert qty_1 == pytest.approx(0.026, abs=1e-3)

    def test_short_clipped_when_btc_borrow_line_exhausted(self):
        """When repeated shorts have consumed the BTC borrow line,
        the SHORT-side ``available_usd`` cap clips qty so the order
        fits Bybit's matching engine. Returns 0.0 (refusal) only
        when the clipped qty falls below ``min_qty``.
        """
        rm = RiskManager({
            "risk_pct": 0.01,
            "min_balance_usd": 50,
            "min_qty": 0.001,
            "max_borrow_btc": 1.0,
            "daily_usd": 1_000_000_000,
        })
        pkg = _pkg(
            direction="short",
            entry=80_000.0, sl=80_080.0,    # distance $80
        )
        # Risk math wants qty = 10_000 × 0.01 / 80 = 1.25 BTC.
        # Borrow line has only $400 remaining (≈ 0.005 BTC at $80k).
        qty = rm.position_size(
            pkg, balance_usd=10_000.0,
            market_type="spot-margin",
            available_usd=400.0,
            total_account_usd=10_000.0,
        )
        # 400 / 80_000 = 0.005 → floor 0.005.
        assert qty == pytest.approx(0.005, abs=1e-3)
        # Submitted notional fits inside the live availability.
        assert qty * pkg.entry <= 400.0 + 1e-9

    def test_total_account_usd_preferred_over_inflated_balance(self):
        """The min_balance_usd gate's S-052 contract — total equity
        wins over the (possibly inflated) ``balance_usd`` — also
        means the gate fires correctly on a wallet whose free USDT
        looks artificially fat post-borrow. This pins the
        ordering: gate uses total_account_usd, sizing uses
        balance_usd, both are net-equity primitives in S-053.
        """
        rm = RiskManager({
            "risk_pct": 0.01,
            "min_balance_usd": 100.0,
            "daily_usd": 1_000_000_000,
        })
        pkg = _pkg(direction="short", entry=80_000, sl=80_500)
        # Net equity below the gate, free USDT inflated by sale
        # proceeds — must REFUSE, not size off the inflated cash.
        qty = rm.position_size(
            pkg, balance_usd=80.0,    # net equity
            market_type="spot-margin",
            available_usd=10_000.0,
            total_account_usd=80.0,
        )
        assert qty == 0.0


# ---------------------------------------------------------------------------
# S-049 — wallet-fetch shape: borrow-capacity fields added.
# ---------------------------------------------------------------------------


class TestFetchSpotCoinBalancesBorrow:
    """``_fetch_spot_coin_balances`` now returns ``quote_borrow_usd``
    and ``base_borrow_usd`` so the coordinator can build the live
    ``available_usd`` primitive. Cash-spot wallets (no borrow line)
    return 0.0 for both — backward-compatible.
    """

    @staticmethod
    def _client_with(usdt_row: dict, base_row: dict):
        """Fake Bybit client whose ``get_wallet_balance`` returns a
        UTA-shaped response with the supplied coin rows."""
        class _C:
            def get_wallet_balance(self, **_):
                return {
                    "result": {
                        "list": [{
                            "coin": [usdt_row, base_row],
                        }],
                    },
                }
        return _C()

    def test_uta_spot_margin_response_carries_borrow_capacity(self):
        from src.units.accounts.execute import _fetch_spot_coin_balances
        client = self._client_with(
            usdt_row={
                "coin": "USDT", "walletBalance": "177", "locked": "0",
                "usdValue": "177", "availableToBorrow": "400",
            },
            base_row={
                "coin": "BTC", "walletBalance": "0.001", "locked": "0",
                "usdValue": "50", "availableToBorrow": "0.008",
            },
        )
        bal = _fetch_spot_coin_balances(client, "BTCUSDT")
        assert bal["quote_usdt"] == pytest.approx(177.0)
        assert bal["quote_borrow_usd"] == pytest.approx(400.0)
        # 0.008 BTC * ($50 / 0.001) = $400.
        assert bal["base_borrow_usd"] == pytest.approx(400.0)

    def test_cash_spot_response_returns_zero_borrow(self):
        """Classic accounts (or UTA with margin off) report empty/missing
        ``availableToBorrow`` → both borrow fields default to 0.0,
        so the coordinator's ``available_usd`` collapses to free cash
        × buffer (matches sell-side semantics)."""
        from src.units.accounts.execute import _fetch_spot_coin_balances
        client = self._client_with(
            usdt_row={
                "coin": "USDT", "walletBalance": "177", "locked": "0",
                "usdValue": "177",
                # availableToBorrow absent
            },
            base_row={
                "coin": "BTC", "walletBalance": "0.001", "locked": "0",
                "usdValue": "50",
                "availableToBorrow": "",  # empty string
            },
        )
        bal = _fetch_spot_coin_balances(client, "BTCUSDT")
        assert bal["quote_usdt"] == pytest.approx(177.0)
        assert bal["quote_borrow_usd"] == 0.0
        assert bal["base_borrow_usd"] == 0.0

    def test_malformed_borrow_value_falls_back_to_zero(self):
        """Best-effort: any parse failure on ``availableToBorrow``
        returns 0.0 so the sizer's behaviour falls back to free-cash
        only — never larger than today.
        """
        from src.units.accounts.execute import _fetch_spot_coin_balances
        client = self._client_with(
            usdt_row={
                "coin": "USDT", "walletBalance": "177", "locked": "0",
                "usdValue": "177", "availableToBorrow": "not-a-number",
            },
            base_row={
                "coin": "BTC", "walletBalance": "0.001", "locked": "0",
                "usdValue": "50", "availableToBorrow": "-0.5",
            },
        )
        bal = _fetch_spot_coin_balances(client, "BTCUSDT")
        assert bal["quote_borrow_usd"] == 0.0
        assert bal["base_borrow_usd"] == 0.0

    def test_legacy_caller_unaffected(self):
        """Existing fields keep their pre-S-049 values byte-for-byte —
        S-049 only ADDS fields, never reshapes existing ones.
        """
        from src.units.accounts.execute import _fetch_spot_coin_balances
        client = self._client_with(
            usdt_row={
                "coin": "USDT", "walletBalance": "200", "locked": "20",
                "usdValue": "200",
            },
            base_row={
                "coin": "BTC", "walletBalance": "0.5", "locked": "0.1",
                "usdValue": "25000",
            },
        )
        bal = _fetch_spot_coin_balances(client, "BTCUSDT")
        # Free USDT = 200 - 20 = 180.
        assert bal["quote_usdt"] == pytest.approx(180.0)
        # Free BTC = 0.5 - 0.1 = 0.4.
        assert bal["base_qty"] == pytest.approx(0.4)
        # base_usd_value scaled to free portion: 25000 * (0.4/0.5) = 20000.
        assert bal["base_usd_value"] == pytest.approx(20_000.0)


# ---------------------------------------------------------------------------
# S-049 — buy-side safety buffer constant.
# ---------------------------------------------------------------------------


class TestBuySafetyBuffer:
    def test_buffer_constant_present_and_below_one(self):
        """The buffer is the headroom factor applied to free USDT (and
        USDT borrow capacity) before the sizer treats it as
        ``available_usd``. Must be < 1.0 (otherwise no headroom) and
        > 0.99 (otherwise live position size shrinks materially).
        """
        from src.units.accounts.execute import _SPOT_BUY_SAFETY_BUFFER
        assert 0.99 < _SPOT_BUY_SAFETY_BUFFER < 1.0

    def test_buffer_matches_sell_side(self):
        """Sell side has ``_SPOT_SELL_SAFETY_BUFFER`` = 0.995. The buy
        side mirrors it so both directions leave the same headroom for
        fees + slippage.
        """
        from src.units.accounts.execute import (
            _SPOT_BUY_SAFETY_BUFFER,
            _SPOT_SELL_SAFETY_BUFFER,
        )
        assert _SPOT_BUY_SAFETY_BUFFER == _SPOT_SELL_SAFETY_BUFFER
